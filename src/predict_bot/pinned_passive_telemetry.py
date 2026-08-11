from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import ENTRY_MODE_PINNED_DIVERGENCE


class PinnedPassiveTelemetryMixin:
    """Evaluate the detector from read-only snapshots even when POLY_GAP is selected."""

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        settings = self._settings()
        if (
            str(settings.get("entryStrategyMode") or "") != ENTRY_MODE_PINNED_DIVERGENCE
            and self._current_active_round() is None
        ):
            last = self._last_pinned_evaluation
            checked = int((last or {}).get("checkedAtMs") or 0) if isinstance(last, dict) else 0
            if base._now_ms() - checked >= 750:
                try:
                    # Through this MRO the Pinned mixin sees POLY_GAP mode and
                    # returns the inherited fresh Poly signal without gating it.
                    raw_poly = super()._poly_state()
                    if isinstance(raw_poly, dict):
                        self._last_pinned_evaluation = self._evaluate_pinned_divergence(raw_poly)
                except Exception as exc:
                    self._last_pinned_evaluation = {
                        "strategy": "R_PINNED_BINANCE_POLY_DIVERGENCE",
                        "asset": self._asset_name_for_pin(),
                        "checkedAtMs": base._now_ms(),
                        "allowed": False,
                        "state": "TELEMETRY_ERROR",
                        "reason": str(exc)[:300],
                    }
            pinned = payload.get("pinnedDivergence")
            if isinstance(pinned, dict):
                pinned["lastEvaluation"] = self._last_pinned_evaluation
                pinned["passiveTelemetryWhilePolyGapSelected"] = True
        return payload
