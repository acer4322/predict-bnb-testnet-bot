from __future__ import annotations

import json
import os
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .cross_oracle_strategies import (
    BINANCE_REALTIME_URL,
    CrossOraclePaperEngine,
    STRATEGY_POLY_GAP_SCALP,
    STRATEGY_POLY_LEAD_ENTRY,
    STRATEGY_POLY_LEAD_EXIT,
    _http_json,
)


ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("PREDICT_CROSS_ORACLE_DB", ROOT / "data" / "cross_oracle.db"))
HOST = os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_HOST", "127.0.0.1")
PORT = int(os.environ.get("PREDICT_CROSS_ORACLE_STRATEGY_PORT", "8768"))
CROSS_ORACLE_STATE_URL = os.environ.get(
    "PREDICT_CROSS_ORACLE_STATE_URL",
    "http://127.0.0.1:8767/state",
)
MAX_POLY_AGE_MS = max(250.0, float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_POLY_AGE_MS", "2000")))
MAX_BINANCE_OBSERVATION_AGE_MS = max(
    250.0,
    float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_BINANCE_OBSERVATION_AGE_MS", "1500")),
)
MAX_BINANCE_BOOK_AGE_MS = max(
    250.0,
    float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_BINANCE_BOOK_AGE_MS", "2000")),
)
MAX_BINANCE_BOOK_SKEW_MS = max(
    50.0,
    float(os.environ.get("PREDICT_POLY_STRATEGY_MAX_BINANCE_BOOK_SKEW_MS", "500")),
)
LEAD_RETRY_WINDOW_MS = max(
    250,
    int(os.environ.get("PREDICT_POLY_LEAD_RETRY_WINDOW_MS", "2000")),
)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result == result and result not in (float("inf"), float("-inf")) else None


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
    age_ms = _number(polymarket.get("ageMs"))
    if age_ms is None or age_ms > MAX_POLY_AGE_MS:
        raise RuntimeError(f"Polymarket quote is stale: age={age_ms!r} ms")
    return polymarket


def _fresh_binance_latest() -> dict[str, Any]:
    payload = _http_json(BINANCE_REALTIME_URL)
    latest = payload.get("latest") if isinstance(payload, dict) else None
    if not isinstance(latest, dict):
        raise RuntimeError("Binance /api/realtime has no latest snapshot")
    now_ms = int(time.time() * 1000)
    observed_ms = _number(latest.get("observed_timestamp_ms"))
    if observed_ms is not None and now_ms - observed_ms > MAX_BINANCE_OBSERVATION_AGE_MS:
        raise RuntimeError(
            f"Binance observation is stale: age={now_ms - observed_ms:.0f} ms"
        )
    book_age_ms = _number(latest.get("book_age_ms"))
    if book_age_ms is not None and book_age_ms > MAX_BINANCE_BOOK_AGE_MS:
        raise RuntimeError(f"Binance Prediction book is stale: age={book_age_ms:.0f} ms")
    book_skew_ms = _number(latest.get("book_skew_ms"))
    if book_skew_ms is not None and book_skew_ms > MAX_BINANCE_BOOK_SKEW_MS:
        raise RuntimeError(f"Binance UP/DOWN book skew is too large: {book_skew_ms:.0f} ms")
    return latest


class ResilientCrossOraclePaperEngine(CrossOraclePaperEngine):
    """Add freshness gates plus retry semantics around the isolated Paper engine."""

    def _evaluate_once(self) -> None:
        # Fail closed before the base engine can create a Paper fill from stale
        # top-of-book data. The base call fetches its own current snapshot again.
        latest = _fresh_binance_latest()
        super()._evaluate_once()

        with self.lock:
            runtime = dict(self.runtime)
            last_flip = dict(self.last_flip) if self.last_flip else None
        if runtime.get("aligned") is not True:
            return
        poly_direction = str(runtime.get("polyDirection") or "")
        if poly_direction not in {"UP", "DOWN"}:
            return
        market_id_raw = runtime.get("binanceMarketId")
        try:
            market_id = int(market_id_raw)
        except (TypeError, ValueError):
            return
        now_ms = int(time.time() * 1000)

        # If the exact flip tick had no executable Binance bid, keep retrying
        # the exit while Polymarket remains confidently on the opposite side.
        for strategy in (STRATEGY_POLY_LEAD_EXIT, STRATEGY_POLY_GAP_SCALP):
            trade = self._open_trade_for_market(strategy, market_id)
            if trade is not None and str(trade["side"]) != poly_direction:
                self._exit_trade_at_bid(
                    trade,
                    latest,
                    now_ms,
                    "POLY_DIRECTION_FLIP_RETRY",
                )

        # Likewise, a lead entry is armed briefly after the flip so a missing
        # ask on one poll does not silently erase the signal forever.
        if not last_flip:
            return
        if int(last_flip.get("binanceMarketId") or -1) != market_id:
            return
        flip_at_ms = int(last_flip.get("atMs") or 0)
        if flip_at_ms <= 0 or now_ms - flip_at_ms > LEAD_RETRY_WINDOW_MS:
            return
        if runtime.get("binanceDirection") == poly_direction:
            return
        poly_up_mid = _number(runtime.get("polyUpMid"))
        binance_up_mid = _number(runtime.get("binanceUpMid"))
        poly_slug = str(runtime.get("polyMarketSlug") or "")
        if poly_up_mid is None or binance_up_mid is None or not poly_slug:
            return
        for strategy in (STRATEGY_POLY_LEAD_ENTRY, STRATEGY_POLY_LEAD_EXIT):
            if self._has_trade_for_market(strategy, market_id):
                continue
            self._open_trade(
                strategy=strategy,
                latest=latest,
                binance_market_id=market_id,
                poly_slug=poly_slug,
                side=poly_direction,
                poly_up_mid=poly_up_mid,
                binance_up_mid=binance_up_mid,
                now_ms=now_ms,
                reason="POLY_FLIP_LEADS_BINANCE_RETRY",
                metadata={"flipAgeMs": now_ms - flip_at_ms},
            )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        parameters = payload.setdefault("parameters", {})
        parameters.update(
            {
                "maxPolyAgeMs": MAX_POLY_AGE_MS,
                "maxBinanceObservationAgeMs": MAX_BINANCE_OBSERVATION_AGE_MS,
                "maxBinanceBookAgeMs": MAX_BINANCE_BOOK_AGE_MS,
                "maxBinanceBookSkewMs": MAX_BINANCE_BOOK_SKEW_MS,
                "leadRetryWindowMs": LEAD_RETRY_WINDOW_MS,
            }
        )
        payload["executionQuoteSource"] = "Binance /api/realtime executable top-of-book"
        payload["timingNote"] = (
            "Polymarket is event-streamed; Binance Paper execution quotes are the newest "
            "fresh /api/realtime observation, so this is a high-frequency-style forward "
            "experiment rather than a claim of sub-250ms Binance execution."
        )
        return payload


class _Handler(BaseHTTPRequestHandler):
    engine: ResilientCrossOraclePaperEngine

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
    engine = ResilientCrossOraclePaperEngine(DB_PATH, _poly_snapshot)
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
