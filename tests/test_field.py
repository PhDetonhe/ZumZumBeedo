"""Integração HTTP, fila, espectrograma e exportação. MySQL real não é simulado como hardware."""
import csv
import hashlib
import io
import json
import tempfile
import threading
import unittest
import uuid
import wave
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
from werkzeug.serving import make_server
from audio_pipeline import VERSION,features,quality
from beeguard_server import create_app
from beeguard_monitor import install_monitor
from beeguard_field import install_field,spectrum,summarize,load_model
from capture_field import enqueue,upload
from export_field import export
from field_dataset import FEATURES,check_session,checked_path
from train_field import train


def wav_bytes():
    raw=(10000*np.sin(2*np.pi*250*np.arange(160000)/16000)).astype('<i2').tobytes()
    f=io.BytesIO()
    with wave.open(f,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(raw)
    return f.getvalue(),raw


class Memory:
    def __init__(self):
        self.rows={};self.links={};self.ss={};self.reviews={};self.available=True;self.fail_bind=False
    def healthy(self):
        if not self.available:raise RuntimeError('offline')
    def get(self,rid):self.healthy();return self.rows.get(rid)
    def insert(self,row):
        self.healthy();row=dict(row);row['captured_at']=row['captured_at'].isoformat()+'Z';self.rows[row['id']]=row;return True
    def latest(self,hive,limit):return list(self.rows.values())[:limit]
    def session(self,sid):self.healthy();return self.ss.get(sid)
    def sessions(self):self.healthy();return list(self.ss.values())
    def create_session(self,row):
        self.healthy();r=dict(row);r['started_at']=r['started_at'].isoformat()+'Z';r['ended_at']=None;self.ss[r['id']]=r
    def close_session(self,sid):self.ss[sid]['ended_at']=datetime.now(timezone.utc).isoformat()
    def binding(self,rid):self.healthy();return self.links.get(rid)
    def bind(self,rid,sid):
        if self.fail_bind:raise RuntimeError('binding unavailable')
        self.links[rid]=sid
    def review(self,rid,row):self.reviews[rid]=row
    def records(self,sid=None,limit=None,offset=0):
        result=[r for rid,r in self.rows.items() if rid in self.links and (sid is None or self.links[rid]==sid)]
        return result[offset:offset+limit] if limit else result


class Field(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Memory()
        self.app=install_field(install_monitor(create_app(self.store,self.root/'audio'),self.store),self.store,audio_dir=self.root/'audio');self.client=self.app.test_client()
        self.sid=str(uuid.uuid4());self.now=datetime.now(timezone.utc)-timedelta(seconds=20)
        self.store.ss[self.sid]=dict(id=self.sid,hive_id='REAL01',apiary_id='A1',gain=1,kind='field',device_id='ESP01',mic_model='INMP441',placement='fixed',started_at=(self.now-timedelta(hours=1)).isoformat(),ended_at=None)
        self.headers={'X-Hive-Id':'REAL01','X-Recording-Id':str(uuid.uuid4()),'X-Captured-At':self.now.isoformat(),'X-Session-Id':self.sid}
    def tearDown(self):self.tmp.cleanup()
    def send(self,headers=None):return self.client.post('/api/audio',data=wav_bytes()[0],headers=headers or self.headers)
    def test_session_binding_idempotent_and_legacy(self):
        r=self.send();self.assertEqual(r.status_code,201);self.assertEqual(r.json['session_id'],self.sid)
        self.assertEqual(self.send().status_code,200);self.assertEqual(len(self.store.rows),1)
        h={**self.headers,'X-Recording-Id':str(uuid.uuid4())};del h['X-Session-Id']
        self.assertEqual(self.send(h).status_code,201);self.assertEqual(len(self.store.links),1)
    def test_wrong_hive_session_and_times_rejected(self):
        for h in [{**self.headers,'X-Hive-Id':'OTHER'},{**self.headers,'X-Session-Id':str(uuid.uuid4())},
                  {**self.headers,'X-Captured-At':(self.now-timedelta(days=2)).isoformat()},
                  {**self.headers,'X-Captured-At':(self.now+timedelta(days=2)).isoformat()}]:
            self.assertEqual(self.send(h).status_code,400)
        self.assertFalse(self.store.rows)
    def test_closed_session_accepts_old_queue_not_new_audio(self):
        self.store.ss[self.sid]['ended_at']=(self.now+timedelta(seconds=15)).isoformat()
        self.assertEqual(self.send().status_code,201)
        self.assertEqual(self.send({**self.headers,'X-Recording-Id':str(uuid.uuid4()),'X-Captured-At':(self.now+timedelta(seconds=16)).isoformat()}).status_code,400)
    def test_binding_failure_retry_does_not_duplicate(self):
        self.store.fail_bind=True;self.assertEqual(self.send().status_code,503)
        self.store.fail_bind=False;self.assertEqual(self.send().status_code,200)
        self.assertEqual(len(self.store.rows),1);self.assertEqual(len(self.store.links),1)
    def test_uuid_cannot_move_session(self):
        self.send();other=str(uuid.uuid4());self.store.ss[other]={**self.store.ss[self.sid],'id':other}
        self.assertEqual(self.send({**self.headers,'X-Session-Id':other}).status_code,409)
    def test_review_validation(self):
        self.send();url='/api/field/recordings/'+self.headers['X-Recording-Id']+'/review'
        payload=dict(sound_label='external_interference',bee_activity='not_observed',context_tag='construction',reviewer='Felipe',notes='obra',exclude_training=False)
        self.assertEqual(self.client.post(url,json=payload).status_code,201)
        self.assertEqual(self.client.post(url,json={**payload,'sound_label':'abandonada'}).status_code,400)
        self.assertEqual(self.client.post(url,json={**payload,'exclude_training':'false'}).status_code,400)
    def test_audio_spectrum_and_no_model(self):
        self.send();base='/api/field/recordings/'+self.headers['X-Recording-Id']
        with self.client.get(base+'/audio') as r:self.assertEqual(r.status_code,200)
        r=self.client.get(base+'/spectrogram');self.assertEqual(r.status_code,200)
        values=np.array(r.json['db']);freq=np.array(r.json['frequencies_hz'])
        self.assertAlmostEqual(freq[np.argmax(values.mean(axis=1))],250)
        self.assertFalse(self.client.get(base+'/prediction').json['enabled'])
    def test_real_http_queue_confirmation(self):
        server=make_server('127.0.0.1',0,self.app);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            marker=enqueue(wav_bytes()[1],self.store.ss[self.sid],self.root/'queue',self.now.isoformat())
            upload(marker,f'http://127.0.0.1:{server.server_port}')
            self.assertFalse(marker.exists());self.assertEqual(len(self.store.links),1)
        finally:server.shutdown();thread.join();server.server_close()
    def test_queue_preserved_without_session_ack(self):
        marker=enqueue(wav_bytes()[1],self.store.ss[self.sid],self.root/'queue',self.now.isoformat())
        meta=json.loads(marker.read_text())
        class Response:
            def __enter__(self):return io.StringIO(json.dumps(dict(id=meta['id'],mysql='ok')))
            def __exit__(self,*args):pass
        with patch('urllib.request.urlopen',return_value=Response()):
            with self.assertRaises(ValueError):upload(marker,'http://localhost')
        self.assertTrue(marker.exists());self.assertTrue((marker.parent/meta['wav']).exists())
    def test_sessions_creation_and_comparison(self):
        payload=dict(apiary_id='A1',hive_id='B2',device_id='ESP2',mic_model='INMP441',gain=1,placement='inside',operator_name='Felipe',kind='bench',notes='teste')
        self.assertEqual(self.client.post('/api/field/sessions',json=payload).status_code,201)
        self.assertEqual(self.client.post('/api/field/sessions',json={**payload,'gain':float('nan')}).status_code,400)
        self.assertEqual(self.client.get('/api/field/comparison?sessions='+self.sid).status_code,200)
        self.assertEqual(self.client.get('/api/field/comparison?sessions=bad').status_code,400)
        self.assertEqual(self.client.get('/api/field/sessions/'+self.sid+'/recordings?offset=-1').status_code,400)


class Exports(unittest.TestCase):
    def test_exclusions_hashes_duplicates_and_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);audio=root/'raw';audio.mkdir();payload,_=wav_bytes();(audio/'x.wav').write_bytes(payload)
            samples=np.frombuffer(wav_bytes()[1],dtype='<i2').astype(float)/32768
            base=dict(id='r1',session_id='s1',apiary_id='A1',hive_id='H1',kind='field',device_id='ESP1',gain=1,
                      mic_model='INMP441',placement='fixed',captured_at='2026-10-08T12:00:00Z',audio_path='x.wav',
                      audio_sha256=hashlib.sha256(payload).hexdigest(),features=features(samples),quality=quality(samples),
                      quality_status='pass',review_id=1,sound_label='clean_bee_sound',exclude_training=False)
            report=export([base],root/'v1',audio,copy_audio=True)
            self.assertEqual(report['training_recordings'],1)
            with (root/'v1'/'training_features.csv').open() as f:r=list(csv.DictReader(f))[0]
            self.assertEqual(r['hive_group'],'A1::H1');self.assertEqual(r['recording_group'],'r1')
            self.assertTrue((root/'v1'/'audio'/'r1.wav').exists())
            with self.assertRaises(ValueError):export([base],root/'v1',audio)
            excluded=[{**base,'id':'bench','kind':'bench'},{**base,'id':'uncertain','sound_label':'uncertain'},
                      {**base,'id':'review','review_id':None},{**base,'id':'quality','quality_status':'review'},
                      {**base,'id':'bad_hash','audio_sha256':'bad'}]
            report=export(excluded,root/'v2',audio);self.assertEqual(report['training_recordings'],0)
            report=export([base,{**base,'id':'r2'}],root/'v3',audio)
            self.assertEqual(report['training_recordings'],0);self.assertEqual(report['excluded_by_reason']['duplicate_audio'],2)
    def test_path_escape_rejected(self):
        with self.assertRaises(ValueError):checked_path('/tmp/audio','../secret.wav')
    def test_spectrum_is_fixed_scale(self):
        a=np.asarray(spectrum(wav_bytes()[0])['db'])
        self.assertTrue(np.isfinite(a).all());self.assertGreaterEqual(a.min(),-100);self.assertLessEqual(a.max(),0)


class Training(unittest.TestCase):
    def dataset(self,path):
        rows=[]
        for hive in range(3):
            for i in range(10):
                label='clean_bee_sound' if i<5 else 'external_interference'
                r=dict(recording_id=f'{hive}-{i}',audio_sha256=f'hash-{hive}-{i}',hive_group=f'A::H{hive}',
                       session_id=f's{hive}',kind='field',training_eligible=True,feature_version=VERSION,sound_label=label)
                r.update({k:(i%5)*.01+(0 if i<5 else 1)+hive*.001 for k in FEATURES});rows.append(r)
        with path.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]));w.writeheader();w.writerows(rows)
    def test_grouped_training_serialization_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);self.dataset(root/'train.csv');report=train(root/'train.csv',root/'model')
            self.assertEqual(len(report['folds']),3);self.assertEqual(report['hives'],3)
            self.assertEqual(report['recordings'],30)
            bundle=load_model(root/'model'/'sound_model.joblib')
            self.assertEqual(bundle['model'].predict([[0]*40])[0],'clean_bee_sound')
            self.assertTrue((root/'model'/'oof_predictions.csv').is_file())
            json.loads((root/'model'/'training_report.json').read_text())
    def test_small_dataset_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'empty.csv').write_text('recording_id\n')
            with self.assertRaises(ValueError):train(root/'empty.csv',root/'model')
            self.assertFalse((root/'model').exists())


if __name__=='__main__':unittest.main()
