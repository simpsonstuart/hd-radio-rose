"""Independent worker supervision and same-origin routing for receiver sessions."""
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

from sources import Registry, binding, identifier
from runtime import discard_rejected_body


def worker_environment(root, runtime, receiver, source):
    rid, slot = receiver['id'], receiver['slot']
    data = Path(root) if rid == 'default' else Path(root) / 'receivers' / rid
    run = Path(runtime) if rid == 'default' else Path(runtime) / 'receivers' / rid
    # Retain legacy default RPC/PCM/SAME ports for tools and smoke tests.
    base = 8090 if slot == 0 else 8100 + slot * 4
    env = dict(os.environ, ROSE_DATA_DIR=str(data), ROSE_RUN_DIR=str(run),
               ROSE_HTTP_PORT=str(base), ROSE_RPC_PORT=str(base+1),
               ROSE_PCM_PORT=str(base+2), ROSE_SAME_PORT=str(base+3), ROSE_RECEIVER_ID=rid)
    env.pop('ROSE_SDR_BINDING', None)
    env.pop('ROSE_TV_BINDING', None)
    if source['kind'] == 'hdhomerun':
        env['ROSE_TV_BINDING'] = json.dumps(source['connection'])
        env['ROSE_SOURCE_METADATA'] = json.dumps({key:source.get(key,'') for key in ('id','name','location','antenna','purpose')})
    if (value := binding(source)) is not None:
        env['ROSE_SDR_BINDING'] = json.dumps(value)
    return env


def call(port, request, timeout=2):
    with socket.create_connection(('127.0.0.1', port), timeout=timeout) as client:
        client.settimeout(timeout)
        client.sendall(json.dumps(request).encode() + b'\n')
        with client.makefile('rb') as stream:
            response = stream.readline(8*1024*1024)
        if not response.endswith(b'\n'):
            raise RuntimeError('Invalid receiver response')
        return json.loads(response)


class Supervisor:
    def __init__(self, registry, runtime=Path('/run/rose')):
        self.registry, self.runtime = registry, Path(runtime)
        self.workers = {}
        self.lock = threading.RLock()
        self.closing = threading.Event()

    def describe(self, rid):
        value = self.registry.snapshot()
        receiver = next((r for r in value['receivers'] if r['id'] == rid), None)
        if receiver is None:
            raise ValueError('Unknown receiver')
        source = next(s for s in value['sources'] if s['id'] == receiver['source_id'])
        return receiver, source

    def start(self, rid):
        with self.lock:
            if self.closing.is_set():
                raise RuntimeError('Platform is shutting down')
            if self.workers.get(rid, {}).get('status') in {'starting','running','stopping'}:
                raise ValueError('Stop the session before restarting it')
            receiver, source = self.describe(rid)
            env = worker_environment(self.registry.root, self.runtime, receiver, source)
            data, run = Path(env['ROSE_DATA_DIR']), Path(env['ROSE_RUN_DIR'])
            data.mkdir(parents=True, exist_ok=True); run.mkdir(parents=True, exist_ok=True)
            self.registry.enable(rid, True)
            worker = {'status':'starting', 'error':'', 'processes':[], 'env':env}
            self.workers[rid] = worker
            # Startup is asynchronous: a stalled USB device must not hold up
            # controls or startup of any other receiver.
            threading.Thread(target=self._launch, args=(rid,worker), daemon=True).start()

    def _launch(self, rid, worker):
        env = worker['env']
        try:
            if 'ROSE_TV_BINDING' in env:
                with self.lock:
                    if worker['status'] != 'starting' or self.closing.is_set(): return
                    process = subprocess.Popen([sys.executable, '/opt/rose/hdhomerun.py'], env=env, start_new_session=True)
                    worker['processes'].append(process)
                    worker['status'] = 'running'
                return
            with self.lock:
                if worker['status'] != 'starting' or self.closing.is_set():
                    return
                app = subprocess.Popen(['/opt/nrsc5-studio/nrsc5-studio'], env=env, start_new_session=True)
                worker['processes'].append(app)
                (Path(env['ROSE_RUN_DIR']) / 'app.pid').write_text(str(app.pid))
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if worker['status'] != 'starting' or self.closing.is_set():
                    return
                if app.poll() is not None:
                    raise RuntimeError(f'Receiver engine exited ({app.returncode})')
                try:
                    if call(int(env['ROSE_RPC_PORT']), {'op':'state'})['ready']:
                        break
                except (OSError, RuntimeError, KeyError, ValueError):
                    pass
                self.closing.wait(.25)
            else:
                raise RuntimeError('Receiver engine did not become ready within 30 seconds')
            with self.lock:
                if worker['status'] != 'starting' or self.closing.is_set():
                    return
                audio = subprocess.Popen([sys.executable, '/opt/rose/audio_server.py'], env=env, start_new_session=True)
                worker['processes'].append(audio)
                worker['status'] = 'running'
        except Exception as error:
            with self.lock:
                if worker['status'] in {'stopping','stopped'}: return
                worker['error'] = str(error)
                self._terminate(worker)
                worker['status'] = 'failed'

    @staticmethod
    def _terminate(worker):
        processes = worker['processes']
        for process in reversed(processes):
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + (30 if 'ROSE_TV_BINDING' in worker.get('env', {}) else 5)
        for process in reversed(processes):
            try:
                process.wait(timeout=max(.01, deadline-time.monotonic()))
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=2)
        worker['processes'] = []

    def stop(self, rid, persist=True):
        with self.lock:
            self.describe(rid)
            if persist:
                self.registry.enable(rid, False)
            worker = self.workers.get(rid)
            if not worker:
                return
            if worker['status'] == 'stopping':
                return
            worker['status'] = 'stopping'
        try:
            if 'ROSE_TV_BINDING' not in worker['env']:
                call(int(worker['env']['ROSE_RPC_PORT']), {'op':'shutdown'}, timeout=8)
        except (OSError, ValueError, RuntimeError):
            pass
        # A slow tuner release must not hold the registry lock or serialize shutdowns.
        self._terminate(worker)
        with self.lock:
            worker['status'] = 'stopped'

    def put_source(self, body):
        with self.lock:
            sid = body.get('id') if isinstance(body, dict) else None
            assigned = [r['id'] for r in self.registry.snapshot()['receivers'] if r['source_id'] == sid]
            if any(self.workers.get(rid, {}).get('status') in {'starting','running','stopping'} for rid in assigned):
                raise ValueError('Stop this receiver session before editing its source')
            return self.registry.put_source(body)

    def snapshot(self):
        value = self.registry.snapshot()
        with self.lock:
            for receiver in value['receivers']:
                worker = self.workers.get(receiver['id'], {})
                receiver.update(status=worker.get('status', 'stopped'), error=worker.get('error', ''))
        return value

    def port(self, rid):
        # Reads never wait on another receiver's shutdown or USB operation.
        worker = self.workers.get(rid)
        if not worker or worker['status'] != 'running':
            raise RuntimeError('Receiver session is not running; start it in Sources')
        return int(worker['env']['ROSE_HTTP_PORT'])

    def monitor(self):
        while not self.closing.wait(1):
            with self.lock:
                for worker in self.workers.values():
                    if worker['status'] == 'running' and any(p.poll() is not None for p in worker['processes']):
                        worker['error'] = 'A receiver service exited; other receivers are unaffected. Stop and start this session to retry.'
                        self._terminate(worker)
                        worker['status'] = 'failed'

    def close(self):
        self.closing.set()
        threads = [threading.Thread(target=self.stop, args=(rid,False)) for rid in list(self.workers)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=32)


def route(path):
    if path.startswith('/api/receivers/'):
        parts = path.split('/', 4)
        if len(parts) != 5:
            raise ValueError('Missing receiver endpoint')
        return identifier(parts[3]), '/' + parts[4]
    if path.startswith('/api/') or path.split('?',1)[0] == '/audio.mp3':
        return 'default', path
    raise ValueError('Unknown route')


def make_handler(supervisor):
    class Handler(BaseHTTPRequestHandler):
        def json_response(self, value, status=200):
            if status >= 400: discard_rejected_body(self)
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(body)

        def dispatch(self, body=None):
            try:
                if self.path == '/healthz':
                    return self.json_response({'ready':not supervisor.closing.is_set()})
                if self.path in {'/api/sources','/api/receivers'} and body is None:
                    return self.json_response(supervisor.snapshot())
                if self.path == '/api/sources' and body is not None:
                    supervisor.put_source(json.loads(body))
                    return self.json_response(supervisor.snapshot())
                rid, target = route(self.path)
                if target == '/lifecycle' and body is not None:
                    operation = json.loads(body)
                    if not isinstance(operation, dict): raise ValueError('Expected lifecycle object')
                    action = operation.get('action')
                    if action == 'start': supervisor.start(rid)
                    elif action == 'stop': supervisor.stop(rid)
                    else: raise ValueError('Choose start or stop')
                    return self.json_response(supervisor.snapshot())
                self.proxy(supervisor.port(rid), target, body)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except (ValueError, TypeError, KeyError) as error:
                self.json_response({'error':str(error)},400)
            except (OSError, RuntimeError, http.client.HTTPException) as error:
                self.json_response({'error':str(error)},503)

        def do_GET(self): self.dispatch()

        def do_POST(self):
            origin = self.headers.get('Origin')
            if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                return self.json_response({'error':'Cross-origin command denied'},403)
            if self.headers.get_content_type() != 'application/json':
                return self.json_response({'error':'Use application/json'},415)
            try:
                length = int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 8192:
                    return self.json_response({'error':'Invalid request size'},413)
                self.connection.settimeout(5)
                body = self.rfile.read(length)
                self._body_consumed = True
                if len(body) != length: raise ValueError('Incomplete body')
                self.dispatch(body)
            except (ValueError, OSError) as error:
                self.json_response({'error':str(error)},400)

        def proxy(self, port, target, body):
            client = http.client.HTTPConnection('127.0.0.1',port,timeout=40)
            headers = {key:self.headers[key] for key in ('Host','Origin','Sec-Fetch-Site','Content-Type') if key in self.headers}
            started = False
            try:
                client.request(self.command, target, body=body, headers=headers)
                response = client.getresponse()
                self.send_response(response.status)
                for key in ('Content-Type','Content-Length','Content-Disposition','Cache-Control'):
                    if response.getheader(key): self.send_header(key,response.getheader(key))
                self.send_header('Connection','close'); self.end_headers()
                started = True
                self.connection.settimeout(15)
                while chunk := response.read1(65536):
                    self.wfile.write(chunk)
            except (OSError, http.client.HTTPException):
                if not started: raise
            finally:
                client.close(); self.close_connection = True

        def log_message(self, _format, *args): pass
    return Handler


def main():
    supervisor = Supervisor(Registry())
    server = ThreadingHTTPServer(('127.0.0.1',8089),make_handler(supervisor))
    server.daemon_threads = True
    def stop(_signum,_frame):
        supervisor.closing.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    for receiver in supervisor.registry.snapshot()['receivers']:
        if receiver['enabled']: supervisor.start(receiver['id'])
    threading.Thread(target=supervisor.monitor,daemon=True).start()
    try:
        server.serve_forever()
    finally:
        server.server_close(); supervisor.close()


if __name__ == '__main__': main()
