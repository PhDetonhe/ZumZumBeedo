import tempfile
import unittest
from datetime import date
from pathlib import Path
from label_urban_population import estimate,label,write_csv,read_csv

class LabelTests(unittest.TestCase):
    def test_interpolation_bounds_and_gap(self):
        left=(date(2022,7,1),10,{'inspection_id':'a'})
        right=(date(2022,7,11),20,{'inspection_id':'b'})
        self.assertEqual(estimate([left,right],date(2022,7,6),21)[0],15)
        self.assertIsNone(estimate([left,right],date(2022,6,30),21))
        self.assertIsNone(estimate([left,right],date(2022,7,12),21))
        self.assertIsNone(estimate([left,right],date(2022,7,6),5))
        self.assertEqual(estimate([left,right],date(2022,7,1),5)[4],'derived_same_published_date')
    def test_real_manifest_simulated_features(self):
        from import_urban import import_data
        root=Path(__file__).resolve().parents[1]
        original=root.parent/'upload/urban.csv'
        if not original.exists(): self.skipTest('Manifesto de integração ausente')
        records,_=read_csv(original)
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            synthetic=[{'source_file':r['file'],'file':f'{j}_{i}.wav','timestamp':'','hive_id':'',
                'feature_version':'test','frames_bees':'','mfcc_00_mean':'12.3'} for j,r in enumerate(records) for i in range(12)]
            write_csv(folder/'features.csv',synthetic,list(synthetic[0]))
            inspections=root/'data/metadata/urban_inspections/population_inspections_2022.csv'
            import_data(original,inspections,folder/'features.csv',folder/'identified')
            report=label(folder/'identified/urban_features_identified.csv',inspections,folder/'labels')
            self.assertEqual(report['labeled_recordings'],29)
            self.assertEqual(report['labeled_windows'],348)
            self.assertEqual(report['labeled_hives'],7)
            rows,_=read_csv(folder/'labels/population_features.csv')
            self.assertTrue(all(r['mfcc_00_mean']=='12.3' for r in rows))
            self.assertTrue(all(int(r['inspection_gap_days'])<=21 for r in rows))

if __name__=='__main__':unittest.main()
