import http.client
from http.server import ThreadingHTTPServer
import threading
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'container'))
from atsc_dx import DX, Control, ControlRejected, ReceptionLog, frequency, measurement
from hdhomerun import Television, make_handler
from receivers import worker_environment, Supervisor
from sources import Registry

CONFIG={'host':'192.168.1.20','device_id':'10ABCDEF','tuner':1}

class FakeControl:
    def __init__(self):
        self.channel='none'; self.calls=[]; self.owner=None; self.target='none'; self.fail_release=False
    def get(self,field):
        return {'channelmap':'us-bcast','target':self.target,'streaminfo':'tsid=0x1234\n3: 5.1 TEST',
                'status':f'ch={self.channel} lock=8vsb ss=75 snq=62 seq=100 bps=19392658 pps=0',
                'lockkey':'none' if self.owner is None else '192.168.1.1'}[field]
    def set(self,field,value,key=None):
        self.calls.append((field,value,key))
        if self.fail_release and value=='none': raise RuntimeError('offline')
        if field=='lockkey' and value!='none':
            if self.owner is not None: raise ControlRejected('resource locked')
            self.owner=int(value)
        else:
            if self.owner!=key: raise ControlRejected('resource locked')
            if field=='channel': self.channel=value
            elif field=='lockkey': self.owner=None

class DXTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'receptions.sqlite3'
        self.control=FakeControl();self.device=Mock()
        self.metadata={'id':'roof','name':'Roof TV','antenna':'Yagi north','location':'Roof'}
        self.dx=DX(self.device,CONFIG,self.metadata,self.path,self.control)

    def scan(self, first=14,last=15):
        self.dx.scan={'id':'test-scan','running':True,'completed':0,'results':[],'error':''}
        with patch.object(self.dx.cancel,'wait',return_value=False): self.dx.run(first,last,2)

    def test_rf_plan_and_missing_metrics(self):
        self.assertEqual([frequency(n) for n in [2,4,5,6,7,13,14,36]],
                         [57000000,69000000,79000000,85000000,177000000,213000000,473000000,605000000])
        for n in [1,37,True,'5']:
            with self.assertRaises(ValueError): frequency(n)
        value=measurement('ch=auto:473000000 lock=8vsb ss=0 snq=101 seq=bad','', 'us-bcast')
        self.assertEqual(value['rf_channel'],14);self.assertEqual(value['strength'],0)
        self.assertIsNone(value['quality']);self.assertIsNone(value['symbol_quality'])
        self.assertIsNone(measurement('ch=qam:33')['rf_channel'])
        self.assertEqual(measurement('ch=8vsb:14', channelmap='us-bcast')['frequency_hz'],473000000)
        self.assertFalse(measurement('lock=(8vsb)')['locked'])

    def test_scan_logs_samples_and_releases_only_own_tuner(self):
        self.scan()
        self.assertEqual(self.dx.scan['status'],'complete')
        self.assertEqual(self.dx.scan['completed'],2)
        self.assertIsNone(self.control.owner);self.assertFalse(self.dx.busy)
        rows=ReceptionLog(self.path).rows()
        self.assertEqual([r['requested_rf'] for r in rows],[15,14])
        self.assertEqual(rows[0]['source']['antenna'],'Yagi north')
        self.assertEqual(rows[0]['measurement']['streaminfo'],'tsid=0x1234\n3: 5.1 TEST')
        self.metadata['antenna']='South'
        self.assertEqual(ReceptionLog(self.path).rows()[0]['source']['antenna'],'Yagi north')
        self.assertFalse(self.dx.journal.exists())
        self.assertEqual(len(ReceptionLog(self.path).rows(before=rows[0]['id'])),1)

    def test_busy_external_tuner_never_retuned_or_force_unlocked(self):
        self.control.owner=123
        self.scan()
        self.assertEqual(self.control.owner,123)
        self.assertEqual(self.dx.scan['status'],'failed')
        self.assertTrue(all(field=='lockkey' and value!='none' for field,value,key in self.control.calls))
        self.assertFalse(self.dx.busy)

    def test_streaming_external_tuner_not_locked(self):
        self.control.target='udp://somewhere'
        self.scan()
        self.assertFalse(self.control.calls)

    def test_cancel_during_dwell_releases_and_does_not_log_incomplete_sample(self):
        self.dx.scan={'id':'test','running':True,'completed':0,'results':[],'error':''}
        def cancel(_): self.dx.cancel.set();return True
        with patch.object(self.dx.cancel,'wait',side_effect=cancel): self.dx.run(14,36,4)
        self.assertEqual(self.dx.scan['status'],'cancelled')
        self.assertFalse(self.dx.log.rows());self.assertIsNone(self.control.owner)

    def test_release_failure_is_recoverable_after_worker_restart(self):
        self.control.fail_release=True
        self.scan(14,14)
        self.assertTrue(self.dx.snapshot()['cleanup_pending'])
        restarted=DX(self.device,CONFIG,self.metadata,self.path,self.control)
        self.assertTrue(restarted.busy)
        self.control.fail_release=False
        restarted.retry_release()
        self.assertFalse(restarted.busy);self.assertFalse(restarted.journal.exists())

    def test_manual_requires_lock_and_reads_new_measurement(self):
        self.control.channel='auto:473000000'
        self.dx.manual('North 30 degrees')
        self.assertEqual(self.dx.log.rows()[0]['notes'],'North 30 degrees')
        with patch.object(self.control,'get',return_value='none'):
            with self.assertRaisesRegex(ValueError,'No confirmed lock'): self.dx.manual('')
        self.assertEqual(len(self.dx.log.rows()),1)

    def test_identity_failure_prevents_tuner_writes(self):
        self.device.discover.side_effect=ValueError('ID mismatch')
        self.scan()
        self.assertFalse(self.control.calls)
        self.dx.poll();self.assertIsNone(self.dx.sample);self.assertIn('ID mismatch',self.dx.error)

    def test_scan_and_playback_exclusion_and_validation(self):
        tv=Television(CONFIG,self.temp.name,self.device,self.dx)
        tv.process=Mock()
        with self.assertRaisesRegex(ValueError,'Stop the TV'): tv.start_scan({})
        tv.process=None
        for body in [{'first':1},{'first':10,'last':2},{'dwell':True},{'dwell':16}]:
            with self.assertRaises(ValueError): tv.start_scan(body)
        self.dx.scan['running']=True
        with self.assertRaisesRegex(ValueError,'Finish or cancel'): tv.play('5.1')
        with self.assertRaisesRegex(ValueError,'already running'): self.dx.start(2,36,4)

    def test_cli_targets_registered_tuner_with_lock_auth_and_timeout(self):
        with patch('atsc_dx.socket.gethostbyname',return_value='192.168.1.20'),patch('atsc_dx.subprocess.run',return_value=Mock(returncode=0,stdout='ok',stderr='')) as run:
            Control(CONFIG).set('channel','auto:473000000',123)
            self.assertEqual(run.call_args.args[0],['hdhomerun_config','192.168.1.20','key','123','set','/tuner1/channel','auto:473000000'])
            self.assertEqual(run.call_args.kwargs['timeout'],4)

    def test_no_lock_attempt_is_retained_without_claiming_reception(self):
        original=self.control.get
        def get(field):
            value=original(field)
            return value.replace('lock=8vsb','lock=none') if field=='status' else value
        with patch.object(self.control,'get',side_effect=get): self.scan(14,14)
        entry=self.dx.log.rows()[0]
        self.assertFalse(entry['measurement']['locked'])
        self.assertEqual(entry['reason'],'scan')

    def test_http_log_export_validation_and_receiver_isolation(self):
        self.control.channel='auto:473000000'
        tv=Television(CONFIG,self.temp.name,self.device,self.dx)
        server=ThreadingHTTPServer(('127.0.0.1',0),make_handler(tv))
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=5)
            try:
                client.request('POST','/api/tv/log',json.dumps({'notes':'Test log'}),{'Content-Type':'application/json'})
                response=client.getresponse();value=json.loads(response.read())
                self.assertEqual(response.status,200);self.assertIsNotNone(value['dx']['last_logged'])
                for path in ['/api/tv/receptions','/api/tv/receptions.json']:
                    client.request('GET',path);response=client.getresponse();value=json.loads(response.read())
                    self.assertEqual(value['entries'][0]['source']['id'],'roof')
                client.request('GET','/api/tv/receptions?before=nope')
                response=client.getresponse();response.read();self.assertEqual(response.status,400)
                client.request('POST','/api/tv/scan',json.dumps({'first':1}),{'Content-Type':'application/json'})
                response=client.getresponse();response.read();self.assertEqual(response.status,400)
                other=ReceptionLog(Path(self.temp.name)/'other'/'receptions.sqlite3')
                self.assertFalse(other.rows())
            finally: client.close()
        finally: server.shutdown();server.server_close();thread.join()

    def test_slow_tuner_release_does_not_hold_supervisor_registry_lock(self):
        registry=Registry(Path(self.temp.name))
        registry.put_source({**self.metadata,'kind':'hdhomerun','connection':{**CONFIG,'transport':'http'}})
        supervisor=Supervisor(registry)
        worker={'status':'running','processes':[],'env':{'ROSE_TV_BINDING':'{}'}}
        supervisor.workers['roof']=worker
        entered=threading.Event();release=threading.Event()
        def terminate(_): entered.set();release.wait(3)
        with patch.object(supervisor,'_terminate',side_effect=terminate):
            thread=threading.Thread(target=supervisor.stop,args=('roof',));thread.start()
            try:
                self.assertTrue(entered.wait(2))
                acquired=supervisor.lock.acquire(timeout=.2)
                if acquired: supervisor.lock.release()
                self.assertTrue(acquired)
            finally: release.set();thread.join()
        self.assertEqual(worker['status'],'stopped')

    def test_source_context_is_forwarded_to_worker(self):
        env=worker_environment(self.temp.name,self.temp.name,{'id':'roof','slot':1},
                               {**self.metadata,'kind':'hdhomerun','connection':CONFIG})
        self.assertEqual(json.loads(env['ROSE_SOURCE_METADATA'])['antenna'],'Yagi north')

if __name__=='__main__':unittest.main()
