"""Regras temporais e endpoints; MySQL real precisa ser verificado no computador."""
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from beeguard_server import create_app
from beeguard_monitor import assess, calibrate, install_monitor

NOW = datetime(2026,10,6,18,0,tzinfo=timezone.utc)


def rows(rms=.02,minutes=30,now=NOW):
    return [dict(captured_at=(now-timedelta(seconds=s)).isoformat(),
                 quality={'rms':rms,'clipping_ratio':0},features={'rms':rms},quality_status='pass')
            for s in range(0,minutes*60,10)]


def reference(rms=.1):
    return dict(rms=rms,created_at=(NOW-timedelta(hours=1)).isoformat())


class Memory:
    def __init__(self):
        self.events=[];self.available=True;self.ref=None
    def healthy(self):
        if not self.available:raise RuntimeError('offline')
    def hives(self):return ['BG01']
    def history(self,*args):self.healthy();return []
    def reference(self,hive):return self.ref
    def observations(self,*args):self.healthy();return self.events
    def save_observation(self,row):self.healthy();self.events.append(row)


class Rules(unittest.TestCase):
    def test_no_reference_or_data(self):
        self.assertEqual(assess(rows(),None,NOW)['status'],'learning')
        self.assertEqual(assess([],reference(),NOW)['status'],'offline')
    def test_offline_is_not_low(self):
        self.assertEqual(assess(rows(now=NOW-timedelta(minutes=3)),reference(),NOW)['status'],'offline')
    def test_sustained_low_and_silence(self):
        self.assertEqual(assess(rows(),reference(),NOW)['status'],'low')
        self.assertEqual(assess(rows(0),reference(),NOW)['status'],'low')
    def test_short_dip_not_persistent(self):
        self.assertEqual(assess(rows(minutes=10),reference(),NOW)['status'],'lower')
    def test_missing_interval_breaks_persistence(self):
        rs=[r for r in rows() if not NOW-timedelta(minutes=20) < datetime.fromisoformat(r['captured_at']) < NOW-timedelta(minutes=17)]
        self.assertEqual(assess(rs,reference(),NOW)['status'],'lower')
    def test_high_usual_clipping_and_new_reference(self):
        self.assertEqual(assess(rows(.3),reference(),NOW)['status'],'high')
        self.assertEqual(assess(rows(.1),reference(),NOW)['status'],'usual')
        rs=rows();ref=reference();ref['created_at']=NOW.isoformat()
        self.assertEqual(assess(rs,ref,NOW)['status'],'lower')
        for r in rs:r['quality']['clipping_ratio']=.2
        self.assertEqual(assess(rs,reference(),NOW)['status'],'quality')
    def test_calibration_valid_quiet_sparse(self):
        self.assertAlmostEqual(calibrate(rows(.1),NOW),.1)
        with self.assertRaises(ValueError):calibrate(rows(0),NOW)
        with self.assertRaises(ValueError):calibrate(rows(minutes=5),NOW)
        with self.assertRaises(ValueError):calibrate(rows(now=NOW-timedelta(minutes=3)),NOW)
    def test_future_rows_do_not_activate_monitor(self):
        self.assertEqual(assess(rows(now=NOW+timedelta(hours=1)),reference(),NOW)['status'],'offline')


class API(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=Memory()
        self.app=install_monitor(create_app(self.store,Path(self.temp.name)/'audio'),self.store)
        self.client=self.app.test_client()
    def tearDown(self):self.temp.cleanup()
    def payload(self):
        return dict(hive_id='BG01',category='obra',activity='alta',notes='<script>alert(1)</script>',observed_at='2026-07-17T12:00:00-03:00')
    def test_dashboard_and_read_endpoints(self):
        with self.client.get('/') as response:
            self.assertEqual(response.status_code,200)
        self.assertEqual(self.client.get('/api/hives').json['hives'],['BG01'])
        self.assertEqual(self.client.get('/api/monitor?hive_id=BG01').json['status'],'offline')
        self.assertEqual(self.client.get('/api/monitor?date=bad').status_code,400)
        self.assertEqual(self.client.get('/api/monitor?hive_id=../x').status_code,400)
    def test_observation_utc_and_validation(self):
        self.assertEqual(self.client.post('/api/observations',json=self.payload()).status_code,201)
        r=self.store.events[0]
        self.assertEqual(r['observed_at'],datetime(2026,7,17,15))
        self.assertEqual(r['notes'],self.payload()['notes'])
        for field,value in [('hive_id','../x'),('category','invalid'),('activity','invalid'),('notes',''),('observed_at','2026-07-17T12:00:00')]:
            self.assertEqual(self.client.post('/api/observations',json={**self.payload(),field:value}).status_code,400)
        self.assertEqual(self.client.post('/api/observations',json=[]).status_code,400)
        self.assertEqual(len(self.store.events),1)
    def test_database_down_not_success(self):
        self.store.available=False
        self.assertEqual(self.client.get('/api/monitor').status_code,503)
        self.assertEqual(self.client.post('/api/observations',json=self.payload()).status_code,503)
    def test_reference_without_data_rejected(self):
        self.assertEqual(self.client.post('/api/reference?hive_id=BG01').status_code,400)


if __name__=='__main__':unittest.main()
