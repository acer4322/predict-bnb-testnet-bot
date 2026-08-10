from __future__ import annotations

import os
from typing import Any

from . import cross_oracle_strategies as strategies
from . import cross_oracle_strategy_chop_guard_v4 as v4
from . import cross_oracle_strategy_rolling_stats as rolling


STRATEGY_POLY_INVERTED_PRICE = "R_POLY_INVERTED_PRICE"
INVERTED_MIN_POLY_SELECTED_MID = min(
    0.99,
    max(
        0.50,
        float(os.environ.get("PREDICT_POLY_INVERTED_MIN_POLY_SELECTED_MID", "0.75")),
    ),
)
INVERTED_MAX_BINANCE_SELECTED_MID = max(
    0.01,
    min(
        0.50,
        float(os.environ.get("PREDICT_POLY_INVERTED_MAX_BINANCE_SELECTED_MID", "0.25")),
    ),
)
INVERTED_MIRROR_TOLERANCE = max(
    0.0,
    min(
        0.50,
        float(os.environ.get("PREDICT_POLY_INVERTED_MIRROR_TOLERANCE", "0.10")),
    ),
)
INVERTED_MAX_ENTRY_ASK = max(
    0.01,
    min(
        1.0,
        float(os.environ.get("PREDICT_POLY_INVERTED_MAX_ENTRY_ASK", "0.30")),
    ),
)


# Extend the runtime summary tuple without modifying the original three strategy
# definitions. RollingStatsPaperEngine imports STRATEGIES by value, so extend
# both module bindings before the concrete v5 engine is constructed.
if STRATEGY_POLY_INVERTED_PRICE not in strategies.STRATEGIES:
    strategies.STRATEGIES = (*strategies.STRATEGIES, STRATEGY_POLY_INVERTED_PRICE)
if STRATEGY_POLY_INVERTED_PRICE not in rolling.STRATEGIES:
    rolling.STRATEGIES = (*rolling.STRATEGIES, STRATEGY_POLY_INVERTED_PRICE)


def inverted_price_signal(
    *,
    poly_selected_mid: float,
    binance_selected_mid: float,
    executable_ask: float,
) -> dict[str, Any]:
    mirror_sum = float(poly_selected_mid) + float(binance_selected_mid)
    mirror_error = abs(mirror_sum - 1.0)
    strong_poly = poly_selected_mid + 1e-12 >= INVERTED_MIN_POLY_SELECTED_MID
    cheap_binance = binance_selected_mid - 1e-12 <= INVERTED_MAX_BINANCE_SELECTED_MID
    mirror_like = mirror_error - 1e-12 <= INVERTED_MIRROR_TOLERANCE
    executable = executable_ask - 1e-12 <= INVERTED_MAX_ENTRY_ASK
    return {
        "triggered": bool(strong_poly and cheap_binance and mirror_like and executable),
        "polySelectedMid": float(poly_selected_mid),
        "binanceSelectedMid": float(binance_selected_mid),
        "binanceSelectedAsk": float(executable_ask),
        "mirrorSum": mirror_sum,
        "mirrorError": mirror_error,
        "strongPoly": strong_poly,
        "cheapBinance": cheap_binance,
        "mirrorLike": mirror_like,
        "executable": executable,
    }


class InvertedPricePaperEngine(v4.HeartbeatRollingStatsPaperEngine):
    """Paper-only test: trust Poly when same-side prices look mirror-inverted.

    Signal examples:
      Poly selected mid ~= 0.80 while Binance same-side mid ~= 0.20
      Poly selected mid ~= 0.90 while Binance same-side mid ~= 0.10

    The signal uses mids so spread does not define the research hypothesis. The
    simulated fill remains conservative at the current Binance Ask and is capped
    separately, so a pathological spread cannot create an unrealistically cheap
    Paper fill. One trade is allowed per Binance 5m market and the position is
    held to official settlement; no flip-exit rule is mixed into this first test.
    """

    def _maybe_open_gap_scalp(
        self,
        *,
        latest: dict[str, Any],
        binance_market_id: int,
        poly_slug: str,
        poly_direction: str,
        poly_up_mid: float,
        binance_up_mid: float,
        now_ms: int,
    ) -> None:
        # Preserve the original R_POLY_GAP_SCALP behavior first.
        super()._maybe_open_gap_scalp(
            latest=latest,
            binance_market_id=binance_market_id,
            poly_slug=poly_slug,
            poly_direction=poly_direction,
            poly_up_mid=poly_up_mid,
            binance_up_mid=binance_up_mid,
            now_ms=now_ms,
        )

        if self._has_trade_for_market(STRATEGY_POLY_INVERTED_PRICE, binance_market_id):
            return

        executable_ask = strategies.selected_quote(latest, poly_direction, "ask")
        if executable_ask is None:
            return
        poly_selected_mid = strategies.selected_probability(poly_up_mid, poly_direction)
        binance_selected_mid = strategies.selected_probability(binance_up_mid, poly_direction)
        signal = inverted_price_signal(
            poly_selected_mid=poly_selected_mid,
            binance_selected_mid=binance_selected_mid,
            executable_ask=executable_ask,
        )
        if not signal["triggered"]:
            return

        self._open_trade(
            strategy=STRATEGY_POLY_INVERTED_PRICE,
            latest=latest,
            binance_market_id=binance_market_id,
            poly_slug=poly_slug,
            side=poly_direction,
            poly_up_mid=poly_up_mid,
            binance_up_mid=binance_up_mid,
            now_ms=now_ms,
            reason="POLY_BINANCE_INVERTED_PRICE",
            metadata={
                **signal,
                "minPolySelectedMid": INVERTED_MIN_POLY_SELECTED_MID,
                "maxBinanceSelectedMid": INVERTED_MAX_BINANCE_SELECTED_MID,
                "mirrorTolerance": INVERTED_MIRROR_TOLERANCE,
                "maxEntryAsk": INVERTED_MAX_ENTRY_ASK,
                "holdToOfficialSettlement": True,
                "oneTradePerMarket": True,
            },
        )

    def snapshot(self) -> dict[str, Any]:
        payload = super().snapshot()
        summaries = payload.setdefault("summaries", {})
        summaries[STRATEGY_POLY_INVERTED_PRICE] = self._summary(
            STRATEGY_POLY_INVERTED_PRICE
        )

        rules = payload.setdefault("strategyRules", {})
        rules[STRATEGY_POLY_INVERTED_PRICE] = (
            "When the Polymarket-selected side mid is >= the configured strong threshold, "
            "the Binance same-side mid is <= the configured cheap threshold, and their "
            "sum is close to 1 within the mirror tolerance, buy that Binance side at Ask "
            "only if Ask remains below the execution cap; one trade per 5m market and "
            "hold to official settlement."
        )

        parameters = payload.setdefault("parameters", {})
        parameters.update(
            invertedMinPolySelectedMid=INVERTED_MIN_POLY_SELECTED_MID,
            invertedMaxBinanceSelectedMid=INVERTED_MAX_BINANCE_SELECTED_MID,
            invertedMirrorTolerance=INVERTED_MIRROR_TOLERANCE,
            invertedMaxEntryAsk=INVERTED_MAX_ENTRY_ASK,
        )

        rolling_meta = payload.get("rolling10MarketStats")
        if isinstance(rolling_meta, dict):
            names = list(rolling_meta.get("strategies") or [])
            if STRATEGY_POLY_INVERTED_PRICE not in names:
                names.append(STRATEGY_POLY_INVERTED_PRICE)
            rolling_meta["strategies"] = names

        payload["invertedPriceStrategy"] = {
            "strategy": STRATEGY_POLY_INVERTED_PRICE,
            "paperOnly": True,
            "directionSource": "POLYMARKET_CONFIDENT_DIRECTION",
            "signalPriceBasis": "MID",
            "executionPriceBasis": "BINANCE_SAME_SIDE_ASK",
            "minPolySelectedMid": INVERTED_MIN_POLY_SELECTED_MID,
            "maxBinanceSelectedMid": INVERTED_MAX_BINANCE_SELECTED_MID,
            "mirrorTolerance": INVERTED_MIRROR_TOLERANCE,
            "maxEntryAsk": INVERTED_MAX_ENTRY_ASK,
            "oneTradePerMarket": True,
            "exit": "OFFICIAL_SETTLEMENT_ONLY",
            "liveOrdersAffected": False,
        }
        return payload


# v4 already installs the heartbeat + rolling-stats engine into the resilient
# 8768 launcher. Replace only that concrete class with this v5 extension.
rolling.base.guard.stable.launch.strategy_module.GapAwareCrossOraclePaperEngine = (
    InvertedPricePaperEngine
)


def main() -> int:
    return v4.main()


if __name__ == "__main__":
    raise SystemExit(main())
