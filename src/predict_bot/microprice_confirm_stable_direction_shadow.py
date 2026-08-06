from __future__ import annotations

import json
import math
from functools import wraps
from typing import Any, Iterable

from . import microprice_variants as _variants


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION"
PATCH_VERSION = "MICROPRICE_CONFIRM_STABLE_DIRECTION_V1"
MIN_RAW_TOP_ASK = 0.60
MAX_RAW_TOP_ASK_EXCLUSIVE = 0.90
MIN_MIDPOINT_DELTA = 0.01


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def stable_direction_signal_decision(
    raw_top_ask: Any,
    midpoint_delta: Any,
) -> dict[str, Any]:
    raw = _finite(raw_top_ask)
    delta = _finite(midpoint_delta)
    price_passed = raw is not None and MIN_RAW_TOP_ASK <= raw < MAX_RAW_TOP_ASK_EXCLUSIVE
    midpoint_passed = delta is not None and delta >= MIN_MIDPOINT_DELTA
    allowed = bool(price_passed and midpoint_passed)
    reasons: list[str] = []
    if not price_passed:
        reasons.append(
            "raw_top_ask must be inside "
            f"[{MIN_RAW_TOP_ASK:.2f}, {MAX_RAW_TOP_ASK_EXCLUSIVE:.2f})"
        )
    if not midpoint_passed:
        reasons.append(
            f"selected-side midpoint_delta must be >= {MIN_MIDPOINT_DELTA:.3f}"
        )
    return {
        "version": PATCH_VERSION,
        "strategy": STRATEGY,
        "sourceStrategy": SOURCE_STRATEGY,
        "allowed": allowed,
        "status": "ALLOW" if allowed else "BLOCK",
        "reason": (
            "allowed: confirmed Microprice direction, 0.60-0.90 raw Ask, "
            "and selected-side midpoint advanced at least 0.01"
            if allowed
            else "; ".join(reasons)
        ),
        "rawTopAsk": raw,
        "midpointDelta": delta,
        "priceBandPassed": price_passed,
        "midpointAdvancePassed": midpoint_passed,
        "usesObserver": False,
        "usesF1RangeScore": False,
        "paperOnly": True,
    }


def _trade_exists(store: Any, market_id: int) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (STRATEGY, int(market_id)),
        ).fetchone() is not None
    except Exception:
        return False


def _source_context(store: Any, market_id: int) -> dict[str, Any]:
    try:
        row = store.db.execute(
            """SELECT id, strategy_version, diagnostics_json
                 FROM trades
                WHERE strategy=? AND market_id=?
                ORDER BY id DESC LIMIT 1""",
            (SOURCE_STRATEGY, int(market_id)),
        ).fetchone()
    except Exception:
        row = None
    if row is None:
        return {"sourceTradeId": None, "sourceStrategyVersion": None, "diagnostics": {}}
    return {
        "sourceTradeId": int(row["id"]),
        "sourceStrategyVersion": row["strategy_version"],
        "diagnostics": _decode_json(row["diagnostics_json"]),
    }


def open_stable_direction_shadow(
    store: Any,
    source: dict[str, Any],
    fee_bps: int,
) -> dict[str, Any] | None:
    if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
        return None
    try:
        market_id = int(source["market_id"])
        topic_id = int(source["topic_id"])
        side = str(source["side"]).upper()
        entry = float(source["entry_price"])
        stake = float(source["stake"])
    except (KeyError, TypeError, ValueError):
        return None
    if side not in {"UP", "DOWN"} or _trade_exists(store, market_id):
        return None

    source_context = _source_context(store, market_id)
    diagnostics = source_context["diagnostics"]
    raw_top_ask = source.get("raw_top_ask")
    if raw_top_ask is None:
        raw_top_ask = diagnostics.get("raw_top_ask")
    midpoint_delta = source.get("midpoint_delta")
    if midpoint_delta is None:
        midpoint_delta = diagnostics.get("midpoint_delta")
    decision = stable_direction_signal_decision(raw_top_ask, midpoint_delta)
    if decision["allowed"] is not True:
        return None

    shadow_diagnostics = {
        "paper_only": True,
        "live_orders_affected": False,
        "shadow_only": True,
        "forward_only": True,
        "source_strategy": SOURCE_STRATEGY,
        "source_trade_id": source_context["sourceTradeId"],
        "source_strategy_version": source_context["sourceStrategyVersion"],
        "source_already_microprice_confirmed": True,
        "variant_mode": "FOLLOW_CONFIRMED_IMBALANCE_STABLE_DIRECTION",
        "selected_side": side,
        "raw_top_ask": decision["rawTopAsk"],
        "midpoint_delta": decision["midpointDelta"],
        "stable_direction_decision": decision,
        "rule": {
            "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
            "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "minimumSelectedMidpointDeltaInclusive": MIN_MIDPOINT_DELTA,
            "sourceMustBeMicropriceConfirm": True,
            "observerRequired": False,
            "f1RangeScoreRequired": False,
            "missingDataFailsClosed": True,
        },
        "source_diagnostics": diagnostics,
    }
    store.open_trade(
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
            f"#{source_context['sourceTradeId'] or '?'}; raw Ask 0.60-0.90 "
            "and selected-side midpoint_delta >=0.01; paper only"
        ),
        strategy_version=PATCH_VERSION,
        diagnostics=shadow_diagnostics,
    )
    return {
        **source,
        "strategy": STRATEGY,
        "paper_only": True,
        "shadow_only": True,
        "live_orders_affected": False,
        "live_forwardable_when_selected": False,
        "stable_direction_decision": decision,
        "strategy_version": PATCH_VERSION,
        "source_strategy": SOURCE_STRATEGY,
        "source_trade_id": source_context["sourceTradeId"],
    }


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_stable_direction_shadow_v1", False):
        return

    @wraps(original)
    def process_with_stable_direction(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        opened = list(original(self, snapshot, int(fee_bps), context) or [])
        result = list(opened)
        for source in opened:
            shadow = open_stable_direction_shadow(self.store, source, int(fee_bps))
            if shadow is not None:
                result.append(shadow)
        return result

    process_with_stable_direction._stable_direction_shadow_v1 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_stable_direction


def _register_research_strategy() -> None:
    from . import research_forward as research

    research.SHADOW_RESEARCH_STRATEGIES = _append_unique(
        research.SHADOW_RESEARCH_STRATEGIES,
        STRATEGY,
    )
    research.RESEARCH_STRATEGIES = (
        *research.PRIMARY_RESEARCH_STRATEGIES,
        *research.SHADOW_RESEARCH_STRATEGIES,
    )
    research.RESEARCH_PARAMETERS = {
        **research.RESEARCH_PARAMETERS,
        STRATEGY: {
            "min_raw_top_ask": MIN_RAW_TOP_ASK,
            "max_raw_top_ask_exclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "min_midpoint_delta": MIN_MIDPOINT_DELTA,
        },
    }


def install_microprice_confirm_stable_direction_shadow() -> None:
    """Register one native research-page Paper Shadow; never register live use."""
    _register_research_strategy()
    _patch_tracker_process()
