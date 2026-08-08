from __future__ import annotations

import json
import os
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .cross_oracle import (
    DB_PATH,
    HOST,
    PORT,
    CrossOracleCollector,
    current_btc_5m_slug,
)


DISCOVERY_RETRY_SECONDS = max(
    0.5,
    float(os.environ.get("PREDICT_CROSS_ORACLE_DISCOVERY_RETRY_SECONDS", "1.0")),
)
POLY_CONTINUITY_MAX_AGE_MS = max(
    500,
    int(os.environ.get("PREDICT_CROSS_ORACLE_CONTINUITY_MAX_AGE_MS", "2000")),
)


class ResilientCrossOracleCollector(CrossOracleCollector):
    """Cross-oracle collector with explicit feed-gap accounting.

    The base collector already retries market discovery. This wrapper makes the
    retry cadence aggressive during a failed rollover and, more importantly,
    exposes continuity generations so downstream lead/lag strategies never
    interpret a post-sleep recovery state as a precise Polymarket flip.
    """

    def __init__(self) -> None:
        super().__init__()
        self.discovery_retry_count = 0
        self.consecutive_discovery_failures = 0
        self.last_discovery_attempt_at_ms: int | None = None
        self.last_discovery_success_at_ms: int | None = None
        self.last_discovery_error: str | None = None
        self.last_tls_error: str | None = None
        self.reconnect_count = 0
        self.stream_restart_count = 0
        self.last_healthy_at_ms: int | None = None
        self.last_recovery_at_ms: int | None = None
        self.last_gap_duration_ms: int | None = None
        self.gap_active = False
        self.gap_opened_at_ms: int | None = None
        self.gap_reason: str | None = None
        self.gap_detail: str | None = None
        self.gap_target_slug: str | None = None
        self._create_continuity_schema()
        with self.db_lock:
            row = self.db.execute(
                "SELECT COALESCE(MAX(gap_generation), 0) FROM cross_oracle_feed_gaps"
            ).fetchone()
        self.gap_generation = int(row[0] if row else 0)
        target_slug, _ = current_btc_5m_slug()
        self._open_gap("COLLECTOR_START", "waiting for first fresh Polymarket quote", target_slug)

    def _create_continuity_schema(self) -> None:
        with self.db_lock:
            self.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS cross_oracle_feed_gaps (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    gap_generation INTEGER NOT NULL UNIQUE,
                    opened_at_ms INTEGER NOT NULL,
                    recovered_at_ms INTEGER,
                    duration_ms INTEGER,
                    reason TEXT NOT NULL,
                    detail TEXT,
                    market_slug TEXT,
                    target_slug TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_cross_oracle_feed_gaps_opened
                    ON cross_oracle_feed_gaps(opened_at_ms);
                """
            )
            self.db.commit()

    def _market_slug(self) -> str | None:
        with self.lock:
            market = dict(self.market or {})
        value = market.get("slug")
        return str(value) if value else None

    def _open_gap(self, reason: str, detail: str | None, target_slug: str | None = None) -> None:
        now_ms = int(time.time() * 1000)
        with self.lock:
            if self.gap_active:
                if detail:
                    self.gap_detail = str(detail)[:500]
                if target_slug:
                    self.gap_target_slug = target_slug
                return
            self.gap_generation += 1
            self.gap_active = True
            self.gap_opened_at_ms = now_ms
            self.gap_reason = reason
            self.gap_detail = str(detail)[:500] if detail else None
            self.gap_target_slug = target_slug
            generation = self.gap_generation
            market_slug = self._market_slug()
        with self.db_lock:
            self.db.execute(
                """INSERT OR REPLACE INTO cross_oracle_feed_gaps(
                       gap_generation, opened_at_ms, reason, detail,
                       market_slug, target_slug
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (generation, now_ms, reason, self.gap_detail, market_slug, target_slug),
            )
            self.db.commit()

    def _close_gap_if_fresh(self, received_ms: int | None = None) -> None:
        now_ms = int(time.time() * 1000)
        with self.lock:
            if not self.gap_active:
                self.last_healthy_at_ms = now_ms
                return
            opened = self.gap_opened_at_ms or now_ms
            if received_ms is None:
                received_ms = int(self.polymarket.get("receivedTimestampMs") or 0)
            if received_ms < opened:
                return
            target_slug, _ = current_btc_5m_slug(now_ms / 1000.0)
            market_slug = self._market_slug()
            if market_slug != target_slug:
                return
            duration = max(0, now_ms - opened)
            generation = self.gap_generation
            self.gap_active = False
            self.last_gap_duration_ms = duration
            self.last_recovery_at_ms = now_ms
            self.last_healthy_at_ms = now_ms
            self.gap_opened_at_ms = None
            self.gap_reason = None
            self.gap_detail = None
            self.gap_target_slug = target_slug
        with self.db_lock:
            self.db.execute(
                """UPDATE cross_oracle_feed_gaps
                      SET recovered_at_ms=?, duration_ms=?
                    WHERE gap_generation=? AND recovered_at_ms IS NULL""",
                (now_ms, duration, generation),
            )
            self.db.commit()

    def _market_supervisor(self) -> None:
        while not self.stop_event.is_set():
            slug, bucket = current_btc_5m_slug()
            current_slug = self._market_slug()
            if current_slug != slug:
                self._open_gap(
                    "MARKET_ROLLOVER_DISCOVERY",
                    f"need current market {slug}; have {current_slug or 'none'}",
                    slug,
                )
                self._discover_market(slug, bucket)
                self.stop_event.wait(DISCOVERY_RETRY_SECONDS)
                continue
            self.stop_event.wait(DISCOVERY_RETRY_SECONDS)

    def _discover_market(self, slug: str, bucket: int) -> bool:
        self.last_discovery_attempt_at_ms = int(time.time() * 1000)
        ok = super()._discover_market(slug, bucket)
        if ok:
            self.consecutive_discovery_failures = 0
            self.last_discovery_success_at_ms = int(time.time() * 1000)
            self.last_discovery_error = None
            return True
        with self.lock:
            detail = str(self.polymarket.get("error") or "market discovery failed")
        self.discovery_retry_count += 1
        self.consecutive_discovery_failures += 1
        self.last_discovery_error = detail[:500]
        if "CERTIFICATE_VERIFY_FAILED" in detail or "self-signed certificate" in detail.lower():
            self.last_tls_error = detail[:500]
        self._open_gap("MARKET_DISCOVERY_FAILED", detail, slug)
        return False

    def _restart_polymarket_stream(self) -> None:
        self.stream_restart_count += 1
        super()._restart_polymarket_stream()

    def _polymarket_error(self, ws: Any, error: Any, generation: int) -> None:
        if generation == self.polymarket_generation:
            self.reconnect_count += 1
            target_slug, _ = current_btc_5m_slug()
            self._open_gap("POLYMARKET_WS_ERROR", str(error), target_slug)
        super()._polymarket_error(ws, error, generation)

    def _polymarket_close(self, ws: Any, code: Any, message: Any, generation: int) -> None:
        if not self.stop_event.is_set() and generation == self.polymarket_generation:
            self.reconnect_count += 1
            target_slug, _ = current_btc_5m_slug()
            self._open_gap(
                "POLYMARKET_WS_CLOSE",
                f"close {code}: {message}",
                target_slug,
            )
        super()._polymarket_close(ws, code, message, generation)

    def _apply_poly_quote(
        self,
        event: dict[str, Any],
        event_type: str,
        source_ms: int | None,
        received_wall_ns: int,
        raw: str,
    ) -> None:
        before = None
        with self.lock:
            before = self.polymarket.get("receivedTimestampMs")
        super()._apply_poly_quote(event, event_type, source_ms, received_wall_ns, raw)
        received_ms = received_wall_ns // 1_000_000
        with self.lock:
            after = self.polymarket.get("receivedTimestampMs")
        if after != before and int(after or 0) >= received_ms:
            self._close_gap_if_fresh(received_ms)

    def snapshot(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        target_slug, _ = current_btc_5m_slug(now_ms / 1000.0)
        market_slug = self._market_slug()
        with self.lock:
            received_ms = int(self.polymarket.get("receivedTimestampMs") or 0)
            status = str(self.polymarket.get("status") or "")
        age_ms = now_ms - received_ms if received_ms else None
        if market_slug != target_slug:
            self._open_gap(
                "MARKET_MAPPING_STALE",
                f"target={target_slug}; current={market_slug or 'none'}",
                target_slug,
            )
        elif age_ms is None or age_ms > POLY_CONTINUITY_MAX_AGE_MS or status != "LIVE":
            self._open_gap(
                "POLYMARKET_FEED_STALE",
                f"status={status}; ageMs={age_ms}",
                target_slug,
            )
        else:
            self._close_gap_if_fresh(received_ms)

        payload = super().snapshot()
        with self.lock:
            continuity = {
                "healthy": not self.gap_active,
                "gapActive": self.gap_active,
                "gapGeneration": self.gap_generation,
                "gapOpenedAtMs": self.gap_opened_at_ms,
                "gapReason": self.gap_reason,
                "gapDetail": self.gap_detail,
                "targetMarketSlug": self.gap_target_slug or target_slug,
                "marketSlug": market_slug,
                "maxAcceptedAgeMs": POLY_CONTINUITY_MAX_AGE_MS,
                "lastHealthyAtMs": self.last_healthy_at_ms,
                "lastRecoveryAtMs": self.last_recovery_at_ms,
                "lastGapDurationMs": self.last_gap_duration_ms,
                "discoveryRetryCount": self.discovery_retry_count,
                "consecutiveDiscoveryFailures": self.consecutive_discovery_failures,
                "lastDiscoveryAttemptAtMs": self.last_discovery_attempt_at_ms,
                "lastDiscoverySuccessAtMs": self.last_discovery_success_at_ms,
                "lastDiscoveryError": self.last_discovery_error,
                "lastTlsError": self.last_tls_error,
                "reconnectCount": self.reconnect_count,
                "streamRestartCount": self.stream_restart_count,
            }
        payload["continuity"] = continuity
        polymarket = payload.get("polymarket")
        if isinstance(polymarket, dict):
            polymarket["continuity"] = continuity
        return payload


class _Handler(BaseHTTPRequestHandler):
    collector: ResilientCrossOracleCollector

    def do_GET(self) -> None:  # noqa: N802
        if self.path not in {"/state", "/health", "/api/state"}:
            self.send_response(404)
            self.end_headers()
            return
        payload = self.collector.snapshot()
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
    collector = ResilientCrossOracleCollector()
    collector.start()
    handler = type("ResilientCrossOracleHandler", (_Handler,), {"collector": collector})
    server = ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Resilient cross-oracle collector listening on http://{HOST}:{PORT}/state; db={DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        collector.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
