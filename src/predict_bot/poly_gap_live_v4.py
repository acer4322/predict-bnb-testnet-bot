from __future__ import annotations

from typing import Any

from . import poly_gap_live as base
from .poly_gap_live_v3 import SignalGenerationPolyGapLiveEngine


class ImmediateRiskPolyGapLiveEngine(SignalGenerationPolyGapLiveEngine):
    """V4: re-evaluate the dedicated loss cap immediately on settings changes.

    Risk settings are committed and checked before a requested runtime resume is
    applied. This closes the small race where an operator lowers the maximum-loss
    cap below the already-realized loss while simultaneously resuming trading.
    """

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        requested = dict(values)
        runtime_present = "runtimeEnabled" in requested
        runtime_enabled = requested.pop("runtimeEnabled", None)

        # Apply stake/risk/reset changes first. The parent remains the only
        # validator/persistence implementation for these settings.
        if requested:
            super().update_settings(requested)

        # A lower/newly-enabled cap must trip before any new round can be armed.
        self._check_max_loss()

        if runtime_present:
            # Parent refuses a resume when loss_tripped is set or the master
            # real-money environment switch is off.
            super().update_settings({"runtimeEnabled": bool(runtime_enabled)})

        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_GAP_DEDICATED_LIVE_V4"
        payload["lossGuard"]["settingsApplyIsImmediate"] = True
        return payload


base.PolyGapLiveEngine = ImmediateRiskPolyGapLiveEngine


def main() -> int:
    return base.main()


if __name__ == "__main__":
    raise SystemExit(main())
