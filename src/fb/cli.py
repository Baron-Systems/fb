from __future__ import annotations

import argparse
import socket
from contextlib import closing

import uvicorn

from .app import create_app
from .db import connect, kv_get, kv_set
from .paths import fb_db_path


def _pick_port(preferred: int) -> int:
    """
    Zero-config: pick a stable port if possible; otherwise find a free one.
    """
    for port in range(preferred, preferred + 50):
        with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("0.0.0.0", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free port found in range")


def main() -> None:
    """CLI entrypoint for pipx installation."""
    import sys
    parser = argparse.ArgumentParser(prog="fb")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="Run the Backup Dashboard (zero-config)")
    args = parser.parse_args()

    if args.cmd == "run":
        db_path = fb_db_path()
        cx = connect(db_path)
        port = kv_get(cx, "dashboard.port")
        if not isinstance(port, int):
            port = _pick_port(7311)
            kv_set(cx, "dashboard.port", port)
        cx.close()

        app = create_app(db_path=db_path, bind_host="0.0.0.0", bind_port=port)
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
    else:
        parser.print_help()
        sys.exit(1)


