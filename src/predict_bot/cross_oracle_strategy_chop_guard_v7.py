from __future__ import annotations

import os
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


class CoverageQualifiedLeadLagPaperEngine(v6.LeadLagValidationPaperEngine):
    """V7: keep partial/outage markets out of 10/30/50 lead probabilities.

    The collector is forward-only, so the first market after deployment may begin
    halfway through a five-minute window. A network outage can also leave a market
    with only a small trajectory fragment. Those rows remain visible for research,
    but they are not allowed to vote in market-level lead probabilities unless the
    observed span and sample count meet the configured minimums.
    """

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

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        validation = payload.get("polyBinanceLeadValidation")
        if isinstance(validation, dict):
            validation["minimumMarketCoverageMs"] = LEAD_MIN_MARKET_COVERAGE_MS
            validation["minimumMarketSamples"] = LEAD_MIN_MARKET_SAMPLES
            validation["partialMarketsVisibleButExcludedFromProbabilities"] = True
            validation["version"] = "poly_binance_lead_validation_v2"
        return payload


v6.v5.rolling.base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = (
    CoverageQualifiedLeadLagPaperEngine
)


def main() -> int:
    return v6.main()


if __name__ == "__main__":
    raise SystemExit(main())
