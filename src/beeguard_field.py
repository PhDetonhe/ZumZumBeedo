"""Coleta de campo, revisão e comparação; compatível com BeeGuard Monitor v2."""
import argparse
import hashlib
import json
import math
import statistics
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from flask import jsonify, request, send_file
from scipy.signal import stft
from audio_pipeline import VERSION
from beeguard_server import create_app, read_config, serialize, decode_audio
from beeguard_monitor import MonitorStore, install_monitor
from field_dataset import ID,LABELS,ACTIVITIES,CONTEXTS,FEATURES,SCHEMA,utc,text,checked_path,vector,check_session

ROOT=Path(__file__).resolve().parents[1]


def session_json(row):
    if not row:return None
    row=dict(row)
    for k in ('started_at','ended_at'):
        if row.get(k):row[k]=utc(row[k]).isoformat()
    row.pop('active_hive',None)
    row['gain']=float(row['gain'])
    return row


class FieldStore(MonitorStore):
    def init_field(self):
        self.init();self.init_monitor()
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_sessions (
            id CHAR(36) PRIMARY KEY, apiary_id VARCHAR(64) NOT NULL, hive_id VARCHAR(64) NOT NULL,
            device_id VARCHAR(64) NOT NULL, mic_model VARCHAR(64) NOT NULL, gain DOUBLE NOT NULL,
            placement VARCHAR(500) NOT NULL, operator_name VARCHAR(100) NOT NULL,
            kind VARCHAR(16) NOT NULL, notes TEXT NOT NULL,
            started_at DATETIME(6) NOT NULL, ended_at DATETIME(6) NULL,
            active_hive VARCHAR(64) GENERATED ALWAYS AS (CASE WHEN ended_at IS NULL THEN hive_id ELSE NULL END) STORED,
            UNIQUE KEY idx_active_hive (active_hive)
        ) ENGINE=InnoDB''',write=True)
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_recording_sessions (
            recording_id CHAR(36) PRIMARY KEY, session_id CHAR(36) NOT NULL,
            FOREIGN KEY (recording_id) REFERENCES beeguard_recordings(id),
            FOREIGN KEY (session_id) REFERENCES beeguard_sessions(id), INDEX idx_session (session_id)
        ) ENGINE=InnoDB''',write=True)
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_reviews (
            id BIGINT AUTO_INCREMENT PRIMARY KEY, recording_id CHAR(36) NOT NULL,
            sound_label VARCHAR(32) NOT NULL, bee_activity VARCHAR(32) NOT NULL,
            context_tag VARCHAR(32) NOT NULL, reviewer VARCHAR(100) NOT NULL,
            exclude_training BOOLEAN NOT NULL, notes TEXT NOT NULL,
            reviewed_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            FOREIGN KEY (recording_id) REFERENCES beeguard_recordings(id), INDEX idx_review (recording_id,id)
        ) ENGINE=InnoDB''',write=True)

    def session(self,sid):
        rows=self.query('SELECT * FROM beeguard_sessions WHERE id=%s',(sid,))
        return session_json(rows[0]) if rows else None

    def sessions(self):
        rows=self.query('''SELECT s.*,COUNT(rs.recording_id) AS recordings FROM beeguard_sessions s
            LEFT JOIN beeguard_recording_sessions rs ON rs.session_id=s.id GROUP BY s.id
            ORDER BY s.started_at DESC LIMIT 200''')
        return [session_json(r) for r in rows]

    def create_session(self,row):
        keys=['id','apiary_id','hive_id','device_id','mic_model','gain','placement','operator_name','kind','notes','started_at']
        self.query('INSERT INTO beeguard_sessions ('+','.join(keys)+') VALUES ('+','.join(['%s']*len(keys))+')',
                   tuple(row[k] for k in keys),write=True)

    def close_session(self,sid):
        self.query('UPDATE beeguard_sessions SET ended_at=UTC_TIMESTAMP(6) WHERE id=%s AND ended_at IS NULL',(sid,),write=True)

    def binding(self,rid):
        rows=self.query('SELECT session_id FROM beeguard_recording_sessions WHERE recording_id=%s',(rid,))
        return rows[0]['session_id'] if rows else None

    def bind(self,rid,sid):
        try:self.query('INSERT INTO beeguard_recording_sessions (recording_id,session_id) VALUES (%s,%s)',(rid,sid),write=True)
        except self.connector.Error as exc:
            if exc.errno!=1062 or self.binding(rid)!=sid:raise

    def review(self,rid,row):
        keys=['sound_label','bee_activity','context_tag','reviewer','exclude_training','notes']
        self.query('INSERT INTO beeguard_reviews (recording_id,'+','.join(keys)+') VALUES ('+','.join(['%s']*7)+')',
                   (rid,)+tuple(row[k] for k in keys),write=True)

    def records(self,sid=None,limit=None,offset=0):
        sql='''SELECT r.*,s.id AS session_id,s.apiary_id,s.device_id,s.mic_model,s.gain,s.placement,
            s.kind,s.operator_name,s.notes AS session_notes,s.started_at AS session_started_at,
            s.ended_at AS session_ended_at,v.id AS review_id,v.sound_label,v.bee_activity,v.context_tag,
            v.reviewer,v.exclude_training,v.notes AS review_notes,v.reviewed_at
            FROM beeguard_recordings r JOIN beeguard_recording_sessions rs ON rs.recording_id=r.id
            JOIN beeguard_sessions s ON s.id=rs.session_id
            LEFT JOIN beeguard_reviews v ON v.id=(SELECT MAX(v2.id) FROM beeguard_reviews v2 WHERE v2.recording_id=r.id)'''
        args=[]
        if sid:sql+=' WHERE s.id=%s';args.append(sid)
        sql+=' ORDER BY r.captured_at DESC,r.id'
        if limit:sql+=' LIMIT %s OFFSET %s';args.extend([limit,offset])
        result=[]
        for raw in self.query(sql,tuple(args)):
            row=serialize(raw)
            for k in ('reviewed_at','session_started_at','session_ended_at'):
                if row.get(k):row[k]=utc(row[k]).isoformat()
            row['gain']=float(row['gain'])
            result.append(row)
        return result


def summarize(rows):
    good=[r for r in rows if r['quality_status']=='pass']
    reviewed=[r for r in rows if r.get('review_id') is not None]
    return dict(recordings=len(rows),quality_pass=len(good),reviewed=len(reviewed),
                training_candidates=sum(r['quality_status']=='pass' and r.get('sound_label') in LABELS[:2]
                                        and not r.get('exclude_training') for r in rows),
                median_rms=statistics.median(r['quality']['rms'] for r in good) if good else None,
                median_frequency_hz=statistics.median(r['features']['dominant_frequency_hz'] for r in good) if good else None,
                sound_labels={label:sum(r.get('sound_label')==label for r in reviewed) for label in LABELS})


def spectrum(payload):
    audio=decode_audio(payload)
    freq,t,z=stft(audio,fs=16000,nperseg=1024,noverlap=768,boundary=None,padded=False)
    # Escala digital fixa; sem normalizar cada gravação pelo próprio máximo.
    values=np.clip(20*np.log10(np.maximum(np.abs(z),1e-10)),-100,0)
    selected=freq<=2000
    return dict(frequencies_hz=freq[selected].tolist(),seconds=t[::4].tolist(),
                db=values[selected,::4].round(1).tolist(),min_db=-100,max_db=0,
                scale='dB relativos à amplitude digital 1; não dB SPL',fft=1024,hop=256)


def load_model(path):
    import joblib
    bundle=joblib.load(path)
    if (bundle.get('schema')!=SCHEMA or bundle.get('feature_version')!=VERSION
            or bundle.get('features')!=FEATURES or bundle.get('target')!='sound_label'):
        raise ValueError('Modelo incompatível com os dados de campo')
    if set(bundle['model'].classes_)!=set(LABELS[:2]):raise ValueError('Classes do modelo incompatíveis')
    return bundle


def install_field(app,store,model=None,audio_dir=None):
    audio_dir=Path(audio_dir or ROOT/'data/live/audio')
    old_index=app.view_functions['index']
    app.add_url_rule('/monitor','acoustic_monitor',old_index)
    app.view_functions['index']=lambda:send_file(ROOT/'web/templates/beeguard_field.html')
    original_receive=app.view_functions['receive']
    original_health=app.view_functions['health']

    def field_health():
        response=app.make_response(original_health())
        if response.status_code==200:
            data=response.get_json()
            data['field_model']='experimental_enabled' if model else 'not_enabled'
            response.set_data(json.dumps(data))
        return response
    app.view_functions['health']=field_health

    def receive_field():
        sid=request.headers.get('X-Session-Id')
        if not sid:return original_receive() # clientes antigos continuam coletando, sem inclusão no dataset de campo
        try:
            sid=str(uuid.UUID(sid));rid=str(uuid.UUID(request.headers.get('X-Recording-Id','')))
            raw=request.headers.get('X-Captured-At','');captured=datetime.fromisoformat(raw.replace('Z','+00:00'))
            if captured.tzinfo is None:raise ValueError('Horário precisa de fuso')
            if utc(captured).timestamp()>datetime.now(timezone.utc).timestamp()+60:raise ValueError('Horário de captura no futuro')
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
        try:
            session=store.session(sid)
            if not session:return jsonify(error='Sessão inexistente'),400
            existing=store.binding(rid)
            if existing and existing!=sid:return jsonify(error='Gravação já vinculada a outra sessão'),409
            check_session(session,request.headers.get('X-Hive-Id'),captured)
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='Sessões/MySQL indisponíveis; mantenha a fila'),503
        response=app.make_response(original_receive())
        if response.status_code in (200,201):
            try:store.bind(rid,sid)
            except Exception:
                app.logger.exception('Falha ao vincular sessão; retry é idempotente')
                return jsonify(error='Vínculo da sessão indisponível; mantenha a fila'),503
            response.set_data(json.dumps({**response.get_json(),'session_id':sid}))
        return response
    app.view_functions['receive']=receive_field

    @app.get('/api/field/sessions')
    def sessions():
        try:return jsonify(sessions=store.sessions(),model_enabled=model is not None)
        except Exception:return jsonify(error='MySQL indisponível'),503

    @app.post('/api/field/sessions')
    def new_session():
        try:
            data=request.get_json(silent=True)
            if not isinstance(data,dict):raise ValueError('Envie um objeto JSON')
            row={k:text(data,k,n) for k,n in [('apiary_id',64),('hive_id',64),('device_id',64),('mic_model',64),('placement',500),('operator_name',100)]}
            for k in ('apiary_id','hive_id','device_id'):
                if not ID.fullmatch(row[k]):raise ValueError(f'{k}: use letras, números, _ ou -')
            gain=data.get('gain')
            if isinstance(gain,bool):raise ValueError('Ganho inválido')
            row['gain']=float(gain)
            if not math.isfinite(row['gain']) or not 0<row['gain']<=64:raise ValueError('Ganho deve ser maior que 0 e até 64')
            row['kind']=data.get('kind')
            if row['kind'] not in ('field','bench'):raise ValueError('Selecione campo ou bancada')
            row['notes']=text(data,'notes',2000,False)
            row.update(id=str(uuid.uuid4()),started_at=datetime.now(timezone.utc).replace(tzinfo=None))
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
        try:store.create_session(row);return jsonify(session=store.session(row['id'])),201
        except Exception as exc:
            if getattr(exc,'errno',None)==1062:return jsonify(error='Já existe uma sessão aberta nessa colmeia. Encerre-a primeiro.'),409
            app.logger.exception('Criar sessão');return jsonify(error='Não foi possível criar a sessão/MySQL'),503

    @app.get('/api/field/sessions/<sid>')
    def session_detail(sid):
        try:
            row=store.session(sid)
            return jsonify(session=row) if row else (jsonify(error='Sessão inexistente'),404)
        except Exception:return jsonify(error='MySQL indisponível'),503

    @app.post('/api/field/sessions/<sid>/close')
    def close(sid):
        try:
            if not store.session(sid):return jsonify(error='Sessão inexistente'),404
            store.close_session(sid);return jsonify(session=store.session(sid))
        except Exception:return jsonify(error='Não foi possível encerrar a sessão/MySQL'),503

    @app.get('/api/field/sessions/<sid>/recordings')
    def field_recordings(sid):
        try:
            offset=int(request.args.get('offset','0'))
            if not 0<=offset<=10000000:raise ValueError()
        except ValueError:return jsonify(error='Página inválida'),400
        try:return jsonify(recordings=store.records(sid,25,offset),summary=summarize(store.records(sid)))
        except Exception:return jsonify(error='Não foi possível consultar as gravações/MySQL'),503

    @app.get('/api/field/comparison')
    def comparison():
        ids=request.args.get('sessions','').split(',')
        if not 1<=len(ids)<=4:return jsonify(error='Escolha de 1 a 4 sessões'),400
        try:
            ids=[str(uuid.UUID(v)) for v in ids]
        except ValueError:return jsonify(error='Identificador de sessão inválido'),400
        try:
            result=[]
            for sid in ids:
                s=store.session(sid)
                if not s:return jsonify(error='Sessão inexistente'),404
                result.append(dict(session=s,summary=summarize(store.records(sid))))
            configs={(r['session']['device_id'],r['session']['mic_model'],r['session']['gain'],r['session']['placement']) for r in result}
            return jsonify(comparison=result,same_setup=len(configs)==1,
                           note='RMS digital não é medida calibrada de população. Ganho, posição, dispositivo, duração e horário afetam a comparação.')
        except Exception:return jsonify(error='Comparação/MySQL indisponível'),503

    @app.post('/api/field/recordings/<rid>/review')
    def review(rid):
        try:
            data=request.get_json(silent=True)
            if not isinstance(data,dict):raise ValueError('Envie um objeto JSON')
            row={k:data.get(k) for k in ('sound_label','bee_activity','context_tag')}
            for k,allowed in [('sound_label',LABELS),('bee_activity',ACTIVITIES),('context_tag',CONTEXTS)]:
                if row[k] not in allowed:raise ValueError(f'{k} inválido')
            row['reviewer']=text(data,'reviewer',100);row['notes']=text(data,'notes',2000,False)
            row['exclude_training']=data.get('exclude_training',False)
            if not isinstance(row['exclude_training'],bool):raise ValueError('exclude_training precisa ser booleano')
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
        try:
            if not store.binding(rid):return jsonify(error='Gravação não pertence a uma sessão de campo/bancada'),404
            store.review(rid,row);return jsonify(status='stored'),201
        except Exception:return jsonify(error='Não foi possível salvar a revisão/MySQL'),503

    def audio_path(rid):
        row=store.get(rid)
        if not row or not store.binding(rid):return None,None
        return checked_path(audio_dir,row['audio_path']),row

    @app.get('/api/field/recordings/<rid>/audio')
    def audio(rid):
        try:
            path,_=audio_path(rid)
            if path is None or not path.is_file():return jsonify(error='Áudio não encontrado'),404
            return send_file(path,mimetype='audio/wav',conditional=True)
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='Áudio/MySQL indisponível'),503

    @app.get('/api/field/recordings/<rid>/spectrogram')
    def spectrogram(rid):
        try:
            path,row=audio_path(rid)
            if path is None or not path.is_file():return jsonify(error='Áudio não encontrado'),404
            payload=path.read_bytes()
            if hashlib.sha256(payload).hexdigest()!=row['audio_sha256']:return jsonify(error='Integridade do áudio inválida'),409
            return jsonify(spectrum(payload))
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='Espectrograma/MySQL indisponível'),503

    @app.get('/api/field/recordings/<rid>/prediction')
    def prediction(rid):
        if model is None:return jsonify(enabled=False,message='Modelo ainda não carregado')
        try:
            row=store.get(rid)
            if not row or not store.binding(rid):return jsonify(error='Gravação não encontrada'),404
            if row['quality_status']!='pass':return jsonify(enabled=True,status='quality_review',message='Revise a qualidade antes de interpretar o modelo')
            label=model['model'].predict([vector(row['features'])])[0]
            return jsonify(enabled=True,label=str(label),model_version=model['model_version'],
                           message='Classificação experimental do áudio; não diagnostica saúde, população ou ambiente.')
        except Exception:return jsonify(error='Não foi possível executar o modelo'),503
    return app


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--init-db',action='store_true')
    parser.add_argument('--model',help='Modelo próprio gerado por train_field.py')
    args=parser.parse_args()
    try:
        store=FieldStore(read_config()['mysql'])
        if args.init_db:
            store.init_field();print('Tabelas de áudio, monitor e coleta prontas.');raise SystemExit(0)
        store.healthy()
        bundle=load_model(args.model) if args.model else None
        app=install_field(install_monitor(create_app(store,ROOT/'data/live/audio'),store),store,bundle)
        from waitress import serve
        print('BeeGuard Campo disponível em http://127.0.0.1:5000',flush=True)
        serve(app,host='127.0.0.1',port=5000,threads=4)
    except Exception as exc:raise SystemExit(f'Não foi possível iniciar coleta/MySQL: {exc}')

