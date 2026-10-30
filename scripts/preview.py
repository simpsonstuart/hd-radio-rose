"""Explicit fixture-only browser preview; never copied into runtime entrypoints.

Run after npm run build: python scripts/preview.py. Uses no SDR and binds loopback.
"""
import json
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Use upstream's documented fields for a disconnected receiver preview.
CONFIG = {
    "frequency_mhz": 101.1, "selected_program": 0, "volume": 1, "muted": False,
    "show_hd5_hd8": True, "preset_slot_count": 6, "presets": [], "gain_mode": "auto",
    "manual_gain_tenths": 197, "agc_amp_target_dbfs_override": None,
    "analog_fallback_mode": "digital_only", "analog_fallback_stereo": True,
    "analog_fallback_rds_enabled": True, "collage_max_tiles": 64,
    "play_log_retention_hours": 24, "spectrum_smoothing_enabled": False,
    "spectrum_smoothing_alpha": .3, "recording_max_minutes": 60,
    "recording_subfolder_per_station": True,
    "sdr": {"transport": "local_soapy", "driver": "rtlsdr", "device_args": "", "gains": {},
            "freq_correction_ppm": 0, "remote_host": "", "remote_port": 55132, "remote_extra_args": ""},
}
STATE = {"preview": True, "ready": True, "version": "0.6.5", "streaming": False,
         "synced": False, "retuning": False, "status": "UI preview — no receiver is attached",
         "frequency": 101.1, "selected": 0, "speaker": None,
         "programs": [{"index": i, "title": "", "artist": "", "album": "", "genre": "", "cover": None,
                       "logo": None, "decoded": False, "fresh": False} for i in range(8)],
         "station": {"programs": [None]*8, "data_services": [], "sync_psmi": None},
         "stale": False, "mer_lower": 0, "mer_upper": 0, "ber": 0, "gain": 0,
         "config": CONFIG, "devices": [], "gains": [], "antennas": [], "refreshing": False,
         "sdr_present": False, "active_gain_mode": None, "agc": None, "recording": None,
         "art": [], "blocked_art": 0, "weather": [], "traffic": None, "payload": []}


class Preview(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "web/dist"), **kwargs)

    def respond(self, body, code=200):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/sources': return self.respond({'sources':[{'id':'default','name':'Fixture receiver','connection':{'transport':'legacy'}}], 'receivers':[{'id':'default','source_id':'default','status':'running'}]})
        if path == '/api/alerts': return self.respond({'active':[], 'history':[], 'settings':{'counties':[], 'include_tests':False}, 'monitoring':False})
        if path == '/api/alerts/counties': return self.respond([])
        if path == '/api/state': return self.respond(STATE)
        if path == '/api/spectrum': return self.respond({"bins": [], "generation": 0})
        if path == '/api/constellation': return self.respond({"available": False, "source": "post-equalization", "lower": [], "upper": []})
        if path == '/api/log': return self.respond({"entries": []})
        if path == '/api/recordings': return self.respond({"files": []})
        return super().do_GET()

    def do_POST(self):
        self.respond({"error": "This is a UI preview. Radio commands require the real receiver."}, 409)


if __name__ == '__main__':
    print('Fixture-only preview: http://127.0.0.1:8090', flush=True)
    ThreadingHTTPServer(('127.0.0.1', 8090), Preview).serve_forever()
