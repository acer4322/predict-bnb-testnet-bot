from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .pinned_binance_poly_strategy import PinnedBinancePolyDivergenceMixin
from .pinned_force_market_execution import PinnedForceMarketExecutionMixin
from .pinned_passive_telemetry import PinnedPassiveTelemetryMixin
from .pinned_signed_edge_guard import PinnedSignedEdgeGuardMixin
from .poly_gap_live_v44 import IntegratedDecisionAndIdempotencyPolyGapLiveEngine


RETIREMENT_KEY = "legacy_execution_retired_to_8781_v1"


class PinnedStrategySelectablePolyGapLiveEngine(
    PinnedPassiveTelemetryMixin,
    PinnedForceMarketExecutionMixin,
    PinnedSignedEdgeGuardMixin,
    PinnedBinancePolyDivergenceMixin,
    IntegratedDecisionAndIdempotencyPolyGapLiveEngine,
):
    """V45 compatibility service with Echtgeld execution permanently retired to 8781.

    The historical detector/telemetry/state remains available because other services
    still depend on this process. New BUY/SELL placement is intentionally impossible:
    8781 is the only Echtgeld execution, pause/resume and risk-control authority.
    """

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        first_retirement = self._setting(RETIREMENT_KEY, "0") != "1"
        self._set_setting("runtime_enabled", "0")
        self._set_setting(RETIREMENT_KEY, "1")
        if first_retirement:
            self._event(
                "WARN",
                "LEGACY_EXECUTION_RETIRED_TO_8781",
                None,
                None,
                "8769 legacy Echtgeld placement permanently retired; 8781 is the only execution authority",
            )

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        # Fail closed even if somebody manually flips the old SQLite bit back to 1.
        settings["runtimeEnabled"] = False
        settings["executionRetiredTo8781"] = True
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("runtimeEnabled") is True:
            raise ValueError("legacy 8769 execution is retired; resume Echtgeld only through 8781")
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
        # No venue write is allowed from this legacy process. Any legacy position
        # unexpectedly present here must be reconciled explicitly into the 8781 path.
        self.status = "RETIRED_TO_8781_LEGACY_POSITION_REQUIRES_RECONCILIATION"
        self.last_error = (
            f"legacy round {row.get('id')} attempted an exit after execution retirement; "
            "venue write blocked; reconcile through 8781"
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V45_RETIRED_TO_8781"
        payload["historicalRealMoney"] = True
        payload["realMoney"] = False
        payload["configuredMasterEnabled"] = bool(base.MASTER_ENABLED)
        payload["masterEnabled"] = False
        payload["executionAuthority"] = "8781_ONLY"
        payload["legacyExecutionRetired"] = True
        payload.setdefault("rules", {}).update(
            v45PinnedDivergenceSelectable=True,
            v45PinnedSignedEdgeMustPersist=True,
            v45PinnedShotgunSuppressed=True,
            v45PinnedPassiveTelemetry=True,
            v44ExecutionPolicyPreservedByV45=True,
            legacyVenueWritesDisabled=True,
            runtimeResumeRejected=True,
            persistedRuntimeBitIgnored=True,
            echtgeldAuthorityPort=8781,
        )
        return payload


base.PolyGapLiveEngine = PinnedStrategySelectablePolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
