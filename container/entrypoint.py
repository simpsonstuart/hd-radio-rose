"""Start dependent services, supervise them, and shut down the whole session."""
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

SERVICES = []
STOP = False


def stop(_signum=None, _frame=None):
    global STOP
    STOP = True


def start(name, args):
    print(f"Starting {name}", flush=True)
    process = subprocess.Popen(args, start_new_session=True)
    SERVICES.append((name, process))
    return process


def ready(args, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and not STOP:
        if any(p.poll() is not None for _, p in SERVICES):
            raise RuntimeError("A required service exited during startup")
        try:
            if subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2).returncode == 0:
                return
        except subprocess.TimeoutExpired:
            pass
        time.sleep(0.25)
    raise RuntimeError(f"Timed out waiting for {args[0]}")


def authentication():
    username = os.environ.get("WEB_USER", "radio")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,64}", username):
        raise ValueError("WEB_USER must contain 1-64 letters, digits, dots, underscores or hyphens")
    password = os.environ.pop("WEB_PASSWORD", "")
    if password:
        if any(c in password for c in "\r\n\0"):
            raise ValueError("WEB_PASSWORD cannot contain line breaks or NUL")
        hashed = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n",
                                text=True, capture_output=True, check=True).stdout.strip()
        Path("/run/rose/htpasswd").write_text(f"{username}:{hashed}\n")
        # Directory is private except for nginx's worker group.
        os.chmod("/run/rose/htpasswd", 0o640)
        import grp
        os.chown("/run/rose/htpasswd", 0, grp.getgrnam("www-data").gr_gid)
        Path("/run/rose/auth.conf").write_text('auth_basic "DX Studio";\nauth_basic_user_file /run/rose/htpasswd;\n')
    elif os.environ.get("ALLOW_ANONYMOUS", "false").lower() == "true":
        print("Anonymous access enabled: use only on a trusted LAN or VPN", flush=True)
        Path("/run/rose/auth.conf").write_text("auth_basic off;\n")
    else:
        raise ValueError("Set WEB_PASSWORD, or explicitly set ALLOW_ANONYMOUS=true for a trusted LAN")


def main():
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, stop)
    Path("/data").mkdir(exist_ok=True)
    Path("/run/rose").mkdir(exist_ok=True)
    # nginx workers need traversal for the password file, but cannot list runtime state.
    os.chmod("/run/rose", 0o711)
    Path("/run/rose/runtime").mkdir(exist_ok=True)
    os.chmod("/run/rose/runtime", 0o700)
    authentication()
    start("receiver manager", [sys.executable, "/opt/rose/receivers.py"])
    ready([sys.executable, "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8089/healthz',timeout=2)"])
    start("web interface", ["nginx", "-g", "daemon off;"])
    while not STOP:
        for name, process in SERVICES:
            if process.poll() is not None:
                raise RuntimeError(f"{name} exited with code {process.returncode}")
        time.sleep(0.5)


def cleanup():
    # The receiver manager finalizes each engine and joins its child processes.
    # Terminate all groups first, then give them a shared grace period.
    for _, process in reversed(SERVICES):
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    deadline = time.monotonic() + 40
    for _, process in reversed(SERVICES):
        try:
            process.wait(timeout=max(0.01, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


if __name__ == "__main__":
    code = 0
    try:
        main()
    except Exception as error:
        print(f"Startup/session failed: {error}", file=sys.stderr, flush=True)
        code = 1
    finally:
        cleanup()
    sys.exit(code)
