"""Exercise the actual HTTP gateway; only the hardware RPC boundary is faked."""
import importlib.util
import json
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('http_gateway', ROOT / 'container/gateway.py')
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)


class Base(BaseHTTPRequestHandler):
    def do_GET(self): self.send_error(404)
    def log_message(self, *args): pass


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), gateway.make_handler(Base))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

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


if __name__ == '__main__': unittest.main()
