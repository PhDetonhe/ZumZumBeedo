"""Coletor de campo: sessão explícita na fila e confirmação idempotente."""
import argparse
import json
import os
import threading
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime,timezone
from pathlib import Path

import serial
from acquisition.capture_audio import read_exact,wait_header,BAUD_RATE,SAMPLE_RATE,END

ROOT=Path(__file__).resolve().parents[1]


def enqueue(pcm,session,folder,captured=None):
    if len(pcm)!=SAMPLE_RATE*10*2:raise ValueError('PCM incompleto')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    rid=str(uuid.uuid4());wav=folder/(rid+'.wav');temp=folder/(rid+'.tmp')
    with wave.open(str(temp),'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(SAMPLE_RATE);w.writeframes(pcm)
    os.replace(temp,wav)
    meta=dict(id=rid,session_id=session['id'],hive_id=session['hive_id'],wav=wav.name,
              captured_at=captured or datetime.now(timezone.utc).isoformat())
    tmp=folder/(rid+'.json.tmp');tmp.write_text(json.dumps(meta),encoding='utf-8')
    marker=folder/(rid+'.pending.json');os.replace(tmp,marker)
    return marker


def upload(marker,server):
    marker=Path(marker);meta=json.loads(marker.read_text(encoding='utf-8'));path=marker.parent/meta['wav']
    if path.resolve().parent!=marker.parent.resolve():raise ValueError('Caminho de fila inválido')
    headers={
        'Content-Type':'audio/wav','X-Recording-Id':meta['id'],'X-Hive-Id':meta['hive_id'],
        'X-Captured-At':meta['captured_at'],'X-Session-Id':meta['session_id']}
    req=urllib.request.Request(server.rstrip('/')+'/api/audio',data=path.read_bytes(),method='POST',headers=headers)
    with urllib.request.urlopen(req,timeout=15) as response:result=json.load(response)
    if (result.get('mysql')!='ok' and result.get('storage')!='ok') or result.get('id')!=meta['id'] or result.get('session_id')!=meta['session_id']:
        raise ValueError('Servidor não confirmou áudio E sessão; fila preservada')
    marker.unlink();path.unlink(missing_ok=True)
    print(f"Salvo na sessão {meta['session_id']}: {meta['id']}",flush=True)


def flush(folder,server):
    for marker in sorted(Path(folder).glob('*.pending.json')):
        try:upload(marker,server)
        except urllib.error.HTTPError as exc:
            if 400<=exc.code<500:
                marker.rename(marker.with_name(marker.name.replace('.pending.json','.failed.json')))
                print(f'HTTP {exc.code}: metadados e WAV preservados para revisão em {marker.name}',flush=True)
            else:raise


def worker(folder,server,stop):
    while not stop.is_set():
        try:flush(folder,server)
        except (OSError,ValueError):print('Envio não confirmado; fila preservada.',flush=True)
        stop.wait(2)


def capture(port,hive_session,folder,stop):
    while not stop.is_set():
        try:
            with serial.Serial(port,BAUD_RATE,timeout=2) as link:
                stop.wait(2);print(f"Coletando {hive_session['hive_id']} em {port}",flush=True)
                while not stop.is_set():
                    count=wait_header(link);captured=datetime.now(timezone.utc).isoformat()
                    if count!=SAMPLE_RATE*10:continue
                    pcm=read_exact(link,count*2)
                    if read_exact(link,len(END))!=END:
                        print('Frame incompleto descartado.',flush=True);continue
                    marker=enqueue(pcm,hive_session,folder,captured)
                    print('Na fila: '+marker.name,flush=True)
        except (serial.SerialException,TimeoutError,OSError) as exc:
            print(f'Serial indisponível: {exc}; reconectando.',flush=True);stop.wait(2)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--session',required=True);p.add_argument('--port',default='COM6')
    p.add_argument('--server',default='http://127.0.0.1:5000');p.add_argument('--flush',action='store_true')
    a=p.parse_args()
    try:sid=str(uuid.UUID(a.session))
    except ValueError:p.error('Sessão precisa ser um UUID do painel')
    folder=ROOT/'data/live/pending_field'/sid;folder.mkdir(parents=True,exist_ok=True)
    if a.flush:
        try:flush(folder,a.server)
        except Exception as exc:raise SystemExit(f'Fila preservada: {exc}')
        raise SystemExit(0)
    cache=folder/'session.json'
    try:
        with urllib.request.urlopen(a.server.rstrip('/')+'/api/field/sessions/'+sid,timeout=10) as r:
            session=json.load(r)['session']
        if session['ended_at']:raise SystemExit('Sessão encerrada. Crie outra para novas capturas; use --flush para pendências.')
        cache.write_text(json.dumps(session),encoding='utf-8')
    except urllib.error.HTTPError as exc:raise SystemExit(f'Sessão recusada pelo servidor: HTTP {exc.code}')
    except (OSError,TimeoutError):
        if not cache.exists():raise SystemExit('Primeira captura exige servidor conectado para confirmar a sessão.')
        session=json.loads(cache.read_text(encoding='utf-8'));print('Servidor offline; usando sessão previamente confirmada.')
    stop=threading.Event();thread=threading.Thread(target=worker,args=(folder,a.server,stop),daemon=True);thread.start()
    try:capture(a.port,session,folder,stop)
    except KeyboardInterrupt:print('Coletor parado. Encerre a sessão no painel quando terminar.')
    finally:stop.set();thread.join(timeout=16)
