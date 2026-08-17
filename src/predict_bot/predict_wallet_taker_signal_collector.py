from __future__ import annotations

"""Retired 8777 compatibility endpoint.

The old public Taker signal collector is no longer part of the active research
stack.  Dashboard V2's current service definition still expects a listener on
8777 next to 8776, so keep a zero-work process temporarily instead of running
the former 250 ms microstructure / Chainlink / SQLite collector.

This module performs no market polling, no websocket connections, no database
writes, no strategy inference and no live trading.  It can be removed entirely
once service-manager.ts drops the historical 8777 member.
"""

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

HOST = os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_WALLET_TAKER_SIGNAL_PORT", "8777"))
VERSION = "PREDICT_WALLET_TAKER_SIGNAL_COLLECTOR_RETIRED_STUB_V1"
STARTED_AT_MS = int(time.time() * 1000)


def state() -> dict[str, Any]:
    return {
        "ok": True,
        "status": "RETIRED_COMPATIBILITY_STUB",
        "version": VERSION,
        "processAlive": True,
        "startedAtMs": STARTED_AT_MS,
        "retired": True,
        "dataCollectionEnabled": False,
        "databaseWritesEnabled": False,
        "websocketEnabled": False,
        "strategyLogic": False,
        "liveOrdersAffected": False,
        "replacement": "TARGET_WALLET_OFFICIAL_V1 on 8776",
        "reason": "temporary listener retained only for Dashboard V2 service-manager compatibility",
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        return

    @staticmethod
    def _disconnected(exc: BaseException) -> bool:
        if isinstance(exc, (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)):
            return True
        return isinstance(exc, OSError) and getattr(exc, "winerror", None) in {10053, 10054}

    def _write(self, payload: Any, status: int = 200) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception as exc:
            if not self._disconnected(exc):
                raise

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in {"/", "/state", "/health"}:
            self._write(state())
            return
        self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; "
        "retired=true; dataCollectionEnabled=false; replacement=8776",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
