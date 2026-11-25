import csv
import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
from scipy.io import wavfile
from train_population import FEATURES,VERSION,weights,train,predict,load_dataset

class TrainingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.data=self.root/'features.csv'
        self.rows=[]
        for hive in range(3):
            for rec in range(4):
                target=5+hive+rec
                for window in range(rec+1):
                    row={c:(target+j*0.01) for j,c in enumerate(FEATURES)}
                    row.update(frames_bees=target,hive_id=f'h{hive}',recording_group=f'h{hive}r{rec}',
                        feature_version=VERSION,label_type='derived_test',audio_id=f'{hive}_{rec}_{window}')
                    self.rows.append(row)
        self.write()
    def tearDown(self): self.tmp.cleanup()
    def write(self):
        with self.data.open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(self.rows[0]))
            w.writeheader();w.writerows(self.rows)
    def test_weights_per_recording(self):
        recs=np.array(['a','b','b','b'])
        w=weights(recs)
        self.assertEqual(w[0],1)
        self.assertAlmostEqual(w[1:].sum(),1)
    def test_evaluation_separation_baseline_and_predict(self):
        out=self.root/'model'
        result=train(self.data,out)
        self.assertEqual(result['independent_recording_groups'],12)
        with (out/'oof_recording_predictions.csv').open() as f:
            predictions=list(csv.DictReader(f))
        self.assertEqual(len(predictions),12)
        splits=json.loads((out/'cv_splits.json').read_text())
        for s in splits:
            self.assertNotIn(s['held_out_hive'],s['train_hives'])
        for p in predictions:
            # Baseline não depende da quantidade de janelas de cada gravação.
            expected=np.mean([5+h+r for h in range(3) if f'h{h}'!=p['hive_id'] for r in range(4)])
            self.assertAlmostEqual(float(p['baseline_frames']),expected)
        audio=self.root/'tone.wav'
        wavfile.write(audio,16000,(10000*np.sin(2*np.pi*250*np.arange(160000)/16000)).astype(np.int16))
        inferred=predict(audio,out/'population_rf.joblib')
        self.assertEqual(inferred['accepted_windows'],1)
        self.assertTrue(np.isfinite(inferred['estimated_frames_experimental']))
        with self.assertRaises(ValueError):train(self.data,out)
        wavfile.write(audio,16000,np.zeros(160000,dtype=np.int16))
        with self.assertRaises(ValueError):predict(audio,out/'population_rf.joblib')
    def test_reject_duplicate_inconsistent_and_leaky_metadata(self):
        self.assertNotIn('frames_bees',FEATURES)
        self.assertNotIn('interpolation_weight',FEATURES)
        self.assertNotIn('timestamp',FEATURES)
        self.rows.append(self.rows[0].copy())
        self.write()
        with self.assertRaises(ValueError):load_dataset(self.data)
        self.rows.pop()
        self.rows[2]['frames_bees']=100
        self.write()
        with self.assertRaises(ValueError):load_dataset(self.data)
    def test_reject_nonfinite_and_version(self):
        self.rows[0][FEATURES[0]]=float('nan')
        self.write()
        with self.assertRaises(ValueError):load_dataset(self.data)
        self.rows[0][FEATURES[0]]=1
        self.rows[0]['feature_version']='other'
        self.write()
        with self.assertRaises(ValueError):load_dataset(self.data)

if __name__=='__main__':unittest.main()
