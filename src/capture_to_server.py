"""Captura USB serial existente -> fila local persistente -> servidor HTTP."""
import argparse
import json
import os
import re
import threading
import time
import urllib.error
import urllib.request
import uuid
import wave
from datetime import datetime, timezone
from pathlib import Path

import serial
from acquisition.capture_audio import read_exact, wait_header, BAUD_RATE, SAMPLE_RATE, END, SERIAL_PORT

from audio_pipeline import RECORD_SAMPLES, WINDOW

ROOT=Path(__file__).resolve().parents[1]


def enqueue_pcm(pcm, hive, folder, captured_at=None):
    if len(pcm) not in (WINDOW*2,RECORD_SAMPLES*2):raise ValueError('Esperado PCM de 30 ou 10 s')
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    clip_id=str(uuid.uuid4())
    final=folder/(clip_id+'.wav');temp=folder/(clip_id+'.tmp')
    with wave.open(str(temp),'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(SAMPLE_RATE);wav.writeframes(pcm)
    os.replace(temp,final)
    metadata={'id':clip_id,'hive_id':hive,
        'captured_at':captured_at or datetime.now(timezone.utc).isoformat(),'wav':final.name}
    temp_json=folder/(clip_id+'.json.tmp')
    temp_json.write_text(json.dumps(metadata),encoding='utf-8')
    marker=folder/(clip_id+'.pending.json')
    os.replace(temp_json,marker)
    return marker


def upload_one(marker, server):
    marker=Path(marker)
    meta=json.loads(marker.read_text(encoding='utf-8'))
    path=marker.parent/meta['wav']
    req=urllib.request.Request(server.rstrip('/')+'/api/audio',data=path.read_bytes(),method='POST',
        headers={'Content-Type':'audio/wav','X-Hive-Id':meta['hive_id'],
            'X-Recording-Id':meta['id'],'X-Captured-At':meta['captured_at']})
    with urllib.request.urlopen(req,timeout=10) as response:
        result=json.load(response)
    if result.get('id')!=meta['id'] or (result.get('mysql')!='ok' and result.get('storage')!='ok'):
        raise ValueError('Servidor não confirmou o armazenamento da gravação')
    # Só remover fila após confirmação do servidor. Retry usa o mesmo UUID.
    marker.unlink()
    path.unlink(missing_ok=True)
    print(f"Armazenamento confirmado: {meta['hive_id']} | {meta['id']}",flush=True)
    return result


def uploader(folder, server, stop):
    while not stop.is_set():
        for marker in sorted(Path(folder).glob('*.pending.json')):
            if stop.is_set():break
            try:upload_one(marker,server)
            except urllib.error.HTTPError as exc:
                if 400<=exc.code<500:
                    marker.rename(marker.with_name(marker.name.replace('.pending.json','.failed.json')))
                    print(f'Rejeitado HTTP {exc.code}; preservado para revisão: {marker.name}',flush=True)
                else:
                    print('Servidor/MySQL indisponível; fila preservada.',flush=True)
                    break
            except (urllib.error.URLError,TimeoutError,OSError,ValueError):
                print('Envio não confirmado; fila preservada para nova tentativa.',flush=True)
                break
        stop.wait(2)


def capture(port_name, hive, folder, stop):
    while not stop.is_set():
        try:
            with serial.Serial(port_name,BAUD_RATE,timeout=2) as port:
                stop.wait(2)
                print(f'Capturando {hive} em {port_name}. Ctrl+C encerra.',flush=True)
                while not stop.is_set():
                    count=wait_header(port)
                    captured=datetime.now(timezone.utc).isoformat()
                    if count not in (WINDOW,RECORD_SAMPLES):
                        print(f'Cabeçalho inesperado: {count}; procurando próximo frame.',flush=True)
                        continue
                    pcm=read_exact(port,count*2)
                    if read_exact(port,len(END))!=END:
                        print('Frame incompleto/corrompido descartado.',flush=True);continue
                    marker=enqueue_pcm(pcm,hive,folder,captured)
                    print('Captura completa na fila: '+marker.name,flush=True)
        except (serial.SerialException,TimeoutError,OSError) as exc:
            print(f'Serial indisponível ({port_name}): {exc}; tentando novamente.',flush=True)
            stop.wait(2)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',default=SERIAL_PORT)
    parser.add_argument('--hive',default='BG01')
    parser.add_argument('--server',default='http://127.0.0.1:5000')
    parser.add_argument('--send-file',help='Testa envio de um WAV PCM16 mono 16 kHz de 30 s, sem ESP32')
    parser.add_argument('--flush',action='store_true',help='Tenta enviar a fila atual e termina')
    a=parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,64}',a.hive):parser.error('Identificador de colmeia inválido')
    folder=ROOT/'data/live/pending';folder.mkdir(parents=True,exist_ok=True)
    if a.send_file:
        with wave.open(a.send_file,'rb') as wav:
            if (wav.getnchannels(),wav.getsampwidth(),wav.getframerate())!=(1,2,SAMPLE_RATE) or wav.getnframes() not in (WINDOW,RECORD_SAMPLES):
                parser.error('Esperado WAV PCM16 mono, 16000 Hz, 30 ou 10 s')
            marker=enqueue_pcm(wav.readframes(wav.getnframes()),a.hive,folder)
        try:upload_one(marker,a.server)
        except Exception as exc:raise SystemExit(f'Envio pendente: {exc}; áudio preservado na fila.')
    elif a.flush:
        for marker in sorted(folder.glob('*.pending.json')):
            try:upload_one(marker,a.server)
            except Exception as exc:raise SystemExit(f'Fila preservada: {exc}')
    else:
        stop=threading.Event()
        worker=threading.Thread(target=uploader,args=(folder,a.server,stop),daemon=True)
        worker.start()
        try:capture(a.port,a.hive,folder,stop)
        except KeyboardInterrupt:print('\nColetor encerrado; pendências preservadas.')
        finally:stop.set();worker.join(timeout=2)
