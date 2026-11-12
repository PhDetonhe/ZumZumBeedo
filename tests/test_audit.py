import json
import tempfile
import unittest
from pathlib import Path
from audit_population import audit, file_hash

class AuditTests(unittest.TestCase):
    def test_real_input_consistency_and_train_only_transforms(self):
        root=Path(__file__).resolve().parents[1]
        data=root.parent/'upload/population_features.csv'
        oof=root.parent/'upload/oof_recording_predictions.csv'
        if not data.exists() or not oof.exists():self.skipTest('Arquivos de auditoria ausentes')
        before=file_hash(data)
        with tempfile.TemporaryDirectory() as temp:
            out=Path(temp)/'out'
            report=audit(data,oof,out)
            self.assertEqual(report['recordings'],29)
            self.assertEqual(report['windows'],348)
            self.assertLess(report['original_rf_max_prediction_difference'],1e-8)
            self.assertEqual(len(report['comparisons']),4)
            for split in json.loads((out/'cv_splits.json').read_text()):
                self.assertNotIn(split['test_hive'],split['train_hives'])
                self.assertFalse(set(split['test_recordings'])&set(split['train_recordings']))
            with self.assertRaises(ValueError):audit(data,oof,out)
        self.assertEqual(before,file_hash(data))

if __name__=='__main__':unittest.main()
