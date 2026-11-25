import csv
import tempfile
import unittest
from pathlib import Path
from import_urban import import_data, parse_name, read_csv, write_csv

class ImportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        self.manifest=self.root/'urban.csv'
        self.inspections=self.root/'inspections.csv'
        self.features=self.root/'features.csv'
        self.file=r'..\raw\urban\audio\07-09-2022_16h15_HIVE-6.wav'
        write_csv(self.manifest,[{'file':self.file,'source':'urban','hive_id':'','frames_bees':''}],['file','source','hive_id','frames_bees'])
        write_csv(self.inspections,[{'hive_tag':'6','inspection_timestamp':'2022-09-07T22:14:19+00:00','frames_bees':'8','inspection_id':'i1','label_source':'real:line:201'}],['hive_tag','inspection_timestamp','frames_bees','inspection_id','label_source'])
        write_csv(self.features,[{'source_file':self.file,'file':'window.wav','mfcc_00_mean':'12.3','frames_bees':''}],['source_file','file','mfcc_00_mean','frames_bees'])
    def tearDown(self): self.tmp.cleanup()
    def test_windows_name_and_date(self):
        time,hive=parse_name(self.file)
        self.assertEqual(time.isoformat(),'2022-09-07T16:15:00')
        self.assertEqual(hive,'6')
        with self.assertRaises(ValueError):parse_name('31-02-2022_12h00_HIVE-6.wav')
    def test_no_timezone_no_labels_features_preserved(self):
        out=self.root/'out'
        result=import_data(self.manifest,self.inspections,self.features,out)
        self.assertEqual(result['recordings_with_candidate'],1)
        self.assertEqual(result['labeled_recordings'],0)
        rows,_=read_csv(out/'urban_features_identified.csv')
        self.assertEqual(rows[0]['mfcc_00_mean'],'12.3')
        self.assertEqual(rows[0]['file'],'window.wav')
        self.assertEqual(rows[0]['hive_id'],'urban:6')
        identified,_=read_csv(out/'urban_identified.csv')
        self.assertEqual((out/identified[0]['file']).resolve(),(self.root/'../raw/urban/audio/07-09-2022_16h15_HIVE-6.wav').resolve())
    def test_explicit_timezone_and_distance_limit(self):
        result=import_data(self.manifest,self.inspections,None,self.root/'out','UTC',max_hours=6)
        self.assertEqual(result['labeled_recordings'],1)
        result=import_data(self.manifest,self.inspections,None,self.root/'out2','UTC',max_hours=1)
        self.assertEqual(result['labeled_recordings'],0)
    def test_incompatible_features_no_partial_output(self):
        write_csv(self.features,[{'source_file':'different.wav'}],['source_file'])
        out=self.root/'out'
        with self.assertRaises(ValueError):import_data(self.manifest,self.inspections,self.features,out)
        self.assertFalse(out.exists())
    def test_preserve_outputs(self):
        out=self.root/'out'
        out.mkdir()
        with self.assertRaises(ValueError):import_data(self.manifest,self.inspections,None,out)

if __name__=='__main__':unittest.main()
