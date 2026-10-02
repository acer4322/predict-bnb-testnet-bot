from __future__ import annotations

import math
from typing import Any

from . import wallet_maker_clone_live as core


DEFAULT_MAXIMUM_COMBINED_MAKER_PRICE = 0.98


def apply_pair_spread_gate(
    plans: dict[str, dict[str, Any] | None],
    *,
    maximum_combined_maker_price: float,
) -> dict[str, dict[str, Any] | None]:
    """Block zero/negative-spread two-sided maker quotes without resizing them.

    This is NOT an arbitrage/locked-profit test.  Each side keeps its original
    V8 payoff-normalized sizing and can fill independently.  The only pair-level
    eligibility check is that the two passive maker prices leave enough cushion
    below 1.00 before either order is submitted.

        combined_maker_price = up_price + down_price
        maker_spread_cushion = 1 - combined_maker_price

    A 0.50 / 0.50 pair is therefore blocked at the default 0.98 cap, while the
    strategy remains a resting-maker strategy rather than an equal-share atomic
    complement strategy.
    """

    output: dict[str, dict[str, Any] | None] = {
        side: (dict(plan) if isinstance(plan, dict) else None)
        for side, plan in plans.items()
    }
    up = output.get("UP")
    down = output.get("DOWN")
    if not isinstance(up, dict) or not isinstance(down, dict):
        return output

    # Never override a venue/post-only/approval block produced by the underlying
    # planner.  Pair spread is an additional gate only.
    if bool(up.get("blocked")) or bool(down.get("blocked")):
        return output

    try:
        up_price = float(up["price"])
        down_price = float(down["price"])
    except (KeyError, TypeError, ValueError):
        return output
    if not all(math.isfinite(value) for value in (up_price, down_price)):
        return output
    if not (0.0 < up_price < 1.0 and 0.0 < down_price < 1.0):
        return output

    maximum = float(maximum_combined_maker_price)
    combined = up_price + down_price
    cushion = 1.0 - combined
    passes = combined <= maximum + 1e-12
    reason = (
        "PAIR_MAKER_SPREAD_OK"
        if passes
        else (
            f"pair maker-spread blocked: passive UP+DOWN {combined:.6f} > "
            f"maximum {maximum:.6f}"
        )
    )
    metadata = {
        "passes": passes,
        "combinedMakerPrice": combined,
        "maximumCombinedMakerPrice": maximum,
        "makerSpreadCushion": cushion,
        "makerSpreadCushionPct": cushion * 100.0,
        "upPrice": up_price,
        "downPrice": down_price,
        "reason": reason,
        "guaranteedProfit": False,
        "equalShareSizing": False,
    }

    for plan in (up, down):
        plan["pairMakerSpread"] = dict(metadata)
        plan["pricingModel"] = f"{str(plan.get('pricingModel') or 'PASSIVE')}+PAIR_MAKER_SPREAD_GATE_V84"
        if not passes:
            plan["blocked"] = True
            plan["blockKind"] = "PAIR_MAKER_SPREAD"
            plan["reason"] = reason
    return output


class PairMakerSpreadV84Mixin:
    """V8.4: keep passive independent maker sizing; remove V5 neutral-anchor entry.

    Important design points:
      * generation 1 and replenishments both quote the current passive best bid;
      * UP/DOWN planned shares/costs are NOT equalized or otherwise rewritten;
      * fills are not required to be simultaneous to make the quote meaningful;
      * a pair is merely prevented from starting when the two passive quote
        prices consume too much of the binary $1 settlement budget.

    Existing V8 paired-generation, max-loss, T-60 and T-30 controls remain in the
    inherited engine.  This mixin only changes quote construction/eligibility.
    """

    def _ensure_defaults(self) -> None:
        super()._ensure_defaults()
        now = core._now_ms()
        with self.db_lock:
            self.db.execute(
                "INSERT OR IGNORE INTO wallet_maker_clone_settings(key,value,updated_at_ms) VALUES(?,?,?)",
                (
                    "maximum_combined_maker_price",
                    f"{DEFAULT_MAXIMUM_COMBINED_MAKER_PRICE:.8f}",
                    now,
                ),
            )
            self.db.commit()

    def _settings(self) -> dict[str, Any]:
        settings = super()._settings()
        try:
            maximum = float(
                self._setting(
                    "maximum_combined_maker_price",
                    f"{DEFAULT_MAXIMUM_COMBINED_MAKER_PRICE:.8f}",
                )
            )
        except (TypeError, ValueError):
            maximum = DEFAULT_MAXIMUM_COMBINED_MAKER_PRICE
        maximum = max(0.01, min(1.0, maximum))
        settings.update(
            maximumCombinedMakerPrice=maximum,
            # Compatibility aliases for the temporary V8.3 Dashboard.  They no
            # longer mean locked profit/equal-share sizing in V8.4.
            maximumCombinedPrice=maximum,
            minimumLockedReturnPct=0.0,
        )
        return settings

    def update_settings(self, values: dict[str, Any]) -> dict[str, Any]:
        current = self._settings()
        # Accept both the new name and the temporary V8.3 Dashboard field so an
        # operator can upgrade backend first without settings-save failures.
        raw_maximum = values.get(
            "maximumCombinedMakerPrice",
            values.get("maximumCombinedPrice", current["maximumCombinedMakerPrice"]),
        )
        try:
            maximum = float(raw_maximum)
        except (TypeError, ValueError) as exc:
            raise ValueError("maximumCombinedMakerPrice must be a number") from exc
        if not math.isfinite(maximum) or not 0.01 <= maximum <= 1.0:
            raise ValueError("maximumCombinedMakerPrice must be between 0.01 and 1.00")

        compatibility_keys = {
            "maximumCombinedMakerPrice",
            "maximumCombinedPrice",
            "minimumLockedReturnPct",
        }
        passthrough = {key: value for key, value in values.items() if key not in compatibility_keys}
        if passthrough:
            super().update_settings(passthrough)
        if "maximumCombinedMakerPrice" in values or "maximumCombinedPrice" in values:
            self._set_setting("maximum_combined_maker_price", f"{maximum:.8f}")
        return self.snapshot()

    def _raw_passive_pair_plans(
        self,
        market: dict[str, Any],
    ) -> dict[str, dict[str, Any] | None]:
        # Deliberately use the inherited V8 replenishment planner for generation
        # 1 as well.  That planner quotes each side from CURRENT PASSIVE BEST BID.
        # This removes V5's neutral 0.50/0.50 anchor without changing the
        # original payoff-normalized per-side sizing.
        planner = super()._plan_replenishment
        return {side: planner(side, market) for side in ("UP", "DOWN")}

    def _spread_gated_pair_plans(
        self,
        market: dict[str, Any],
    ) -> dict[str, dict[str, Any] | None]:
        settings = self._settings()
        return apply_pair_spread_gate(
            self._raw_passive_pair_plans(market),
            maximum_combined_maker_price=float(settings["maximumCombinedMakerPrice"]),
        )

    def _plan_order(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        return self._spread_gated_pair_plans(market).get(str(side).upper())

    def _plan_replenishment(self, side: str, market: dict[str, Any]) -> dict[str, Any] | None:
        return self._spread_gated_pair_plans(market).get(str(side).upper())

    def _pair_maker_spread_preview(self) -> dict[str, Any] | None:
        market = self.market if isinstance(self.market, dict) else None
        if market is None:
            return None
        plans = self._spread_gated_pair_plans(market)
        for side in ("UP", "DOWN"):
            plan = plans.get(side)
            if isinstance(plan, dict) and isinstance(plan.get("pairMakerSpread"), dict):
                return dict(plan["pairMakerSpread"])
        return None

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        payload["version"] = self.VERSION
        settings = self._settings()
        preview = self._pair_maker_spread_preview()
        payload.setdefault("settings", {}).update(
            maximumCombinedMakerPrice=float(settings["maximumCombinedMakerPrice"]),
            # Compatibility alias while the V8.3 dashboard is being replaced.
            maximumCombinedPrice=float(settings["maximumCombinedMakerPrice"]),
            minimumLockedReturnPct=0.0,
        )
        payload["pairMakerSpreadPreview"] = preview
        # Compatibility shape: old UI can still show combined/pass until it is
        # rebuilt, but locked-return/equal-share fields intentionally disappear.
        payload["pairLockedEdgePreview"] = (
            {
                "passes": preview.get("passes"),
                "combinedPrice": preview.get("combinedMakerPrice"),
                "reason": preview.get("reason"),
            }
            if isinstance(preview, dict)
            else None
        )
        payload.setdefault("rules", {}).update(
            pairMakerSpreadGateV84=True,
            initialPricingV84="CURRENT_PASSIVE_BEST_BID",
            replenishmentPricingV84="CURRENT_PASSIVE_BEST_BID",
            neutralAnchorRemovedV84=True,
            pairSizingV84="INDEPENDENT_ORIGINAL_PAYOFF_NORMALIZED",
            equalShareSizingV84=False,
            lockedProfitRequirementV84=False,
            maximumCombinedMakerPriceGateV84=True,
            pairSpreadGateMeaningV84="ENTRY_QUOTE_QUALITY_FILTER_NOT_LOCKED_ARBITRAGE",
        )
        return payload
