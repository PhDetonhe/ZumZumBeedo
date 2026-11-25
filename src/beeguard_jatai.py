"""BeeGuard Jataí: espectrogramas, espécie e inspeções sem diagnóstico automático."""
import argparse
import hashlib
import io
import json
import math
import os
import queue
import tempfile
import threading
import uuid
from datetime import datetime,timedelta,timezone
from pathlib import Path

import numpy as np
from flask import jsonify,request,send_file
from scipy.signal import stft
from beeguard_server import create_app,read_config,serialize,decode_audio
from beeguard_monitor import install_monitor
from beeguard_field import FieldStore,install_field
from field_dataset import ID,utc,text,checked_path

ROOT=Path(__file__).resolve().parents[1]
SPECIES={'jatai_unconfirmed':'Jataí · identificação pendente',
         'tetragonisca_angustula':'Tetragonisca angustula',
         'tetragonisca_fiebrigi':'Tetragonisca fiebrigi',
         'apis_mellifera':'Apis mellifera','unknown':'Espécie não identificada'}
META=['species_id','identified_by','colony_stage','mic_location']
PLOT_LOCK=threading.Lock()


def aware(value):
    if not isinstance(value,str):raise ValueError('Informe data e horário com fuso')
    result=datetime.fromisoformat(value.replace('Z','+00:00'))
    if result.tzinfo is None:raise ValueError('Horário precisa de fuso')
    result=utc(result)
    if result>datetime.now(timezone.utc)+timedelta(minutes=5):raise ValueError('Horário não pode estar no futuro')
    return result


def optional_number(data,key,lo,hi):
    raw=data.get(key)
    if raw is None or raw=='':return None
    if isinstance(raw,bool):raise ValueError(f'{key} inválido')
    value=float(raw)
    if not math.isfinite(value) or not lo<=value<=hi:raise ValueError(f'{key} deve estar entre {lo} e {hi}')
    return value


def session_data(data):
    if not isinstance(data,dict):raise ValueError('Envie um objeto JSON')
    row={k:text(data,k,n) for k,n in [('apiary_id',64),('hive_id',64),('device_id',64),('mic_model',64),('placement',500),('operator_name',100)]}
    for k in ('apiary_id','hive_id','device_id'):
        if not ID.fullmatch(row[k]):raise ValueError(f'{k}: use letras, números, _ ou -')
    gain=optional_number(data,'gain',.01,64)
    if gain is None:raise ValueError('Informe o ganho real do firmware')
    row.update(gain=gain,kind=data.get('kind'),notes=text(data,'notes',2000,False),
               id=str(uuid.uuid4()),started_at=datetime.now(timezone.utc).replace(tzinfo=None))
    if row['kind'] not in ('field','bench'):raise ValueError('Tipo de coleta inválido')
    species=data.get('species_id','unknown')
    if not isinstance(species,str) or species not in SPECIES:raise ValueError('Espécie inválida')
    meta=dict(species_id=species,identified_by=text(data,'identified_by',100,False),
              colony_stage=data.get('colony_stage','unknown'),mic_location=data.get('mic_location','entrance'))
    if species not in ('unknown','jatai_unconfirmed') and not meta['identified_by']:
        raise ValueError('Para espécie identificada, informe quem confirmou a identificação')
    if meta['colony_stage'] not in ('newly_installed','established','unknown'):raise ValueError('Fase da colônia inválida')
    if meta['mic_location'] not in ('inside','entrance','outside'):raise ValueError('Posição do microfone inválida')
    return row,meta


class JataiStore(FieldStore):
    def init_jatai(self):
        self.init_field()
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_species_sessions (
            session_id CHAR(36) PRIMARY KEY, species_id VARCHAR(48) NOT NULL,
            identified_by VARCHAR(100) NOT NULL, colony_stage VARCHAR(32) NOT NULL,
            mic_location VARCHAR(16) NOT NULL,
            FOREIGN KEY (session_id) REFERENCES beeguard_sessions(id)
        ) ENGINE=InnoDB''',write=True)
        self.query('''CREATE TABLE IF NOT EXISTS beeguard_inspections (
            id CHAR(36) PRIMARY KEY, hive_id VARCHAR(64) NOT NULL, observed_at DATETIME(6) NOT NULL,
            condition_observed VARCHAR(24) NOT NULL, observer VARCHAR(100) NOT NULL,
            observer_role VARCHAR(24) NOT NULL, context_tag VARCHAR(32) NOT NULL,
            temperature_c DOUBLE NULL, humidity_pct DOUBLE NULL, noise_observed VARCHAR(16) NOT NULL,
            measurement_location VARCHAR(16) NOT NULL,
            notes TEXT NOT NULL, created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
            INDEX idx_inspect_hive_time (hive_id,observed_at)
        ) ENGINE=InnoDB''',write=True)

    def metadata(self):
        return {r['session_id']:{k:r[k] for k in META} for r in self.query('SELECT * FROM beeguard_species_sessions')}

    def session(self,sid):
        row=super().session(sid)
        if row:row.update(self.metadata().get(sid,dict(species_id='unknown',identified_by='',colony_stage='unknown',mic_location='unknown')))
        return row

    def sessions(self):
        rows=super().sessions();meta=self.metadata()
        for row in rows:row.update(meta.get(row['id'],dict(species_id='unknown',identified_by='',colony_stage='unknown',mic_location='unknown')))
        return rows

    def create_species_session(self,row,meta):
        # Sessão e identificação precisam ser gravadas juntas, inclusive em caso de falha.
        con=self.connect()
        try:
            cur=con.cursor();keys=['id','apiary_id','hive_id','device_id','mic_model','gain','placement','operator_name','kind','notes','started_at']
            cur.execute('INSERT INTO beeguard_sessions ('+','.join(keys)+') VALUES ('+','.join(['%s']*len(keys))+')',tuple(row[k] for k in keys))
            cur.execute('INSERT INTO beeguard_species_sessions (session_id,'+','.join(META)+') VALUES (%s,%s,%s,%s,%s)',(row['id'],)+tuple(meta[k] for k in META))
            con.commit();cur.close()
        except Exception:con.rollback();raise
        finally:con.close()

    def records(self,sid=None,limit=None,offset=0):
        rows=super().records(sid,limit,offset);meta=self.metadata()
        for row in rows:row.update(meta.get(row['session_id'],dict(species_id='unknown',identified_by='',colony_stage='unknown',mic_location='unknown')))
        return rows

    def inspect(self,row):
        keys=['id','hive_id','observed_at','condition_observed','observer','observer_role','context_tag','temperature_c','humidity_pct','noise_observed','measurement_location','notes']
        self.query('INSERT INTO beeguard_inspections ('+','.join(keys)+') VALUES ('+','.join(['%s']*len(keys))+')',tuple(row[k] for k in keys),write=True)

    def inspections(self,hive,start=None,end=None):
        sql='SELECT * FROM beeguard_inspections WHERE hive_id=%s';args=[hive]
        if start is not None:
            sql+=' AND observed_at BETWEEN %s AND %s';args.extend([start.replace(tzinfo=None),end.replace(tzinfo=None)])
        rows=self.query(sql+' ORDER BY observed_at DESC',tuple(args))
        for r in rows:
            for k in ('observed_at','created_at'):r[k]=utc(r[k]).isoformat()
        return rows

    def hive_audio(self,hive,start,end):
        rows=self.query('''SELECT r.*,s.id AS session_id,s.kind,s.device_id,s.gain,s.placement,s.apiary_id,
            m.species_id,m.mic_location FROM beeguard_recordings r
            JOIN beeguard_recording_sessions rs ON r.id=rs.recording_id
            JOIN beeguard_sessions s ON s.id=rs.session_id
            LEFT JOIN beeguard_species_sessions m ON m.session_id=s.id
            WHERE r.hive_id=%s AND r.captured_at BETWEEN %s AND %s ORDER BY r.captured_at''',
            (hive,start.replace(tzinfo=None),end.replace(tzinfo=None)))
        return [serialize(r) for r in rows]

    def has_hive(self,hive):
        return bool(self.query('SELECT id FROM beeguard_sessions WHERE hive_id=%s LIMIT 1',(hive,)))


def spectral_data(payload,fmax=4000):
    if fmax not in (2000,4000,8000):raise ValueError('Escolha 2000, 4000 ou 8000 Hz')
    audio=decode_audio(payload)
    freq,t,z=stft(audio,fs=16000,nperseg=2048,noverlap=1536,boundary=None,padded=False)
    power=np.abs(z)**2
    db=np.clip(10*np.log10(np.maximum(power,1e-12)),-100,0)
    valid=freq<=fmax
    mean=power.mean(axis=1);mean[0]=0
    total=float(mean.sum());peak=float(freq[np.argmax(mean)]) if total>0 else None
    bands={f'{lo}_{hi}_hz':float(mean[(freq>=lo)&(freq<hi)].sum()/total) if total>0 else 0.0
           for lo,hi in [(0,500),(500,1500),(1500,4000),(4000,8001)]}
    return freq[valid],t,db[valid],dict(peak_frequency_hz=peak,band_energy_fraction=bands,
         rms=float(np.sqrt(np.mean(audio**2))),fft=2048,hop=512,fmax=fmax,
         note='Pico do áudio captado; não identifica sozinho a frequência de voo nem a saúde das abelhas.')


class Spectrograms:
    def __init__(self,store,audio_root,output):
        self.store=store;self.audio_root=Path(audio_root);self.output=Path(output)
        self.output.mkdir(parents=True,exist_ok=True);self.jobs=queue.Queue(maxsize=100)

    def read(self,rid):
        row=self.store.get(rid)
        if not row or not self.store.binding(rid):raise FileNotFoundError('Áudio sem sessão ou inexistente')
        path=checked_path(self.audio_root,row['audio_path']);payload=path.read_bytes()
        if hashlib.sha256(payload).hexdigest()!=row['audio_sha256']:raise ValueError('Hash do WAV diverge do registrado')
        return row,payload

    def png(self,rid,fmax=4000):
        row,payload=self.read(rid)
        target=self.output/f"{row['id']}_{row['audio_sha256'][:16]}_{fmax}Hz.png"
        with PLOT_LOCK:
            if target.is_file():return target
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_agg import FigureCanvasAgg
            f,t,db,summary=spectral_data(payload,fmax)
            fig=Figure(figsize=(10,4),dpi=130);FigureCanvasAgg(fig);ax=fig.subplots()
            image=ax.pcolormesh(t,f,db,shading='auto',cmap='magma',vmin=-100,vmax=0)
            ax.set(xlabel='Tempo (s)',ylabel='Frequência (Hz)',ylim=(0,fmax),xlim=(0,10),
                   title=f"{row['hive_id']} | {row['captured_at']} | FFT 2048 / hop 512")
            fig.colorbar(image,ax=ax,label='dB relativos à amplitude digital 1 (não dB SPL)')
            fig.tight_layout()
            with tempfile.NamedTemporaryFile(dir=self.output,suffix='.tmp',delete=False) as tmp:temp=Path(tmp.name)
            try:fig.savefig(temp,format='png');os.replace(temp,target)
            finally:temp.unlink(missing_ok=True)
        return target

    def enqueue(self,rid):
        try:self.jobs.put_nowait(rid)
        except queue.Full:print('Fila de espectrogramas cheia; WAV preservado. Use --render-pending.',flush=True)

    def work(self,stop):
        while not stop.is_set():
            try:rid=self.jobs.get(timeout=1)
            except queue.Empty:continue
            try:self.png(rid)
            except Exception as exc:print(f'PNG pendente {rid}: {exc}. WAV preservado.',flush=True)
            finally:self.jobs.task_done()


def install_jatai(app,store,artifacts):
    app.add_url_rule('/campo','field_v3',app.view_functions['index'])
    app.view_functions['index']=lambda:send_file(ROOT/'web/templates/beeguard_jatai.html')
    original=app.view_functions['receive']
    def receive_jatai():
        response=app.make_response(original())
        if response.status_code in (200,201):
            data=response.get_json()
            if data.get('session_id'):artifacts.enqueue(data['id'])
        return response
    app.view_functions['receive']=receive_jatai

    @app.post('/api/jatai/sessions')
    def species_session():
        try:row,meta=session_data(request.get_json(silent=True))
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
        try:store.create_species_session(row,meta);return jsonify(session=store.session(row['id'])),201
        except Exception as exc:
            if getattr(exc,'errno',None)==1062:return jsonify(error='Já existe sessão aberta nessa colmeia. Encerre-a no painel Campo.'),409
            app.logger.exception('Cadastro de espécie/sessão');return jsonify(error='Não foi possível salvar sessão e espécie/MySQL'),503

    @app.post('/api/jatai/inspections')
    def inspection():
        try:
            d=request.get_json(silent=True)
            if not isinstance(d,dict):raise ValueError('Envie um objeto JSON')
            row={k:text(d,k,n) for k,n in [('hive_id',64),('observer',100),('notes',2000)]}
            if not ID.fullmatch(row['hive_id']):raise ValueError('Colmeia inválida')
            for k,allowed in [('condition_observed',('no_concern_observed','concern_observed','not_assessed')),
                              ('observer_role',('specialist','team')),('noise_observed',('yes','no','unknown')),
                              ('measurement_location',('ambient','inside','not_measured')),
                              ('context_tag',('construction','traffic','weather','handling','relocation','other','none','unknown'))]:
                row[k]=d.get(k)
                if row[k] not in allowed:raise ValueError(f'{k} inválido')
            row.update(id=str(uuid.uuid4()),observed_at=aware(d.get('observed_at')).replace(tzinfo=None),
                       temperature_c=optional_number(d,'temperature_c',-20,70),humidity_pct=optional_number(d,'humidity_pct',0,100))
            if row['measurement_location']=='not_measured' and (row['temperature_c'] is not None or row['humidity_pct'] is not None):
                raise ValueError('Informe o local de medição da temperatura/umidade')
        except (ValueError,TypeError) as exc:return jsonify(error=str(exc)),400
        try:
            if not store.has_hive(row['hive_id']):return jsonify(error='Cadastre uma sessão desta colmeia primeiro'),400
            store.inspect(row);return jsonify(status='stored',id=row['id']),201
        except Exception:return jsonify(error='Inspeção/MySQL indisponível'),503

    @app.get('/api/jatai/history')
    def history():
        hive=request.args.get('hive_id','')
        try:
            if not ID.fullmatch(hive):raise ValueError('Colmeia inválida')
            raw=request.args.get('date')
            if raw:
                start=datetime.strptime(raw,'%Y-%m-%d').replace(tzinfo=timezone(timedelta(hours=-3))).astimezone(timezone.utc)
                end=start+timedelta(days=1)
            else:end=datetime.now(timezone.utc);start=end-timedelta(hours=24)
        except ValueError as exc:return jsonify(error=str(exc)),400
        try:
            rows=store.hive_audio(hive,start,end);points={};setups=set()
            for r in rows:
                setups.add((r['session_id'],r['device_id'],r['gain'],r['placement'],r.get('species_id')))
                if r['quality_status']!='pass':continue
                minute=utc(r['captured_at']).replace(second=0,microsecond=0).isoformat()
                points.setdefault(minute,[]).append(r)
            result=[]
            for at,rs in sorted(points.items()):
                result.append(dict(at=at,rms=float(np.median([r['quality']['rms'] for r in rs])),
                     frequency_hz=float(np.median([r['features']['dominant_frequency_hz'] for r in rs])),samples=len(rs)))
            return jsonify(hive_id=hive,series=result,recordings=len(rows),quality_review=sum(r['quality_status']!='pass' for r in rows),
                 inspections=store.inspections(hive,start,end),session_count=len(setups),
                 note='Gráfico descritivo; mudanças de sessão/posição/ganho e ruído podem alterar o sinal. Inspeção não é diagnóstico do áudio.')
        except Exception:return jsonify(error='Histórico/MySQL indisponível'),503

    @app.get('/api/jatai/recordings/<rid>/spectrum')
    def spectrum(rid):
        try:
            fmax=int(request.args.get('fmax','4000'));_,payload=artifacts.read(rid)
            f,t,db,summary=spectral_data(payload,fmax)
            return jsonify(frequencies_hz=f.tolist(),seconds=t[::2].tolist(),db=db[:,::2].round(1).tolist(),min_db=-100,max_db=0,summary=summary)
        except FileNotFoundError:return jsonify(error='WAV não encontrado'),404
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='Espectrograma indisponível'),503

    @app.get('/api/jatai/recordings/<rid>/spectrogram.png')
    def png(rid):
        try:
            fmax=int(request.args.get('fmax','4000'))
            if fmax not in (2000,4000,8000):raise ValueError('Faixa inválida')
            path=artifacts.png(rid,fmax)
            return send_file(path,mimetype='image/png',as_attachment=request.args.get('download')=='1',download_name=path.name)
        except FileNotFoundError:return jsonify(error='WAV não encontrado'),404
        except ValueError as exc:return jsonify(error=str(exc)),400
        except Exception:return jsonify(error='PNG indisponível; WAV original preservado'),503
    return app


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--init-db',action='store_true');p.add_argument('--render-pending',action='store_true');a=p.parse_args()
    try:
        store=JataiStore(read_config()['mysql'])
        if a.init_db:store.init_jatai();print('Tabelas de espécie e inspeções prontas.');raise SystemExit(0)
        store.healthy();artifacts=Spectrograms(store,ROOT/'data/live/audio',ROOT/'data/live/spectrograms')
        if a.render_pending:
            failures=0
            for row in store.records():
                try:artifacts.png(row['id'])
                except Exception as exc:failures+=1;print(f"Pendente {row['id']}: {exc}")
            print(f'Processamento encerrado; {failures} falhas.');raise SystemExit(1 if failures else 0)
        app=install_jatai(install_field(install_monitor(create_app(store,ROOT/'data/live/audio'),store),store),store,artifacts)
        stop=threading.Event();worker=threading.Thread(target=artifacts.work,args=(stop,),daemon=True);worker.start()
        # Recupera a fila de PNGs mais recentes após reinício. Para todas as gravações, --render-pending.
        for row in store.records(limit=100):artifacts.enqueue(row['id'])
        from waitress import serve
        print('BeeGuard Jataí disponível em http://127.0.0.1:5000 · modelos de diagnóstico desativados',flush=True)
        try:serve(app,host='127.0.0.1',port=5000,threads=4)
        finally:stop.set();worker.join(timeout=5)
    except Exception as exc:raise SystemExit(f'Não foi possível iniciar BeeGuard Jataí: {exc}')
