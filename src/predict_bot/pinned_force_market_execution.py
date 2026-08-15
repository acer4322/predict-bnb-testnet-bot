from __future__ import annotations

from typing import Any

from .pinned_binance_poly_strategy import ENTRY_MODE_PINNED_DIVERGENCE


class PinnedForceMarketExecutionMixin:
    """Suppress creation of new Shotgun GTC entries while pinned mode is selected."""

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        if str(settings.get("entryStrategyMode") or "") == ENTRY_MODE_PINNED_DIVERGENCE:
            settings["shotgunEnabled"] = False
            settings["shotgunSuppressedByPinnedDivergence"] = True
        else:
            settings["shotgunSuppressedByPinnedDivergence"] = False
        return settings
