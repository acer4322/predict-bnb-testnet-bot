from __future__ import annotations

import json
import math
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import target_taker_public_side_test_v1 as base
from .public_source_snapshot_archive_v2 import PublicSourceSnapshotArchiveV2

VERSION = "UNIFIED_CONTROLLER_PUBLIC_SOURCE_V1_PUBLIC_ONLY"
HOST = base.HOST
PORT = base.PORT
CANONICAL_MICRO_URL = "http://127.0.0.1:8766/api/microstructure-lite"
CANONICAL_MICRO_POLL_SECONDS = 0.25
CANONICAL_MICRO_STALE_MS = 1500
PUBLIC_SNAPSHOT_ARCHIVE_DB = base.ROOT / "data" / "public_source_snapshot_archive_v2.db"


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


class UnifiedControllerPublicSourceV1(base.TargetTakerPublicSideTest):
    """Public-only feature source for 8784 research.

    No Target wallet events, no Target settlement state, no Echtgeld handoff and no
    paper strategy execution. Market identity comes from Predict.fun public state.
    """

    def __init__(self) -> None:
        super().__init__()
        self.public_snapshot_archive = PublicSourceSnapshotArchiveV2(PUBLIC_SNAPSHOT_ARCHIVE_DB)
        self.public_snapshot_archive_last_error: str | None = None

    def _refresh_official(self) -> None:
        # Deliberately disabled: 8784 does not need 8776 Target-aware state.
        return

    def _official_market(self) -> tuple[int | None, str | None]:
        with self.lock:
            predict = dict(self.predict_btc)
        market = base._record(predict.get("market"))
        market_id = base._positive_int(market.get("id") or market.get("marketId"))
        title = str(market.get("title") or "") or None
        return market_id, title

    def _advance(self) -> None:
        market_id, title = self._official_market()
        if market_id is None:
            return
        if market_id != self.current_market_id:
            self.current_market_id = int(market_id)
            self.current_title = title
            self.current_trade = None
            self.last_decision = None
            self.last_decision_key = None
        snapshot = self._public_snapshot()
        with self.lock:
            self.last_public_snapshot = snapshot
        try:
            self.public_snapshot_archive.record(snapshot)
            self.public_snapshot_archive_last_error = None
        except Exception as exc:
            # Research archive failure must never degrade the public source used by strategies.
            self.public_snapshot_archive_last_error = f"{type(exc).__name__}: {str(exc)[:500]}"

    def _canonical_micro_loop(self) -> None:
        while not self.stop_event.is_set():
            started = time.monotonic()
            try:
                with urllib.request.urlopen(CANONICAL_MICRO_URL, timeout=0.8) as response:
                    payload = json.load(response)
                snap = payload.get("latestSnapshot") if isinstance(payload, dict) else None
                if not isinstance(snap, dict) or not snap:
                    raise RuntimeError("canonical microstructure snapshot unavailable")
                with self.micro.state_lock:
                    self.micro.latest_snapshot = dict(snap)
                with self.lock:
                    self.last_canonical_micro_fetch_ms = int(time.time() * 1000)
                    self.last_canonical_micro_age_ms = payload.get("snapshotAgeMs")
                    self.last_canonical_micro_error = None
            except Exception as exc:
                with self.lock:
                    self.last_canonical_micro_error = str(exc)[:500]
            elapsed = time.monotonic() - started
            self.stop_event.wait(max(0.02, CANONICAL_MICRO_POLL_SECONDS - elapsed))

    def start(self) -> None:
        # 8783 no longer opens a duplicate Binance microstructure websocket stack.
        # It consumes the canonical 8766 compact feature snapshot instead.
        self.last_canonical_micro_fetch_ms = None
        self.last_canonical_micro_age_ms = None
        self.last_canonical_micro_error = None
        threading.Thread(
            target=self._canonical_micro_loop,
            name="unified-public-canonical-micro",
            daemon=True,
        ).start()
        threading.Thread(
            target=self._chainlink_loop,
            name="target-public-side-chainlink",
            daemon=True,
        ).start()
        self.thread.start()

    def stop(self) -> None:
        try:
            super().stop()
        finally:
            try:
                self.public_snapshot_archive.close()
            except Exception:
                pass

    def health_snapshot(self) -> dict[str, Any]:
        now = int(time.time() * 1000)
        with self.lock:
            loop_ms = self.last_loop_ms
            error = self.last_error
            predict_ms = self.last_predict_fetch_ms
            snap = dict(self.last_public_snapshot) if self.last_public_snapshot else None
        required = (
            "sampledAtMs", "marketId", "windowEndMs", "secondsLeft",
            "predictUpBid", "predictUpAsk", "predictDownBid", "predictDownAsk",
        )
        missing = [name for name in required if not snap or not _finite(snap.get(name))]
        fresh = bool(loop_ms is not None and now - loop_ms < 3_000)
        with self.lock:
            canonical_ms = getattr(self, "last_canonical_micro_fetch_ms", None)
            canonical_age_ms = getattr(self, "last_canonical_micro_age_ms", None)
            canonical_error = getattr(self, "last_canonical_micro_error", None)
        canonical_fresh = bool(
            canonical_ms is not None
            and now - int(canonical_ms) < CANONICAL_MICRO_STALE_MS
            and canonical_error is None
        )
        ready = fresh and canonical_fresh and error is None and not missing
        return {
            "ok": ready,
            "status": "ONLINE" if ready else "DEGRADED",
            "version": VERSION,
            "strategy": "PUBLIC_SOURCE_ONLY_NO_STRATEGY",
            "processAlive": True,
            "processHealthy": ready,
            "strategyInputReady": ready,
            "strategyStatus": "READY" if ready else "WAITING_PUBLIC_INPUT",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "targetEventsUsedForDecision": False,
            "targetDataRead": False,
            "writesTo8776": False,
            "reads8776": False,
            "echtgeldHandoff": False,
            "predictSourceUrl": base.PREDICT_STATE_URL,
            "binanceMicrostructureSource": "8766_CANONICAL_MICROSTRUCTURE_LITE",
            "binanceMicrostructureUrl": CANONICAL_MICRO_URL,
            "binanceMicrostructureFresh": canonical_fresh,
            "binanceMicrostructureFetchAgeMs": (now - int(canonical_ms)) if canonical_ms else None,
            "binanceMicrostructureSnapshotAgeMs": canonical_age_ms,
            "binanceMicrostructureError": canonical_error,
            "publicSnapshotArchive": str(PUBLIC_SNAPSHOT_ARCHIVE_DB),
            "publicSnapshotArchiveVersion": "PUBLIC_SOURCE_SNAPSHOT_ARCHIVE_V2",
            "publicSnapshotArchiveError": self.public_snapshot_archive_last_error,
            "lastLoopAgeMs": now - loop_ms if loop_ms else None,
            "predictSourceAgeMs": now - predict_ms if predict_ms else None,
            "missingFeatures": missing,
            "lastError": error,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            snap = dict(self.last_public_snapshot) if self.last_public_snapshot else None
            market_id = self.current_market_id
            title = self.current_title
        health = self.health_snapshot()
        return {
            "ok": bool(health["processHealthy"]),
            "version": VERSION,
            "strategy": "PUBLIC_SOURCE_ONLY_NO_STRATEGY",
            "paperOnly": True,
            "forwardOnly": True,
            "liveOrdersAffected": False,
            "targetEventsUsedForDecision": False,
            "targetDataRead": False,
            "reads8776": False,
            "echtgeldHandoff": False,
            "currentMarket": {"marketId": market_id, "title": title, "active": bool(market_id)},
            "latestPublicSnapshot": snap,
            "health": health,
            "dataIntegrity": {"ready": bool(health["strategyInputReady"]), "missingFeatures": list(health["missingFeatures"])},
            "sources": {
                "predictPublic": {"url": base.PREDICT_STATE_URL},
                "spotFutures": {"source": "8766 canonical Binance microstructure lite"},
                "chainlink": {"source": "public Chainlink websocket"},
                "targetWallet": None,
                "targetSettlement": None,
            },
        }


class Handler(BaseHTTPRequestHandler):
    runtime: UnifiedControllerPublicSourceV1
    def log_message(self, *_args: Any) -> None:
        return
    def _write(self, payload: Any, code: int = 200) -> None:
        import json
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in {"/", "/state", "/health"}:
            payload = self.runtime.health_snapshot() if path == "/health" else self.runtime.snapshot()
            self._write(payload)
        else:
            self._write({"ok": False, "error": "not found"}, 404)


def main() -> int:
    runtime = UnifiedControllerPublicSourceV1()
    runtime.start()
    handler = type("UnifiedControllerPublicSourceV1Handler", (Handler,), {"runtime": runtime})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"{VERSION} listening on http://{HOST}:{PORT}/state; publicOnly=true; reads8776=false; "
        "targetDataRead=false; echtgeldHandoff=false; liveOrdersAffected=false",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown(); server.server_close(); runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
