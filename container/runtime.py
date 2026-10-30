"""Per-receiver process configuration. Defaults retain standalone compatibility."""
import os
from pathlib import Path

DATA = Path(os.environ.get('ROSE_DATA_DIR', '/data'))
RUN = Path(os.environ.get('ROSE_RUN_DIR', '/run/rose'))


def port(name, fallback):
    return int(os.environ.get('ROSE_' + name + '_PORT', fallback))


def discard_rejected_body(handler):
    """Avoid a TCP reset hiding an early HTTP rejection on small POSTs.

    Never trust an attacker-controlled size for an unbounded read or wait.
    nginx rejects large bodies before proxying; this also serves direct tests.
    """
    if getattr(handler, 'command', '') != 'POST' or getattr(handler, '_body_consumed', False):
        return
    handler._body_consumed = True
    try:
        length = int(handler.headers.get('Content-Length', '0'))
        if 0 < length <= 65536:
            previous = handler.connection.gettimeout()
            try:
                handler.connection.settimeout(.25)
                handler.rfile.read(length)
            finally:
                handler.connection.settimeout(previous)
    except (OSError, ValueError):
        pass
