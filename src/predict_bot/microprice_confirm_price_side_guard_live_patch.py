from __future__ import annotations

from decimal import Decimal
from functools import wraps
from typing import Any, Iterable

from . import m_realtime as _realtime
from . import microprice_variants as _variants
from .microprice_confirm_optimization_shadows import (
    DOWN_BLOCK_MAX_EXCLUSIVE,
    DOWN_BLOCK_MIN_INCLUSIVE,
    OPTIMIZATION_VERSION,
    PRICE_SIDE_GUARD_STRATEGY,
    SOURCE_STRATEGY,
    UP_BLOCK_BELOW,
    _price_side_block_reason,
)


PRICE_SIDE_GUARD_LIVE_VERSION = "MICROPRICE_CONFIRM_PRICE_SIDE_GUARD_LIVE_V1"
DOWN_BELOW_DEAD_ZONE_MAX_EXECUTION = Decimal("0.49999999")


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _register_live_strategy() -> None:
    from . import live_trading as live
    from . import research_forward as research

    strategy = PRICE_SIDE_GUARD_STRATEGY
    live.LIVE_RESEARCH_STRATEGIES = _append_unique(
        live.LIVE_RESEARCH_STRATEGIES,
        strategy,
    )
    live.LIVE_SUPPORTED_STRATEGIES = _append_unique(
        live.LIVE_SUPPORTED_STRATEGIES,
        strategy,
    )
    live.LIVE_RESEARCH_REPRICE_GAPS = {
        **live.LIVE_RESEARCH_REPRICE_GAPS,
        strategy: Decimal("0.05"),
    }

    _realtime.LIVE_RESEARCH_STRATEGIES = _append_unique(
        _realtime.LIVE_RESEARCH_STRATEGIES,
        strategy,
    )
    _realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES = {
        *_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES,
        strategy,
    }

    # The strategy uses the same optional confirmation-add execution path as
    # R_MICROPRICE_CONFIRM, but remains inactive unless explicitly selected.
    all_live_sources = tuple(live.LIVE_SUPPORTED_STRATEGIES)
    research.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources
    live.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources


def _guard_trade(tracker: Any, market_id: int) -> dict[str, Any] | None:
    try:
        row = tracker.store.db.execute(
            """SELECT id, side, entry_price, strategy_version
                 FROM trades
                WHERE strategy=? AND market_id=?
                ORDER BY id DESC LIMIT 1""",
            (PRICE_SIDE_GUARD_STRATEGY, int(market_id)),
        ).fetchone()
    except Exception:
        return None
    return dict(row) if row is not None else None


def _append_guard_candidate(
    tracker: Any,
    opened: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    result = list(opened)
    for source in opened:
        if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
            continue
        try:
            market_id = int(source["market_id"])
            side = str(source["side"]).upper()
            source_entry = float(source["entry_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if side not in {"UP", "DOWN"}:
            continue
        if _price_side_block_reason(side, source_entry) is not None:
            continue
        guard_trade = _guard_trade(tracker, market_id)
        if guard_trade is None:
            continue
        try:
            guard_entry = float(guard_trade["entry_price"])
        except (KeyError, TypeError, ValueError):
            continue
        if _price_side_block_reason(side, guard_entry) is not None:
            continue

        result.append(
            {
                **source,
                "strategy": PRICE_SIDE_GUARD_STRATEGY,
                "entry_price": guard_entry,
                "paper_only": True,
                "live_orders_affected": False,
                "live_forwardable_when_selected": True,
                "variant_mode": "FOLLOW_V2_WITH_PRICE_SIDE_GUARD",
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": guard_trade.get("id"),
                "strategy_version": (
                    guard_trade.get("strategy_version")
                    or OPTIMIZATION_VERSION
                ),
                "price_side_guard_passed": True,
                "price_side_guard_rule": {
                    "blockUpEntryBelowExclusive": UP_BLOCK_BELOW,
                    "blockDownEntryMinInclusive": DOWN_BLOCK_MIN_INCLUSIVE,
                    "blockDownEntryMaxExclusive": DOWN_BLOCK_MAX_EXCLUSIVE,
                },
            }
        )
    return result


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_microprice_price_side_guard_live_v1", False):
        return

    @wraps(original)
    def process_with_price_side_guard_live(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        opened = list(
            original(
                self,
                snapshot,
                int(fee_bps),
                context,
            )
            or []
        )
        return _append_guard_candidate(self, opened)

    process_with_price_side_guard_live._microprice_price_side_guard_live_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_price_side_guard_live


def _patch_live_price_guards() -> None:
    from . import live_trading as live

    engine_class = live.LiveM0WEngine

    original_signal_gate = engine_class._research_signal_price_is_allowed
    if not getattr(
        original_signal_gate,
        "_microprice_price_side_guard_live_v1",
        False,
    ):

        def research_signal_price_with_guard(
            signal: dict[str, Any],
            strategy: str,
        ) -> tuple[bool, str]:
            allowed, reason = original_signal_gate(signal, strategy)
            if not allowed or str(strategy).upper() != PRICE_SIDE_GUARD_STRATEGY:
                return allowed, reason
            try:
                side = str(signal["side"]).upper()
                entry = float(signal["entry_price"])
            except (KeyError, TypeError, ValueError):
                return False, "PRICE_SIDE_GUARD live signal is missing side or entry price"
            block_reason = _price_side_block_reason(side, entry)
            if block_reason is not None:
                return False, (
                    f"PRICE_SIDE_GUARD blocked {side} entry {entry:.8f}: "
                    f"{block_reason}"
                )
            return True, ""

        research_signal_price_with_guard._microprice_price_side_guard_live_v1 = True  # type: ignore[attr-defined]
        engine_class._research_signal_price_is_allowed = staticmethod(
            research_signal_price_with_guard
        )

    original_maximum = engine_class._maximum_reprice_limit
    if not getattr(
        original_maximum,
        "_microprice_price_side_guard_live_v1",
        False,
    ):

        def maximum_reprice_with_guard(
            signal: dict[str, Any],
            strategy: str,
            signal_price: Decimal,
        ) -> Decimal:
            maximum = original_maximum(signal, strategy, signal_price)
            if str(strategy).upper() != PRICE_SIDE_GUARD_STRATEGY:
                return maximum
            side = str(signal.get("side") or "").upper()
            if side == "DOWN" and signal_price < Decimal(
                str(DOWN_BLOCK_MIN_INCLUSIVE)
            ):
                # A signal below 0.50 may not reprice into the excluded
                # 0.50–0.60 band. Signals at or above 0.60 remain unaffected.
                return min(maximum, DOWN_BELOW_DEAD_ZONE_MAX_EXECUTION)
            return maximum

        maximum_reprice_with_guard._microprice_price_side_guard_live_v1 = True  # type: ignore[attr-defined]
        engine_class._maximum_reprice_limit = staticmethod(
            maximum_reprice_with_guard
        )


def install_microprice_confirm_price_side_guard_live_patch() -> None:
    _register_live_strategy()
    _patch_tracker_process()
    _patch_live_price_guards()
