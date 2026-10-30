"""Two real independent engines, without opening SDR hardware. Run after smoke-http."""
import base64
import json
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:18080'
AUTH = 'Basic ' + base64.b64encode(b'radio:ci-smoke-password').decode()


def request(path, body=None):
    headers = {'Authorization':AUTH}
    if body is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(body).encode()
    with urllib.request.urlopen(urllib.request.Request(BASE+path,data=body,headers=headers),timeout=35) as response:
        return json.load(response)


def ready(rid):
    for _ in range(90):
        try:
            if request(f'/api/receivers/{rid}/healthz')['ready']: return
        except (urllib.error.URLError, OSError): pass
        time.sleep(.5)
    raise AssertionError(f'{rid} never became ready')


request('/api/receivers/default/lifecycle',{'action':'stop'})
request('/api/sources',{'id':'default','name':'Primary','connection':{'transport':'local_soapy','driver':'rtlsdr','serial':'ci-primary'}})
request('/api/sources',{'id':'ci-second','name':'Independent receiver','connection':{'transport':'rtl_tcp_remote','host':'127.0.0.1','port':1234}})
request('/api/receivers/default/lifecycle',{'action':'start'})
request('/api/receivers/ci-second/lifecycle',{'action':'start'})
ready('default'); ready('ci-second')
prefix = '/api/receivers/ci-second'
primary = request('/api/state')
assert primary['config']['presets'][0]['name'] == 'CI preset'
assert not any(p['name'] == 'CI preset' for p in request(prefix+'/api/state')['config']['presets'])
assert primary['config']['sdr']['device_args'] == 'serial=ci-primary'
request(prefix+'/api/command',{'type':'SelectBand','value':'noaa'})
assert request('/api/state')['band'] == 'fm'
assert request(prefix+'/api/state')['band'] == 'noaa'
request(prefix+'/api/command',{'type':'SelectBand','value':'am'})
request(prefix+'/api/command',{'type':'SetAmConverter','value':False})
assert request('/api/state')['band'] == 'fm'
assert not request(prefix+'/api/state')['am_converter']
request(prefix+'/api/alerts/settings',{'counties':['006037'],'include_tests':False})
assert request('/api/alerts')['settings']['counties'] != ['006037']
try:
    request(prefix+'/api/command',{'type':'SelectSdrTransport','value':'local_soapy'})
    raise AssertionError('Managed source assignment could be changed by receiver API')
except urllib.error.HTTPError as error:
    assert error.code == 409
request(prefix+'/lifecycle',{'action':'stop'})
assert request('/api/state')['ready']
request(prefix+'/lifecycle',{'action':'start'})
ready('ci-second')
second = request(prefix+'/api/state')
assert second['band'] == 'am' and not second['am_converter']
assert request(prefix+'/api/alerts')['settings']['counties'] == ['006037']
assert request(prefix+'/api/recordings')['files'] == []
request(prefix+'/lifecycle',{'action':'stop'})
print('Two real receiver engines: source binding, band/settings/alert isolation, restart persistence passed')
