"""Exercise the actual HTTP gateway; only the hardware RPC boundary is faked."""
import importlib.util
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import tempfile
import unittest
from unittest.mock import patch, Mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('http_gateway', ROOT / 'container/gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)
spec = importlib.util.spec_from_file_location('same_http', ROOT / 'container/same_alerts.py')
same = importlib.util.module_from_spec(spec)
spec.loader.exec_module(same)


class Base(BaseHTTPRequestHandler):
    def do_GET(self): self.send_error(404)
    def log_message(self, *args): pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = tempfile.TemporaryDirectory(dir=ROOT)
        cls.alerts = same.Alerts(cls.directory.name)
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), gateway.make_handler(Base, cls.alerts))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.directory.cleanup()

    def request(self, method, path, body=None, headers=None):
        client = HTTPConnection(*self.server.server_address, timeout=3)
        client.request(method, path, body=body, headers=headers or {})
        response = client.getresponse()
        status, data = response.status, response.read()
        client.close()
        return status, data

    def test_state_uses_receiver_rpc(self):
        with patch.object(gateway, 'rpc', return_value={'ready': True}) as call:
            status, body = self.request('GET', '/api/state')
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body), {'ready': True})
            call.assert_called_once_with({'op': 'state'})

    def test_alert_settings_api_and_origin_protection(self):
        payload = json.dumps({'counties':['053077'], 'include_tests':False})
        headers = {'Content-Type':'application/json'}
        status, body = self.request('POST', '/api/alerts/settings', payload, headers)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['settings']['counties'], ['053077'])
        status, body = self.request('GET', '/api/alerts')
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(body)['monitoring'])
        status, _ = self.request('POST', '/api/alerts/settings', payload, {**headers, 'Origin':'https://other.invalid'})
        self.assertEqual(status, 403)
        status, _ = self.request('POST', '/api/alerts/settings', '{}', headers)
        self.assertEqual(status, 400)
        status, body = self.request('GET', '/api/alerts/counties')
        self.assertEqual(status, 200)
        self.assertGreater(len(json.loads(body)), 3000)

    def test_command_is_validated_before_receiver_call(self):
        with patch.object(gateway, 'rpc', return_value={'ready': True}) as call:
            status, _ = self.request('POST', '/api/command', json.dumps({'type':'SelectProgram','value':8}), {'Content-Type':'application/json'})
            self.assertEqual(status, 400)
            call.assert_not_called()
            status, _ = self.request('POST', '/api/command', json.dumps({'type':'SelectProgram','value':7}), {'Content-Type':'application/json'})
            self.assertEqual(status, 200)
            call.assert_called_once_with({'op':'command','command':{'type':'SelectProgram','value':7}})

    def test_cross_origin_and_non_json_rejected(self):
        with patch.object(gateway, 'rpc') as call:
            status, _ = self.request('POST', '/api/command', '{}', {'Content-Type':'application/json','Origin':'https://other.invalid'})
            self.assertEqual(status, 403)
            status, _ = self.request('POST', '/api/command', '{}', {'Content-Type':'text/plain'})
            self.assertEqual(status, 415)
            call.assert_not_called()

    def test_oversized_body_rejected(self):
        status, _ = self.request('POST', '/api/command', ' ' * 8193, {'Content-Type':'application/json'})
        self.assertEqual(status, 413)

    def test_weather_channels_and_bands_are_validated(self):
        for khz in range(162400,162551,25):
            self.assertEqual(gateway.validate_command({'type':'TuneWeather','value':khz/1000})['value'],khz/1000)
        for value in [99.1,162.41,162.399,162.575,True]:
            with self.assertRaises(ValueError): gateway.validate_command({'type':'TuneWeather','value':value})
        for band in ['fm','noaa']:
            self.assertEqual(gateway.validate_command({'type':'SelectBand','value':band})['value'],band)
        with self.assertRaises(ValueError): gateway.validate_command({'type':'SelectBand','value':'am'})

    def test_tuning_rejects_off_channel_frequencies(self):
        with patch.object(gateway, 'rpc', return_value={'ready': True}) as call:
            for value in (99, 99.2, 99.15, 87.7, 108.1):
                status, _ = self.request('POST', '/api/command', json.dumps({'type':'TuneMhz','value':value}), {'Content-Type':'application/json'})
                self.assertEqual(status, 400)
            call.assert_not_called()
            for value in (87.9, 99.1, 107.9):
                status, _ = self.request('POST', '/api/command', json.dumps({'type':'TuneMhz','value':value}), {'Content-Type':'application/json'})
                self.assertEqual(status, 200)
            with self.assertRaises(ValueError):
                gateway.validate_command({'type':'SetPreset','value':[0,{'name':'Invalid','frequency_mhz':99.2,'program':0}]})

    def test_disconnected_json_client_does_not_get_second_response(self):
        handler = object.__new__(gateway.make_handler(Base))
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        for failure_point in (handler.end_headers, handler.wfile.write):
            for error in (BrokenPipeError, ConnectionResetError):
                handler.send_response.reset_mock()
                failure_point.side_effect = error('client disconnected')
                handler.json_response({'ready': True})
                handler.send_response.assert_called_once_with(200)
                self.assertTrue(handler.close_connection)
            failure_point.side_effect = None

    def test_busy_receiver_wait_is_bounded_without_opening_socket(self):
        with gateway.RPC_LOCK, patch.object(gateway.socket, 'create_connection') as connect:
            with self.assertRaisesRegex(RuntimeError, 'Receiver busy'):
                gateway.rpc({'op': 'state'}, timeout=.01)
            connect.assert_not_called()

    def test_receiver_connection_failure_releases_lock(self):
        with patch.object(gateway.socket, 'create_connection', side_effect=TimeoutError):
            with self.assertRaises(TimeoutError):
                gateway.rpc({'op': 'state'}, timeout=.01)
        self.assertTrue(gateway.RPC_LOCK.acquire(blocking=False))
        gateway.RPC_LOCK.release()


if __name__ == '__main__': unittest.main()
