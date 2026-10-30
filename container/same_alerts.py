"""County-filtered SAME alerts. Input is consensus output from multimon-ng EAS.

The TCP input is private, pre-volume 22050 Hz mono s16le NOAA audio. Every
connection gets a fresh modem so headers from different channels cannot agree.
"""
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import socket
import subprocess
import threading
import time

HEADER = re.compile(r'ZCZC-(WXR|CIV|EAS|PEP)-([A-Z]{3})-([0-9]{6}(?:-[0-9]{6}){0,30})\+([0-9]{4})-([0-9]{7})-([A-Z0-9 /]{8})-')
TESTS = {'RWT', 'RMT', 'DMO', 'NPT'}
NAMES = {'TOR':'Tornado Warning', 'SVR':'Severe Thunderstorm Warning',
         'FFW':'Flash Flood Warning', 'FLW':'Flood Warning', 'FFA':'Flash Flood Watch',
         'FLA':'Flood Watch', 'TOA':'Tornado Watch', 'SVA':'Severe Thunderstorm Watch',
         'SMW':'Special Marine Warning', 'TSW':'Tsunami Warning', 'TSA':'Tsunami Watch',
         'HUW':'Hurricane Warning', 'HUA':'Hurricane Watch', 'TRW':'Tropical Storm Warning',
         'TRA':'Tropical Storm Watch', 'BZW':'Blizzard Warning', 'WSW':'Winter Storm Warning',
         'WSA':'Winter Storm Watch', 'EWW':'Extreme Wind Warning', 'HWW':'High Wind Warning',
         'DSW':'Dust Storm Warning', 'FRW':'Fire Warning', 'EVI':'Evacuation Immediate',
         'CEM':'Civil Emergency Message', 'SPW':'Shelter In Place Warning',
         'RWT':'Required Weekly Test', 'RMT':'Required Monthly Test',
         'DMO':'Practice / Demonstration', 'NPT':'National Periodic Test'}


def parse_header(raw, now=None):
    now = time.time() if now is None else now
    match = HEADER.fullmatch(raw)
    if not match:
        raise ValueError('Invalid SAME header')
    origin, event, locations, duration, stamp, sender = match.groups()
    hours, minutes = int(duration[:2]), int(duration[2:])
    if minutes >= 60 or not 0 < hours * 60 + minutes <= 360:
        raise ValueError('Invalid SAME duration')
    day, hour, minute = int(stamp[:3]), int(stamp[3:5]), int(stamp[5:])
    if not 1 <= day <= 366 or hour > 23 or minute > 59:
        raise ValueError('Invalid SAME timestamp')
    year = dt.datetime.fromtimestamp(now, dt.timezone.utc).year
    dates = []
    for y in (year - 1, year, year + 1):
        date = dt.datetime(y, 1, 1, hour, minute, tzinfo=dt.timezone.utc) + dt.timedelta(days=day - 1)
        if date.year == y:
            dates.append(date.timestamp())
    issued = min(dates, key=lambda value: abs(value - now))
    expires = issued + (hours * 60 + minutes) * 60
    if issued > now + 300 or expires <= now:
        raise ValueError('Expired or future SAME message')
    return dict(id=hashlib.sha256(raw.encode()).hexdigest()[:24], raw=raw,
                event=event, title=NAMES.get(event, f'SAME alert {event}'),
                origin=origin, locations=locations.split('-'), sender=sender.strip(),
                issued=issued, expires=expires, test=event in TESTS, received=now,
                acknowledged=False)


def matches(location, county):
    # Whole-county selection includes every subdivision. State/national codes
    # cover all selected counties in their scope.
    return location == '000000' or (location[1:3] == county[1:3] and
           (location[3:] == '000' or location[3:] == county[3:]) and
           (location[0] == '0' or county[0] == '0' or location[0] == county[0]))


class Alerts:
    def __init__(self, directory=Path('/data')):
        self.path = Path(directory) / 'same-alerts.json'
        self.lock = threading.RLock()
        self.settings = dict(counties=[], include_tests=False)
        self.history = []
        self.last_audio = 0
        self.decoder_error = ''
        self.listener = None
        self.stopped = threading.Event()
        try:
            stored = json.loads(self.path.read_text())
            self.settings = self.validate_settings(stored['settings'])
            self.history = [a for a in stored['history'] if isinstance(a, dict) and
                            isinstance(a.get('expires'), (float, int)) and a['expires'] > time.time()][:50]
        except (OSError, ValueError, KeyError, TypeError):
            pass

    @staticmethod
    def validate_settings(value):
        if not isinstance(value, dict) or set(value) != {'counties', 'include_tests'}:
            raise ValueError('Expected counties and include_tests')
        codes = value['counties']
        if not isinstance(codes, list) or len(codes) > 20 or any(
                not isinstance(c, str) or not re.fullmatch(r'[0-9]{6}', c) for c in codes):
            raise ValueError('Select up to 20 six-digit SAME county codes')
        if not isinstance(value['include_tests'], bool):
            raise ValueError('include_tests must be a boolean')
        return dict(counties=list(dict.fromkeys(codes)), include_tests=value['include_tests'])

    def save(self):
        temp = self.path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(settings=self.settings, history=self.history)))
        temp.replace(self.path)

    def configure(self, value):
        with self.lock:
            self.settings = self.validate_settings(value)
            self.save()
            return self.snapshot()

    def acknowledge(self, value):
        if not isinstance(value, dict) or set(value) != {'id'} or not isinstance(value['id'], str):
            raise ValueError('Expected alert id')
        with self.lock:
            for alert in self.history:
                if alert['id'] == value['id']:
                    alert['acknowledged'] = True
            self.save()
            return self.snapshot()

    def ingest(self, raw, now=None):
        # NNNN ends the voice message, NOT the warning's validity period.
        if raw == 'NNNN':
            return False
        try:
            alert = parse_header(raw, now)
        except ValueError:
            return False
        with self.lock:
            if any(a['id'] == alert['id'] for a in self.history):
                return False
            if not any(matches(loc, county) for loc in alert['locations'] for county in self.settings['counties']):
                return False
            if alert['test'] and not self.settings['include_tests']:
                return False
            self.history.insert(0, alert)
            self.history = self.history[:50]
            self.save()
        return True

    def snapshot(self):
        with self.lock:
            return dict(settings=dict(self.settings), history=list(self.history),
                        active=[a for a in self.history if a['expires'] > time.time() and not a['acknowledged']],
                        monitoring=time.monotonic() - self.last_audio < 5,
                        error=self.decoder_error)

    def start(self):
        self.listener = socket.socket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(('127.0.0.1', 8093))
        self.listener.listen(4)
        self.listener.settimeout(1)
        threading.Thread(target=self.run, daemon=True).start()

    def stop(self):
        self.stopped.set()
        if self.listener:
            self.listener.close()

    def run(self):
        while not self.stopped.is_set():
            try:
                client, _ = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            process = None
            reader = None
            try:
                process = subprocess.Popen(['multimon-ng', '-q', '-v', '0', '-a', 'EAS', '-t', 'raw', '-'],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE)
                self.decoder_error = ''
                reader = threading.Thread(target=self.read_messages, args=(process,), daemon=True)
                reader.start()
                client.settimeout(3)
                pending = b''
                with client:
                    while not self.stopped.is_set():
                        data = client.recv(8192)
                        if not data:
                            break
                        # TCP can split a 16-bit sample at any byte boundary.
                        pending += data
                        length = len(pending) & ~1
                        process.stdin.write(pending[:length])
                        process.stdin.flush()
                        pending = pending[length:]
                        self.last_audio = time.monotonic()
            except (OSError, ValueError) as error:
                self.decoder_error = f'SAME decoder unavailable: {error}'
                client.close()
            finally:
                self.last_audio = 0
                if process:
                    process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait()
                    if reader:
                        reader.join(timeout=2)
                    try:
                        process.stdin.close()
                    except OSError:
                        pass
                    process.stdout.close()

    def read_messages(self, process):
        # Verbosity zero emits consensus only, never the '(part)' headers.
        for line in iter(process.stdout.readline, b''):
            text = line.decode('ascii', errors='replace').strip()
            if text.startswith('EAS: '):
                try:
                    self.ingest(text[5:])
                except OSError as error:
                    self.decoder_error = f'Cannot save SAME alert: {error}'


service = Alerts()
