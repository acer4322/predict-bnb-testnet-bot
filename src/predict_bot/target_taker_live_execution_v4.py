from __future__ import annotations

from typing import Any

from .target_taker_live_execution_v3 import (
    TargetTakerLiveConfig,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V3TargetTakerLiveExecutor,
    _data,
    _market_id,
)


class TargetTakerLiveExecutor(_V3TargetTakerLiveExecutor):
    """V3 executor with exact Predict.fun market metadata resolution.

    The public signal already carries the canonical Predict.fun market ID. For
    live execution, resolve that ID directly instead of relying on list ordering
    or pagination. Any malformed or mismatched response fails closed before
    approvals, signing, or order submission.
    """

    @staticmethod
    def _predict_market(client: Any, market_id: int) -> dict[str, Any]:
        expected = int(market_id)
        if expected <= 0:
            raise TargetTakerLiveError("Predict.fun market ID must be positive")
        payload = client._request("GET", f"/v1/markets/{expected}")
        if not isinstance(payload, dict):
            raise TargetTakerLiveError(
                f"Predict.fun exact market lookup returned invalid payload for {expected}"
            )
        market = _data(payload)
        if not isinstance(market, dict) or _market_id(market) != expected:
            returned = _market_id(market) if isinstance(market, dict) else 0
            raise TargetTakerLiveError(
                f"Predict.fun exact market lookup mismatch: expected {expected}, returned {returned}"
            )
        return market


__all__ = [
    "TargetTakerLiveConfig",
    "TargetTakerLiveError",
    "TargetTakerLiveExecutor",
]
