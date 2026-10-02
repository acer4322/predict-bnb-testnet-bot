from __future__ import annotations

from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_predict_direct_v8_2 as base
from .wallet_maker_clone_pair_spread_gate import PairMakerSpreadV84Mixin


RETIREMENT_KEY = "legacy_execution_retired_to_8781_v1"


class PairMakerSpreadPredictDirectV84WalletMakerCloneEngine(
    PairMakerSpreadV84Mixin,
    base.PredictDirectV82WalletMakerCloneEngine,
):
    VERSION = "WALLET_MAKER_CLONE_PREDICT_DIRECT_V8_4_RETIRED_TO_8781"

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
                f"{core.ASSET} Predict-direct maker clone new-order execution retired; 8781 is authoritative",
            )

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings["runtimeEnabled"] = False
        settings["executionRetiredTo8781"] = True
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if values.get("runtimeEnabled") is True:
            raise ValueError(
                f"legacy {core.ASSET} Predict-direct clone execution is retired; resume Echtgeld only through 8781"
            )
        if "runtimeEnabled" in values:
            values = dict(values)
            values["runtimeEnabled"] = False
        return super().update_settings(values)

    def _place_pair(self, pair: dict[str, Any], market: dict[str, Any]) -> None:
        self.status = "RETIRED_TO_8781"
        return

    def snapshot(self):
        payload = super().snapshot()
        payload["executionVenue"] = "PREDICT_DIRECT"
        payload["historicalExecutionPath"] = "PREDICT_NATIVE_POST_ONLY_LIMIT_PASSIVE_MAKER_SPREAD_V84"
        payload["executionPath"] = "RETIRED_TO_8781_NO_NEW_ORDERS"
        payload["historicalRealMoney"] = True
        payload["realMoney"] = False
        payload["configuredMasterEnabled"] = bool(core.MASTER_ENABLED)
        payload["masterEnabled"] = False
        payload["executionAuthority"] = "8781_ONLY"
        payload["legacyExecutionRetired"] = True
        payload.setdefault("rules", {}).update(
            legacyNewOrdersDisabled=True,
            runtimeResumeRejected=True,
            persistedRuntimeBitIgnored=True,
            historicalOrderReconciliationStillAllowed=True,
            historicalOrderCancellationStillAllowed=True,
            echtgeldAuthorityPort=8781,
        )
        return payload


def main() -> int:
    engine = PairMakerSpreadPredictDirectV84WalletMakerCloneEngine()
    engine.start()
    handler = type("PairMakerSpreadPredictDirectV84WalletMakerCloneHandler", (core._Handler,), {"engine": engine})
    server = core.ThreadingHTTPServer((core.HOST, core.PORT), handler)
    print(
        f"{engine.VERSION} {core.ASSET} listening on http://{core.HOST}:{core.PORT}/state; "
        f"venue=PREDICT_DIRECT; execution=RETIRED_TO_8781; db={core.DB_PATH}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        return 130
    finally:
        server.shutdown()
        server.server_close()
        engine.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
