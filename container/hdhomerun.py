"""HDHomeRun provider: fixed-tuner HTTP input and bounded browser HLS output.

The radio DSP never handles TV. Device metadata and lineup remain provider data,
leaving RF scanning, signal measurements and DX observations for later adapters.
"""
from collections import deque
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import threading
import time
from urllib.parse import urlsplit
import uuid

from runtime import RUN, port, discard_rejected_body
from sources import connection

CHANNEL = re.compile(r'[0-9]{1,5}(?:\.[0-9]{1,3})?\Z')
DEVICE_FIELDS = ('FriendlyName','ModelNumber','FirmwareName','FirmwareVersion','DeviceID','TunerCount')
ERRORS = {'804':'Tuner is in use by another application', '805':'All tuners are in use',
          '803':'Device is busy scanning', '807':'No video data: check reception',
          '811':'Channel requires protected playback'}


def channel_rows(rows):
    if not isinstance(rows, list) or len(rows) > 2000:
        raise ValueError('Invalid or oversized channel lineup')
    result, seen = [], set()
    for row in rows:
        if not isinstance(row, dict): continue
        number = row.get('GuideNumber')
        if not isinstance(number, str) or not CHANNEL.fullmatch(number) or number in seen: continue
        seen.add(number)
        tags = row.get('Tags', '')
        tags = tags.split(',') if isinstance(tags, str) else tags if isinstance(tags, list) else []
        drm = row.get('DRM') in (1, True, '1', 'true') or 'drm' in [str(t).strip().lower() for t in tags]
        audio = str(row.get('AudioCodec', ''))[:40]
        reason = 'Protected channel: supported HDHomeRun playback required' if drm else (
            'AC-4 audio is not supported by this FFmpeg build' if re.sub(r'[^a-z0-9]', '', audio.lower()) == 'ac4' else '')
        result.append({'number':number,'name':str(row.get('GuideName', number))[:160],
                       'video_codec':str(row.get('VideoCodec',''))[:40], 'audio_codec':audio,
                       'hd':row.get('HD') in (1,True,'1'), 'drm':drm, 'unavailable_reason':reason})
    return result


class Device:
    def __init__(self, config): self.config = connection(config, kind='hdhomerun')

    def get_json(self, path):
        client = http.client.HTTPConnection(self.config['host'],80,timeout=5)
        try:
            client.request('GET',path)
            response = client.getresponse()
            if response.status != 200: raise RuntimeError(f'HDHomeRun {path}: HTTP {response.status}')
            data = response.read(2*1024*1024+1)
            if len(data) > 2*1024*1024: raise ValueError('HDHomeRun response too large')
            return json.loads(data)
        finally: client.close()

    def discover(self):
        value = self.get_json('/discover.json')
        if not isinstance(value, dict) or str(value.get('DeviceID','')).upper() != self.config['device_id']:
            raise ValueError('Device ID mismatch: check the registered host and device ID')
        count = value.get('TunerCount')
        if type(count) is not int or not 1 <= count <= 16 or self.config['tuner'] >= count:
            raise ValueError('The registered tuner does not exist on this device')
        # Never relay DeviceAuth, device-supplied URLs, or unexpected fields.
        return {key:value[key] for key in DEVICE_FIELDS if key in value}

    def lineup(self): return channel_rows(self.get_json('/lineup.json'))

    def open_stream(self, number):
        if not CHANNEL.fullmatch(number): raise ValueError('Invalid channel')
        client = http.client.HTTPConnection(self.config['host'],5004,timeout=8)
        try:
            # A named tuner never silently falls back to /auto or steals another source.
            client.request('GET',f"/tuner{self.config['tuner']}/v{number}")
            response = client.getresponse()
            if response.status != 200:
                code = (response.getheader('X-HDHomeRun-Error') or '').split(' ')[0]
                raise RuntimeError(ERRORS.get(code, f'HDHomeRun stream failed: HTTP {response.status} (busy, protected or no signal)'))
            return client, response
        except Exception:
            client.close(); raise


def encoder_args(directory):
    return ['ffmpeg','-hide_banner','-loglevel','warning','-nostdin','-y',
            '-threads','2','-f','mpegts','-i','pipe:0','-map','0:v:0','-map','0:a:0?',
            '-vf',"yadif,scale=w='min(1280,iw)':h='min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1,fps=30",
            '-c:v','libx264','-preset','veryfast','-threads','2','-pix_fmt','yuv420p',
            '-b:v','2500k','-maxrate','3000k','-bufsize','6000k','-g','60','-keyint_min','60','-sc_threshold','0',
            '-c:a','aac','-b:a','128k','-ac','2','-ar','48000',
            '-f','hls','-hls_time','2','-hls_list_size','6','-hls_delete_threshold','2',
            '-hls_flags','delete_segments+independent_segments+temp_file',
            '-hls_segment_filename',str(directory/'segment%06d.ts'),str(directory/'index.m3u8')]


class Television:
    def __init__(self, config, runtime=RUN, device=None):
        self.device = device or Device(config)
        self.config = config
        self.root = Path(runtime)/'tv-hls'
        self.root.mkdir(parents=True,exist_ok=True)
        # Only this provider's generated segments; never touch persistent recordings.
        for child in self.root.iterdir():
            if child.is_dir() and re.fullmatch(r'[a-f0-9]{32}',child.name): shutil.rmtree(child)
        self.lock = threading.RLock()
        self.refresh_lock = threading.Lock()
        self.info, self.channels = {}, []
        self.updated, self.device_error, self.error = None, '', ''
        self.process = self.stream = self.response = None
        self.generation = self.selected = None
        self.last_view = self.started = 0
        self.logs = deque(maxlen=12)
        self.closed = threading.Event()

    def refresh(self):
        with self.refresh_lock:
            try:
                info, channels = self.device.discover(), self.device.lineup()
                with self.lock:
                    self.info, self.channels, self.updated, self.device_error = info, channels, time.time(), ''
            except (OSError, ValueError, RuntimeError, http.client.HTTPException) as error:
                with self.lock: self.device_error = str(error)
                raise

    def snapshot(self):
        with self.lock:
            live = self.process is not None and self.process.poll() is None
            ready = live and (self.root/self.generation/'index.m3u8').is_file()
            return {'kind':'hdhomerun','ready':True,'device':self.info,'channels':self.channels,
                    'lineup_updated':self.updated,'device_error':self.device_error,'playback_error':self.error,
                    'tuner':self.config['tuner'], 'channel':self.selected,
                    'playback':{'running':live,'ready':ready,'generation':self.generation,
                                'url':f'/tv/hls/{self.generation}/index.m3u8' if ready else None},
                    'diagnostics':list(self.logs)}

    def play(self, number):
        if not isinstance(number,str) or not CHANNEL.fullmatch(number): raise ValueError('Choose a channel from the lineup')
        # Recheck identity and lineup before every new tune, even if cached metadata exists.
        self.refresh()
        with self.lock:
            row = next((r for r in self.channels if r['number'] == number),None)
            if row is None: raise ValueError('Channel is not in the device lineup; refresh the lineup')
            if row['unavailable_reason']: raise ValueError(row['unavailable_reason'])
            if self.selected == number and self.process and self.process.poll() is None:
                self.last_view = time.monotonic(); return self.snapshot()
            self.stop()
            self.error = ''; self.logs.clear()
            stream, response = self.device.open_stream(number)
            token = uuid.uuid4().hex
            directory = self.root/token; directory.mkdir()
            try:
                process = subprocess.Popen(encoder_args(directory),stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
            except Exception:
                response.close(); stream.close(); shutil.rmtree(directory); raise
            self.process, self.stream, self.response = process, stream, response
            self.generation, self.selected = token, number
            self.last_view = self.started = time.monotonic()
            def feed():
                try:
                    while data := response.read1(65536):
                        process.stdin.write(data)
                except (OSError,http.client.HTTPException) as error:
                    with self.lock:
                        if self.generation == token: self.error = 'TV input disconnected: ' + str(error)
                finally:
                    try: process.stdin.close()
                    except OSError: pass
                    response.close(); stream.close()
            def logs():
                for line in iter(process.stderr.readline,b''):
                    with self.lock:
                        if self.generation == token: self.logs.append(line.decode(errors='replace').strip()[:400])
                process.stderr.close()
            threading.Thread(target=feed,daemon=True).start()
            threading.Thread(target=logs,daemon=True).start()
            return self.snapshot()

    def stop(self):
        with self.lock:
            process, stream, token = self.process, self.stream, self.generation
            self.process = self.stream = self.response = None
            self.generation = None
            if process:
                process.terminate()
                try: process.wait(timeout=3)
                except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)
            if stream: stream.close()
            if token: shutil.rmtree(self.root/token,ignore_errors=True)

    def media(self, path):
        match = re.fullmatch(r'/tv/hls/([a-f0-9]{32})/(index\.m3u8|segment[0-9]{6,12}\.ts)',path)
        if not match: raise FileNotFoundError('Unknown video segment')
        with self.lock:
            token,name = match.groups()
            if token != self.generation or not self.process or self.process.poll() is not None:
                raise FileNotFoundError('Stream ended or channel changed')
            data = (self.root/token/name).read_bytes()
            self.last_view = time.monotonic()
            return data, 'application/vnd.apple.mpegurl' if name.endswith('.m3u8') else 'video/mp2t'

    def check(self):
        with self.lock:
            if not self.process: return
            manifest = self.root/self.generation/'index.m3u8'
            if self.process.poll() is not None:
                self.error = self.error or 'TV decoder exited. Check reception and codec support; see diagnostics.'
                self.stop()
            elif time.monotonic()-self.last_view > 60:
                self.stop(); self.error = 'Stream released after 60 seconds without video requests.'
            elif (time.time()-manifest.stat().st_mtime if manifest.exists() else time.monotonic()-self.started) > 35:
                self.stop(); self.error = 'Video stopped advancing. Check reception, codec support and available CPU.'

    def monitor(self):
        try: self.refresh()
        except (OSError,ValueError,RuntimeError,http.client.HTTPException): pass
        while not self.closed.wait(1): self.check()


def make_handler(tv):
    class Handler(BaseHTTPRequestHandler):
        def json_response(self,value,status=200):
            if status >= 400: discard_rejected_body(self)
            data=json.dumps(value).encode()
            self.send_response(status); self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store')
            self.end_headers(); self.wfile.write(data)
        def do_GET(self):
            try:
                path=urlsplit(self.path).path
                if path == '/healthz': return self.json_response({'ready':not tv.closed.is_set()})
                if path == '/api/state': return self.json_response(tv.snapshot())
                data, mime = tv.media(path)
                self.send_response(200); self.send_header('Content-Type',mime)
                self.send_header('Content-Length',str(len(data))); self.send_header('Cache-Control','no-store')
                self.end_headers(); self.wfile.write(data)
            except FileNotFoundError: self.json_response({'error':'Video segment unavailable'},404)
            except (BrokenPipeError,ConnectionResetError): self.close_connection=True
        def do_POST(self):
            try:
                origin=self.headers.get('Origin')
                if self.headers.get('Sec-Fetch-Site') == 'cross-site' or (origin and urlsplit(origin).netloc != self.headers.get('Host')):
                    return self.json_response({'error':'Cross-origin command denied'},403)
                if self.headers.get_content_type() != 'application/json': return self.json_response({'error':'Use application/json'},415)
                length=int(self.headers.get('Content-Length','0'))
                if not 0 < length <= 8192: return self.json_response({'error':'Invalid request size'},413)
                self.connection.settimeout(5)
                data=self.rfile.read(length); self._body_consumed=True
                body=json.loads(data)
                if not isinstance(body,dict): raise ValueError('Expected command object')
                if self.path == '/api/tv/play': tv.play(body.get('channel'))
                elif self.path == '/api/tv/stop': tv.stop()
                elif self.path == '/api/tv/refresh': tv.refresh()
                else: return self.json_response({'error':'Unknown TV command'},404)
                self.json_response(tv.snapshot())
            except (ValueError,TypeError) as error: self.json_response({'error':str(error)},400)
            except (OSError,RuntimeError,http.client.HTTPException) as error: self.json_response({'error':str(error)},503)
        def log_message(self,*args): pass
    return Handler


def main():
    config=connection(json.loads(os.environ['ROSE_TV_BINDING']),kind='hdhomerun')
    tv=Television(config)
    server=ThreadingHTTPServer(('127.0.0.1',port('HTTP',8090)),make_handler(tv)); server.daemon_threads=True
    def stop(_signum,_frame):
        tv.closed.set()
        threading.Thread(target=server.shutdown,daemon=True).start()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    threading.Thread(target=tv.monitor,daemon=True).start()
    try: server.serve_forever()
    finally: tv.closed.set(); server.server_close(); tv.stop()


if __name__ == '__main__': main()
