"""One MP3 encoder shared by bounded live listeners; no audio files accumulate."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time


class Broadcast:
    def __init__(self, limit=16, backlog=32):
        self.limit = limit
        self.backlog = backlog
        self.listeners = set()
        self.lock = threading.Lock()
        self.last_data = 0.0
        self.failed = False

    def subscribe(self):
        with self.lock:
            if len(self.listeners) >= self.limit or self.failed:
                return None
            listener = queue.Queue(self.backlog)
            self.listeners.add(listener)
            return listener

    def unsubscribe(self, listener):
        with self.lock:
            self.listeners.discard(listener)

    def publish(self, data):
        with self.lock:
            self.last_data = time.monotonic()
            for listener in tuple(self.listeners):
                try:
                    listener.put_nowait(data)
                except queue.Full:
                    # Drop the slow client instead of blocking reception or growing RAM.
                    self.listeners.remove(listener)
                    while True:
                        try:
                            listener.get_nowait()
                        except queue.Empty:
                            break
                    listener.put_nowait(None)

    def healthy(self):
        return not self.failed and time.monotonic() - self.last_data < 10


BROADCAST = Broadcast()


def app_alive():
    try:
        os.kill(int(Path("/run/rose/app.pid").read_text()), 0)
        return time.time() - Path("/run/rose/heartbeat").stat().st_mtime < 15
    except (OSError, ValueError):
        return False


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route == "/healthz":
            healthy = BROADCAST.healthy() and app_alive()
            body = b'{"ready":true}\n' if healthy else b'{"ready":false}\n'
            self.send_response(200 if healthy else 503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route != "/audio.mp3":
            self.send_error(404)
            return
        listener = BROADCAST.subscribe()
        if listener is None:
            self.send_error(503, "Audio unavailable or listener limit reached")
            return
        try:
            self.connection.settimeout(10)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                data = listener.get(timeout=10)
                if data is None:
                    break
                self.wfile.write(data)
                self.wfile.flush()
        except (OSError, queue.Empty):
            pass
        finally:
            BROADCAST.unsubscribe(listener)
            self.close_connection = True

    def log_message(self, _format, *args):
        pass


def main():
    process = subprocess.Popen([
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin",
        "-f", "s16le", "-ar", "44100", "-ac", "2", "-i", "tcp://127.0.0.1:8092",
        "-c:a", "libmp3lame", "-b:a", "160k", "-write_xing", "0",
        "-id3v2_version", "0", "-flush_packets", "1", "-f", "mp3", "pipe:1",
    ], stdout=subprocess.PIPE)
    from gateway import make_handler
    from same_alerts import service
    service.start()
    server = ThreadingHTTPServer(("127.0.0.1", 8090), make_handler(Handler, service))
    server.daemon_threads = True

    def encode():
        while data := process.stdout.read1(4096):
            BROADCAST.publish(data)
        BROADCAST.failed = True
        server.shutdown()

    def stop(_signum, _frame):
        process.terminate()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    threading.Thread(target=encode, daemon=True).start()
    try:
        server.serve_forever()
    finally:
        service.stop()
        server.server_close()
        process.terminate()
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    if BROADCAST.failed:
        raise SystemExit("Audio encoder exited")


if __name__ == "__main__":
    main()
