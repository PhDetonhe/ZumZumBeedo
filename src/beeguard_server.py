"""BeeGuard: recebe WAV por HTTP, analisa e registra em MySQL."""
import argparse
import hashlib
import io
import json
import os
import re
import tempfile
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from flask import Flask, jsonify, request
from audio_pipeline import features, quality, RATE, WINDOW, VERSION

ROOT = Path(__file__).resolve().parents[1]


def read_config():
    path = ROOT / 'config' / 'beeguard_config.json'
    if not path.exists():
        raise ValueError('Copie config/beeguard_config.example.json para config/beeguard_config.json e configure o MySQL.')
    return json.loads(path.read_text(encoding='utf-8-sig'))


class MySQLStore:
    def __init__(self, config):
        import mysql.connector
        self.connector=mysql.connector
        self.config=config
        self.database=config.get('database','beeguard')
        if not re.fullmatch(r'[A-Za-z0-9_]{1,64}',self.database):
            raise ValueError('Nome de banco inválido')

    def connect(self, database=True):
        cfg=self.config
        connection=self.connector.connect(host=cfg.get('host','127.0.0.1'),port=int(cfg.get('port',3306)),
            user=cfg['user'],password=cfg.get('password',''),
            database=self.database if database else None,connection_timeout=5)
        cursor=connection.cursor();cursor.execute("SET time_zone = '+00:00'");cursor.close()
        return connection

    def init(self):
        connection=self.connect(database=False)
        try:
            cursor=connection.cursor()
            cursor.execute(f'CREATE DATABASE IF NOT EXISTS `{self.database}` CHARACTER SET utf8mb4')
            cursor.execute(f'USE `{self.database}`')
            cursor.execute('''CREATE TABLE IF NOT EXISTS beeguard_recordings (
                id CHAR(36) PRIMARY KEY,
                hive_id VARCHAR(64) NOT NULL,
                captured_at DATETIME(6) NOT NULL,
                received_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
                audio_sha256 CHAR(64) NOT NULL,
                audio_path VARCHAR(512) NOT NULL,
                sample_rate INT NOT NULL,
                duration_seconds DOUBLE NOT NULL,
                quality_status VARCHAR(16) NOT NULL,
                quality_json JSON NOT NULL,
                features_json JSON NOT NULL,
                model_status VARCHAR(32) NOT NULL DEFAULT 'not_enabled',
                INDEX idx_hive_time (hive_id, captured_at)
            ) ENGINE=InnoDB''')
            connection.commit()
            cursor.close()
        finally:connection.close()

    def healthy(self):
        connection=self.connect()
        try:
            cursor=connection.cursor();cursor.execute('SELECT 1');cursor.fetchone();cursor.close()
        finally:connection.close()

    def get(self, clip_id):
        connection=self.connect()
        try:
            cursor=connection.cursor(dictionary=True)
            cursor.execute('SELECT * FROM beeguard_recordings WHERE id=%s',(clip_id,))
            row=cursor.fetchone();cursor.close()
            return serialize(row) if row else None
        finally:connection.close()

    def insert(self, row):
        connection=self.connect()
        try:
            cursor=connection.cursor()
            cursor.execute('''INSERT INTO beeguard_recordings
                (id,hive_id,captured_at,audio_sha256,audio_path,sample_rate,duration_seconds,
                quality_status,quality_json,features_json,model_status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                (row['id'],row['hive_id'],row['captured_at'],row['audio_sha256'],row['audio_path'],
                 row['sample_rate'],row['duration_seconds'],row['quality_status'],
                 json.dumps(row['quality'],allow_nan=False),json.dumps(row['features'],allow_nan=False),row['model_status']))
            connection.commit();cursor.close()
        except self.connector.Error as exc:
            connection.rollback()
            if exc.errno!=1062:raise
            existing=self.get(row['id'])
            if not same_record(existing,row):raise ValueError('ID já usado para outro áudio ou colmeia')
            return False
        finally:connection.close()
        return True

    def latest(self, hive, limit):
        connection=self.connect()
        try:
            cursor=connection.cursor(dictionary=True)
            sql='SELECT * FROM beeguard_recordings'
            params=[]
            if hive:sql+=' WHERE hive_id=%s';params.append(hive)
            sql+=' ORDER BY captured_at DESC LIMIT %s';params.append(limit)
            cursor.execute(sql,tuple(params));rows=cursor.fetchall();cursor.close()
            return [serialize(r) for r in rows]
        finally:connection.close()


def serialize(row):
    row=dict(row)
    for field in ['captured_at','received_at']:
        if isinstance(row.get(field),datetime):row[field]=row[field].isoformat()+'Z'
    for original,name in [('quality_json','quality'),('features_json','features')]:
        if original in row:
            value=row.pop(original)
            row[name]=json.loads(value) if isinstance(value,(str,bytes)) else value
    return row


def same_record(existing,row):
    # capturado_at também pertence à identidade do evento.
    if not existing:return False
    time=existing['captured_at']
    if isinstance(time,str):time=datetime.fromisoformat(time.replace('Z','+00:00')).replace(tzinfo=None)
    return existing['hive_id']==row['hive_id'] and existing['audio_sha256']==row['audio_sha256'] and time==row['captured_at']


def decode_audio(payload):
    try:
        with wave.open(io.BytesIO(payload),'rb') as wav:
            if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate(),wav.getnframes())!=(1,2,RATE,WINDOW):
                raise ValueError('Esperado WAV PCM16 mono, 16000 Hz, exatamente 10 segundos')
            pcm=wav.readframes(WINDOW)
            if len(pcm)!=WINDOW*2:raise ValueError('WAV incompleto')
    except (wave.Error,EOFError) as exc:raise ValueError('WAV inválido') from exc
    return np.frombuffer(pcm,dtype='<i2').astype(np.float64)/32768


def create_app(store, audio_dir):
    app=Flask(__name__)
    app.config['MAX_CONTENT_LENGTH']=1024*1024
    audio_dir=Path(audio_dir).resolve()
    audio_dir.mkdir(parents=True,exist_ok=True)

    @app.get('/health')
    def health():
        try:store.healthy()
        except Exception:return jsonify(status='unavailable',mysql='unavailable'),503
        return jsonify(status='ok',mysql='ok',model='not_enabled')

    @app.post('/api/audio')
    def receive():
        try:
            clip_id=str(uuid.UUID(request.headers.get('X-Recording-Id','')))
            hive=request.headers.get('X-Hive-Id','')
            if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',hive):raise ValueError('X-Hive-Id inválido')
            captured=datetime.fromisoformat(request.headers.get('X-Captured-At','').replace('Z','+00:00'))
            if captured.tzinfo is None:raise ValueError('X-Captured-At precisa de fuso')
            captured=captured.astimezone(timezone.utc).replace(tzinfo=None)
            payload=request.get_data()
            audio=decode_audio(payload)
            digest=hashlib.sha256(payload).hexdigest()
            row={'id':clip_id,'hive_id':hive,'captured_at':captured,'audio_sha256':digest,
                'audio_path':f'{clip_id}_{digest[:16]}.wav','sample_rate':RATE,'duration_seconds':10.0,
                'quality':quality(audio),'features':features(audio),'model_status':'not_enabled'}
            row['quality_status']=row['quality']['quality_status']
            row['features']['feature_version']=VERSION
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
        # Consulta antes de gravar: retries não duplicam eventos.
        try:
            existing=store.get(clip_id)
            if existing:
                if not same_record(existing,row):return jsonify(error='ID usado para outro evento'),409
                return jsonify(id=clip_id,status='already_stored',mysql='ok'),200
            destination=audio_dir/row['audio_path']
            with tempfile.NamedTemporaryFile(dir=audio_dir,delete=False) as tmp:
                temp=Path(tmp.name);tmp.write(payload)
            try:os.replace(temp,destination)
            finally:temp.unlink(missing_ok=True)
            inserted=store.insert(row)
        except ValueError as exc:return jsonify(error=str(exc)),409
        except Exception:
            app.logger.exception('Falha ao persistir áudio; cliente poderá reenviar')
            return jsonify(error='Persistência indisponível; áudio permanece na fila do coletor'),503
        print(f"Salvo MySQL: {hive} | {clip_id} | RMS={row['quality']['rms']:.5f}",flush=True)
        return jsonify(id=clip_id,status='stored' if inserted else 'already_stored',mysql='ok',
                       quality_status=row['quality_status'],model_status='not_enabled'),201 if inserted else 200

    @app.get('/api/recordings')
    def recordings():
        try:limit=max(1,min(100,int(request.args.get('limit','20'))))
        except ValueError:return jsonify(error='limit inválido'),400
        hive=request.args.get('hive_id')
        try:return jsonify(recordings=store.latest(hive,limit))
        except Exception:return jsonify(error='MySQL indisponível'),503

    @app.get('/')
    def index():
        return '''<!doctype html><html lang="pt-br"><meta charset="utf-8"><title>BeeGuard</title>
<style>body{background:#101923;color:#e9f1f8;font:16px system-ui;max-width:1100px;margin:40px auto;padding:20px}h1{color:#f4c65b}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #304050}.note{color:#adc0d0}</style>
<h1>BeeGuard · Monitoramento acústico</h1><p id="status">Verificando servidor…</p>
<p class="note">Gravações de 10 s · características acústicas · modelo de população desativado</p>
<table><thead><tr><th>Colmeia</th><th>Captura UTC</th><th>RMS</th><th>Frequência dominante</th><th>Qualidade técnica</th></tr></thead><tbody id="rows"></tbody></table>
<script>async function update(){try{let response=await fetch('/api/recordings');if(!response.ok)throw Error('MySQL indisponível');let data=await response.json();document.querySelector('#status').textContent='Servidor conectado · '+data.recordings.length+' registros recentes';let body=document.querySelector('#rows');body.replaceChildren();for(let r of data.recordings){let tr=document.createElement('tr');for(let value of [r.hive_id,r.captured_at,Number(r.features.rms).toFixed(5),Number(r.features.dominant_frequency_hz).toFixed(1)+' Hz',r.quality_status]){let td=document.createElement('td');td.textContent=value;tr.appendChild(td)}body.appendChild(tr)}}catch(e){document.querySelector('#status').textContent=e.message}}update();setInterval(update,3000)</script></html>'''
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-db',action='store_true')
    args=parser.parse_args()
    try:
        config=read_config();store=MySQLStore(config['mysql'])
        if args.init_db:store.init();print('Banco e tabela prontos.');raise SystemExit(0)
        store.healthy()
        app=create_app(store,ROOT/'data/live/audio')
        from waitress import serve
        print('BeeGuard disponível em http://127.0.0.1:5000',flush=True)
        serve(app,host='127.0.0.1',port=5000,threads=4)
    except Exception as exc:raise SystemExit(f'Não foi possível iniciar o servidor/MySQL: {exc}')
