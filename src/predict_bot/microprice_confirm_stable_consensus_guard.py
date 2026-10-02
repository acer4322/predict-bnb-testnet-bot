from __future__ import annotations

import math
from decimal import Decimal
from functools import wraps
from typing import Any, Iterable

from . import m_realtime as _realtime
from . import microprice_variants as _variants
from .microprice_confirm_observer_guard_patch import (
    microprice_confirm_observer_guard_decision,
)


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_GUARD"
OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_STABLE_CONSENSUS_OBSERVER"
PATCH_VERSION = "MICROPRICE_CONFIRM_STABLE_CONSENSUS_V1"
MIN_RAW_TOP_ASK = 0.60
MAX_RAW_TOP_ASK_EXCLUSIVE = 0.90
MIN_EXECUTION_PRICE = Decimal("0.60")
MAX_EXECUTION_PRICE = Decimal("0.89999999")


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _f1_gate(context: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(context, dict):
        return None
    gates = context.get("m01o_observer_gates")
    if isinstance(gates, dict) and isinstance(gates.get("F1"), dict):
        return dict(gates["F1"])
    direct = context.get("strategy_observer_gate")
    if isinstance(direct, dict):
        return dict(direct)
    direct = context.get("m01o_observer_gate")
    return dict(direct) if isinstance(direct, dict) else None


def stable_consensus_historical_decision(
    gate: dict[str, Any] | None,
    *,
    expected_market_id: int | None = None,
) -> dict[str, Any]:
    """Apply the causal historical transition guard under a new version id."""
    result = dict(
        microprice_confirm_observer_guard_decision(
            gate,
            expected_market_id=expected_market_id,
        )
    )
    result["version"] = OBSERVER_VERSION
    result["ruleVersion"] = PATCH_VERSION
    result["stableConsensusPriceBand"] = {
        "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
        "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
    }
    result["priceBandEvaluatedSeparately"] = True
    return result


def stable_consensus_signal_decision(
    gate: dict[str, Any] | None,
    raw_top_ask: Any,
    *,
    expected_market_id: int | None = None,
) -> dict[str, Any]:
    result = stable_consensus_historical_decision(
        gate,
        expected_market_id=expected_market_id,
    )
    raw = _finite(raw_top_ask)
    result["rawTopAsk"] = raw
    if result.get("allowed") is not True:
        return result
    if raw is None:
        result.update(
            allowed=False,
            status="BLOCK",
            reason="stable consensus raw_top_ask is unavailable",
            priceBandPassed=False,
        )
        return result
    price_passed = bool(
        MIN_RAW_TOP_ASK <= raw < MAX_RAW_TOP_ASK_EXCLUSIVE
    )
    result["priceBandPassed"] = price_passed
    result["allowed"] = price_passed
    result["status"] = "ALLOW" if price_passed else "BLOCK"
    result["reason"] = (
        "allowed: historical Observer passed and signal-side raw_top_ask is "
        f"inside [{MIN_RAW_TOP_ASK:.2f}, {MAX_RAW_TOP_ASK_EXCLUSIVE:.2f})"
        if price_passed
        else (
            f"blocked: signal-side raw_top_ask {raw:.8f} is outside "
            f"[{MIN_RAW_TOP_ASK:.2f}, {MAX_RAW_TOP_ASK_EXCLUSIVE:.2f})"
        )
    )
    return result


def _trade_exists(store: Any, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (STRATEGY, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


def _source_trade_id(store: Any, market_id: int) -> int | None:
    try:
        row = store.db.execute(
            """SELECT id FROM trades
                 WHERE strategy=? AND market_id=?
                 ORDER BY id DESC LIMIT 1""",
            (SOURCE_STRATEGY, int(market_id)),
        ).fetchone()
    except Exception:
        return None
    return int(row["id"]) if row is not None else None


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_stable_consensus_guard_v1", False):
        return

    @wraps(original)
    def process_with_stable_consensus(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        opened = list(original(self, snapshot, int(fee_bps), context) or [])
        gate = _f1_gate(context)
        result = list(opened)
        for source in opened:
            if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
                continue
            try:
                market_id = int(source["market_id"])
                topic_id = int(source["topic_id"])
                side = str(source["side"]).upper()
                entry = float(source["entry_price"])
                stake = float(source["stake"])
            except (KeyError, TypeError, ValueError):
                continue
            raw_top_ask = source.get("raw_top_ask")
            decision = stable_consensus_signal_decision(
                gate,
                raw_top_ask,
                expected_market_id=market_id,
            )
            if decision.get("allowed") is not True or _trade_exists(
                self.store,
                market_id,
            ):
                continue

            source_id = _source_trade_id(self.store, market_id)
            diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "stable_consensus_guard": True,
                "stable_consensus_version": PATCH_VERSION,
                "source_strategy": SOURCE_STRATEGY,
                "source_trade_id": source_id,
                "source_strategy_version": source.get("strategy_version"),
                "variant_mode": "FOLLOW_CONFIRMED_IMBALANCE_STABLE_CONSENSUS",
                "selected_side": side,
                "raw_top_ask": _finite(raw_top_ask),
                "raw_top_bid": _finite(source.get("raw_top_bid")),
                "observer_version": OBSERVER_VERSION,
                "observer_gate": gate,
                "stable_consensus_decision": decision,
                "rule": {
                    "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
                    "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
                    "historicalTransitionGuard": True,
                    "causalHistoricalSettlementsOnly": True,
                },
            }
            self.store.open_trade(
                strategy=STRATEGY,
                topic_id=topic_id,
                market_id=market_id,
                side=side,
                entry=entry,
                target=None,
                stake=stake,
                fee_rate_bps=int(fee_bps),
                note=(
                    f"{STRATEGY} mirrored from {SOURCE_STRATEGY} trade "
                    f"#{source_id or '?'}; historical F1 transition guard and "
                    "signal-side 0.60-0.90 consensus band passed; paper only"
                ),
                strategy_version=PATCH_VERSION,
                diagnostics=diagnostics,
            )
            result.append(
                {
                    **source,
                    "strategy": STRATEGY,
                    "paper_only": True,
                    "live_orders_affected": False,
                    "live_forwardable_when_selected": True,
                    "stable_consensus_guard": True,
                    "stable_consensus_decision": decision,
                    "strategy_version": PATCH_VERSION,
                    "source_strategy": SOURCE_STRATEGY,
                    "source_trade_id": source_id,
                }
            )
        return result

    process_with_stable_consensus._stable_consensus_guard_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_stable_consensus


def _patch_observer_dispatch(research: Any, live: Any) -> None:
    original = research.futures_lead_observer_decision
    if not getattr(original, "_stable_consensus_observer_v1", False):

        @wraps(original)
        def observer_decision_with_stable_consensus(
            version: str,
            gate: dict[str, Any] | None,
            *,
            expected_market_id: int | None = None,
        ) -> dict[str, Any]:
            if str(version or "").strip().upper() == OBSERVER_VERSION:
                return stable_consensus_historical_decision(
                    gate,
                    expected_market_id=expected_market_id,
                )
            return original(
                version,
                gate,
                expected_market_id=expected_market_id,
            )

        observer_decision_with_stable_consensus._stable_consensus_observer_v1 = True  # type: ignore[attr-defined]
        research.futures_lead_observer_decision = (
            observer_decision_with_stable_consensus
        )

    research.FUTURES_LEAD_OBSERVER_VERSIONS = _append_unique(
        research.FUTURES_LEAD_OBSERVER_VERSIONS,
        OBSERVER_VERSION,
    )
    live.FUTURES_LEAD_OBSERVER_VERSIONS = tuple(
        research.FUTURES_LEAD_OBSERVER_VERSIONS
    )
    live.futures_lead_observer_decision = research.futures_lead_observer_decision


def _register_live_strategy(live: Any, research: Any) -> None:
    live.LIVE_RESEARCH_STRATEGIES = _append_unique(
        live.LIVE_RESEARCH_STRATEGIES,
        STRATEGY,
    )
    live.LIVE_SUPPORTED_STRATEGIES = _append_unique(
        live.LIVE_SUPPORTED_STRATEGIES,
        STRATEGY,
    )
    live.LIVE_OBSERVER_STRATEGIES = _append_unique(
        live.LIVE_OBSERVER_STRATEGIES,
        STRATEGY,
    )
    live.LIVE_RESEARCH_REPRICE_GAPS = {
        **live.LIVE_RESEARCH_REPRICE_GAPS,
        STRATEGY: Decimal("0.05"),
    }
    live.LIVE_RESEARCH_PRICE_RANGES = {
        **live.LIVE_RESEARCH_PRICE_RANGES,
        STRATEGY: (MIN_EXECUTION_PRICE, MAX_EXECUTION_PRICE),
    }

    _realtime.LIVE_RESEARCH_STRATEGIES = _append_unique(
        _realtime.LIVE_RESEARCH_STRATEGIES,
        STRATEGY,
    )
    _realtime.LIVE_OBSERVER_STRATEGIES = _append_unique(
        _realtime.LIVE_OBSERVER_STRATEGIES,
        STRATEGY,
    )
    _realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES = {
        *_realtime.LIVE_FORWARDABLE_PAPER_STRATEGIES,
        STRATEGY,
    }

    all_live_sources = tuple(live.LIVE_SUPPORTED_STRATEGIES)
    live.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources
    research.CONFIRMATION_ADD_SOURCE_STRATEGIES = all_live_sources


def _signal_raw_top_ask(signal: dict[str, Any]) -> float | None:
    for key in (
        "raw_top_ask",
        "signal_prediction_ask",
        "entry_price",
    ):
        value = _finite(signal.get(key))
        if value is not None:
            return value
    return None


def _patch_live_guards(live: Any) -> None:
    engine_class = live.LiveM0WEngine

    original_signal = engine_class._research_signal_price_is_allowed
    if not getattr(original_signal, "_stable_consensus_guard_v1", False):

        def research_signal_with_stable_consensus(
            signal: dict[str, Any],
            strategy: str,
        ) -> tuple[bool, str]:
            allowed, reason = original_signal(signal, strategy)
            if not allowed or str(strategy or "").upper() != STRATEGY:
                return allowed, reason
            try:
                market_id = int(signal["market_id"])
            except (KeyError, TypeError, ValueError):
                return False, "stable consensus live signal is missing market_id"
            gate = _f1_gate(signal)
            decision = stable_consensus_signal_decision(
                gate,
                _signal_raw_top_ask(signal),
                expected_market_id=market_id,
            )
            if decision.get("allowed") is not True:
                return False, str(decision.get("reason") or "stable consensus blocked")
            signal["stable_consensus_live_revalidated"] = True
            signal["stable_consensus_decision"] = decision
            return True, ""

        research_signal_with_stable_consensus._stable_consensus_guard_v1 = True  # type: ignore[attr-defined]
        engine_class._research_signal_price_is_allowed = staticmethod(
            research_signal_with_stable_consensus
        )

    original_observer = engine_class._strategy_observer_gate_is_safe
    if not getattr(original_observer, "_stable_consensus_observer_v1", False):

        def strategy_observer_with_stable_consensus(
            signal: dict[str, Any],
            reference: dict[str, Any],
            version: str,
        ) -> tuple[bool, str]:
            allowed, reason = original_observer(signal, reference, version)
            if (
                not allowed
                or str(version or "").strip().upper() != OBSERVER_VERSION
            ):
                return allowed, reason
            raw = _signal_raw_top_ask(signal)
            if raw is None:
                return False, "Stable Consensus Observer raw_top_ask is unavailable"
            if not MIN_RAW_TOP_ASK <= raw < MAX_RAW_TOP_ASK_EXCLUSIVE:
                return False, (
                    f"Stable Consensus Observer blocked raw_top_ask {raw:.8f}; "
                    f"requires [{MIN_RAW_TOP_ASK:.2f}, "
                    f"{MAX_RAW_TOP_ASK_EXCLUSIVE:.2f})"
                )
            signal["stable_consensus_observer_passed"] = True
            return True, ""

        strategy_observer_with_stable_consensus._stable_consensus_observer_v1 = True  # type: ignore[attr-defined]
        engine_class._strategy_observer_gate_is_safe = staticmethod(
            strategy_observer_with_stable_consensus
        )

    original_maximum = engine_class._maximum_reprice_limit
    if not getattr(original_maximum, "_stable_consensus_guard_v1", False):

        def maximum_reprice_with_stable_consensus(
            signal: dict[str, Any],
            strategy: str,
            signal_price: Decimal,
        ) -> Decimal:
            maximum = original_maximum(signal, strategy, signal_price)
            if (
                str(strategy or "").upper() == STRATEGY
                or signal.get("stable_consensus_observer_passed") is True
            ):
                return min(maximum, MAX_EXECUTION_PRICE)
            return maximum

        maximum_reprice_with_stable_consensus._stable_consensus_guard_v1 = True  # type: ignore[attr-defined]
        engine_class._maximum_reprice_limit = staticmethod(
            maximum_reprice_with_stable_consensus
        )


def install_microprice_confirm_stable_consensus_guard() -> None:
    """Install the paper/live strategy and the selectable Observer version."""
    from . import live_trading as live
    from . import research_forward as research

    _patch_observer_dispatch(research, live)
    _register_live_strategy(live, research)
    _patch_tracker_process()
    _patch_live_guards(live)
