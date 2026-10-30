"""Validated browser API and confined downloads for the headless receiver."""
import json
import math
import mimetypes
from pathlib import Path
import socket
import threading
import time
from urllib.parse import parse_qs, urlsplit

DATA = Path("/data")
RPC_LOCK = threading.Lock()


def rpc(request, timeout=35):
    # Polling must not accumulate behind a stalled USB/receiver operation.
    deadline = time.monotonic() + timeout
    if not RPC_LOCK.acquire(timeout=min(timeout, 1)):
        raise RuntimeError("Receiver busy with another request; retry shortly")
    try:
        with socket.create_connection(("127.0.0.1", 8091), timeout=max(.001, deadline - time.monotonic())) as client:
            client.settimeout(max(.001, deadline - time.monotonic()))
            client.sendall(json.dumps(request, allow_nan=False).encode() + b"\n")
            with client.makefile("rb") as stream:
                response = stream.readline(8 * 1024 * 1024)
            if not response.endswith(b"\n"):
                raise RuntimeError("Invalid receiver response")
            return json.loads(response)
    finally:
        RPC_LOCK.release()


def number(value, low, high, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("Expected a finite number")
    if not low <= value <= high or (integer and int(value) != value):
        raise ValueError(f"Value must be between {low} and {high}")
    return int(value) if integer else value


def string(value, limit=512):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 for c in value):
        raise ValueError("Invalid text value")
    return value


def fm_channel(value):
    value = number(value, 87.9, 107.9)
    tenths = round(value * 10)
    if abs(value * 10 - tenths) > .0001 or tenths % 2 != 1:
        raise ValueError("Use FM channels from 87.9 to 107.9 MHz in 0.2 MHz steps")
    return tenths / 10


UNITS = {"Start", "Stop", "RefreshSdrDevices", "ResetSdrConfig", "ClearGainCache",
         "StartRecording", "StopRecording", "ClearCollage", "ClearArtBlocklist", "ClearLog"}
BOOLEANS = {"SetShowHd5Hd8", "SetMute", "SetAnalogFallbackStereo", "SetAnalogFallbackRdsEnabled",
            "SetSpectrumSmoothingEnabled", "SetRecordingSubfolderPerStation", "SetCollageSecondaryClickFallback"}
NUMBERS = {"TuneMhz": (87.9, 107.9, False), "SelectProgram": (0, 7, True),
           "SavePreset": (0, 47, True), "RecallPreset": (0, 47, True), "ClearPreset": (0, 47, True),
           "SetVolume": (0, 1, False), "SetPresetSlotCount": (1, 48, True),
           "SetCollageTileCap": (1, 512, True), "SetPlayLogRetention": (1, 168, True),
           "SetManualGainTenths": (-1000, 1000, True), "SetSdrFreqCorrectionPpm": (-1000, 1000, False),
           "SetSdrRemotePort": (1, 65535, True), "SetSpectrumSmoothingAlpha": (0.1, 1, False),
           "SetRecordingMaxMinutes": (1, 240, True)}
ENUMS = {"SetGainMode": {"auto", "manual", "hardware_agc"},
         "SelectBand": {"fm", "noaa"},
         "SetAnalogFallbackMode": {"digital_only", "automatic", "analog_only"},
         "SelectSdrTransport": {"local_soapy", "soapy_remote", "rtl_tcp_remote"},
         "SetRecordingMode": {"off", "on"}}


def validate_command(command):
    if not isinstance(command, dict) or set(command) - {"type", "value"}:
        raise ValueError("Expected a command object")
    kind, value = command.get("type"), command.get("value")
    if not isinstance(kind, str):
        raise ValueError("Missing command type")
    if kind in UNITS:
        if value is not None:
            raise ValueError("This command has no value")
        return {"type": kind}
    if kind == "TuneWeather":
        value = number(value, 162.4, 162.55)
        khz = round(value * 1000)
        if abs(value * 1000 - khz) > .0001 or khz not in range(162400, 162551, 25):
            raise ValueError("Choose one of the seven NOAA weather channels")
        value = khz / 1000
    elif kind in BOOLEANS:
        if not isinstance(value, bool):
            raise ValueError("Expected a boolean")
    elif kind in NUMBERS:
        value = number(value, *NUMBERS[kind])
        if kind == "TuneMhz":
            value = fm_channel(value)
    elif kind in ENUMS:
        if not isinstance(value, str) or value not in ENUMS[kind]:
            raise ValueError("Unknown setting")
    elif kind in {"SetSdrRemoteHost", "SetSdrRemoteExtraArgs", "SetSdrAntenna"}:
        value = string(value)
    elif kind == "SetAgcAmpTargetDbfs":
        value = None if value is None else number(value, -30, -10)
    elif kind == "SetRecordingDir":
        base = (DATA / 'recordings').resolve()
        path = (base / string(value)).resolve()
        if not path.is_relative_to(base):
            raise ValueError("Recording folder must stay inside /data/recordings")
        value = str(path)
    elif kind == "BlockCover":
        if not isinstance(value, str) or not value.isdecimal() or len(value) > 20:
            raise ValueError("Expected an artwork hash string")
        value = number(int(value), 0, 2**64 - 1, True)
    elif kind == "SelectSdrDevice":
        if not isinstance(value, dict) or set(value) != {"driver", "device_args"}:
            raise ValueError("Expected driver and device_args")
        value = {"driver": string(value["driver"], 64), "device_args": string(value["device_args"])}
    elif kind == "SetSdrGainElement":
        if not isinstance(value, dict) or set(value) != {"element", "value_db"}:
            raise ValueError("Expected gain element and value_db")
        value = {"element": string(value["element"], 64), "value_db": number(value["value_db"], -200, 200)}
    elif kind == "SetPreset":
        if not isinstance(value, list) or len(value) != 2 or not isinstance(value[1], dict):
            raise ValueError("Expected preset slot and settings")
        slot, preset = value
        if set(preset) != {"name", "frequency_mhz", "program"}:
            raise ValueError("Invalid preset fields")
        value = [number(slot, 0, 47, True), {"name": string(preset["name"], 128),
                 "frequency_mhz": fm_channel(preset["frequency_mhz"]),
                 "program": number(preset["program"], 0, 7, True)}]
    else:
        raise ValueError("Unknown or unsupported command")
    return {"type": kind, "value": value}


def confined_file(root, value, extensions):
    root = root.resolve()
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root) or not path.is_file() or path.suffix.lower() not in extensions:
        raise ValueError("File is outside the permitted media directory")
    return path


def make_handler(base, alerts=None):
    class Handler(base):
        def json_response(self, data, status=200):
            body = json.dumps(data, allow_nan=False).encode()
            try:
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                # Client abandoned the request. Never send a second response.
                self.close_connection = True

        def file_response(self, path, download=False):
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "no-cache")
            if download:
                from urllib.parse import quote
                self.send_header("Content-Disposition", f"attachment; filename*=UTF-8''{quote(path.name)}")
            self.end_headers()
            with path.open("rb") as source:
                while chunk := source.read(65536):
                    self.wfile.write(chunk)

        def do_GET(self):
            parsed = urlsplit(self.path)
            route = parsed.path
            if not route.startswith("/api/"):
                return super().do_GET()
            try:
                if route == "/api/alerts" and alerts is not None:
                    return self.json_response(alerts.snapshot())
                if route == "/api/alerts/counties" and alerts is not None:
                    return self.json_response(json.loads(Path(__file__).with_name('same-counties.json').read_text()))
                if route in {"/api/state", "/api/spectrum", "/api/log"}:
                    result = rpc({"op": route.rsplit("/", 1)[1]})
                    return self.json_response(result, 503 if "error" in result else 200)
                if route == "/api/log.csv":
                    result = rpc({"op": "export"})
                    if "error" in result:
                        return self.json_response(result, 500)
                    return self.file_response(DATA / "play-log.csv", True)
                if route == "/api/recordings":
                    root = (DATA / "recordings").resolve()
                    files = []
                    if root.exists():
                        for path in root.rglob("*.opus"):
                            if path.resolve().is_relative_to(root) and path.is_file():
                                stat = path.stat()
                                files.append({"name": path.relative_to(root).as_posix(), "bytes": stat.st_size, "modified": stat.st_mtime})
                    return self.json_response({"files": sorted(files, key=lambda f: f["modified"], reverse=True)[:1000]})
                query = parse_qs(parsed.query)
                if route == "/api/media":
                    return self.file_response(confined_file(DATA, query.get("path", [""])[0], {".png", ".jpg", ".jpeg", ".gif"}))
                if route == "/api/recording":
                    return self.file_response(confined_file(DATA / "recordings", query.get("name", [""])[0], {".opus"}), True)
                self.send_error(404)
            except (BrokenPipeError, ConnectionResetError):
                self.close_connection = True
            except (ValueError, FileNotFoundError):
                self.json_response({"error": "Media not found or path not allowed"}, 404)
            except (OSError, RuntimeError) as error:
                self.json_response({"error": str(error)}, 503)

        def do_POST(self):
            try:
                if self.path not in {"/api/command", "/api/alerts/settings", "/api/alerts/ack"}:
                    return self.send_error(404)
                origin = self.headers.get("Origin")
                if self.headers.get("Sec-Fetch-Site") == "cross-site" or (origin and urlsplit(origin).netloc != self.headers.get("Host")):
                    return self.json_response({"error": "Cross-origin command denied"}, 403)
                if self.headers.get_content_type() != "application/json":
                    return self.json_response({"error": "Use application/json"}, 415)
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 8192:
                    return self.json_response({"error": "Invalid request size"}, 413)
                self.connection.settimeout(5)
                body = json.loads(self.rfile.read(length))
                if self.path.startswith('/api/alerts/'):
                    if alerts is None:
                        return self.json_response({'error':'Alert service unavailable'}, 503)
                    result = alerts.configure(body) if self.path.endswith('/settings') else alerts.acknowledge(body)
                    return self.json_response(result)
                command = validate_command(body)
                result = rpc({"op": "command", "command": command})
                self.json_response(result, 409 if "error" in result else 200)
            except (ValueError, TypeError, KeyError) as error:
                self.json_response({"error": str(error)}, 400)
            except (OSError, RuntimeError) as error:
                self.json_response({"error": str(error)}, 503)
    return Handler
