from __future__ import annotations

import math
from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v4 as base


class NeutralAnchorWalletMakerCloneEngine(base.FinalFillSafeWalletMakerCloneEngine):
    """V5 experiment: neutral 0.50/0.50 complementary anchor, no opening delay/spread veto.

    V4's cancel-finality fixes remain active, but the opening warm-up and wide-book
    blocker are intentionally not used in this experiment. Instead the two outcome
    books are combined into one complementary fair anchor. A symmetric 0.01/0.99
    opening shell therefore produces a 0.50/0.50 reference rather than copying the
    0.01 bid. As the books become informative, the anchor follows their paired mids.

    V5 deliberately still submits only one parent order per side per market. Before
    enabling multi-parent replenishment we profile makerHash fragmentation, because
    one Binance parent order can produce many small Predict match legs.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V5_NEUTRAL_ANCHOR"

    def _complementary_anchor(self) -> dict[str, float] | None:
        up = self.books.get("UP") or {}
        down = self.books.get("DOWN") or {}
        up_bid = core._finite(up.get("bestBid"))
        up_ask = core._finite(up.get("bestAsk"))
        down_bid = core._finite(down.get("bestBid"))
        down_ask = core._finite(down.get("bestAsk"))
        if None in {up_bid, up_ask, down_bid, down_ask}:
            return None
        assert up_bid is not None and up_ask is not None
        assert down_bid is not None and down_ask is not None
        if not (0 < up_bid < up_ask < 1 and 0 < down_bid < down_ask < 1):
            return None

        up_mid = (up_bid + up_ask) / 2.0
        down_mid = (down_bid + down_ask) / 2.0
        fair_up = (up_mid + (1.0 - down_mid)) / 2.0
        fair_up = min(0.99, max(0.01, fair_up))
        return {"UP": fair_up, "DOWN": 1.0 - fair_up}

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        settings = self._settings()
        anchor = self._complementary_anchor()
        if anchor is None:
            return None
        book = self.books.get(side) or {}
        bid = core._finite(book.get("bestBid"))
        ask = core._finite(book.get("bestAsk"))
        if bid is None or ask is None:
            return None

        precision = max(1, int(market.get("precision") or 2))
        tick = 10 ** (-precision)
        reference = float(anchor[side])
        raw = reference - int(settings["bidOffsetTicks"]) * tick
        passive_ceiling = ask - tick
        price = min(raw, passive_ceiling)
        price = max(price, bid)
        price = math.floor((price + 1e-12) / tick) * tick
        price = round(price, precision)
        if price <= 0 or price >= 1 or price + 1e-12 >= ask:
            return {
                "side": side,
                "blocked": True,
                "reason": f"neutral-anchor post-only blocked price {price:.6f} >= ask {ask:.6f}",
                "price": price,
                "bestBid": bid,
                "bestAsk": ask,
            }

        target_profit = float(settings["targetPotentialProfitUsdt"])
        shares = target_profit / max(1e-9, 1.0 - price)
        cost = shares * price
        cost = max(float(settings["minimumOrderUsdt"]), cost)
        cost = min(float(settings["maximumOrderUsdt"]), cost)
        shares = cost / price
        return {
            "side": side,
            "blocked": False,
            "price": price,
            "referencePrice": reference,
            "pricingModel": "COMPLEMENTARY_MID_NEUTRAL_0P5",
            "bestBid": bid,
            "bestAsk": ask,
            "targetProfit": shares * (1.0 - price),
            "plannedCost": cost,
            "plannedShares": shares,
            "tokenId": str(market[f"{side.lower()}_token_id"]),
        }

    def _tick(self) -> None:
        settings = self._settings()
        if not core.MASTER_ENABLED:
            self.status = "MASTER_DISABLED"
            return
        if not self._ensure_client():
            return

        interlock = self._normal_live_conflict()
        if interlock.get("blocked") is True:
            if settings["runtimeEnabled"]:
                self._set_setting("runtime_enabled", "0")
                self._event("ERROR", "NORMAL_LIVE_INTERLOCK_TRIPPED", None, None, str(interlock.get("reason")))
                self._cancel_recorded_active("NORMAL_LIVE_INTERLOCK")
            self.status = "BLOCKED_NORMAL_LIVE"
            return

        market = self._prime_market()
        if market is None:
            self.status = "WAITING_MARKET"
            return
        self._poll_books(market)

        pair = self._current_pair()
        if pair is not None and int(pair["market_id"]) != int(market["market_id"]):
            if str(pair.get("state") or "") != "CANCEL_PENDING":
                self._cancel_pair(int(pair["id"]), "MARKET_ROLLOVER")
            if not self._final_reconcile_pair(pair):
                self.status = "ROLLOVER_RECONCILING_FINAL_FILLS"
                return
            self._set_pair(int(pair["id"]), state="ROLLED", close_reason="MARKET_ROLLOVER")
            pair = None

        if pair is None:
            pair = self._new_pair(market)

        if str(pair.get("state") or "") == "CANCEL_PENDING":
            if not self._final_reconcile_pair(pair):
                self.status = "CANCEL_PENDING_RECONCILIATION"
                return
            pair = self._current_pair() or pair

        self._reconcile_pair(pair, market)
        pair = self._current_pair() or pair

        now_ms = core._now_ms()
        seconds_left = base.market_seconds_left(market, now_ms)
        live_orders = [
            row
            for row in self._pair_orders(int(pair["id"]))
            if row.get("order_id") and str(row.get("state") or "") not in base.TERMINAL_ORDER_STATES
        ]
        if seconds_left <= base.RESTING_CANCEL_SECONDS and live_orders:
            self._cancel_pair(int(pair["id"]), "PRE_SETTLEMENT_CUTOFF_V5")
            self._final_reconcile_pair(pair)
            self.status = "PRE_SETTLEMENT_CANCEL_PENDING"
            return

        if not settings["runtimeEnabled"]:
            self.status = "PAUSED"
            return
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return

        orders = self._pair_orders(int(pair["id"]))
        if not orders and str(pair.get("state") or "") in {"READY", "CANCELED"}:
            if self._complementary_anchor() is None:
                self.status = "WAITING_BOTH_BOOKS"
                return
            self._place_pair(pair, market)
            self.status = "PAIR_ACTIVE"
            return
        self.status = "PAIR_ACTIVE"

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        payload.setdefault("rules", {}).update(
            openingWarmupEnabledV5=False,
            wideBookSpreadVetoEnabledV5=False,
            pricingReferenceV5="COMPLEMENTARY_MID_NEUTRAL_0P5",
            parentOrdersPerSidePerMarketV5=1,
            makerHashFragmentationStudyRequiredBeforeMultiParent=True,
        )
        return payload


base.WalletMakerCloneEngine = NeutralAnchorWalletMakerCloneEngine
base.base.WalletMakerCloneEngine = NeutralAnchorWalletMakerCloneEngine
base.base.base.WalletMakerCloneEngine = NeutralAnchorWalletMakerCloneEngine
base.base.base.base.WalletMakerCloneEngine = NeutralAnchorWalletMakerCloneEngine


def main() -> int:
    return base.base.base.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
