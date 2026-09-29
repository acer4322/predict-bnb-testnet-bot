from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import PinnedBinancePolyDivergenceMixin
from .pinned_force_market_execution import PinnedForceMarketExecutionMixin
from .pinned_passive_telemetry import PinnedPassiveTelemetryMixin
from .pinned_signed_edge_guard import PinnedSignedEdgeGuardMixin
from .poly_gap_multi_asset_live_v2 import LiveGradeMultiAssetPolyGapLiveEngine


SAFE_PAUSE_MIGRATION_KEY = "multi_asset_live_v3_safe_pause_migrated"
RETIREMENT_KEY = "legacy_execution_retired_to_8781_v1"


class PinnedMultiAssetPolyGapLiveEngine(
    PinnedPassiveTelemetryMixin,
    PinnedForceMarketExecutionMixin,
    PinnedSignedEdgeGuardMixin,
    PinnedBinancePolyDivergenceMixin,
    LiveGradeMultiAssetPolyGapLiveEngine,
):
    """V3 compatibility service with ETH/BNB Echtgeld execution retired to 8781.

    The service stays online because downstream observers/diagnostics still depend on
    it. Its historical state and market telemetry remain readable, but it can no
    longer resume or place BUY/SELL orders. 8781 owns all Echtgeld execution and risk.
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
                f"{self.asset} V3 force-paused new entries",
            )
        first_retirement = self._setting(RETIREMENT_KEY, "0") != "1"
        self._set_setting("runtime_enabled", "0")
        self._set_setting(RETIREMENT_KEY, "1")
        if first_retirement:
            self._event(
                "WARN",
                "LEGACY_EXECUTION_RETIRED_TO_8781",
                None,
                None,
                f"{self.asset} 8772/8773 legacy Echtgeld placement permanently retired; 8781 is authoritative",
            )

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings["runtimeEnabled"] = False
        settings["executionRetiredTo8781"] = True
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("runtimeEnabled") is True:
            raise ValueError(
                f"legacy {self.asset} execution is retired; resume Echtgeld only through 8781"
            )
        if "runtimeEnabled" in values:
            values = dict(values)
            values["runtimeEnabled"] = False
        return super().update_settings(values)

    def _entry_allowed(self) -> bool:
        return False

    def _open_round(self, row: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        self.status = "RETIRED_TO_8781"
        self._update_round(
            int(row["id"]),
            state="REJECTED",
            close_reason="LEGACY_EXECUTION_RETIRED_TO_8781",
        )

    def _exit_round(self, row: dict[str, Any], *args: Any, **kwargs: Any) -> None:
        self.status = "RETIRED_TO_8781_LEGACY_POSITION_REQUIRES_RECONCILIATION"
        self.last_error = (
            f"legacy {self.asset} round {row.get('id')} attempted an exit after execution retirement; "
            "venue write blocked; reconcile through 8781"
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_MULTI_ASSET_LIVE_V3_RETIRED_TO_8781"
        payload["historicalRealMoney"] = True
        payload["realMoney"] = False
        payload["configuredMasterEnabled"] = bool(base.MASTER_ENABLED)
        payload["masterEnabled"] = False
        payload["executionAuthority"] = "8781_ONLY"
        payload["legacyExecutionRetired"] = True
        payload["multiAssetLiveV3"] = {
            "enabled": True,
            "asset": self.asset,
            "safePauseMigrationApplied": self._setting(SAFE_PAUSE_MIGRATION_KEY, "0") == "1",
            "explicitRuntimeResumeRequired": False,
            "executionRetiredTo8781": True,
            "pinnedDivergenceSelectable": True,
            "pinnedSignedEdgeMustPersist": True,
            "pinnedShotgunSuppressed": True,
            "pinnedPassiveTelemetry": True,
            "observerVersionRequired": "MULTI_PREDICTION_OBSERVER_V2",
        }
        payload.setdefault("rules", {}).update(
            multiAssetLiveV3=True,
            pinnedDivergenceSelectableV3=True,
            pinnedSignedEdgeMustPersistV3=True,
            pinnedShotgunSuppressedV3=True,
            pinnedPassiveTelemetryV3=True,
            firstV3StartupForcePausesNewEntries=True,
            legacyVenueWritesDisabled=True,
            runtimeResumeRejected=True,
            persistedRuntimeBitIgnored=True,
            echtgeldAuthorityPort=8781,
        )
        return payload


base.PolyGapLiveEngine = PinnedMultiAssetPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
