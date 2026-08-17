from __future__ import annotations

import math
from typing import Any

from . import wallet_maker_clone_live as core
from . import wallet_maker_clone_live_v3 as v3base
from . import wallet_maker_clone_live_v7 as base


NO_NEW_ENTRY_SECONDS = 60.0
HARD_CANCEL_SECONDS = 30.0
DEFAULT_MAXIMUM_ENTRY_COUNT = 3
DEFAULT_MAXIMUM_LOSS_USDT = 5.0
MAXIMUM_ENTRY_COUNT_LIMIT = 50


class BoundedRiskPairedWalletMakerCloneEngine(base.PairedCycleWalletMakerCloneEngine):
    """V8: bounded paired cycles with an explicit exposure stop.

    V7 proved useful as a paired-cycle experiment, but it can keep creating a new
    UP+DOWN generation whenever the preceding generation becomes fully filled.
    V8 keeps the paired completion rule and adds operator-defined bounds:

    * target potential profit is configurable again (per outcome order);
    * each market has a configurable maximum number of paired generations;
    * generation N+1 requires BOTH generation N orders to be confirmed FILLED;
    * no new generation is submitted at or after T-60;
    * unfinished/resting clone orders are cancelled at or after T-30;
    * a configurable current-market worst-case settlement loss latches a risk
      stop, pauses runtime, and requests cancellation of all remaining orders.

    The maximum-loss guard is intentionally based only on venue-confirmed fill
    quantities/costs already stored by reconciliation. It is a stop trigger, not
    a guarantee that final loss cannot move beyond the threshold: fills already
    in flight can race a cancellation and one-sided filled inventory cannot be
    undone by cancelling the remaining order.
    """

    VERSION = "WALLET_MAKER_CLONE_LIVE_V8_BOUNDED_PAIRED_RISK"

    def _ensure_defaults(self) -> None:
        # V6 intentionally overwrote target_profit_usdt back to 1.00 on every
        # startup. Bypass that experiment lock while retaining V3's Binance $1
        # venue-floor migration and the normal V1/V2 defaults.
        v3base.MinimumOrderSafeWalletMakerCloneEngine._ensure_defaults(self)
        now = core._now_ms()
        defaults = {
            "maximum_entry_count": str(DEFAULT_MAXIMUM_ENTRY_COUNT),
            "maximum_loss_usdt": f"{DEFAULT_MAXIMUM_LOSS_USDT:.8f}",
            "risk_stop_latched": "0",
            "risk_stop_reason": "",
        }
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_maker_clone_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()
        # V8 is fill-driven. The old V1 cancel-only requote mode must not be
        # mixed into this experiment.
        self._set_setting("auto_requote", "0")

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        try:
            maximum_entries = int(self._setting("maximum_entry_count", str(DEFAULT_MAXIMUM_ENTRY_COUNT)))
        except (TypeError, ValueError):
            maximum_entries = DEFAULT_MAXIMUM_ENTRY_COUNT
        try:
            maximum_loss = float(self._setting("maximum_loss_usdt", f"{DEFAULT_MAXIMUM_LOSS_USDT:.8f}"))
        except (TypeError, ValueError):
            maximum_loss = DEFAULT_MAXIMUM_LOSS_USDT
        settings.update(
            maximumEntryCount=max(1, min(MAXIMUM_ENTRY_COUNT_LIMIT, maximum_entries)),
            maximumLossUsdt=max(0.01, maximum_loss),
            riskStopLatched=self._setting("risk_stop_latched", "0") == "1",
            riskStopReason=self._setting("risk_stop_reason", ""),
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        extra = {"maximumEntryCount", "maximumLossUsdt", "resetRiskStop"}
        unknown_extra = set(values) - (
            {
                "runtimeEnabled",
                "targetPotentialProfitUsdt",
                "bidOffsetTicks",
                "minimumOrderUsdt",
                "maximumOrderUsdt",
                "minimumRemainingSeconds",
                "autoRequote",
                "requoteTicks",
                "maxOrderAgeSeconds",
            }
            | extra
        )
        if unknown_extra:
            raise ValueError("unsupported settings: " + ", ".join(sorted(unknown_extra)))

        if values.get("autoRequote") is True:
            raise ValueError("V8 uses fill-driven paired replenishment; Auto Requote must remain OFF")

        current = self._settings()
        reset_risk = bool(values.get("resetRiskStop", False))
        requested_runtime = values.get("runtimeEnabled")
        if reset_risk and requested_runtime is True:
            raise ValueError("resetRiskStop must be performed while paused; resume in a separate action")
        if requested_runtime is True and current.get("riskStopLatched") and not reset_risk:
            raise ValueError("risk stop is latched; resetRiskStop while paused before resuming")

        if "maximumEntryCount" in values:
            raw_entries = values["maximumEntryCount"]
            if isinstance(raw_entries, bool):
                raise ValueError("maximumEntryCount must be an integer")
            try:
                maximum_entries = int(raw_entries)
            except (TypeError, ValueError) as exc:
                raise ValueError("maximumEntryCount must be an integer") from exc
            try:
                exact_entries = float(raw_entries)
            except (TypeError, ValueError) as exc:
                raise ValueError("maximumEntryCount must be an integer") from exc
            if not math.isfinite(exact_entries) or abs(exact_entries - maximum_entries) > 1e-9:
                raise ValueError("maximumEntryCount must be an integer")
            if not 1 <= maximum_entries <= MAXIMUM_ENTRY_COUNT_LIMIT:
                raise ValueError(
                    f"maximumEntryCount must be between 1 and {MAXIMUM_ENTRY_COUNT_LIMIT}"
                )
        else:
            maximum_entries = int(current["maximumEntryCount"])

        if "maximumLossUsdt" in values:
            try:
                maximum_loss = float(values["maximumLossUsdt"])
            except (TypeError, ValueError) as exc:
                raise ValueError("maximumLossUsdt must be a number") from exc
            if not math.isfinite(maximum_loss) or not 0.01 <= maximum_loss <= 10000.0:
                raise ValueError("maximumLossUsdt must be between 0.01 and 10000")
        else:
            maximum_loss = float(current["maximumLossUsdt"])

        # Bypass V6's fixed-$1 update_settings lock. V3 still applies the
        # Binance Prediction minimum-order invariant and V1/V2 validation.
        base_values = {key: value for key, value in values.items() if key not in extra}
        if base_values:
            v3base.MinimumOrderSafeWalletMakerCloneEngine.update_settings(self, base_values)

        if "maximumEntryCount" in values:
            self._set_setting("maximum_entry_count", maximum_entries)
        if "maximumLossUsdt" in values:
            self._set_setting("maximum_loss_usdt", f"{maximum_loss:.8f}")
        if reset_risk:
            if self._setting("runtime_enabled", "0") == "1":
                raise ValueError("pause V8 before resetting the risk stop")
            self._set_setting("risk_stop_latched", "0")
            self._set_setting("risk_stop_reason", "")
            self._event(
                "WARN",
                "RISK_STOP_RESET_V8",
                None,
                None,
                "operator explicitly reset the V8 risk-stop latch; runtime remains paused",
            )
        self._set_setting("auto_requote", "0")
        return self.snapshot()

    def _plan_replenishment(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        settings = self._settings()
        book = self.books.get(side) or {}
        bid = core._finite(book.get("bestBid"))
        ask = core._finite(book.get("bestAsk"))
        if bid is None or ask is None:
            return None

        precision = max(1, int(market.get("precision") or 2))
        tick = 10 ** (-precision)
        raw = bid - int(settings["bidOffsetTicks"]) * tick
        price = math.floor((raw + 1e-12) / tick) * tick
        price = round(price, precision)
        if price <= 0 or price >= 1:
            return None
        if price + 1e-12 >= ask:
            return {
                "side": side,
                "blocked": True,
                "reason": f"replenishment post-only blocked price {price:.6f} >= ask {ask:.6f}",
                "price": price,
                "bestBid": bid,
                "bestAsk": ask,
            }

        requested_profit = float(settings["targetPotentialProfitUsdt"])
        theoretical_shares = requested_profit / max(1e-9, 1.0 - price)
        theoretical_cost = theoretical_shares * price
        cost = max(float(settings["minimumOrderUsdt"]), theoretical_cost)
        cost = min(float(settings["maximumOrderUsdt"]), cost)
        shares = cost / price
        effective_profit = shares * (1.0 - price)
        return {
            "side": side,
            "blocked": False,
            "price": price,
            "referencePrice": bid,
            "pricingModel": "CURRENT_PASSIVE_BEST_BID_REPLENISH_V8",
            "bestBid": bid,
            "bestAsk": ask,
            "requestedPotentialProfit": requested_profit,
            "targetProfit": effective_profit,
            "theoreticalCostBeforeVenueBounds": theoretical_cost,
            "venueBoundDistorted": abs(effective_profit - requested_profit) > 0.05,
            "plannedCost": cost,
            "plannedShares": shares,
            "tokenId": str(market[f"{side.lower()}_token_id"]),
        }

    def _entry_count(self, pair_id: int) -> int:
        # Count a generation as soon as either side's plan row exists. This is
        # deliberately conservative: an incomplete placement attempt still
        # consumes one entry slot rather than silently allowing an extra retry.
        with self.db_lock:
            row = self.db.execute(
                "SELECT COALESCE(MAX(generation),0) AS generation FROM wallet_maker_clone_orders WHERE pair_id=?",
                (int(pair_id),),
            ).fetchone()
        return int(row["generation"] if row else 0)

    def _pair_exposure(self, pair_id: int) -> dict[str, float]:
        with self.db_lock:
            rows = self.db.execute(
                """SELECT side,
                          COALESCE(filled_usdt_amount,0.0) AS filled_usdt_amount,
                          COALESCE(filled_share_qty,0.0) AS filled_share_qty
                   FROM wallet_maker_clone_orders
                   WHERE pair_id=?""",
                (int(pair_id),),
            ).fetchall()

        total_cost = 0.0
        up_shares = 0.0
        down_shares = 0.0
        for row in rows:
            cost = max(0.0, float(row["filled_usdt_amount"] or 0.0))
            shares = max(0.0, float(row["filled_share_qty"] or 0.0))
            total_cost += cost
            if str(row["side"]).upper() == "UP":
                up_shares += shares
            elif str(row["side"]).upper() == "DOWN":
                down_shares += shares

        pnl_if_up = up_shares - total_cost
        pnl_if_down = down_shares - total_cost
        worst_case_pnl = min(pnl_if_up, pnl_if_down)
        return {
            "totalCostUsdt": total_cost,
            "upShares": up_shares,
            "downShares": down_shares,
            "pnlIfUpUsdt": pnl_if_up,
            "pnlIfDownUsdt": pnl_if_down,
            "worstCasePnlUsdt": worst_case_pnl,
            "worstCaseLossUsdt": max(0.0, -worst_case_pnl),
        }

    def _live_orders(self, pair_id: int) -> list[dict[str, Any]]:
        return [
            row
            for row in self._pair_orders(int(pair_id))
            if row.get("order_id")
            and str(row.get("state") or "") not in base.base.base.base.TERMINAL_ORDER_STATES
        ]

    def _latch_maximum_loss_stop(
        self,
        pair: dict[str, Any],
        exposure: dict[str, float],
        maximum_loss: float,
    ) -> None:
        already_latched = self._setting("risk_stop_latched", "0") == "1"
        reason = (
            f"current-market worst-case settlement loss {exposure['worstCaseLossUsdt']:.6f} USDT "
            f">= configured maximum {maximum_loss:.6f} USDT"
        )
        self._set_setting("risk_stop_latched", "1")
        self._set_setting("risk_stop_reason", reason)
        self._set_setting("runtime_enabled", "0")
        if not already_latched:
            self._event(
                "ERROR",
                "MAXIMUM_LOSS_RISK_STOP_LATCHED_V8",
                int(pair["market_id"]),
                int(pair["id"]),
                reason,
            )
        if self._live_orders(int(pair["id"])):
            if str(pair.get("state") or "") != "CANCEL_PENDING":
                self._cancel_pair(int(pair["id"]), "MAXIMUM_LOSS_RISK_STOP_V8")
            self._final_reconcile_pair(pair)
        self.status = "MAXIMUM_LOSS_STOPPED"

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
                self._event(
                    "ERROR",
                    "NORMAL_LIVE_INTERLOCK_TRIPPED",
                    None,
                    None,
                    str(interlock.get("reason")),
                )
                self._cancel_recorded_active("NORMAL_LIVE_INTERLOCK")
            # Do not leave a cancel request unreconciled merely because the
            # normal-live interlock remains blocked.
            pending_pair = self._current_pair()
            if pending_pair is not None and str(pending_pair.get("state") or "") == "CANCEL_PENDING":
                self._final_reconcile_pair(pending_pair)
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
        settings = self._settings()

        exposure = self._pair_exposure(int(pair["id"]))
        maximum_loss = float(settings["maximumLossUsdt"])
        if settings.get("riskStopLatched"):
            self._latch_maximum_loss_stop(pair, exposure, maximum_loss)
            return
        if exposure["worstCaseLossUsdt"] + 1e-9 >= maximum_loss:
            self._latch_maximum_loss_stop(pair, exposure, maximum_loss)
            return

        now_ms = core._now_ms()
        seconds_left = base.base.base.base.market_seconds_left(market, now_ms)
        orders = self._pair_orders(int(pair["id"]))
        live_orders = self._live_orders(int(pair["id"]))

        # T-30 is an unconditional hard cancellation window for unfinished
        # orders. Cancel request acceptance remains non-terminal; V4's final
        # reconciliation is kept intact to capture late fill races.
        if seconds_left <= HARD_CANCEL_SECONDS:
            if live_orders:
                if str(pair.get("state") or "") != "CANCEL_PENDING":
                    self._cancel_pair(int(pair["id"]), "HARD_T30_CUTOFF_V8")
                self._final_reconcile_pair(pair)
                self.status = "HARD_T30_CANCEL_PENDING"
            else:
                self.status = "HARD_T30_CUTOFF"
            return

        if not settings["runtimeEnabled"]:
            self.status = "PAUSED"
            return

        # T-60 is intentionally different from T-30: existing resting orders are
        # allowed to finish the current complementary cycle, but no fresh pair is
        # ever submitted once this boundary is reached.
        if seconds_left <= NO_NEW_ENTRY_SECONDS:
            self.status = "NO_NEW_ENTRY_AFTER_T60"
            return

        # Keep the legacy operator gate as an optional stricter entry guard. V8's
        # fixed T-60 rule always wins even if this setting is lower than 60.
        if seconds_left < float(settings["minimumRemainingSeconds"]):
            self.status = "WAITING_NEXT_MARKET"
            return

        entry_count = self._entry_count(int(pair["id"]))
        maximum_entries = int(settings["maximumEntryCount"])
        if entry_count >= maximum_entries:
            self.status = "MAX_ENTRY_COUNT_REACHED"
            return

        if not orders:
            if self._complementary_anchor() is None:
                self.status = "WAITING_BOTH_BOOKS"
                return
            # Generation 1 is V5's complementary neutral-anchor pair and uses
            # the now-configurable targetPotentialProfitUsdt setting.
            super()._place_pair(pair, market)
            self.status = "PAIR_ACTIVE"
            return

        latest_up = self._latest_side_order(int(pair["id"]), "UP")
        latest_down = self._latest_side_order(int(pair["id"]), "DOWN")
        ready, reason = base.paired_generation_ready(
            latest_up,
            latest_down,
            now_ms=now_ms,
            max_generations=maximum_entries,
        )
        if ready:
            # _place_replenishment_pair inserts both plan rows before sending the
            # concurrent venue requests, so this consumes exactly one generation
            # / entry slot for the market.
            self._place_replenishment_pair(pair, market)
            return

        self.status = "MAX_ENTRY_COUNT_REACHED" if reason == "GENERATION_CAP_REACHED" else reason

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        settings = self._settings()
        pair = self._current_pair()
        if pair is not None:
            entry_count = self._entry_count(int(pair["id"]))
            exposure = self._pair_exposure(int(pair["id"]))
        else:
            entry_count = 0
            exposure = {
                "totalCostUsdt": 0.0,
                "upShares": 0.0,
                "downShares": 0.0,
                "pnlIfUpUsdt": 0.0,
                "pnlIfDownUsdt": 0.0,
                "worstCasePnlUsdt": 0.0,
                "worstCaseLossUsdt": 0.0,
            }
        payload["currentMarketEntryCount"] = entry_count
        payload["currentMarketExposure"] = exposure
        payload["riskStopReason"] = settings.get("riskStopReason") or None
        payload.setdefault("settings", {}).update(
            maximumEntryCount=int(settings["maximumEntryCount"]),
            maximumLossUsdt=float(settings["maximumLossUsdt"]),
            riskStopLatched=bool(settings["riskStopLatched"]),
        )
        payload.setdefault("rules", {}).update(
            experimentV8="BOUNDED_PAIRED_RISK",
            targetPotentialProfitConfigurableV8=True,
            maximumEntriesEnforcedV8=True,
            requiresComplementBeforeNextEntryV8=True,
            noNewEntrySecondsBeforeEndV8=NO_NEW_ENTRY_SECONDS,
            hardCancelSecondsBeforeEndV8=HARD_CANCEL_SECONDS,
            maximumLossRiskStopV8=True,
            maximumLossMeasureV8="CURRENT_MARKET_CONFIRMED_FILL_WORST_CASE_SETTLEMENT_LOSS",
            riskStopPersistsUntilExplicitResetV8=True,
            riskStopResetDoesNotResumeV8=True,
        )
        return payload


# Patch V7 -> V6 -> V5 -> V4 -> V3 -> V2 -> V1 entrypoint chain.
base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine
base.base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine
base.base.base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine
base.base.base.base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine
base.base.base.base.base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine
base.base.base.base.base.base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine
base.base.base.base.base.base.base.WalletMakerCloneEngine = BoundedRiskPairedWalletMakerCloneEngine


def main() -> int:
    return base.base.base.base.base.base.base.main()


if __name__ == "__main__":
    raise SystemExit(main())
