from __future__ import annotations

import math
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class DrawdownControlDecision:
    allowed: bool
    status: str
    reason: str
    history_ready: bool
    prior_market_count: int
    prior_net_return_bps: float | None
    side_alignment_bps: float | None
    lookback_markets: int
    min_abs_prior_net_return_bps: float
    max_opposed_move_bps: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class MarketRegimeDrawdownController:
    """Causal guard for reversal entries during a strong cross-market move.

    The controller only consumes completed-market returns.  It blocks a new
    signal when both conditions hold:

    * the signal side opposes the current market's start-to-entry move by more
      than ``max_opposed_move_bps``; and
    * the net return of the preceding ``lookback_markets`` completed markets
      is at least ``min_abs_prior_net_return_bps`` in magnitude.

    It does not inspect the current market's winner or any later observation.
    """

    def __init__(
        self,
        *,
        lookback_markets: int = 6,
        min_abs_prior_net_return_bps: float = 20.0,
        max_opposed_move_bps: float = 3.0,
    ) -> None:
        if lookback_markets <= 0:
            raise ValueError("lookback_markets must be positive")
        if not math.isfinite(min_abs_prior_net_return_bps) or min_abs_prior_net_return_bps <= 0:
            raise ValueError("min_abs_prior_net_return_bps must be positive")
        if not math.isfinite(max_opposed_move_bps) or max_opposed_move_bps < 0:
            raise ValueError("max_opposed_move_bps must be non-negative")
        self.lookback_markets = int(lookback_markets)
        self.min_abs_prior_net_return_bps = float(min_abs_prior_net_return_bps)
        self.max_opposed_move_bps = float(max_opposed_move_bps)
        self._completed: deque[tuple[int, float]] = deque(maxlen=self.lookback_markets)

    def record_completed_market(
        self,
        market_id: int,
        *,
        start_price: float,
        end_price: float,
    ) -> bool:
        values = (float(start_price), float(end_price))
        if not all(math.isfinite(value) and value > 0 for value in values):
            return False
        normalized_market_id = int(market_id)
        if any(item[0] == normalized_market_id for item in self._completed):
            return False
        return_bps = (values[1] / values[0] - 1.0) * 10_000.0
        self._completed.append((normalized_market_id, return_bps))
        return True

    def evaluate(
        self,
        *,
        side: str,
        start_price: float,
        spot_price: float,
    ) -> DrawdownControlDecision:
        normalized_side = str(side or "").strip().upper()
        if normalized_side not in {"UP", "DOWN"}:
            raise ValueError("side must be UP or DOWN")
        values = (float(start_price), float(spot_price))
        if not all(math.isfinite(value) and value > 0 for value in values):
            raise ValueError("start_price and spot_price must be finite and positive")

        history_count = len(self._completed)
        history_ready = history_count >= self.lookback_markets
        side_sign = 1.0 if normalized_side == "UP" else -1.0
        side_alignment_bps = (
            (values[1] / values[0] - 1.0) * 10_000.0 * side_sign
        )
        prior_net_return_bps = (
            sum(item[1] for item in self._completed) if history_ready else None
        )
        common = {
            "history_ready": history_ready,
            "prior_market_count": history_count,
            "prior_net_return_bps": prior_net_return_bps,
            "side_alignment_bps": side_alignment_bps,
            "lookback_markets": self.lookback_markets,
            "min_abs_prior_net_return_bps": self.min_abs_prior_net_return_bps,
            "max_opposed_move_bps": self.max_opposed_move_bps,
        }
        if not history_ready:
            return DrawdownControlDecision(
                allowed=False,
                status="BLOCK_NOT_READY",
                reason=(
                    f"completed-market history {history_count}/{self.lookback_markets} "
                    "is incomplete"
                ),
                **common,
            )

        strong_prior_move = (
            abs(float(prior_net_return_bps))
            >= self.min_abs_prior_net_return_bps
        )
        materially_opposed = side_alignment_bps < -self.max_opposed_move_bps
        if strong_prior_move and materially_opposed:
            return DrawdownControlDecision(
                allowed=False,
                status="BLOCK_REGIME_REVERSAL",
                reason=(
                    f"signal opposes the current start move by "
                    f"{abs(side_alignment_bps):.3f} bps while the prior "
                    f"{self.lookback_markets} markets netted "
                    f"{prior_net_return_bps:+.3f} bps"
                ),
                **common,
            )
        return DrawdownControlDecision(
            allowed=True,
            status="ALLOW",
            reason="drawdown-control conditions not jointly present",
            **common,
        )

    def state(self) -> dict[str, Any]:
        return {
            "lookbackMarkets": self.lookback_markets,
            "minAbsPriorNetReturnBps": self.min_abs_prior_net_return_bps,
            "maxOpposedMoveBps": self.max_opposed_move_bps,
            "completedMarkets": [
                {"marketId": market_id, "returnBps": return_bps}
                for market_id, return_bps in self._completed
            ],
        }
