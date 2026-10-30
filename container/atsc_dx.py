"""HDHomeRun ATSC diagnostics, exclusive RF scans and durable observations."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import re
import secrets
import socket
import sqlite3
import subprocess
import threading
import time
import uuid
from pathlib import Path


def utc():
    return datetime.now(timezone.utc).isoformat()


def frequency(rf):
    if type(rf) is not int or not 2 <= rf <= 36:
        raise ValueError('RF channel must be 2 through 36 (US/Canada broadcast)')
    return ((57 + 6*(rf-2)) if rf <= 4 else (79 + 6*(rf-5)) if rf <= 6
            else (177 + 6*(rf-7)) if rf <= 13 else (473 + 6*(rf-14))) * 1000000


def measurement(status, streaminfo='', channelmap=''):
    fields = dict(re.findall(r'(\w+)=([^\s]+)', status))
    def metric(key, maximum):
        value = fields.get(key, '')
        return int(value) if value.isdigit() and 0 <= int(value) <= maximum else None
    channel = fields.get('ch', '')
    suffix = channel.rsplit(':', 1)[-1]
    hz = int(suffix) if suffix.isdigit() and int(suffix) >= 1000000 else None
    if hz is None and channelmap == 'us-bcast' and suffix.isdigit() and 2 <= int(suffix) <= 36:
        hz = frequency(int(suffix))
    rf = next((n for n in range(2, 37) if frequency(n) == hz), None) if channelmap == 'us-bcast' else None
    lock = fields.get('lock', 'unknown')
    return {'observed_at':utc(), 'channel':channel, 'frequency_hz':hz, 'rf_channel':rf,
            'channelmap':channelmap, 'lock':lock, 'locked':lock not in ('none','unknown','') and not lock.startswith('('),
            'strength':metric('ss',100), 'quality':metric('snq',100), 'symbol_quality':metric('seq',100),
            'bits_per_second':metric('bps',10000000000), 'packets_per_second':metric('pps',100000000),
            'streaminfo':streaminfo[:16384], 'raw_status':status[:4096]}


class ControlRejected(RuntimeError):
    pass


class Control:
    """Official CLI, fixed IPv4 host and tuner; no shell or broadcast discovery."""
    def __init__(self, config):
        self.config = config

    def command(self, operation, field, value=None, key=None):
        address = socket.gethostbyname(self.config['host'])
        args = ['hdhomerun_config', address]
        if key is not None: args += ['key', str(key)]
        args += [operation, f"/tuner{self.config['tuner']}/{field}"]
        if value is not None: args.append(str(value))
        try:
            result = subprocess.run(args, capture_output=True, text=True, timeout=4, check=False)
        except FileNotFoundError as error:
            raise RuntimeError('Signal control requires hdhomerun-config in the server image') from error
        except subprocess.TimeoutExpired as error:
            raise RuntimeError('HDHomeRun control timed out') from error
        output = result.stdout.strip()
        if result.returncode or output.upper().startswith('ERROR'):
            raise ControlRejected((result.stderr.strip() or output or 'HDHomeRun control failed')[:500])
        return output[:16384]

    def get(self, field): return self.command('get', field)
    def set(self, field, value, key=None): return self.command('set', field, value, key)


class ReceptionLog:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS receptions (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        try:
            with db: yield db
        finally: db.close()

    def add(self, value):
        with self.connect() as db:
            db.execute('INSERT INTO receptions(payload) VALUES (?)', (json.dumps(value),))
            # Bounded per-receiver history; newest 10,000 observations survive restarts.
            db.execute('DELETE FROM receptions WHERE id <= (SELECT MAX(id)-10000 FROM receptions)')

    def rows(self, before=None, limit=100):
        with self.connect() as db:
            rows = db.execute('SELECT id,payload FROM receptions WHERE id < ? ORDER BY id DESC LIMIT ?',
                              (before if before is not None else 9223372036854775807, limit)).fetchall()
        return [{'id':i, **json.loads(payload)} for i,payload in rows]


class DX:
    def __init__(self, device, config, metadata, path, control=None):
        self.device, self.config, self.metadata = device, config, dict(metadata)
        self.control = control or Control(config)
        self.log = ReceptionLog(path)
        self.lock = threading.RLock()
        self.io = threading.Lock()
        self.cancel = threading.Event()
        self.thread = None
        self.sample, self.error = None, ''
        self.last_logged = None
        self.scan = {'running':False, 'status':'idle', 'completed':0, 'total':0, 'results':[], 'error':''}
        self.journal = Path(path).with_suffix('.lock.json')
        self.key = None
        if self.journal.exists():
            saved = json.loads(self.journal.read_text())
            if saved['device_id'] != config['device_id'] or saved['tuner'] != config['tuner']:
                raise RuntimeError('Previous scan lock belongs to another device/tuner; restore the original source to release it')
            self.key = saved['key']
            self.scan['error'] = 'Previous scan ended before release. Retry tuner release before playback or scanning.'

    def save_key(self, key):
        # Persist ownership before requesting a lock so a crash can be recovered.
        temporary = self.journal.with_suffix('.tmp')
        temporary.write_text(json.dumps({'device_id':self.config['device_id'], 'tuner':self.config['tuner'], 'key':key}))
        temporary.replace(self.journal)
        self.key = key

    def clear_key(self):
        self.journal.unlink(missing_ok=True)
        self.key = None

    @property
    def busy(self):
        with self.lock: return self.scan['running'] or self.key is not None

    def snapshot(self):
        with self.lock:
            return {'measurement':self.sample, 'error':self.error, 'scan':{**self.scan, 'results':list(self.scan['results'])},
                    'last_logged':self.last_logged, 'cleanup_pending':self.key is not None and not self.scan['running'], 'context':self.metadata}

    def read(self):
        channelmap = self.control.get('channelmap')
        status = self.control.get('status')
        info = self.control.get('streaminfo')
        # Another client can retune between requests; do not pair different RFs.
        after = self.control.get('status')
        value = measurement(status, info, channelmap)
        if measurement(after)['channel'] != value['channel']:
            raise RuntimeError('Tuner changed during measurement; retry')
        with self.lock: self.sample, self.error = value, ''
        return value

    def poll(self):
        if self.busy or not self.io.acquire(blocking=False): return
        try:
            if self.busy: return
            self.device.discover()
            self.read()
        except Exception as error:
            with self.lock: self.sample, self.error = None, str(error)
        finally: self.io.release()

    def record(self, value, reason, notes='', scan_id=None, requested_rf=None):
        self.log.add({'source':self.metadata, 'device_id':self.config['device_id'], 'tuner':self.config['tuner'],
                      'reason':reason, 'notes':notes, 'scan_id':scan_id, 'requested_rf':requested_rf,
                      'measurement':value})
        with self.lock: self.last_logged = utc()

    def manual(self, notes):
        if not isinstance(notes,str) or len(notes)>1000: raise ValueError('Notes must be at most 1000 characters')
        with self.io:
            if self.busy: raise ValueError('Wait for the scan to finish before logging manually')
            self.device.discover()
            value = self.read()
            if not value['locked']: raise ValueError('No confirmed lock to log; scans retain unsuccessful attempts separately')
            self.record(value, 'manual', notes)

    def start(self, first, last, dwell):
        frequency(first); frequency(last)
        if first > last: raise ValueError('First RF channel must not exceed last')
        if type(dwell) is not int or not 2 <= dwell <= 15: raise ValueError('Dwell must be 2 through 15 seconds')
        with self.lock:
            if self.busy: raise ValueError('Scan already running or tuner release needs retry')
            self.cancel.clear()
            self.scan = {'id':uuid.uuid4().hex, 'running':True, 'status':'starting', 'completed':0,
                         'total':last-first+1, 'current_rf':None, 'results':[], 'error':'', 'started_at':utc()}
            self.thread = threading.Thread(target=self.run, args=(first,last,dwell), daemon=True)
            self.thread.start()

    def release(self):
        # Authenticated writes only: never force-unlock a tuner belonging to another app.
        if self.key is None: return
        self.control.set('channel','none',self.key)
        self.control.set('lockkey','none',self.key)
        self.clear_key()

    def retry_release(self):
        with self.io:
            with self.lock:
                if self.scan['running']: raise ValueError('Cancel the scan first')
                self.device.discover()
                if self.control.get('lockkey') == 'none': self.clear_key()
                else: self.release()
                self.scan['error'] = ''

    def run(self, first, last, dwell):
        with self.io:
            try:
                self.device.discover()
                if self.control.get('channelmap') != 'us-bcast':
                    raise ValueError('RF scanning requires the device channel map us-bcast; configure antenna reception first')
                if self.control.get('target') not in ('none',''):
                    raise ValueError('Tuner is streaming to another application')
                key = secrets.randbelow(0xfffffffe)+1
                self.save_key(key)
                try: self.control.set('lockkey',str(key))
                except ControlRejected:
                    self.clear_key()
                    raise
                if self.control.get('target') not in ('none',''):
                    # Release only our lock, without stopping the pre-existing stream.
                    self.control.set('lockkey','none',key); self.clear_key()
                    raise ValueError('Tuner became busy; scan cancelled')
                for rf in range(first,last+1):
                    if self.cancel.is_set(): break
                    with self.lock:
                        self.scan.update(status='scanning',current_rf=rf)
                        self.sample = None
                    self.control.set('channel',f'auto:{frequency(rf)}',key)
                    if self.cancel.wait(dwell): break
                    value = self.read()
                    if value['frequency_hz'] != frequency(rf): raise RuntimeError('Tuner did not remain on requested RF channel')
                    self.record(value, 'scan', scan_id=self.scan['id'], requested_rf=rf)
                    with self.lock:
                        self.scan['results'].append(value)
                        self.scan['completed'] += 1
                with self.lock: self.scan['status'] = 'cancelled' if self.cancel.is_set() else 'complete'
            except Exception as error:
                with self.lock: self.scan.update(status='failed',error=str(error))
            finally:
                try: self.release()
                except Exception as error:
                    with self.lock: self.scan['error'] += ' Tuner release failed; retry release: '+str(error)
                with self.lock:
                    self.scan.update(running=False,finished_at=utc())
                    self.sample = None

    def close(self):
        self.cancel.set()
        if self.thread: self.thread.join(timeout=25)
