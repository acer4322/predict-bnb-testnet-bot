from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import PinnedBinancePolyDivergenceMixin
from .poly_gap_multi_asset_live_v2 import LiveGradeMultiAssetPolyGapLiveEngine


SAFE_PAUSE_MIGRATION_KEY = "multi_asset_live_v3_safe_pause_migrated"


class PinnedMultiAssetPolyGapLiveEngine(
    PinnedBinancePolyDivergenceMixin,
    LiveGradeMultiAssetPolyGapLiveEngine,
):
    """V3: live-capable ETH/BNB with selectable pinned-divergence entry mode.

    The first V3 startup force-pauses new entries once, even when an older asset
    database accidentally persisted runtime_enabled=1.  Operators must explicitly
    Resume Echtgeld in Dashboard V2 after reviewing the new strategy/settings.
    Existing positions are still reconciled/managed by the inherited engine.
    """

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        if self._setting(SAFE_PAUSE_MIGRATION_KEY, "0") != "1":
            self._set_setting("runtime_enabled", "0")
            self._set_setting(SAFE_PAUSE_MIGRATION_KEY, "1")
            self._event(
                "WARN",
                "MULTI_ASSET_V3_SAFE_PAUSE",
                None,
                None,
                f"{self.asset} V3 enabled live capability but force-paused new entries; "
                "explicit Dashboard V2 Resume is required",
            )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_MULTI_ASSET_LIVE_V3"
        payload["multiAssetLiveV3"] = {
            "enabled": True,
            "asset": self.asset,
            "safePauseMigrationApplied": self._setting(SAFE_PAUSE_MIGRATION_KEY, "0") == "1",
            "explicitRuntimeResumeRequired": True,
            "pinnedDivergenceSelectable": True,
            "observerVersionRequired": "MULTI_PREDICTION_OBSERVER_V2",
        }
        payload.setdefault("rules", {}).update(
            multiAssetLiveV3=True,
            pinnedDivergenceSelectableV3=True,
            firstV3StartupForcePausesNewEntries=True,
        )
        return payload


base.PolyGapLiveEngine = PinnedMultiAssetPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
