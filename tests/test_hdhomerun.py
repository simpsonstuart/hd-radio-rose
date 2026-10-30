import http.client
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'container'))
from sources import Registry,connection
from receivers import worker_environment,Supervisor
from hdhomerun import Device,Television,channel_rows,encoder_args,make_handler

CONFIG={'transport':'http','host':'192.168.1.20','device_id':'10ABCDEF','tuner':0}


def tv_source(sid='tv',tuner=0,host='192.168.1.20'):
    return {'id':sid,'name':sid,'kind':'hdhomerun','connection':{**CONFIG,'tuner':tuner,'host':host}}


class ProviderTests(unittest.TestCase):
    def test_registers_tv_alongside_unbound_legacy_radio_and_deduplicates_tuners(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            registry=Registry(Path(directory))
            registry.put_source(tv_source())
            registry.put_source(tv_source('tv-two',1))
            with self.assertRaisesRegex(ValueError,'already belongs'):
                registry.put_source(tv_source('alias',0,'other-host'))
            with self.assertRaisesRegex(ValueError,'Default remains'):
                registry.put_source(tv_source('default',2))
            self.assertEqual(len(Registry(Path(directory)).snapshot()['sources']),3)
            value=registry.snapshot()
            env=worker_environment(directory,directory,value['receivers'][1],value['sources'][1])
            self.assertNotIn('ROSE_SDR_BINDING',env)
            self.assertEqual(json.loads(env['ROSE_TV_BINDING']),CONFIG)

    def test_connection_rejects_urls_paths_credentials_and_invalid_tuners(self):
        for change in [{'host':'http://radio/'},{'host':'a@b'},{'device_id':'bad'},{'tuner':True},{'tuner':16},{'transport':'rtsp'}]:
            with self.assertRaises(ValueError): connection({**CONFIG,**change},kind='hdhomerun')

    def test_metadata_checks_identity_and_omits_auth_and_supplied_urls(self):
        device=Device(CONFIG)
        raw={'DeviceID':'10ABCDEF','TunerCount':2,'FriendlyName':'Roof TV','DeviceAuth':'secret','BaseURL':'http://elsewhere'}
        with patch.object(device,'get_json',return_value=raw):
            self.assertEqual(device.discover(),{'DeviceID':'10ABCDEF','TunerCount':2,'FriendlyName':'Roof TV'})
        for invalid in [{**raw,'DeviceID':'12345678'},{**raw,'TunerCount':0},[]]:
            with patch.object(device,'get_json',return_value=invalid),self.assertRaises(ValueError): device.discover()

    def test_lineup_labels_protection_and_unsupported_audio_without_trusting_urls(self):
        rows=channel_rows([{'GuideNumber':'5.1','GuideName':'Station','URL':'file:///etc/passwd'},
                           {'GuideNumber':'5.2','Tags':'favorite, drm'}, {'GuideNumber':'105.1','AudioCodec':'AC-4'},
                           {'GuideNumber':'6','DRM':1}, {'GuideNumber':'../../bad'}, {'GuideNumber':'5.1'}])
        self.assertEqual(len(rows),4)
        self.assertNotIn('URL',rows[0])
        self.assertFalse(rows[0]['unavailable_reason'])
        self.assertTrue(all(row['unavailable_reason'] for row in rows[1:]))

    def test_fixed_tuner_stream_and_busy_error(self):
        client=Mock();response=Mock(status=200);client.getresponse.return_value=response
        with patch('hdhomerun.http.client.HTTPConnection',return_value=client):
            Device({**CONFIG,'tuner':1}).open_stream('5.1')
            client.request.assert_called_once_with('GET','/tuner1/v5.1')
        response.status=503;response.getheader.return_value='804 Tuner In Use'
        with patch('hdhomerun.http.client.HTTPConnection',return_value=client),self.assertRaisesRegex(RuntimeError,'in use'):
            Device(CONFIG).open_stream('5.1')
        client.close.assert_called()

    def test_tv_worker_launch_uses_no_sdr_or_audio_encoder_service(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            registry=Registry(Path(directory));registry.put_source(tv_source())
            supervisor=Supervisor(registry)
            worker={'status':'starting','processes':[],'env':{'ROSE_TV_BINDING':json.dumps(CONFIG)}}
            with patch('receivers.subprocess.Popen') as popen:
                supervisor._launch('tv',worker)
            self.assertEqual(popen.call_count,1)
            self.assertTrue(popen.call_args.args[0][-1].endswith('hdhomerun.py'))
            self.assertEqual(worker['status'],'running')


class PlaybackTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT);self.addCleanup(self.temp.cleanup)
        self.device=Mock()
        self.device.discover.return_value={'DeviceID':'10ABCDEF','TunerCount':2}
        self.device.lineup.return_value=channel_rows([{'GuideNumber':'5.1'},{'GuideNumber':'5.2','DRM':1}])
        self.tv=Television(CONFIG,self.temp.name,self.device)

    def running(self):
        token='a'*32
        self.tv.error='';self.tv.generation=token;self.tv.selected='5.1';self.tv.last_view=self.tv.started=time.monotonic()
        self.tv.process=Mock();self.tv.process.poll.return_value=None
        directory=self.tv.root/token;directory.mkdir()
        return directory

    def test_no_tuning_for_missing_or_protected_channel(self):
        for channel in ['5.2','99.1','5.1?x=1',None]:
            with self.assertRaises(ValueError):self.tv.play(channel)
        self.device.open_stream.assert_not_called()

    def test_hls_is_generation_scoped_and_requests_renew_idle_lease(self):
        directory=self.running();(directory/'index.m3u8').write_bytes(b'#EXTM3U\n')
        self.tv.last_view=0
        data,mime=self.tv.media('/tv/hls/'+self.tv.generation+'/index.m3u8')
        self.assertEqual(data,b'#EXTM3U\n');self.assertIn('mpegurl',mime)
        self.assertGreater(self.tv.last_view,0)
        for path in ['/tv/hls/'+('b'*32)+'/index.m3u8','/tv/hls/'+self.tv.generation+'/../../sources.json']:
            with self.assertRaises(FileNotFoundError):self.tv.media(path)
        self.assertTrue(self.tv.snapshot()['playback']['ready'])
        self.tv.stop();self.assertFalse(directory.exists())

    def test_joining_same_channel_does_not_allocate_a_second_tuner(self):
        self.running();process=self.tv.process
        self.tv.play('5.1')
        self.assertIs(self.tv.process,process)
        self.device.open_stream.assert_not_called()
        self.tv.stop()

    def test_idle_and_failed_encoder_release_tuner(self):
        self.running();process=self.tv.process;self.tv.last_view=time.monotonic()-61
        self.tv.check();process.terminate.assert_called_once();self.assertIsNone(self.tv.process)
        self.running();self.tv.process.poll.return_value=1
        self.tv.check();self.assertIn('decoder exited',self.tv.error)

    def test_encoder_uses_pipe_input_and_bounded_hls(self):
        args=encoder_args(Path(self.temp.name))
        self.assertEqual(args[args.index('-i')+1],'pipe:0')
        self.assertEqual(args[args.index('-hls_list_size')+1],'6')
        self.assertIn('delete_segments+independent_segments+temp_file',args)
        self.assertIn('libx264',args);self.assertIn('aac',args)


class DeviceHttpTests(unittest.TestCase):
    def test_real_http_discovery_and_lineup_skip_remote_urls(self):
        class Fake(BaseHTTPRequestHandler):
            def do_GET(self):
                value={'DeviceID':'10ABCDEF','TunerCount':2} if self.path=='/discover.json' else [{'GuideNumber':'7.1','URL':'http://untrusted/stream'}]
                body=json.dumps(value).encode();self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Fake)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        original=http.client.HTTPConnection
        try:
            with patch('hdhomerun.http.client.HTTPConnection',side_effect=lambda host,port,timeout:original('127.0.0.1',server.server_port,timeout=timeout)):
                device=Device(CONFIG)
                self.assertEqual(device.discover()['DeviceID'],'10ABCDEF')
                self.assertEqual(device.lineup()[0]['number'],'7.1')
        finally:server.shutdown();server.server_close();thread.join()


if __name__=='__main__':unittest.main()
