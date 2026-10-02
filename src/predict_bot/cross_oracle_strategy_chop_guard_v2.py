from __future__ import annotations

import os
from typing import Any

from . import cross_oracle_strategy_chop_guard as guard


IMMEDIATE_CHOP_BREAKER_REVERSALS = max(
    2,
    int(os.environ.get("PREDICT_POLY_CHOP_GUARD_IMMEDIATE_REVERSALS", "2")),
)


class ImmediateChopBreakerPaperEngine(guard.ChopGuardPaperEngine):
    """Expose an immediate same-market breaker on top of persistent hysteresis.

    The persistent guard changes state only from finalized markets. This wrapper
    blocks NEW live exposure sooner within the current market: by default after
    two confirmed reversals. The persistent regime classifier is intentionally
    slower (three reversals per market and three CHOPPY markets in a five-market
    window by default), so a single noisy market does not over-contaminate future
    markets while same-market churn remains protected.
    """

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        state = payload.get("chopGuard")
        if not isinstance(state, dict):
            return payload
        current = state.get("currentMarket")
        current_reversals = 0
        current_evaluable = False
        if isinstance(current, dict):
            try:
                current_reversals = int(current.get("reversals") or 0)
            except (TypeError, ValueError):
                current_reversals = 0
            current_evaluable = current.get("evaluable", True) is True
        current_choppy = bool(
            current_evaluable
            and current_reversals >= IMMEDIATE_CHOP_BREAKER_REVERSALS
        )
        persistent_paused = bool(state.get("paused"))
        block_new_entries = persistent_paused or current_choppy
        if persistent_paused:
            block_reason = str(state.get("reason") or "persistent Paper CHOP pause")
        elif current_choppy:
            block_reason = (
                f"current market already has {current_reversals} confirmed reversals; "
                "same-market churn breaker active"
            )
        else:
            block_reason = "Paper CHOP guard allows new exposure"
        state.update(
            currentMarketChoppy=current_choppy,
            currentMarketConfirmedReversals=current_reversals,
            currentMarketImmediateBreakerReversals=IMMEDIATE_CHOP_BREAKER_REVERSALS,
            persistentMarketChoppyReversals=guard.CHOP_GUARD_REVERSALS_PER_MARKET,
            persistentPaused=persistent_paused,
            blockNewEntries=block_new_entries,
            blockReason=block_reason,
            sameMarketImmediateBreaker=True,
            immediateAndPersistentThresholdsSeparated=True,
        )
        return payload


# Keep every layer installed by cross_oracle_strategy_chop_guard and replace only
# the concrete engine class constructed by the existing 8768 sidecar launcher.
guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = ImmediateChopBreakerPaperEngine


def main() -> int:
    return guard.main()


if __name__ == "__main__":
    raise SystemExit(main())
