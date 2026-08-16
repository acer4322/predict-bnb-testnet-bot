from __future__ import annotations

from typing import Any

from . import multi_prediction_observer as observer_base
from . import poly_fast_live as fast_base
from . import poly_fast_signal_v9 as v9

ASSETS = v9.ASSETS
HOST = v9.HOST
PORT = v9.PORT
ROOT = v9.ROOT
DB_PATH = v9.DB_PATH
DISABLED_ENTRY_ASSETS = {"BNB"}
ENABLED_ENTRY_ASSETS = tuple(asset for asset in ASSETS if asset not in DISABLED_ENTRY_ASSETS)


class AssetGuardedSignalEvaluator(v9.SafeRejectedEntryRearmEvaluator):
    """V9 lifecycle with BNB entry disabled fail-closed.

    BNB observation/lifecycle remains alive so an already-open BNB round can still
    emit its risk-reducing reversal exit. Only new BNB ENTRY intents are blocked.
    """

    def _evaluate_and_forward(self) -> None:
        if self.asset not in DISABLED_ENTRY_ASSETS:
            return super()._evaluate_and_forward()

        # Keep lifecycle polling and EXIT management operational for a pre-existing
        # BNB position. Never strand an already-open position just because new
        # entries are disabled.
        market = self._prime_market(force=False)
        if not isinstance(market, dict):
            return
        poly_state = self._gap_poly_state()
        active = self._active_round()
        if isinstance(active, dict):
            self._maybe_exit(active, poly_state)
            return

        self.last_gap_evaluation = {
            "allowed": False,
            "state": "BLOCKED_ASSET_DISABLED",
            "strategy": "R_POLY_GAP_SCALP_LIVE",
            "asset": self.asset,
            "reason": "BNB new Poly entries are temporarily disabled; observation and risk-reducing exits remain enabled",
            "entryDisabled": True,
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["assetEntryPolicy"] = {
            "asset": self.asset,
            "entryEnabled": self.asset not in DISABLED_ENTRY_ASSETS,
            "exitEnabledForExistingRound": True,
            "disabledReason": (
                "TEMPORARY_BNB_ENTRY_BLOCK"
                if self.asset in DISABLED_ENTRY_ASSETS
                else None
            ),
        }
        return payload


class PolyFastSignalRuntimeV10(v9.PolyFastSignalRuntimeV9):
    def __init__(self) -> None:
        self.observer = v9.v8.v4.v3.SelfContainedFastObserver(db_path=DB_PATH)
        self.engines = {
            asset: AssetGuardedSignalEvaluator(
                self.observer,
                asset=asset,
                db_path=ROOT / "data" / f"poly_fast_signal_{asset.lower()}.db",
            )
            for asset in ASSETS
        }

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = "POLY_FAST_SIGNAL_V10_BNB_ENTRY_BLOCK"
        payload["architecture"].update(
            entryAssets=list(ENABLED_ENTRY_ASSETS),
            disabledEntryAssets=sorted(DISABLED_ENTRY_ASSETS),
            disabledAssetsStillObserveForExit=True,
        )
        return payload


class _Handler(fast_base._Handler):
    runtime: PolyFastSignalRuntimeV10


def main() -> int:
    runtime = PolyFastSignalRuntimeV10()
    runtime.start()
    handler = type("PolyFastSignalV10Handler", (_Handler,), {"runtime": runtime})
    server = fast_base.ThreadingHTTPServer((HOST, PORT), handler)
    print(
        f"Poly Fast Signal V10 listening on http://{HOST}:{PORT}/state; "
        f"entry-assets={','.join(ENABLED_ENTRY_ASSETS)}; BNB-entry=blocked; "
        "existing-BNB-exit=enabled; Echtgeld=8781-only",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.10)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        runtime.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
