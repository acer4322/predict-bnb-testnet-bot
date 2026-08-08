from __future__ import annotations

import json
import sqlite3
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .cross_oracle_strategies import (
    STRATEGY_POLY_GAP_SCALP,
    STRATEGY_POLY_LEAD_EXIT,
)
from .cross_oracle_strategy_server import (
    CONFIDENCE_ACTIVE_STATUSES,
    CROSS_ORACLE_STATE_URL,
    DB_PATH,
    HOST,
    PORT,
    ResilientCrossOraclePaperEngine,
)


class GapAwareCrossOraclePaperEngine(ResilientCrossOraclePaperEngine):
    """Paper engine that treats cross-oracle continuity gaps as non-evaluable.

    A recovered Polymarket direction is a new baseline, never a precise flip.
    Strategies whose exit depends on seeing every Polymarket flip are excluded
    for the affected market when a feed gap occurs.
    """

    def __init__(self, db_path: Any, provider: Any) -> None:
        super().__init__(db_path, provider)
        self.feed_continuity: dict[str, Any] = {}
        self.last_feed_gap_generation: int | None = None
        self.pending_recovery_resync = True
        self.feed_gap_excluded_trades = 0
        self.feed_gap_excluded_confidence = 0
        self.feed_gap_resyncs = 0
        self._invalidate_simple_exit_positions_on_process_start()

    def _invalidate_simple_exit_positions_on_process_start(self) -> None:
        now_ms = int(time.time() * 1000)
        with self.db_lock:
            cursor = self.db.execute(
                """UPDATE cross_oracle_strategy_trades
                      SET status='NOT_EVALUABLE_PROCESS_RESTART', closed_at_ms=?,
                          exit_reason='PROCESS_RESTART_DATA_GAP'
                    WHERE status='OPEN' AND strategy IN (?, ?)""",
                (now_ms, STRATEGY_POLY_LEAD_EXIT, STRATEGY_POLY_GAP_SCALP),
            )
            self.db.commit()
        self.feed_gap_excluded_trades += max(0, int(cursor.rowcount or 0))

    def _skip_confidence_sources_through_now(self) -> None:
        maximum = self._max_sim_trade_id()
        if maximum <= self.last_source_trade_id:
            return
        self.last_source_trade_id = maximum
        self._persist_source_cursor()

    def _reset_poly_flip_baseline(self) -> None:
        with self.lock:
            self.last_confident_poly_direction.clear()
            self.last_flip = None
            self.runtime["polyDirection"] = None
            self.runtime["aligned"] = False
        self.last_confidence_flip_ms = int(time.time() * 1000)

    def _gap_affects_current_slug(self, continuity: dict[str, Any]) -> str | None:
        market_slug = str(continuity.get("marketSlug") or "")
        target_slug = str(continuity.get("targetMarketSlug") or "")
        if market_slug and market_slug == target_slug:
            return market_slug
        return None

    def _invalidate_gap_sensitive_positions(self, continuity: dict[str, Any]) -> None:
        now_ms = int(time.time() * 1000)
        affected_slug = self._gap_affects_current_slug(continuity)
        if not affected_slug:
            return
        with self.lock:
            runtime_market_id = self.runtime.get("binanceMarketId")
        try:
            runtime_market_id_int = int(runtime_market_id)
        except (TypeError, ValueError):
            runtime_market_id_int = -1
        active_placeholders = ",".join("?" for _ in CONFIDENCE_ACTIVE_STATUSES)
        with self.db_lock:
            trade_cursor = self.db.execute(
                """UPDATE cross_oracle_strategy_trades
                      SET status='NOT_EVALUABLE_FEED_GAP', closed_at_ms=?,
                          exit_reason='POLY_FEED_GAP'
                    WHERE status='OPEN' AND poly_market_slug=?
                      AND strategy IN (?, ?)""",
                (
                    now_ms,
                    affected_slug,
                    STRATEGY_POLY_LEAD_EXIT,
                    STRATEGY_POLY_GAP_SCALP,
                ),
            )
            params: list[Any] = [now_ms, *CONFIDENCE_ACTIVE_STATUSES, affected_slug]
            confidence_sql = f"""UPDATE poly_confidence_shadows
                                      SET status='NOT_EVALUABLE_FEED_GAP', finalized_at_ms=?,
                                          exit_reason='POLY_FEED_GAP'
                                    WHERE status IN ({active_placeholders})
                                      AND poly_market_slug=?"""
            confidence_cursor = self.db.execute(confidence_sql, params)
            pending_count = 0
            if runtime_market_id_int >= 0:
                pending_cursor = self.db.execute(
                    """UPDATE poly_confidence_shadows
                          SET status='NOT_EVALUABLE_FEED_GAP', finalized_at_ms=?,
                              exit_reason='POLY_FEED_GAP_BEFORE_ATTACH'
                        WHERE status='PENDING_ATTACH' AND binance_market_id=?""",
                    (now_ms, runtime_market_id_int),
                )
                pending_count = max(0, int(pending_cursor.rowcount or 0))
            self.db.commit()
        self.feed_gap_excluded_trades += max(0, int(trade_cursor.rowcount or 0))
        self.feed_gap_excluded_confidence += (
            max(0, int(confidence_cursor.rowcount or 0)) + pending_count
        )

    def observe_continuity(self, continuity: dict[str, Any]) -> None:
        generation_raw = continuity.get("gapGeneration")
        try:
            generation = int(generation_raw)
        except (TypeError, ValueError):
            raise RuntimeError("Polymarket continuity generation unavailable")
        gap_active = continuity.get("gapActive") is True
        generation_changed = self.last_feed_gap_generation != generation
        self.feed_continuity = dict(continuity)
        if generation_changed:
            self._invalidate_gap_sensitive_positions(continuity)
            self._reset_poly_flip_baseline()
            self.last_feed_gap_generation = generation
            self.pending_recovery_resync = True
        if gap_active:
            # Do not mirror source trades that opened while the Polymarket feed
            # was discontinuous; on recovery the cursor advances past them.
            raise RuntimeError(
                f"Polymarket feed gap active: {continuity.get('gapReason') or 'unknown'}"
            )
        if self.pending_recovery_resync:
            self._skip_confidence_sources_through_now()
            self._reset_poly_flip_baseline()
            self.pending_recovery_resync = False
            self.feed_gap_resyncs += 1
            raise RuntimeError("Polymarket feed recovered; establishing a fresh direction baseline")

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["feedContinuityGuard"] = {
            "status": "GAP" if self.feed_continuity.get("gapActive") else "HEALTHY",
            "failClosed": True,
            "recoveryDirectionCountsAsFlip": False,
            "gapGeneration": self.feed_continuity.get("gapGeneration"),
            "gapReason": self.feed_continuity.get("gapReason"),
            "gapDetail": self.feed_continuity.get("gapDetail"),
            "lastGapDurationMs": self.feed_continuity.get("lastGapDurationMs"),
            "lastRecoveryAtMs": self.feed_continuity.get("lastRecoveryAtMs"),
            "discoveryRetryCount": self.feed_continuity.get("discoveryRetryCount"),
            "consecutiveDiscoveryFailures": self.feed_continuity.get("consecutiveDiscoveryFailures"),
            "lastTlsError": self.feed_continuity.get("lastTlsError"),
            "reconnectCount": self.feed_continuity.get("reconnectCount"),
            "excludedGapSensitiveTrades": self.feed_gap_excluded_trades,
            "excludedConfidenceShadows": self.feed_gap_excluded_confidence,
            "recoveryResyncs": self.feed_gap_resyncs,
        }
        return payload


def _full_collector_state() -> dict[str, Any]:
    request = urllib.request.Request(
        CROSS_ORACLE_STATE_URL,
        headers={"Accept": "application/json", "User-Agent": "BTC-5M-Lab-Poly-GapGuard/1.0"},
    )
    with urllib.request.urlopen(request, timeout=1.5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("cross-oracle /state returned invalid payload")
    return payload


class _Handler(BaseHTTPRequestHandler):
    engine: GapAwareCrossOraclePaperEngine

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
    engine_ref: dict[str, GapAwareCrossOraclePaperEngine] = {}

    def guarded_provider() -> dict[str, Any]:
        payload = _full_collector_state()
        continuity = payload.get("continuity")
        polymarket = payload.get("polymarket")
        if not isinstance(continuity, dict):
            raise RuntimeError("cross-oracle collector has no continuity state")
        if not isinstance(polymarket, dict):
            raise RuntimeError("cross-oracle collector has no Polymarket snapshot")
        engine = engine_ref.get("engine")
        if engine is None:
            raise RuntimeError("gap-aware strategy engine is not initialized")
        engine.observe_continuity(continuity)
        age_ms = polymarket.get("ageMs")
        try:
            age = float(age_ms)
        except (TypeError, ValueError):
            raise RuntimeError("Polymarket quote age unavailable")
        if age > float(continuity.get("maxAcceptedAgeMs") or 2000):
            raise RuntimeError(f"Polymarket quote is stale after continuity guard: {age:.0f} ms")
        return polymarket

    engine = GapAwareCrossOraclePaperEngine(DB_PATH, guarded_provider)
    engine_ref["engine"] = engine
    engine.start()
    handler = type("GapAwareCrossOracleStrategyHandler", (_Handler,), {"engine": engine})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Gap-aware cross-oracle Paper strategies listening on http://{HOST}:{PORT}/state; db={DB_PATH}",
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
