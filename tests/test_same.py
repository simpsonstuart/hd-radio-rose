import datetime as dt
import importlib.util
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import io

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('same_alerts', ROOT / 'container/same_alerts.py')
same = importlib.util.module_from_spec(spec)
spec.loader.exec_module(same)


def header(event='TOR', location='053077', when=None, duration='0030'):
    when = when or dt.datetime.now(dt.timezone.utc)
    return f'ZCZC-WXR-{event}-{location}+{duration}-{when:%j%H%M}-KXXX/NWS-'


class SameTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(dir=ROOT)
        self.alerts = same.Alerts(self.directory.name)
        self.alerts.configure(dict(counties=['053077'], include_tests=False))

    def tearDown(self):
        self.directory.cleanup()

    def test_county_and_subdivision_scope(self):
        for code in ['053077', '153077', '053000', '000000']:
            self.assertTrue(same.matches(code, '053077'))
        for code in ['053001', '041077', '041000']:
            self.assertFalse(same.matches(code, '053077'))
        self.assertFalse(same.matches('153077', '253077'))
        self.assertTrue(same.matches('053077', '253077'))

    def test_filter_and_consensus_dedup_persist(self):
        self.assertFalse(self.alerts.ingest(header(location='053001')))
        self.assertTrue(self.alerts.ingest(header()))
        self.assertFalse(self.alerts.ingest(header()))
        restored = same.Alerts(self.directory.name)
        self.assertFalse(restored.ingest(header()))
        self.assertEqual(len(restored.snapshot()['active']), 1)

    def test_eom_does_not_clear_warning_and_ack_does(self):
        self.alerts.ingest(header())
        self.alerts.ingest('NNNN')
        alert = self.alerts.snapshot()['active'][0]
        self.alerts.acknowledge({'id': alert['id']})
        self.assertEqual(self.alerts.snapshot()['active'], [])
        self.assertFalse(self.alerts.ingest(header()))

    def test_tests_opt_in(self):
        self.assertFalse(self.alerts.ingest(header(event='RWT')))
        self.alerts.configure(dict(counties=['053077'], include_tests=True))
        self.assertTrue(self.alerts.ingest(header(event='RWT')))
        self.assertTrue(self.alerts.snapshot()['active'][0]['test'])

    def test_expiry_future_and_invalid_headers(self):
        now = dt.datetime.now(dt.timezone.utc)
        for raw in [header(when=now-dt.timedelta(hours=1)), header(when=now+dt.timedelta(hours=1)),
                    header(duration='0060'), header(duration='0000'), header(location='53077'),
                    header()+'junk', 'ZCZC-WXR-TOR-053077+0030-0002561-KXXX/NWS-']:
            self.assertFalse(self.alerts.ingest(raw), raw)
        self.alerts.ingest(header())
        self.alerts.history[0]['expires'] = time.time() - 1
        self.assertEqual(self.alerts.snapshot()['active'], [])

    def test_utc_year_rollover(self):
        now = dt.datetime(2027, 1, 1, 0, 5, tzinfo=dt.timezone.utc)
        raw = header(when=now-dt.timedelta(minutes=10))
        parsed = same.parse_header(raw, now.timestamp())
        self.assertEqual(parsed['issued'], (now-dt.timedelta(minutes=10)).timestamp())

    def test_empty_selection_and_validation(self):
        self.alerts.configure(dict(counties=[], include_tests=False))
        self.assertFalse(self.alerts.ingest(header()))
        for codes in [[53077], ['53077'], ['053077\n'], '053077', ['053077']*21]:
            with self.assertRaises(ValueError):
                self.alerts.configure(dict(counties=codes, include_tests=False))

    def test_county_catalog(self):
        import json
        rows = json.loads((ROOT/'container/same-counties.json').read_text())
        self.assertGreater(len(rows), 3000)
        self.assertTrue(any(c['code']=='053077' and c['name']=='Yakima' for c in rows))

    def test_pcm_socket_preserves_split_samples_and_closes_decoder(self):
        from unittest.mock import MagicMock
        client = MagicMock()
        client.recv.side_effect = [b'\x01', b'\x02\x03', b'\x04', b'']
        self.alerts.listener = Mock()
        self.alerts.listener.accept.side_effect = [(client, None), OSError('closed')]
        process = Mock()
        process.stdout = io.BytesIO(b'')
        chunks = []
        process.stdin.write.side_effect = lambda data: chunks.append(data)
        with patch.object(same.subprocess, 'Popen', return_value=process):
            self.alerts.run()
        self.assertEqual(b''.join(chunks), b'\x01\x02\x03\x04')
        self.assertTrue(all(len(c) % 2 == 0 for c in chunks))
        process.terminate.assert_called_once()
        self.assertFalse(self.alerts.snapshot()['monitoring'])
