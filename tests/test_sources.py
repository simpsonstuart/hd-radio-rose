"""Source ownership, persistence, process isolation and real HTTP routing."""
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'container'))
from sources import Registry, binding
from receivers import Supervisor, worker_environment, make_handler, route


def source(sid='default', serial='00000001'):
    return {'id':sid,'name':sid,'kind':'sdr','connection':{'transport':'local_soapy','driver':'rtlsdr','serial':serial}}


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT)
        self.addCleanup(self.temp.cleanup)
        self.registry = Registry(Path(self.temp.name))

    def test_migration_retains_default_and_requires_stable_binding(self):
        self.assertEqual(self.registry.snapshot()['receivers'][0]['slot'], 0)
        with self.assertRaisesRegex(ValueError,'Bind the Default'):
            self.registry.put_source(source('second'))
        self.registry.put_source(source())
        self.registry.put_source(source('second','00000002'))
        reloaded = Registry(Path(self.temp.name)).snapshot()
        self.assertEqual(len(reloaded['sources']),2)
        self.assertFalse(reloaded['receivers'][1]['enabled'])
        with self.assertRaisesRegex(ValueError,'already belongs'):
            self.registry.put_source(source('duplicate','00000002'))
        self.assertEqual(len(Registry(Path(self.temp.name)).snapshot()['sources']),2)

    def test_remote_binding_and_duplicate_endpoint(self):
        self.registry.put_source(source())
        remote = source('remote')
        remote['connection'] = {'transport':'rtl_tcp_remote','host':'RADIO.LAN.','port':1234}
        stored = self.registry.put_source(remote)
        self.assertEqual(binding(stored)['remote_host'],'radio.lan')
        remote['id'] = 'alias'
        with self.assertRaises(ValueError): self.registry.put_source(remote)
        remote['connection'] = {'transport':'soapy_remote','host':'radio.lan','port':55132,'driver':'hackrf','serial':'abc123'}
        stored = self.registry.put_source(remote)
        self.assertEqual(binding(stored)['remote_extra_args'],'remote:driver=hackrf,remote:serial=abc123')

    def test_invalid_ids_and_arguments_never_reach_files_or_processes(self):
        for sid in ['../escape','/tmp','A','x/y','x?y','']:
            with self.assertRaises(ValueError): self.registry.put_source(source(sid))
        for serial in ['', 'one,driver=hackrf', '../one', 'one\n']:
            with self.assertRaises(ValueError): self.registry.put_source(source(serial=serial))
        corrupt = self.registry.snapshot(); corrupt['version'] = 999
        self.registry.path.write_text(json.dumps(corrupt))
        with self.assertRaises(ValueError): Registry(Path(self.temp.name))

    def test_worker_ports_paths_and_settings_are_disjoint(self):
        self.registry.put_source(source())
        self.registry.put_source(source('second','00000002'))
        state = self.registry.snapshot()
        envs = [worker_environment(self.registry.root,Path('/run/rose'),r,s)
                for r,s in zip(state['receivers'],state['sources'])]
        for key in ['ROSE_DATA_DIR','ROSE_RUN_DIR','ROSE_HTTP_PORT','ROSE_RPC_PORT','ROSE_PCM_PORT','ROSE_SAME_PORT','ROSE_SDR_BINDING']:
            self.assertNotEqual(envs[0][key],envs[1][key])
        self.assertEqual(Path(envs[0]['ROSE_DATA_DIR']),self.registry.root)
        self.assertEqual(len({e[k] for e in envs for k in ['ROSE_HTTP_PORT','ROSE_RPC_PORT','ROSE_PCM_PORT','ROSE_SAME_PORT']}),8)

    def test_running_source_cannot_be_reassigned(self):
        supervisor = Supervisor(self.registry)
        supervisor.workers['default'] = {'status':'running','processes':[Mock()]}
        with self.assertRaisesRegex(ValueError,'Stop this receiver'):
            supervisor.put_source(source())
        with self.assertRaises(ValueError): supervisor.start('default')

    def test_failed_worker_does_not_stop_other_receiver(self):
        supervisor = Supervisor(self.registry)
        failed, healthy = Mock(), Mock()
        failed.poll.return_value = 1; healthy.poll.return_value = None
        supervisor.workers = {'bad':{'status':'running','processes':[failed]},'good':{'status':'running','processes':[healthy]}}
        with patch.object(supervisor.closing,'wait',side_effect=[False,True]), patch.object(supervisor,'_terminate') as terminate:
            supervisor.monitor()
        self.assertEqual(supervisor.workers['bad']['status'],'failed')
        self.assertEqual(supervisor.workers['good']['status'],'running')
        terminate.assert_called_once_with(supervisor.workers['bad'])

    def test_routes_cannot_cross_receiver_namespace(self):
        self.assertEqual(route('/api/state'),('default','/api/state'))
        self.assertEqual(route('/api/receivers/roof/api/media?path=one.png'),('roof','/api/media?path=one.png'))
        self.assertEqual(route('/api/receivers/roof/audio.mp3?t=1'),('roof','/audio.mp3?t=1'))
        for path in ['/api/receivers/../api/state','/api/receivers/%2f/api/state','/outside']:
            with self.assertRaises(ValueError): route(path)


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.servers=[]
        self.requests=[]
        self.block=threading.Event()
        owner=self
        def backend(name):
            class Backend(BaseHTTPRequestHandler):
                def do_GET(self):
                    if self.path == '/slow': owner.block.wait(3)
                    body = (name+':'+self.path).encode()
                    self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                def do_POST(self):
                    owner.requests.append((name,self.path,self.rfile.read(int(self.headers['Content-Length']))))
                    self.do_GET()
                def log_message(self,*args): pass
            return Backend
        self.ports={name:self.server(backend(name)) for name in ['default','roof']}
        self.supervisor=Mock()
        self.supervisor.port.side_effect=lambda rid:self.ports[rid]
        self.supervisor.snapshot.return_value={'sources':[],'receivers':[]}
        self.port=self.server(make_handler(self.supervisor))

    def server(self,handler):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        server.daemon_threads=True
        threading.Thread(target=server.serve_forever,daemon=True).start()
        self.servers.append(server)
        return server.server_port

    def tearDown(self):
        self.block.set()
        for server in self.servers: server.shutdown(); server.server_close()

    def request(self,path,body=None,headers=None):
        client=HTTPConnection('127.0.0.1',self.port,timeout=4)
        client.request('GET' if body is None else 'POST',path,body=body,headers=headers or {})
        response=client.getresponse(); result=(response.status,response.read()); client.close(); return result

    def test_state_audio_media_and_commands_route_to_selected_worker(self):
        self.assertEqual(self.request('/api/state'),(200,b'default:/api/state'))
        for path in ['/api/state','/api/media?path=image.png','/api/recording?name=one.opus','/api/alerts','/audio.mp3']:
            self.assertEqual(self.request('/api/receivers/roof'+path),(200,('roof:'+path).encode()))
        body=b'{"type":"SelectBand","value":"noaa"}'
        self.assertEqual(self.request('/api/receivers/roof/api/command',body,{'Content-Type':'application/json'})[0],200)
        self.assertEqual(self.requests,[('roof','/api/command',body)])

    def test_registry_rejects_cross_origin_and_oversized_requests(self):
        self.assertEqual(self.request('/api/sources',b'{}',{'Content-Type':'application/json','Origin':'https://other.invalid'})[0],403)
        self.assertEqual(self.request('/api/sources',b'x'*8193,{'Content-Type':'application/json'})[0],413)
        self.supervisor.put_source.assert_not_called()

    def test_stalled_receiver_does_not_block_other_receiver(self):
        result=[]
        thread=threading.Thread(target=lambda:result.append(self.request('/api/receivers/roof/slow')))
        thread.start()
        self.assertEqual(self.request('/api/state'),(200,b'default:/api/state'))
        self.block.set(); thread.join(timeout=4)
        self.assertEqual(result,[(200,b'roof:/slow')])


if __name__ == '__main__': unittest.main()
