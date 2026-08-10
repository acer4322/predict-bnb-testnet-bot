from __future__ import annotations

import os
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

    def _evaluate_once(self) -> None:
        super()._evaluate_once()
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

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        validation = payload.get("polyBinanceLeadValidation")
        if isinstance(validation, dict):
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
            validation["partialMarketsVisibleButExcludedFromProbabilities"] = True
            validation["eventStatisticsUseCoverageQualifiedMarketsOnly"] = True
            validation["windowStatisticsComputedBeforeEventDetailTrimming"] = True
            validation["version"] = "poly_binance_lead_validation_v2"
        return payload


v6.v5.rolling.base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = (
    CoverageQualifiedLeadLagPaperEngine
)


def main() -> int:
    return v6.main()


if __name__ == "__main__":
    raise SystemExit(main())
