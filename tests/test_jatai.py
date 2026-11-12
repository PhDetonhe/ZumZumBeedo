import csv
import hashlib
import io
import tempfile
import unittest
import uuid
import wave
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import MagicMock,patch

import numpy as np
from beeguard_server import create_app
from beeguard_monitor import install_monitor
from beeguard_field import install_field
from beeguard_jatai import install_jatai,Spectrograms,JataiStore,session_data,spectral_data
from export_jatai import export_species
from test_field import Memory


def payload(gain=1):
    f=io.BytesIO();samples=(5000*gain*np.sin(2*np.pi*650*np.arange(160000)/16000)).astype('<i2')
    with wave.open(f,'wb') as w:w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000);w.writeframes(samples.tobytes())
    return f.getvalue()


class Store(Memory):
    def __init__(self):super().__init__();self.events=[]
    def create_species_session(self,row,meta):
        self.create_session(row);self.ss[row['id']].update(meta)
    def has_hive(self,hive):return any(s['hive_id']==hive for s in self.ss.values())
    def inspect(self,row):
        row=dict(row);row['observed_at']=row['observed_at'].replace(tzinfo=timezone.utc).isoformat();row['created_at']=datetime.now(timezone.utc).isoformat();self.events.append(row)
    def inspections(self,hive,start=None,end=None):return [r for r in self.events if r['hive_id']==hive]
    def hive_audio(self,hive,start,end):
        return [{**r,**{k:self.ss[self.links[r['id']]].get(k) for k in ('device_id','gain','placement','species_id')},'session_id':self.links[r['id']]}
                for r in self.rows.values() if r['hive_id']==hive and r['id'] in self.links]


class Jatai(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store()
        self.art=Spectrograms(self.store,self.root/'audio',self.root/'png')
        self.app=install_jatai(install_field(install_monitor(create_app(self.store,self.root/'audio'),self.store),self.store,audio_dir=self.root/'audio'),self.store,self.art)
        self.client=self.app.test_client()
    def tearDown(self):self.tmp.cleanup()
    def session(self):
        return dict(apiary_id='SESI',hive_id='JATAI01',device_id='ESP01',mic_model='INMP441',gain=1,placement='entrada fixa 5 cm',operator_name='Equipe',kind='field',notes='caixa cedida',species_id='jatai_unconfirmed',identified_by='',colony_stage='unknown',mic_location='entrance')
    def create(self):
        result=self.client.post('/api/jatai/sessions',json=self.session());self.assertEqual(result.status_code,201)
        sid=result.json['session']['id'];self.store.ss[sid]['started_at']=(datetime.now(timezone.utc)-timedelta(minutes=2)).isoformat();return sid
    def send(self,sid):
        rid=str(uuid.uuid4());now=(datetime.now(timezone.utc)-timedelta(seconds=20)).isoformat()
        result=self.client.post('/api/audio',data=payload(),headers={'X-Session-Id':sid,'X-Hive-Id':'JATAI01','X-Recording-Id':rid,'X-Captured-At':now})
        self.assertEqual(result.status_code,201);return rid
    def inspection(self):
        return dict(hive_id='JATAI01',observed_at=(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat(),condition_observed='not_assessed',observer='Profissional',observer_role='specialist',noise_observed='yes',context_tag='construction',temperature_c=25,humidity_pct=60,measurement_location='ambient',notes='Ruído de obra percebido; condição da colônia não avaliada.')
    def test_species_validation_and_pages(self):
        sid=self.create();self.assertEqual(self.store.ss[sid]['species_id'],'jatai_unconfirmed')
        with self.client.get('/') as r:self.assertEqual(r.status_code,200)
        with self.client.get('/campo') as r:self.assertEqual(r.status_code,200)
        p=self.session();p['species_id']='tetragonisca_angustula'
        self.assertEqual(self.client.post('/api/jatai/sessions',json=p).status_code,400)
        p['identified_by']='Profissional';self.assertEqual(self.client.post('/api/jatai/sessions',json=p).status_code,201)
    def test_png_export_and_queue(self):
        rid=self.send(self.create());self.assertEqual(self.art.jobs.get_nowait(),rid)
        response=self.client.get(f'/api/jatai/recordings/{rid}/spectrum?fmax=8000')
        self.assertEqual(response.status_code,200);self.assertEqual(response.json['frequencies_hz'][-1],8000)
        self.assertLess(abs(response.json['summary']['peak_frequency_hz']-650),8)
        with self.client.get(f'/api/jatai/recordings/{rid}/spectrogram.png?download=1') as png:
            self.assertEqual(png.status_code,200);self.assertTrue(png.data.startswith(b'\x89PNG'))
            self.assertIn('attachment',png.headers['Content-Disposition'])
        self.assertEqual(len(list((self.root/'png').glob('*.png'))),1)
        self.assertEqual(self.client.get(f'/api/jatai/recordings/{rid}/spectrum?fmax=16000').status_code,400)
    def test_hash_corruption_is_not_plotted(self):
        rid=self.send(self.create());row=self.store.rows[rid];(self.root/'audio'/row['audio_path']).write_bytes(b'corrupted')
        self.assertEqual(self.client.get(f'/api/jatai/recordings/{rid}/spectrum').status_code,400)
        self.assertFalse(list((self.root/'png').glob('*.png')))
    def test_inspection_stays_separate_and_hive_history(self):
        rid=self.send(self.create());result=self.client.post('/api/jatai/inspections',json=self.inspection())
        self.assertEqual(result.status_code,201);self.assertEqual(len(self.store.events),1)
        self.assertNotIn('condition_observed',self.store.rows[rid]);self.assertFalse(self.store.reviews)
        response=self.client.get('/api/jatai/history?hive_id=JATAI01')
        self.assertEqual(response.status_code,200);self.assertEqual(len(response.json['inspections']),1)
        self.assertEqual(response.json['recordings'],1)
    def test_invalid_observation_not_saved(self):
        self.create();p=self.inspection()
        for changes in [{'temperature_c':float('nan')},{'humidity_pct':101},{'observed_at':'2026-10-20T10:00:00'},
                        {'condition_observed':'diagnosed_healthy'},{'hive_id':'OTHER'},{'measurement_location':'not_measured'}]:
            self.assertEqual(self.client.post('/api/jatai/inspections',json={**p,**changes}).status_code,400)
        self.assertFalse(self.store.events)
    def test_model_not_enabled(self):
        rid=self.send(self.create());r=self.client.get(f'/api/field/recordings/{rid}/prediction')
        self.assertFalse(r.json['enabled'])
    def test_fixed_scale_does_not_hide_gain(self):
        _,_,a,_=spectral_data(payload(1));_,_,b,_=spectral_data(payload(2))
        self.assertAlmostEqual(float(b.max()-a.max()),6.02,delta=.1)
    def test_atomic_session_rollback(self):
        store=object.__new__(JataiStore);con=MagicMock();store.connect=lambda:con
        con.cursor.return_value.execute.side_effect=[None,RuntimeError('species insert failed')]
        row,meta=session_data(self.session())
        with self.assertRaises(RuntimeError):store.create_species_session(row,meta)
        con.rollback.assert_called_once();con.commit.assert_not_called();con.close.assert_called_once()
    def test_species_export_preserves_metadata_without_health_labels(self):
        sid=self.create();rid=self.send(sid);row=self.store.rows[rid]
        row.update(session_id=sid,apiary_id='SESI',species_id='jatai_unconfirmed',identified_by='',colony_stage='unknown',mic_location='entrance',kind='field',review_id=None)
        with patch('export_jatai.ROOT',self.root),patch('export_jatai.export') as export_base:
            def fake_export(rows,out,audio_root,copy):
                Path(out).mkdir()
                for n in ['manifest.csv','training_features.csv']:
                    (Path(out)/n).write_text('recording_id\n'+rid+'\n')
                return dict(recordings=1)
            export_base.side_effect=fake_export
            report=export_species(self.store,self.root/'out','jatai_unconfirmed')
            self.assertFalse(report['health_labels_assigned']);self.assertTrue(report['species_identification_pending'])
            with (self.root/'out'/'manifest.csv').open() as f:item=list(csv.DictReader(f))[0]
            self.assertEqual(item['species_id'],'jatai_unconfirmed')
            self.assertTrue((self.root/'out'/'inspections.csv').exists())


if __name__=='__main__':unittest.main()
