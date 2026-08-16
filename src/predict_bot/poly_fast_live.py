from __future__ import annotations

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

# The multi-asset live modules resolve their asset identity at import time.
# Seed a valid value, then each embedded engine captures its own asset/symbol.
os.environ.setdefault("PREDICT_POLY_GAP_LIVE_ASSET", "ETH")
os.environ.setdefault("PREDICT_POLY_GAP_LIVE_SYMBOL", "ETHUSDT")
os.environ["PREDICT_POLY_GAP_LIVE_ENABLED"] = os.environ.get(
    "PREDICT_POLY_FAST_LIVE_ENABLED",
    os.environ.get("PREDICT_POLY_GAP_LIVE_ENABLED", "true"),
)

from . import multi_prediction_observer as observer_base
from . import poly_gap_live as live_base
from . import poly_gap_multi_asset_live_v1 as multi_v1
from .multi_prediction_observer_v2 import LiveGradeMultiPredictionObserver
from .poly_gap_multi_asset_live_v3 import PinnedMultiAssetPolyGapLiveEngine

ROOT = Path(__file__).resolve().parents[2]
HOST = os.environ.get("PREDICT_POLY_FAST_LIVE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_POLY_FAST_LIVE_PORT", "8782"))
DB_PATH = Path(
    os.environ.get(
        "PREDICT_POLY_FAST_OBSERVER_DB",
        ROOT / "data" / "poly_fast_observer.db",
    )
)
FAST_BINANCE_POLL_SECONDS = max(
    0.05,
    float(os.environ.get("PREDICT_POLY_FAST_BINANCE_POLL_SECONDS", "0.20")),
)
FAST_SAMPLE_SECONDS = max(
    0.05,
    float(os.environ.get("PREDICT_POLY_FAST_SAMPLE_SECONDS", "0.10")),
)
ASSETS = ("ETH", "BNB")


class FastEmbeddedObserver(LiveGradeMultiPredictionObserver):
    """One-process Poly/Binance observer with no BTC dependency and no hot-path DB writes."""

    def start(self) -> None:
        threading.Thread(target=self._market_loop, name="poly-fast-markets", daemon=True).start()
        threading.Thread(target=self._fast_binance_loop, name="poly-fast-binance", daemon=True).start()
        threading.Thread(target=self._memory_sample_loop, name="poly-fast-samples", daemon=True).start()

    def _refresh_btc_local(self) -> None:
        # Fast Live is intentionally self-contained; never touch 8766/8767.
        return

    def _load_trajectory_locked(self, asset: str, bucket: int) -> None:
        # Do not hydrate old samples from SQLite into a live decision window.
        return

    def _fast_binance_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                if time.monotonic() >= self.binance_backoff_until:
                    self._poll_binance_books()
            except Exception as exc:
                self.last_error = f"fast Binance books: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.01, FAST_BINANCE_POLL_SECONDS - elapsed))

    def _memory_sample_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                # _sample_once appends trajectory points in memory. Its returned
                # SQLite rows are deliberately discarded.
                self._sample_once()
            except Exception as exc:
                self.last_error = f"fast sample loop: {str(exc)[:400]}"
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.01, FAST_SAMPLE_SECONDS - elapsed))

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_EMBEDDED_OBSERVER_V1"
        payload["fastPath"] = {
            "enabled": True,
            "btcLocalDependencies": False,
            "httpHopToStrategy": False,
            "trajectoryPersistence": False,
            "binancePollMs": int(FAST_BINANCE_POLL_SECONDS * 1000),
            "sampleMs": int(FAST_SAMPLE_SECONDS * 1000),
        }
        return payload


class EmbeddedPinnedEngine(PinnedMultiAssetPolyGapLiveEngine):
    """V3 live executor whose observer read is an in-process memory snapshot."""

    def __init__(
        self,
        observer: FastEmbeddedObserver,
        *,
        asset: str,
        db_path: Path,
    ) -> None:
        asset = str(asset).upper()
        if asset not in ASSETS:
            raise ValueError(f"unsupported fast-live asset: {asset}")
        self._embedded_observer = observer
        # MultiAssetPolyGapLiveEngine captures these globals into self.asset /
        # self.symbol during __init__. They are not used as shared runtime state.
        multi_v1.ASSET = asset
        multi_v1.SYMBOL = multi_v1.SUPPORTED_ASSETS[asset]
        super().__init__(db_path=db_path)

    def _asset_observer_state(self) -> dict[str, Any] | None:
        observer = self._embedded_observer
        try:
            with observer.lock:
                row = observer._asset_snapshot(self.asset)
        except Exception as exc:
            self.last_error = f"{self.asset} embedded observer state: {str(exc)[:300]}"
            return None
        # Preserve the exact V2 marker required by the existing live freshness policy.
        self._asset_observer_version = "MULTI_PREDICTION_OBSERVER_V2"
        self._last_asset_observer_state = row
        return row

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["polyFastLive"] = {
            "enabled": True,
            "port": PORT,
            "observerTransport": "IN_PROCESS_MEMORY",
            "localhostHttpSignalHop": False,
            "externalDependencies": ["Binance Prediction", "Polymarket"],
            "researchServicesRequired": False,
        }
        return payload


class PolyFastLiveRuntime:
    def __init__(self) -> None:
        self.observer = FastEmbeddedObserver(db_path=DB_PATH)
        self.engines: dict[str, EmbeddedPinnedEngine] = {}
        for asset in ASSETS:
            db_path = ROOT / "data" / f"poly_fast_live_{asset.lower()}.db"
            self.engines[asset] = EmbeddedPinnedEngine(
                self.observer,
                asset=asset,
                db_path=db_path,
            )

    def start(self) -> None:
        self.observer.start()
        for engine in self.engines.values():
            engine.start()

    def stop(self) -> None:
        for engine in self.engines.values():
            try:
                engine.stop()
            except Exception:
                pass
        self.observer.stop()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": "POLY_FAST_LIVE_V1",
            "realMoney": True,
            "host": HOST,
            "port": PORT,
            "architecture": {
                "processes": 1,
                "observerTransport": "IN_PROCESS_MEMORY",
                "btcCoreRequired": False,
                "multiAssetSupervisorRequired": False,
                "predictFunRequired": False,
                "makerResearchRequired": False,
            },
            "observer": self.observer.snapshot(),
            "assets": {asset: engine.snapshot() for asset, engine in self.engines.items()},
        }

    def update_settings(self, asset: str, values: dict[str, Any]) -> dict[str, Any]:
        key = str(asset).upper()
        engine = self.engines.get(key)
        if engine is None:
            raise ValueError("asset must be ETH or BNB")
        return engine.update_settings(values)


class _Handler(BaseHTTPRequestHandler):
    runtime: PolyFastLiveRuntime

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, allow_nan=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/state", "/health", "/api/state"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        payload = self.runtime.snapshot()
        query = parse_qs(parsed.query)
        asset = str((query.get("asset") or [""])[0]).upper()
        if asset:
            row = payload["assets"].get(asset)
            if row is None:
                self._send(400, {"ok": False, "error": "asset must be ETH or BNB"})
                return
            self._send(200, {"ok": True, "state": row, "fastPath": payload["architecture"]})
            return
        self._send(200, {"ok": True, "state": payload})

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        if parsed.path not in {"/settings", "/api/settings"}:
            self._send(404, {"ok": False, "error": "not found"})
            return
        try:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 64_000:
                raise ValueError("invalid request body length")
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("settings body must be a JSON object")
            query = parse_qs(parsed.query)
            asset = str(body.pop("asset", (query.get("asset") or [""])[0])).upper()
            state = self.runtime.update_settings(asset, body)
            self._send(200, {"ok": True, "state": state})
        except ValueError as exc:
            self._send(400, {"ok": False, "error": str(exc)})
        except Exception as exc:
            self._send(500, {"ok": False, "error": str(exc)[:500]})

    def log_message(self, _format: str, *_args: Any) -> None:
        return


def main() -> int:
    runtime = PolyFastLiveRuntime()
    runtime.start()
    handler = type("PolyFastLiveHandler", (_Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Live V1 listening on http://{HOST}:{PORT}/state; "
        f"assets={','.join(ASSETS)}; in-process observer; "
        f"Binance poll={FAST_BINANCE_POLL_SECONDS:.3f}s",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.10)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
