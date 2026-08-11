from __future__ import annotations

from typing import Any

from . import wallet_maker_clone_live_v2 as base


BINANCE_PREDICTION_MIN_ORDER_USDT = 1.0


class MinimumOrderSafeWalletMakerCloneEngine(base.SafeWalletMakerCloneEngine):
    """V3: enforce Binance Prediction's hard 1 USDT minimum order value.

    V1 already defaulted ``minimum_order_usdt`` to 1 USDT, but the setting
    validator still allowed values below 1.  That is unsafe because low-price
    payoff-normalized quotes can otherwise produce a syntactically valid local
    plan that Binance will reject.  V3 makes the venue minimum a hard backend
    invariant, migrates any stale sub-1 persisted value, and keeps the final
    planned cost at or above 1 USDT even if an old database is loaded.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V3"

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        try:
            minimum = float(self._setting("minimum_order_usdt", "1.0"))
        except (TypeError, ValueError):
            minimum = 0.0
        if minimum < BINANCE_PREDICTION_MIN_ORDER_USDT:
            self._set_setting(
                "minimum_order_usdt",
                f"{BINANCE_PREDICTION_MIN_ORDER_USDT:.8f}",
            )
            self._event(
                "WARN",
                "CLONE_MIN_ORDER_MIGRATED_V3",
                None,
                None,
                "minimum order raised to Binance Prediction hard floor: 1.00 USDT",
            )

        try:
            maximum = float(self._setting("maximum_order_usdt", "25.0"))
        except (TypeError, ValueError):
            maximum = 0.0
        if maximum < BINANCE_PREDICTION_MIN_ORDER_USDT:
            self._set_setting(
                "maximum_order_usdt",
                f"{BINANCE_PREDICTION_MIN_ORDER_USDT:.8f}",
            )

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        settings["minimumOrderUsdt"] = max(
            BINANCE_PREDICTION_MIN_ORDER_USDT,
            float(settings.get("minimumOrderUsdt") or BINANCE_PREDICTION_MIN_ORDER_USDT),
        )
        settings["maximumOrderUsdt"] = max(
            BINANCE_PREDICTION_MIN_ORDER_USDT,
            float(settings.get("maximumOrderUsdt") or BINANCE_PREDICTION_MIN_ORDER_USDT),
        )
        settings["venueMinimumOrderUsdt"] = BINANCE_PREDICTION_MIN_ORDER_USDT
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        if "minimumOrderUsdt" in values:
            try:
                minimum = float(values["minimumOrderUsdt"])
            except (TypeError, ValueError) as exc:
                raise ValueError("minimumOrderUsdt must be a number") from exc
            if minimum < BINANCE_PREDICTION_MIN_ORDER_USDT:
                raise ValueError(
                    "Binance Prediction requires every order to be at least 1.00 USDT"
                )
        if "maximumOrderUsdt" in values:
            try:
                maximum = float(values["maximumOrderUsdt"])
            except (TypeError, ValueError) as exc:
                raise ValueError("maximumOrderUsdt must be a number") from exc
            if maximum < BINANCE_PREDICTION_MIN_ORDER_USDT:
                raise ValueError(
                    "maximumOrderUsdt cannot be below Binance Prediction's 1.00 USDT minimum"
                )
        return super().update_settings(values)

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        plan = super()._plan_order(side, market)
        if not isinstance(plan, dict) or plan.get("blocked") is True:
            return plan
        price = float(plan.get("price") or 0.0)
        if price <= 0.0:
            return None

        # Defense in depth: an old/corrupt setting must never create a Binance
        # order below the venue floor.  Recompute shares after lifting cost.
        cost = max(
            BINANCE_PREDICTION_MIN_ORDER_USDT,
            float(plan.get("plannedCost") or 0.0),
        )
        plan["plannedCost"] = cost
        plan["plannedShares"] = cost / price
        plan["targetProfit"] = float(plan["plannedShares"]) * (1.0 - price)
        return plan

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        payload.setdefault("rules", {}).update(
            binancePredictionMinimumOrderUsdt=BINANCE_PREDICTION_MIN_ORDER_USDT,
            minimumOrderHardFloorV3=True,
        )
        return payload


base.WalletMakerCloneEngine = MinimumOrderSafeWalletMakerCloneEngine


def main() -> int:
    return base.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
