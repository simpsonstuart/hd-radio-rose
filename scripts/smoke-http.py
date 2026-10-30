"""Real HTTP/API smoke test. No SDR hardware is required for these checks."""
import base64
import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:18080"
AUTH = "Basic " + base64.b64encode(b"radio:ci-smoke-password").decode()


def request(path, data=None, headers=None):
    h = {"Authorization": AUTH, **(headers or {})}
    if data is not None:
        h['Content-Type'] = 'application/json'
        data = json.dumps(data).encode()
    with urllib.request.urlopen(urllib.request.Request(BASE + path, data=data, headers=h), timeout=35) as response:
        return response.read()


for path in ("/", "/audio.mp3", "/api/state", "/api/recordings", "/api/alerts", "/api/alerts/counties", "/sources.tar.gz"):
    try:
        urllib.request.urlopen(BASE + path, timeout=3)
        raise AssertionError(f"{path} allowed unauthenticated access")
    except urllib.error.HTTPError as error:
        assert error.code == 401, (path, error.code)
assert b'ROSE Radio' in request('/')
state = json.loads(request('/api/state'))
assert state['ready'] and len(state['programs']) == 8
assert not state['streaming']
for endpoint in ('spectrum', 'log', 'recordings'):
    assert isinstance(json.loads(request('/api/' + endpoint)), dict)
for command in [
    {'type': 'SelectProgram', 'value': 2},
    {'type': 'SetPreset', 'value': [0, {'name': 'CI preset', 'frequency_mhz': 101.1, 'program': 2}]},
    {'type': 'SetShowHd5Hd8', 'value': True},
]:
    result = json.loads(request('/api/command', command))
    assert result['ready'] and 'error' not in result
assert result['config']['presets'][0]['name'] == 'CI preset'
assert result['selected'] == 2
try:
    request('/api/command', {'type': 'TuneMhz', 'value': 101.1})
    raise AssertionError('Idle receiver accepted tuning')
except urllib.error.HTTPError as error:
    assert error.code == 409
for command in [{'type': 'SavePreset', 'value': 1000000}, {'type': 'SelectProgram', 'value': 99}]:
    try:
        request('/api/command', command)
        raise AssertionError('Invalid command accepted')
    except urllib.error.HTTPError as error:
        assert error.code == 400
try:
    request('/api/command', {'type': 'Stop'}, {'Origin': 'https://attacker.invalid'})
    raise AssertionError('Cross-origin command accepted')
except urllib.error.HTTPError as error:
    assert error.code == 403
try:
    request('/api/media?path=/etc/passwd')
    raise AssertionError('Path escape accepted')
except urllib.error.HTTPError as error:
    assert error.code == 404
assert b'title' in request('/api/log.csv').lower()
weather = json.loads(request('/api/command', {'type':'SelectBand','value':'noaa'}))
assert weather['band'] == 'noaa' and weather['frequency'] == 162.4
assert not weather['streaming'] and not weather['synced']
try:
    request('/api/command', {'type':'TuneWeather','value':162.55})
    raise AssertionError('Idle NOAA receiver accepted tuning')
except urllib.error.HTTPError as error:
    assert error.code == 409
assert json.loads(request('/api/command', {'type':'SelectBand','value':'fm'}))['band'] == 'fm'
print('Native API, auth, settings, presets, CSV and input validation passed')
