from __future__ import annotations

import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .cross_oracle_strategies import CrossOraclePaperEngine


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_CROSS_ORACLE_DB", ROOT / "data" / "cross_oracle.db"))
HOST = os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_PORT", "8768"))
CROSS_ORACLE_STATE_URL = os.environ.get(
    "PREDICT_CROSS_ORACLE_STATE_URL",
    "http://127.0.0.1:8767/state",
)


def _poly_snapshot() -> dict[str, Any]:
    request = urllib.request.Request(
        CROSS_ORACLE_STATE_URL,
        headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Poly-Strategy/1.0"},
    )
    with urllib.request.urlopen(request, timeout=1.5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    polymarket = payload.get("polymarket") if isinstance(payload, dict) else None
    if not isinstance(polymarket, dict):
        raise RuntimeError("cross-oracle /state has no Polymarket snapshot")
    return polymarket


class _Handler(BaseHTTPRequestHandler):
    engine: CrossOraclePaperEngine

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/state", "/health", "/api/state"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = self.engine.snapshot()
        payload["generatedAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    engine = CrossOraclePaperEngine(DB_PATH, _poly_snapshot)
    engine.start()
    handler = type("CrossOracleStrategyHandler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Cross-oracle Paper strategies listening on http://{HOST}:{PORT}/state; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
