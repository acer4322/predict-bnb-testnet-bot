from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import ENTRY_MODE_PINNED_DIVERGENCE


class PinnedPassiveTelemetryMixin:
    """Keep the detector observable from read-only snapshots, including PAUSED runtime."""

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        if self._current_active_round() is None:
            last = self._last_pinned_evaluation
            checked = int((last or {}).get("checkedAtMs") or 0) if isinstance(last, dict) else 0
            if base._now_ms() - checked >= 750:
                try:
                    settings = self._settings()
                    observed_poly = super()._poly_state()
                    # In PINNED_DIVERGENCE mode the Pinned mixin above already
                    # evaluates and stores the detector while gating the returned
                    # direction. In ordinary POLY_GAP mode it returns the raw
                    # inherited Poly signal, so evaluate passively here.
                    if (
                        str(settings.get("entryStrategyMode") or "") != ENTRY_MODE_PINNED_DIVERGENCE
                        and isinstance(observed_poly, dict)
                    ):
                        self._last_pinned_evaluation = self._evaluate_pinned_divergence(observed_poly)
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
                pinned["passiveTelemetryWhileRuntimePaused"] = True
        return payload
