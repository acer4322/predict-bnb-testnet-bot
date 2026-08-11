from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import PinnedBinancePolyDivergenceMixin
from .pinned_signed_edge_guard import PinnedSignedEdgeGuardMixin
from .poly_gap_live_v44 import IntegratedDecisionAndIdempotencyPolyGapLiveEngine


class PinnedStrategySelectablePolyGapLiveEngine(
    PinnedSignedEdgeGuardMixin,
    PinnedBinancePolyDivergenceMixin,
    IntegratedDecisionAndIdempotencyPolyGapLiveEngine,
):
    """V45: selectable normal Poly GAP or pinned-Binance/strong-Poly entry.

    Entry signal admission is the only policy added here.  Every actual order,
    exit, TAKE_PROFIT lock, source-age guard and idempotency path remains inherited
    from V44 and its V40-V43 lineage. Pinned entries additionally require the
    configured strong edge to survive the signed BUY quote before placement.
    """

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V45"
        payload.setdefault("rules", {}).update(
            v45PinnedDivergenceSelectable=True,
            v45PinnedSignedEdgeMustPersist=True,
            v44ExecutionPolicyPreservedByV45=True,
        )
        return payload


base.PolyGapLiveEngine = PinnedStrategySelectablePolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
