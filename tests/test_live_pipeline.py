"""Testes HTTP/fila com repositório em memória; não substituem teste do MySQL real."""
import io
import json
import tempfile
import threading
import unittest
import uuid
import wave
from pathlib import Path
from unittest.mock import patch

import numpy as np
from werkzeug.serving import make_server
from beeguard_server import create_app,decode_audio,MySQLStore
from capture_to_server import enqueue_pcm,upload_one

class MemoryStore:
    def __init__(self):self.rows={};self.available=True
    def healthy(self):
        if not self.available:raise RuntimeError('offline')
    def get(self,id):self.healthy();return self.rows.get(id)
    def insert(self,row):self.healthy();self.rows[row['id']]=row;return True
    def latest(self,hive,limit):
        self.healthy()
        rows=[r.copy() for r in self.rows.values() if not hive or r['hive_id']==hive]
        for r in rows:r['captured_at']=r['captured_at'].isoformat()+'Z'
        return sorted(rows,key=lambda r:r['captured_at'],reverse=True)[:limit]


def pcm():return (10000*np.sin(2*np.pi*250*np.arange(160000)/16000)).astype('<i2').tobytes()


def wav_bytes(raw=None):
    f=io.BytesIO()
    with wave.open(f,'wb') as w:
        w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(pcm() if raw is None else raw)
    return f.getvalue()

class LiveTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=MemoryStore();self.app=create_app(self.store,self.root/'audio');self.client=self.app.test_client()
        self.headers={'X-Hive-Id':'BG01','X-Recording-Id':str(uuid.uuid4()),'X-Captured-At':'2026-10-06T10:00:00+00:00'}
    def tearDown(self):self.temp.cleanup()
    def send(self,data=None,headers=None):return self.client.post('/api/audio',data=data or wav_bytes(),headers=headers or self.headers,content_type='audio/wav')
    def test_analysis_persistence_and_idempotency(self):
        response=self.send();self.assertEqual(response.status_code,201)
        row=next(iter(self.store.rows.values()))
        self.assertEqual(row['hive_id'],'BG01');self.assertEqual(row['model_status'],'not_enabled')
        self.assertLess(abs(row['features']['dominant_frequency_hz']-250),32)
        self.assertTrue((self.root/'audio'/row['audio_path']).exists())
        self.assertEqual(self.send().status_code,200);self.assertEqual(len(self.store.rows),1)
        other={**self.headers,'X-Hive-Id':'BG02'}
        self.assertEqual(self.send(headers=other).status_code,409)
        self.assertEqual(self.client.get('/api/recordings?hive_id=BG01').json['recordings'][0]['id'],row['id'])
    def test_invalid_truncated_and_bad_headers(self):
        self.assertEqual(self.send(data=b'invalid').status_code,400)
        self.assertEqual(self.send(data=wav_bytes(b'\0'*1000)).status_code,400)
        self.assertEqual(self.send(headers={**self.headers,'X-Hive-Id':'../bad'}).status_code,400)
        self.assertEqual(self.send(headers={**self.headers,'X-Captured-At':'2026-10-06T10:00:00'}).status_code,400)
        self.assertEqual(self.store.rows,{})
    def test_health_and_mysql_down(self):
        self.assertEqual(self.client.get('/health').status_code,200)
        self.store.available=False
        self.assertEqual(self.client.get('/health').status_code,503)
        self.assertEqual(self.send().status_code,503)
    def test_silence_flagged_not_diagnosed(self):
        response=self.send(data=wav_bytes(b'\0'*(160000*2)))
        self.assertEqual(response.status_code,201)
        self.assertEqual(response.json['quality_status'],'review')
    def test_real_http_upload_queue_confirmation(self):
        server=make_server('127.0.0.1',0,self.app)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            marker=enqueue_pcm(pcm(),'BG01',self.root/'pending')
            result=upload_one(marker,f'http://127.0.0.1:{server.server_port}')
            self.assertEqual(result['status'],'stored')
            self.assertFalse(marker.exists());self.assertFalse(list((self.root/'pending').glob('*.wav')))
            self.assertEqual(len(self.store.rows),1)
        finally:server.shutdown();thread.join();server.server_close()
    def test_queue_preserved_without_confirmation(self):
        marker=enqueue_pcm(pcm(),'BG01',self.root/'pending')
        with patch('urllib.request.urlopen',side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):upload_one(marker,'http://127.0.0.1:5000')
        self.assertTrue(marker.exists());self.assertEqual(len(list((self.root/'pending').glob('*.wav'))),1)
    def test_database_name_validation(self):
        with self.assertRaises(ValueError):MySQLStore({'database':'bad`;drop database x;--'})

if __name__=='__main__':unittest.main()
