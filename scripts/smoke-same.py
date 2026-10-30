"""Run inside the smoke container: PCM -> real modem -> authenticated web API."""
import datetime as dt
import importlib.util
import json
from pathlib import Path
import socket
import time
import urllib.request

spec = importlib.util.spec_from_file_location('waveform', Path(__file__).with_name('test-same-decoder.py'))
wave = importlib.util.module_from_spec(spec)
spec.loader.exec_module(wave)


def request(path='', body=None):
    req = urllib.request.Request('http://127.0.0.1:8090/api/alerts' + path,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


request('/settings', {'counties':['053077'], 'include_tests':True})
now = dt.datetime.now(dt.timezone.utc)
header = f'ZCZC-WXR-RWT-053077+0030-{now:%j%H%M}-KXXX/NWS-'
with socket.create_connection(('127.0.0.1', 8093), timeout=10) as client:
    client.sendall(wave.waveform([header]*3 + ['NNNN']*3))
    for _ in range(50):
        state = request()
        if state['active']:
            break
        time.sleep(.1)
    assert len(state['active']) == 1, state
    alert = state['active'][0]
    assert alert['raw'] == header and alert['test'], alert
    request('/ack', {'id':alert['id']})
    client.sendall(wave.waveform([header]*3))
    time.sleep(.5)
    assert not request()['active'], 'Repeated header re-alerted after dismissal'
print('SAME PCM -> modem -> API -> dismissal smoke test passed')
