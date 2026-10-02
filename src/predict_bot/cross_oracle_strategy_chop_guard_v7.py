from __future__ import annotations

import copy
import os
import threading
import time
from typing import Any

from . import cross_oracle_strategy_chop_guard_v6 as v6


LEAD_MIN_MARKET_COVERAGE_MS = max(
    60_000,
    min(
        295_000,
        int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_MIN_MARKET_COVERAGE_MS", "240000")),
    ),
)
LEAD_MIN_MARKET_SAMPLES = max(
    20,
    int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_MIN_MARKET_SAMPLES", "240")),
)
# 900 five-minute markets = 75 hours.  This keeps a little more than three
# full days of raw aligned Poly/Binance trajectories for opening-leader and
# regime/chop research while remaining bounded.
LEAD_RETENTION_MARKETS = max(
    72,
    int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_RETENTION_MARKETS", "900")),
)
LEAD_PRUNE_INTERVAL_SECONDS = max(
    30.0,
    float(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_PRUNE_SECONDS", "60")),
)
LEAD_DETAILED_EVENT_MARKETS = max(
    3,
    min(
        20,
        int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_DETAIL_MARKETS", "10")),
    ),
)
# Raw samples continue to be persisted on every Paper evaluation tick, but the
# expensive 10/30/50 market analytics are display/research data and do not need
# to be rebuilt for every 250 ms receipt.  Lead analytics use stale-while-
# revalidate so their rebuild never occurs in the HTTP request thread.
LEAD_UI_ANALYTICS_CACHE_MS = max(
    2_000,
    int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_UI_CACHE_MS", "10000")),
)
LEAD_UI_ANALYTICS_RETRY_MS = max(
    2_000,
    min(
        30_000,
        int(os.environ.get("PREDICT_POLY_LEAD_VALIDATION_UI_RETRY_MS", "5000")),
    ),
)

# /state also contains strategy summaries, rolling-market CTEs, CHOP state and
# heartbeat reads.  Any one of those SQLite reads can be delayed by a busy DB.
# Cache the *entire* completed state snapshot and rebuild it in a background
# worker.  The request path only copies process-local dictionaries/locks.
STATE_UI_SNAPSHOT_CACHE_MS = max(
    2_000,
    int(os.environ.get("PREDICT_CROSS_ORACLE_STATE_UI_CACHE_MS", "10000")),
)
STATE_UI_SNAPSHOT_RETRY_MS = max(
    2_000,
    min(
        30_000,
        int(os.environ.get("PREDICT_CROSS_ORACLE_STATE_UI_RETRY_MS", "5000")),
    ),
)


class CoverageQualifiedLeadLagPaperEngine(v6.LeadLagValidationPaperEngine):
    """V7: quality-gated and bounded Poly-vs-Binance lead research.

    The collector is forward-only, so the first market after deployment may begin
    halfway through a five-minute window. A network outage can also leave a market
    with only a small trajectory fragment. Those rows remain visible for research,
    but they are not allowed to vote in either market-level or event-level lead
    probabilities unless the observed span and sample count meet the configured
    minimums. Raw trajectory retention defaults to 900 five-minute markets (about
    75 hours) so three-day causal research is available without unbounded growth.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._next_lead_prune_at = 0.0
        super().__init__(*args, **kwargs)

        self._lead_ui_refresh_lock = threading.Lock()
        self._lead_ui_refresh_running = False
        self._lead_ui_refresh_error: str | None = None
        self._lead_ui_refresh_started_at_ms: int | None = None
        self._lead_ui_refresh_completed_at_ms: int | None = None
        self._lead_ui_refresh_duration_ms: int | None = None
        self._lead_ui_refresh_next_allowed_at_ms = 0

        self._state_ui_refresh_lock = threading.Lock()
        self._state_ui_cache: dict[str, Any] | None = None
        self._state_ui_cache_at_ms = 0
        self._state_ui_refresh_running = False
        self._state_ui_refresh_error: str | None = None
        self._state_ui_refresh_started_at_ms: int | None = None
        self._state_ui_refresh_completed_at_ms: int | None = None
        self._state_ui_refresh_duration_ms: int | None = None
        self._state_ui_refresh_next_allowed_at_ms = 0

    def _evaluate_once(self) -> None:
        # v6 intentionally invalidates its analytics cache whenever it persists a
        # new raw sample. Keep the last completed display snapshot regardless of
        # age; an expired snapshot is replaced asynchronously instead of making
        # an HTTP request rebuild tens of thousands of raw rows.
        previous_cache = self._lead_cache
        previous_cache_at_ms = self._lead_cache_at_ms
        super()._evaluate_once()
        if self._lead_cache is None and previous_cache is not None:
            self._lead_cache = previous_cache
            self._lead_cache_at_ms = previous_cache_at_ms

        now = time.monotonic()
        if now < self._next_lead_prune_at:
            return
        self._next_lead_prune_at = now + LEAD_PRUNE_INTERVAL_SECONDS
        with self.db_lock:
            self.db.execute(
                """DELETE FROM poly_binance_lead_samples
                    WHERE binance_market_id NOT IN (
                        SELECT binance_market_id
                        FROM poly_binance_lead_samples
                        GROUP BY binance_market_id
                        ORDER BY MAX(observed_at_ms) DESC, binance_market_id DESC
                        LIMIT ?
                    )""",
                (LEAD_RETENTION_MARKETS,),
            )
            self.db.commit()

    def _lead_ui_refresh_metadata(self, now_ms: int, cache_age_ms: int | None) -> dict[str, Any]:
        with self._lead_ui_refresh_lock:
            running = self._lead_ui_refresh_running
            error = self._lead_ui_refresh_error
            started_at_ms = self._lead_ui_refresh_started_at_ms
            completed_at_ms = self._lead_ui_refresh_completed_at_ms
            duration_ms = self._lead_ui_refresh_duration_ms
            retry_at_ms = self._lead_ui_refresh_next_allowed_at_ms
        if self._lead_cache is None:
            status = "BUILDING"
        elif running:
            status = "REFRESHING"
        elif error:
            status = "STALE_ERROR"
        elif cache_age_ms is not None and cache_age_ms >= LEAD_UI_ANALYTICS_CACHE_MS:
            status = "STALE"
        else:
            status = "FRESH"
        return {
            "status": status,
            "refreshing": running,
            "cacheAgeMs": cache_age_ms,
            "cacheTtlMs": LEAD_UI_ANALYTICS_CACHE_MS,
            "lastRefreshStartedAtMs": started_at_ms,
            "lastRefreshCompletedAtMs": completed_at_ms,
            "lastRefreshDurationMs": duration_ms,
            "lastRefreshError": error,
            "retryAtMs": retry_at_ms if error and retry_at_ms > now_ms else None,
            "requestPathBlocksOnRefresh": False,
        }

    def _refresh_lead_ui_analytics(self) -> None:
        started_at_ms = int(time.time() * 1000)
        started = time.monotonic()
        error: str | None = None
        try:
            # v6 only reuses a cache younger than its own short TTL. This worker
            # is scheduled after the V7 TTL expires, so any expensive rebuild is
            # paid here rather than in /state.
            super()._lead_validation_snapshot()
        except Exception as exc:
            error = str(exc)[:400]
        finally:
            finished_at_ms = int(time.time() * 1000)
            duration_ms = int((time.monotonic() - started) * 1000)
            with self._lead_ui_refresh_lock:
                self._lead_ui_refresh_running = False
                self._lead_ui_refresh_error = error
                self._lead_ui_refresh_started_at_ms = started_at_ms
                self._lead_ui_refresh_completed_at_ms = finished_at_ms
                self._lead_ui_refresh_duration_ms = duration_ms
                self._lead_ui_refresh_next_allowed_at_ms = (
                    finished_at_ms + LEAD_UI_ANALYTICS_RETRY_MS if error else 0
                )

    def _schedule_lead_ui_refresh(self, now_ms: int) -> bool:
        with self._lead_ui_refresh_lock:
            if self._lead_ui_refresh_running:
                return False
            if now_ms < self._lead_ui_refresh_next_allowed_at_ms:
                return False
            self._lead_ui_refresh_running = True
            self._lead_ui_refresh_error = None
            self._lead_ui_refresh_started_at_ms = now_ms
        threading.Thread(
            target=self._refresh_lead_ui_analytics,
            name="poly-lead-ui-analytics",
            daemon=True,
        ).start()
        return True

    def _lead_validation_snapshot(self) -> dict[str, Any]:
        now_ms = int(time.time() * 1000)
        cache = self._lead_cache
        cache_age_ms = (
            max(0, now_ms - int(self._lead_cache_at_ms)) if cache is not None else None
        )
        if cache is None or (
            cache_age_ms is not None and cache_age_ms >= LEAD_UI_ANALYTICS_CACHE_MS
        ):
            self._schedule_lead_ui_refresh(now_ms)

        metadata = self._lead_ui_refresh_metadata(now_ms, cache_age_ms)
        if cache is None:
            return {
                "version": "poly_binance_lead_validation_v2",
                "paperOnly": True,
                "liveOrdersAffected": False,
                "forwardOnly": True,
                "currentRegime": "BUILDING",
                "sampleRows": None,
                "sampledMarketsTotal": None,
                "windows": {},
                "recentMarkets": [],
                "uiAnalyticsRefresh": metadata,
            }

        payload = dict(cache)
        payload["uiAnalyticsRefresh"] = metadata
        return payload

    def _market_analysis(self, market_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
        payload = super()._market_analysis(market_id, rows)
        start = payload.get("sampleStartMs")
        end = payload.get("sampleEndMs")
        try:
            coverage_ms = max(0, int(end) - int(start)) if start is not None and end is not None else 0
        except (TypeError, ValueError):
            coverage_ms = 0
        samples = int(payload.get("samples") or 0)
        qualified = bool(
            coverage_ms >= LEAD_MIN_MARKET_COVERAGE_MS
            and samples >= LEAD_MIN_MARKET_SAMPLES
        )
        payload["coverageMs"] = coverage_ms
        payload["coverageSeconds"] = coverage_ms / 1000.0
        payload["coverageQualified"] = qualified
        payload["minimumCoverageMs"] = LEAD_MIN_MARKET_COVERAGE_MS
        payload["minimumSamples"] = LEAD_MIN_MARKET_SAMPLES
        if not qualified:
            payload["leaderBeforeCoverageGate"] = payload.get("marketLeader")
            payload["marketLeader"] = "INSUFFICIENT"
            payload["insufficientReason"] = (
                f"trajectory coverage {coverage_ms / 1000.0:.1f}s/{LEAD_MIN_MARKET_COVERAGE_MS / 1000.0:.1f}s; "
                f"samples {samples}/{LEAD_MIN_MARKET_SAMPLES}"
            )
        return payload

    def _window_stats(self, markets: list[dict[str, Any]], window: int) -> dict[str, Any]:
        selected = markets[:window]
        qualified = [market for market in selected if market.get("coverageQualified") is True]
        payload = super()._window_stats(qualified, window)
        payload["sampledMarkets"] = len(selected)
        payload["coverageQualifiedMarkets"] = len(qualified)
        payload["coverageExcludedMarkets"] = len(selected) - len(qualified)
        payload["insufficientMarkets"] = len(selected) - int(payload.get("evaluableMarkets") or 0)
        payload["marketIds"] = [int(market["marketId"]) for market in selected]
        payload["qualifiedMarketIds"] = [int(market["marketId"]) for market in qualified]
        payload["eventStatisticsUseCoverageQualifiedMarketsOnly"] = True
        return payload

    def _decorate_state_snapshot(self, payload: dict[str, Any]) -> dict[str, Any]:
        # Lead snapshots are shallow-copied by v6. Deep-copy only this nested
        # section before trimming event details so the completed lead cache itself
        # remains immutable while another request may be reading it.
        raw_validation = payload.get("polyBinanceLeadValidation")
        if isinstance(raw_validation, dict):
            validation = copy.deepcopy(raw_validation)
            payload["polyBinanceLeadValidation"] = validation
            recent = validation.get("recentMarkets")
            if isinstance(recent, list):
                for index, market in enumerate(recent):
                    if index >= LEAD_DETAILED_EVENT_MARKETS and isinstance(market, dict):
                        market["events"] = []
                        market["eventDetailsOmittedFromSnapshot"] = True
            validation["minimumMarketCoverageMs"] = LEAD_MIN_MARKET_COVERAGE_MS
            validation["minimumMarketSamples"] = LEAD_MIN_MARKET_SAMPLES
            validation["retentionMarkets"] = LEAD_RETENTION_MARKETS
            validation["retentionApproxHours"] = LEAD_RETENTION_MARKETS * 5.0 / 60.0
            validation["pruneIntervalSeconds"] = LEAD_PRUNE_INTERVAL_SECONDS
            validation["detailedEventMarkets"] = LEAD_DETAILED_EVENT_MARKETS
            validation["uiAnalyticsCacheMs"] = LEAD_UI_ANALYTICS_CACHE_MS
            validation["uiAnalyticsRetryMs"] = LEAD_UI_ANALYTICS_RETRY_MS
            validation["partialMarketsVisibleButExcludedFromProbabilities"] = True
            validation["eventStatisticsUseCoverageQualifiedMarketsOnly"] = True
            validation["windowStatisticsComputedBeforeEventDetailTrimming"] = True
            validation["version"] = "poly_binance_lead_validation_v2"
        return payload

    def _build_state_snapshot(self) -> dict[str, Any]:
        # Important: this calls the parent implementation directly. All SQLite
        # summaries, rolling CTEs, CHOP reads and lead-cache composition therefore
        # happen only in the background state worker, never in the HTTP thread.
        return self._decorate_state_snapshot(super().snapshot())

    def _state_ui_refresh_metadata(self, now_ms: int, cache_age_ms: int | None) -> dict[str, Any]:
        with self._state_ui_refresh_lock:
            running = self._state_ui_refresh_running
            error = self._state_ui_refresh_error
            started_at_ms = self._state_ui_refresh_started_at_ms
            completed_at_ms = self._state_ui_refresh_completed_at_ms
            duration_ms = self._state_ui_refresh_duration_ms
            retry_at_ms = self._state_ui_refresh_next_allowed_at_ms
        if self._state_ui_cache is None:
            status = "BUILDING"
        elif running:
            status = "REFRESHING"
        elif error:
            status = "STALE_ERROR"
        elif cache_age_ms is not None and cache_age_ms >= STATE_UI_SNAPSHOT_CACHE_MS:
            status = "STALE"
        else:
            status = "FRESH"
        return {
            "status": status,
            "refreshing": running,
            "cacheAgeMs": cache_age_ms,
            "cacheTtlMs": STATE_UI_SNAPSHOT_CACHE_MS,
            "lastRefreshStartedAtMs": started_at_ms,
            "lastRefreshCompletedAtMs": completed_at_ms,
            "lastRefreshDurationMs": duration_ms,
            "lastRefreshError": error,
            "retryAtMs": retry_at_ms if error and retry_at_ms > now_ms else None,
            "requestPathBlocksOnRefresh": False,
            "databaseReadsOnRequestPath": False,
        }

    def _refresh_state_ui_snapshot(self) -> None:
        started_at_ms = int(time.time() * 1000)
        started = time.monotonic()
        error: str | None = None
        payload: dict[str, Any] | None = None
        try:
            payload = self._build_state_snapshot()
        except Exception as exc:
            error = str(exc)[:400]
        finally:
            finished_at_ms = int(time.time() * 1000)
            duration_ms = int((time.monotonic() - started) * 1000)
            with self._state_ui_refresh_lock:
                if payload is not None:
                    self._state_ui_cache = payload
                    self._state_ui_cache_at_ms = finished_at_ms
                self._state_ui_refresh_running = False
                self._state_ui_refresh_error = error
                self._state_ui_refresh_started_at_ms = started_at_ms
                self._state_ui_refresh_completed_at_ms = finished_at_ms
                self._state_ui_refresh_duration_ms = duration_ms
                self._state_ui_refresh_next_allowed_at_ms = (
                    finished_at_ms + STATE_UI_SNAPSHOT_RETRY_MS if error else 0
                )

    def _schedule_state_ui_refresh(self, now_ms: int) -> bool:
        with self._state_ui_refresh_lock:
            if self._state_ui_refresh_running:
                return False
            if now_ms < self._state_ui_refresh_next_allowed_at_ms:
                return False
            self._state_ui_refresh_running = True
            self._state_ui_refresh_error = None
            self._state_ui_refresh_started_at_ms = now_ms
        threading.Thread(
            target=self._refresh_state_ui_snapshot,
            name="cross-oracle-state-ui-snapshot",
            daemon=True,
        ).start()
        return True

    def snapshot(self) -> dict[str, Any]:
        # HTTP-facing fast path: no SQLite reads and no analytics computation.
        now_ms = int(time.time() * 1000)
        with self._state_ui_refresh_lock:
            cache = self._state_ui_cache
            cache_at_ms = self._state_ui_cache_at_ms
        cache_age_ms = max(0, now_ms - cache_at_ms) if cache is not None else None
        if cache is None or (
            cache_age_ms is not None and cache_age_ms >= STATE_UI_SNAPSHOT_CACHE_MS
        ):
            self._schedule_state_ui_refresh(now_ms)

        with self.lock:
            runtime = dict(self.runtime)
            last_flip = dict(self.last_flip) if self.last_flip else None
        metadata = self._state_ui_refresh_metadata(now_ms, cache_age_ms)

        if cache is None:
            return {
                "status": runtime.get("status"),
                "paperOnly": True,
                "liveOrdersAffected": False,
                "feesIncluded": False,
                "parameters": {},
                "runtime": runtime,
                "lastFlip": last_flip,
                "summaries": {},
                "openPositions": [],
                "recentTrades": [],
                "strategyRules": {},
                "polyBinanceLeadValidation": {
                    "version": "poly_binance_lead_validation_v2",
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                    "forwardOnly": True,
                    "currentRegime": "BUILDING",
                    "windows": {},
                    "recentMarkets": [],
                },
                "stateSnapshotRefresh": metadata,
            }

        payload = dict(cache)
        # Keep the high-frequency runtime indicators fresh even though expensive
        # DB-backed diagnostics intentionally use a slower snapshot cadence.
        payload["status"] = runtime.get("status")
        payload["runtime"] = runtime
        payload["lastFlip"] = last_flip
        payload["stateSnapshotRefresh"] = metadata
        return payload


v6.v5.rolling.base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = (
    CoverageQualifiedLeadLagPaperEngine
)


def main() -> int:
    return v6.main()


if __name__ == "__main__":
    raise SystemExit(main())
