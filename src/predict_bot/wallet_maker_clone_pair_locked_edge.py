from __future__ import annotations

import math
from typing import Any

from . import wallet_maker_clone_live as core


DEFAULT_MAXIMUM_COMBINED_PRICE = 0.98
DEFAULT_MINIMUM_LOCKED_RETURN_PCT = 1.0


def apply_equal_share_locked_edge(
    plans: dict[str, dict[str, Any] | None],
    *,
    minimum_order_usdt: float,
    maximum_order_usdt: float,
    maximum_combined_price: float,
    minimum_locked_return_pct: float,
) -> dict[str, dict[str, Any] | None]:
    """Equalize UP/DOWN shares and block pairs without sufficient locked edge.

    This is deliberately a pre-submission gross-settlement gate.  For equal
    quantities q on complementary binary outcomes:

        total_cost = q * (up_price + down_price)
        settlement = q
        locked_profit = q - total_cost

    The function preserves any earlier venue/post-only block.  It never turns a
    blocked raw plan back into an executable one.
    """

    output: dict[str, dict[str, Any] | None] = {
        side: (dict(plan) if isinstance(plan, dict) else None)
        for side, plan in plans.items()
    }
    up = output.get("UP")
    down = output.get("DOWN")
    if not isinstance(up, dict) or not isinstance(down, dict):
        return output
    if bool(up.get("blocked")) or bool(down.get("blocked")):
        return output

    try:
        up_price = float(up["price"])
        down_price = float(down["price"])
        raw_up_shares = max(0.0, float(up.get("plannedShares") or 0.0))
        raw_down_shares = max(0.0, float(down.get("plannedShares") or 0.0))
    except (KeyError, TypeError, ValueError):
        return output

    if not all(math.isfinite(value) for value in (up_price, down_price, raw_up_shares, raw_down_shares)):
        return output
    if not (0.0 < up_price < 1.0 and 0.0 < down_price < 1.0):
        return output

    minimum_cost = max(0.0, float(minimum_order_usdt))
    maximum_cost = max(minimum_cost, float(maximum_order_usdt))
    q_min = max(minimum_cost / up_price, minimum_cost / down_price)
    q_max = min(maximum_cost / up_price, maximum_cost / down_price)
    raw_target_q = max(raw_up_shares, raw_down_shares, q_min)

    sizing_block = q_min > q_max + 1e-12
    q = q_min if sizing_block else min(max(raw_target_q, q_min), q_max)
    up_cost = q * up_price
    down_cost = q * down_price
    total_cost = up_cost + down_cost
    combined_price = up_price + down_price
    locked_profit = q - total_cost
    locked_return_pct = (locked_profit / total_cost * 100.0) if total_cost > 0 else float("-inf")

    price_gate_ok = combined_price <= float(maximum_combined_price) + 1e-12
    roi_gate_ok = locked_return_pct + 1e-12 >= float(minimum_locked_return_pct)
    passes = (not sizing_block) and price_gate_ok and roi_gate_ok and locked_profit > 0.0

    if sizing_block:
        reason = (
            f"pair locked-edge blocked: equal-share sizing cannot satisfy per-side cost bounds; "
            f"qMin={q_min:.6f} > qMax={q_max:.6f}"
        )
    elif not price_gate_ok:
        reason = (
            f"pair locked-edge blocked: combined price {combined_price:.6f} > "
            f"maximum {float(maximum_combined_price):.6f}"
        )
    elif not roi_gate_ok:
        reason = (
            f"pair locked-edge blocked: locked return {locked_return_pct:.4f}% < "
            f"minimum {float(minimum_locked_return_pct):.4f}%"
        )
    elif locked_profit <= 0.0:
        reason = f"pair locked-edge blocked: gross locked profit {locked_profit:.8f} <= 0"
    else:
        reason = "PAIR_LOCKED_EDGE_OK"

    metadata = {
        "passes": passes,
        "combinedPrice": combined_price,
        "maximumCombinedPrice": float(maximum_combined_price),
        "equalShares": q,
        "upCostUsdt": up_cost,
        "downCostUsdt": down_cost,
        "totalCostUsdt": total_cost,
        "lockedProfitUsdt": locked_profit,
        "lockedReturnPct": locked_return_pct,
        "minimumLockedReturnPct": float(minimum_locked_return_pct),
        "qMinByCostBounds": q_min,
        "qMaxByCostBounds": q_max,
        "reason": reason,
    }

    for side, plan, cost in (("UP", up, up_cost), ("DOWN", down, down_cost)):
        price = float(plan["price"])
        plan["plannedShares"] = q
        plan["plannedCost"] = cost
        plan["targetProfit"] = q * (1.0 - price)
        plan["pairEdge"] = dict(metadata)
        plan["pricingModel"] = f"{str(plan.get('pricingModel') or 'PASSIVE')}+EQUAL_SHARE_LOCKED_EDGE_V83"
        if not passes:
            plan["blocked"] = True
            plan["blockKind"] = "PAIR_LOCKED_EDGE"
            plan["reason"] = reason

    return output


class PairLockedEdgeV83Mixin:
    """Pre-submission equal-share locked-edge gate for both V8 execution venues."""

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        now = core._now_ms()
        defaults = {
            "maximum_combined_price": f"{DEFAULT_MAXIMUM_COMBINED_PRICE:.8f}",
            "minimum_locked_return_pct": f"{DEFAULT_MINIMUM_LOCKED_RETURN_PCT:.8f}",
        }
        with self.db_lock:
            for key, value in defaults.items():
                self.db.execute(
                    "INSERT OR IGNORE INTO wallet_maker_clone_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                    (key, value, now),
                )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        try:
            maximum_combined = float(
                self._setting("maximum_combined_price", f"{DEFAULT_MAXIMUM_COMBINED_PRICE:.8f}")
            )
        except (TypeError, ValueError):
            maximum_combined = DEFAULT_MAXIMUM_COMBINED_PRICE
        try:
            minimum_locked_return = float(
                self._setting("minimum_locked_return_pct", f"{DEFAULT_MINIMUM_LOCKED_RETURN_PCT:.8f}")
            )
        except (TypeError, ValueError):
            minimum_locked_return = DEFAULT_MINIMUM_LOCKED_RETURN_PCT
        settings.update(
            maximumCombinedPrice=max(0.01, min(1.0, maximum_combined)),
            minimumLockedReturnPct=max(0.0, min(100.0, minimum_locked_return)),
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        edge_keys = {"maximumCombinedPrice", "minimumLockedReturnPct"}
        current = self._settings()

        if "maximumCombinedPrice" in values:
            try:
                maximum_combined = float(values["maximumCombinedPrice"])
            except (TypeError, ValueError) as exc:
                raise ValueError("maximumCombinedPrice must be a number") from exc
            if not math.isfinite(maximum_combined) or not 0.01 <= maximum_combined <= 1.0:
                raise ValueError("maximumCombinedPrice must be between 0.01 and 1.00")
        else:
            maximum_combined = float(current["maximumCombinedPrice"])

        if "minimumLockedReturnPct" in values:
            try:
                minimum_locked_return = float(values["minimumLockedReturnPct"])
            except (TypeError, ValueError) as exc:
                raise ValueError("minimumLockedReturnPct must be a number") from exc
            if not math.isfinite(minimum_locked_return) or not 0.0 <= minimum_locked_return <= 100.0:
                raise ValueError("minimumLockedReturnPct must be between 0 and 100")
        else:
            minimum_locked_return = float(current["minimumLockedReturnPct"])

        passthrough = {key: value for key, value in values.items() if key not in edge_keys}
        if passthrough:
            super().update_settings(passthrough)

        if "maximumCombinedPrice" in values:
            self._set_setting("maximum_combined_price", f"{maximum_combined:.8f}")
        if "minimumLockedReturnPct" in values:
            self._set_setting("minimum_locked_return_pct", f"{minimum_locked_return:.8f}")
        return self.snapshot()

    def _raw_pair_plans(
        self,
        market: dict[str, Any],
        *,
        replenishment: bool,
    ) -> dict[str, dict[str, Any] | None]:
        planner = super()._plan_replenishment if replenishment else super()._plan_order
        return {side: planner(side, market) for side in ("UP", "DOWN")}

    def _locked_edge_pair_plans(
        self,
        market: dict[str, Any],
        *,
        replenishment: bool,
    ) -> dict[str, dict[str, Any] | None]:
        settings = self._settings()
        return apply_equal_share_locked_edge(
            self._raw_pair_plans(market, replenishment=replenishment),
            minimum_order_usdt=float(settings["minimumOrderUsdt"]),
            maximum_order_usdt=float(settings["maximumOrderUsdt"]),
            maximum_combined_price=float(settings["maximumCombinedPrice"]),
            minimum_locked_return_pct=float(settings["minimumLockedReturnPct"]),
        )

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        return self._locked_edge_pair_plans(market, replenishment=False).get(str(side).upper())

    def _plan_replenishment(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        return self._locked_edge_pair_plans(market, replenishment=True).get(str(side).upper())

    def _pair_edge_preview(self) -> dict[str, Any] | None:
        market = self.market if isinstance(self.market, dict) else None
        if market is None:
            return None
        plans = self._locked_edge_pair_plans(market, replenishment=False)
        for side in ("UP", "DOWN"):
            plan = plans.get(side)
            if isinstance(plan, dict) and isinstance(plan.get("pairEdge"), dict):
                return dict(plan["pairEdge"])
        return None

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        settings = self._settings()
        payload.setdefault("settings", {}).update(
            maximumCombinedPrice=float(settings["maximumCombinedPrice"]),
            minimumLockedReturnPct=float(settings["minimumLockedReturnPct"]),
        )
        payload["pairLockedEdgePreview"] = self._pair_edge_preview()
        payload.setdefault("rules", {}).update(
            pairLockedEdgeV83=True,
            pairSizingV83="EQUAL_UP_DOWN_SHARES",
            maximumCombinedPriceGateV83=True,
            minimumLockedReturnPctGateV83=True,
            lockedEdgeMeasureV83="GROSS_EQUAL_SHARE_WORST_CASE_SETTLEMENT_RETURN",
            zeroEdgePairsBlockedV83=True,
        )
        return payload
