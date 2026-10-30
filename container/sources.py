"""Versioned source registry, independent of decoder implementations.

A source is an exclusive hardware/endpoint resource. A receiver is its assigned
worker session. More provider types can supply their own capabilities and worker
adapter without adding television concepts to the NRSC5 engine.
"""
import copy
import json
from pathlib import Path
import re
import threading

CAPABILITIES = {
    'sdr': ['fm', 'am', 'noaa', 'hd-radio', 'spectrum', 'digital-recording', 'same'],
    'hdhomerun': ['tv', 'channel-lineup', 'live-video', 'device-info'],
}
MAX_RECEIVERS = 8


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r'[a-z][a-z0-9-]{0,31}', value):
        raise ValueError('IDs must start with a letter and contain up to 32 lowercase letters, numbers or hyphens')
    return value


def text(value, limit=160):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError('Invalid text')
    return value.strip()


def connection(value, legacy=False, kind='sdr'):
    if not isinstance(value, dict):
        raise ValueError('Source connection must be an object')
    transport = value.get('transport')
    if kind == 'hdhomerun':
        host = text(value.get('host', ''), 253).lower().rstrip('.')
        device = text(value.get('device_id', ''), 8).upper()
        tuner = value.get('tuner', 0)
        if transport != 'http' or not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', host):
            raise ValueError('HDHomeRun needs an HTTP hostname or IPv4 address, without a URL or port')
        if not re.fullmatch(r'[0-9A-F]{8}', device):
            raise ValueError('Enter the eight-digit HDHomeRun Device ID from its label or web interface')
        if type(tuner) is not int or not 0 <= tuner <= 15:
            raise ValueError('Tuner index must be 0–15; the device must actually have that tuner')
        return {'transport':'http', 'host':host, 'device_id':device, 'tuner':tuner}
    if kind != 'sdr':
        raise ValueError('Unsupported source provider')
    if transport == 'legacy' and legacy:
        return {'transport': 'legacy'}
    if transport not in {'local_soapy', 'soapy_remote', 'rtl_tcp_remote'}:
        raise ValueError('Unsupported source transport')
    result = {'transport': transport}
    if transport != 'rtl_tcp_remote':
        driver = value.get('driver')
        if driver not in {'rtlsdr', 'hackrf'}:
            raise ValueError('Select RTL-SDR or HackRF')
        serial = text(value.get('serial', ''), 128)
        if not re.fullmatch(r'[A-Za-z0-9_-]+', serial):
            raise ValueError('A stable device serial is required; indexes are not source identities')
        result.update(driver=driver, serial=serial)
    if transport != 'local_soapy':
        host = text(value.get('host', ''), 253).lower().rstrip('.')
        if not re.fullmatch(r'[a-z0-9][a-z0-9.-]*', host):
            raise ValueError('Enter a hostname or IPv4 address without a URL or port')
        port = value.get('port', 1234 if transport == 'rtl_tcp_remote' else 55132)
        if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('Invalid remote port')
        result.update(host=host, port=port)
    return result


def binding(source):
    if source['kind'] != 'sdr':
        return None
    c = source['connection']
    if c['transport'] == 'legacy':
        return None
    result = dict(transport=c['transport'], driver=c.get('driver', 'rtlsdr'),
                  device_args='', remote_host=None, remote_port=None, remote_extra_args=None)
    if c['transport'] == 'local_soapy':
        result['device_args'] = 'serial=' + c['serial']
    else:
        result.update(remote_host=c['host'], remote_port=c['port'])
        if c['transport'] == 'soapy_remote':
            result['remote_extra_args'] = f"remote:driver={c['driver']},remote:serial={c['serial']}"
    return result


class Registry:
    def __init__(self, root=Path('/data')):
        self.root = Path(root)
        self.path = self.root / 'sources.json'
        self.lock = threading.RLock()
        if self.path.exists():
            self.value = json.loads(self.path.read_text(encoding='utf-8'))
            self.validate(self.value)
        else:
            self.value = {'version': 1, 'sources': [
                {'id':'default', 'name':'Default receiver', 'kind':'sdr', 'location':'', 'antenna':'', 'purpose':'', 'connection':{'transport':'legacy'}}
            ], 'receivers':[{'id':'default', 'source_id':'default', 'slot':0, 'enabled':True}]}
            self.save(self.value)

    def validate(self, value):
        if value.get('version') != 1:
            raise ValueError('Unsupported source registry version')
        sources, receivers = value['sources'], value['receivers']
        if not 1 <= len(sources) <= MAX_RECEIVERS or not 1 <= len(receivers) <= MAX_RECEIVERS:
            raise ValueError('Registry supports 1–8 receiver sources')
        ids = set()
        identities = set()
        legacy = False
        for s in sources:
            sid = identifier(s['id'])
            if sid in ids or s['kind'] not in CAPABILITIES or not text(s['name']):
                raise ValueError('Duplicate ID, unsupported provider or empty source name')
            ids.add(sid)
            if sid == 'default' and s['kind'] != 'sdr':
                raise ValueError('Default remains an SDR for legacy API compatibility; add a separate TV source')
            c = connection(s['connection'], legacy=sid == 'default', kind=s['kind'])
            if c != s['connection']:
                raise ValueError('Noncanonical connection')
            identity = ('hdhomerun', c['device_id'], c['tuner']) if s['kind'] == 'hdhomerun' else json.dumps(c, sort_keys=True)
            if identity in identities:
                raise ValueError('This device or endpoint already belongs to another source')
            identities.add(identity)
            legacy |= c['transport'] == 'legacy'
        if legacy and sum(s['kind'] == 'sdr' for s in sources) > 1:
            raise ValueError('Bind the Default source to its serial or remote endpoint before adding another source')
        rids, slots, assigned = set(), set(), set()
        for r in receivers:
            identifier(r['id'])
            if r['id'] in rids or r['slot'] in slots or r['source_id'] in assigned or r['source_id'] not in ids:
                raise ValueError('Duplicate receiver, slot or source assignment')
            if type(r['slot']) is not int or not 0 <= r['slot'] < MAX_RECEIVERS or type(r['enabled']) is not bool:
                raise ValueError('Invalid receiver slot or enabled flag')
            rids.add(r['id']); slots.add(r['slot']); assigned.add(r['source_id'])
        if not any(r['id'] == 'default' and r['source_id'] == 'default' and r['slot'] == 0 for r in receivers):
            raise ValueError('Default receiver is required for legacy API compatibility')

    def save(self, value):
        self.root.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix('.json.tmp')
        temporary.write_text(json.dumps(value, indent=2), encoding='utf-8')
        temporary.replace(self.path)

    def snapshot(self):
        with self.lock:
            result = copy.deepcopy(self.value)
        for source in result['sources']:
            source['capabilities'] = CAPABILITIES[source['kind']]
        return result

    def put_source(self, body):
        if not isinstance(body, dict) or set(body) - {'id','name','kind','location','antenna','purpose','connection'}:
            raise ValueError('Invalid source object')
        sid = identifier(body.get('id'))
        source = {'id':sid, 'kind':body.get('kind', 'sdr'), 'name':text(body.get('name', '')),
                  **{key:text(body.get(key, '')) for key in ('location','antenna','purpose')},
                  'connection':connection(body.get('connection'), kind=body.get('kind', 'sdr'))}
        with self.lock:
            value = copy.deepcopy(self.value)
            value['sources'] = [s for s in value['sources'] if s['id'] != sid] + [source]
            # Initially one receiver per SDR; the separate assignment record is
            # the extension point for future multi-tuner provider adapters.
            if not any(r['source_id'] == sid for r in value['receivers']):
                used = {r['slot'] for r in value['receivers']}
                slot = next((i for i in range(MAX_RECEIVERS) if i not in used), MAX_RECEIVERS)
                value['receivers'].append({'id':sid, 'source_id':sid, 'slot':slot, 'enabled':False})
            self.validate(value)
            self.save(value)
            self.value = value
        return source

    def enable(self, rid, enabled):
        with self.lock:
            value = copy.deepcopy(self.value)
            receiver = next((r for r in value['receivers'] if r['id'] == rid), None)
            if receiver is None:
                raise ValueError('Unknown receiver')
            receiver['enabled'] = enabled
            self.validate(value)
            self.save(value)
            self.value = value
