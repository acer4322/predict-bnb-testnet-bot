from __future__ import annotations

import json
import math
import time
from contextlib import nullcontext
from functools import wraps
from typing import Any

from . import microprice_variants as _variants
from .research_strategy_registry_patch import register_shadow_strategy


SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
LEGACY_STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION"
BASE_STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_BASE"
STRICT_STRATEGY = "R_MICROPRICE_CONFIRM_STABLE_DIRECTION_STRICT"
STRATEGIES = (BASE_STRATEGY, STRICT_STRATEGY)

# Backward-compatible aliases for code/tests that imported the first version.
STRATEGY = BASE_STRATEGY
PATCH_VERSION = "MICROPRICE_CONFIRM_STABLE_DIRECTION_AB_V3"
HORIZON_SECONDS = 180.0
MIN_RAW_TOP_ASK = 0.60
MAX_RAW_TOP_ASK_EXCLUSIVE = 0.90
MIN_MIDPOINT_DELTA = 0.01
STRICT_MAX_EFFECTIVE_CROSSOVERS = 1
STRICT_MIN_EFFICIENCY_RATIO = 0.55
STRICT_MAX_DIRECTION_FLIPS = 0


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


def _config(store: Any) -> dict[str, Any]:
    try:
        value = store.config()
    except Exception:
        value = {}
    return value if isinstance(value, dict) else {}


def _strategy_enabled(store: Any, strategy: str) -> bool:
    cfg = _config(store)
    key = f"strategy_{strategy.lower()}_enabled"
    if key in cfg:
        return bool(cfg[key])
    # One-release migration: the old single toggle controls both A/B groups
    # until the new keys are persisted by DEFAULT_CONFIG.
    legacy_key = f"strategy_{LEGACY_STRATEGY.lower()}_enabled"
    if legacy_key in cfg:
        return bool(cfg[legacy_key])
    return True


def _strategy_stake(store: Any, strategy: str, fallback: float) -> float:
    value = _finite(_config(store).get(f"strategy_{strategy.lower()}_stake"))
    return value if value is not None and value > 0 else float(fallback)


def stable_direction_base_decision(
    raw_top_ask: Any,
    midpoint_delta: Any,
) -> dict[str, Any]:
    raw = _finite(raw_top_ask)
    delta = _finite(midpoint_delta)
    price_passed = bool(
        raw is not None
        and MIN_RAW_TOP_ASK <= raw < MAX_RAW_TOP_ASK_EXCLUSIVE
    )
    midpoint_passed = bool(
        delta is not None and delta >= MIN_MIDPOINT_DELTA
    )
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
        "strategy": BASE_STRATEGY,
        "sourceStrategy": SOURCE_STRATEGY,
        "allowed": allowed,
        "status": "ALLOW" if allowed else "BLOCK",
        "reason": (
            "allowed: confirmed Microprice direction, raw Ask 0.60-<0.90, "
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


# Backward-compatible name: the former single strategy is now BASE.
stable_direction_signal_decision = stable_direction_base_decision


def stable_direction_strict_decision(
    raw_top_ask: Any,
    midpoint_delta: Any,
    observer_context: dict[str, Any] | None,
) -> dict[str, Any]:
    base = stable_direction_base_decision(raw_top_ask, midpoint_delta)
    observer = dict(observer_context or {})
    crossovers = _finite(observer.get("currentEffectiveCrossovers"))
    short_er = _finite(observer.get("currentShortEr"))
    median_er = _finite(observer.get("currentMedianEr60s"))
    selected_er = short_er if short_er is not None else median_er
    both_touched = observer.get("currentBothSidesTouched")
    aligned = observer.get("signalAlignedWithStartMove")
    direction_flips = _finite(observer.get("directionFlipCount"))
    market_matches = observer.get("marketMatches")
    data_quality = str(observer.get("dataQualityStatus") or "MISSING")
    indicator_readiness = str(
        observer.get("indicatorReadiness") or "NOT_READY"
    )

    checks = {
        "basePassed": base["allowed"] is True,
        "marketMatches": market_matches is True,
        "dataQualityReady": data_quality == "READY",
        "indicatorReady": indicator_readiness == "READY",
        "effectiveCrossoversPassed": bool(
            crossovers is not None
            and 0 <= crossovers <= STRICT_MAX_EFFECTIVE_CROSSOVERS
        ),
        "efficiencyRatioPassed": bool(
            selected_er is not None
            and selected_er >= STRICT_MIN_EFFICIENCY_RATIO
        ),
        "oneSidedTouchPassed": both_touched is False,
        "startMoveAlignmentPassed": aligned is True,
        "directionFlipPassed": bool(
            direction_flips is not None
            and 0 <= direction_flips <= STRICT_MAX_DIRECTION_FLIPS
        ),
    }
    allowed = all(checks.values())
    blockers: list[str] = []
    if not checks["basePassed"]:
        blockers.append(base["reason"])
    if not checks["marketMatches"]:
        blockers.append("Observer current market does not match the source market")
    if not checks["dataQualityReady"]:
        blockers.append(f"Observer data quality is {data_quality}")
    if not checks["indicatorReady"]:
        blockers.append(f"Observer indicator readiness is {indicator_readiness}")
    if not checks["effectiveCrossoversPassed"]:
        blockers.append(
            "current effective crossovers must be available and <= "
            f"{STRICT_MAX_EFFECTIVE_CROSSOVERS}"
        )
    if not checks["efficiencyRatioPassed"]:
        blockers.append(
            "current short/median ER must be available and >= "
            f"{STRICT_MIN_EFFICIENCY_RATIO:.2f}"
        )
    if not checks["oneSidedTouchPassed"]:
        blockers.append("both outcome sides must not have touched the threshold")
    if not checks["startMoveAlignmentPassed"]:
        blockers.append("signal side must align with the current start-price move")
    if not checks["directionFlipPassed"]:
        blockers.append("confirmation direction changed earlier in this market")

    return {
        "version": PATCH_VERSION,
        "strategy": STRICT_STRATEGY,
        "sourceStrategy": SOURCE_STRATEGY,
        "allowed": allowed,
        "status": "ALLOW" if allowed else "BLOCK",
        "reason": (
            "allowed: BASE passed plus one-sided, efficient, aligned trend confirmation"
            if allowed
            else "; ".join(blockers)
        ),
        "rawTopAsk": base["rawTopAsk"],
        "midpointDelta": base["midpointDelta"],
        "selectedEfficiencyRatio": selected_er,
        "currentShortEr": short_er,
        "currentMedianEr60s": median_er,
        "currentEffectiveCrossovers": crossovers,
        "currentBothSidesTouched": both_touched,
        "currentStartMoveBps": _finite(observer.get("currentStartMoveBps")),
        "signalAlignedWithStartMove": aligned,
        "directionFlipCount": direction_flips,
        "dataQualityStatus": data_quality,
        "indicatorReadiness": indicator_readiness,
        "marketMatches": market_matches,
        "checks": checks,
        "usesObserver": True,
        "usesF1RangeScore": False,
        "paperOnly": True,
    }


def _trade_exists(store: Any, strategy: str, market_id: int) -> bool:
    candidates = (strategy,)
    if strategy == BASE_STRATEGY:
        candidates = (BASE_STRATEGY, LEGACY_STRATEGY)
    try:
        placeholders = ",".join("?" for _ in candidates)
        return store.db.execute(
            f"SELECT 1 FROM trades WHERE strategy IN ({placeholders}) "
            "AND market_id=? LIMIT 1",
            (*candidates, int(market_id)),
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
        return {
            "sourceTradeId": None,
            "sourceStrategyVersion": None,
            "diagnostics": {},
        }
    return {
        "sourceTradeId": int(row["id"]),
        "sourceStrategyVersion": row["strategy_version"],
        "diagnostics": _decode_json(row["diagnostics_json"]),
    }


def _observer_context(
    engine: Any,
    *,
    market_id: int,
    side: str,
    direction_flip_count: int,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "currentMarketId": None,
        "marketMatches": False,
        "currentEffectiveCrossovers": None,
        "currentShortEr": None,
        "currentMedianEr60s": None,
        "currentBothSidesTouched": None,
        "currentStartMoveBps": None,
        "signalAlignedWithStartMove": None,
        "directionFlipCount": int(direction_flip_count),
        "dataQualityStatus": "MISSING_OR_STALE",
        "indicatorReadiness": "NOT_READY",
    }
    observer = getattr(engine, "market_observer", None)
    if observer is None:
        context["observerError"] = "market_observer unavailable"
        return context
    try:
        gate = observer.m01o_entry_gate(
            min_settled_samples=0,
            min_current_range_score=0,
            profile="F1",
            now_ts=time.time(),
        )
    except Exception as exc:
        context["observerError"] = str(exc)
        return context

    context.update(
        {
            "currentMarketId": gate.get("currentMarketId"),
            "currentEffectiveCrossovers": gate.get(
                "currentEffectiveCrossovers"
            ),
            "currentShortEr": gate.get("currentShortEr"),
            "currentMedianEr60s": gate.get("currentMedianEr60s"),
            "currentBothSidesTouched": gate.get(
                "currentBothSidesTouched"
            ),
            "dataQualityStatus": gate.get("dataQualityStatus"),
            "indicatorReadiness": gate.get("indicatorReadiness"),
            "observerGateSnapshot": gate,
        }
    )
    try:
        observed_market_id = int(gate.get("currentMarketId"))
    except (TypeError, ValueError):
        observed_market_id = -1
    context["marketMatches"] = observed_market_id == int(market_id)

    lock = getattr(observer, "lock", None)
    manager = lock if lock is not None else nullcontext()
    try:
        with manager:
            start_price = _finite(getattr(observer, "start_price", None))
            spot_price = _finite(getattr(observer, "last_spot_price", None))
    except Exception:
        start_price = None
        spot_price = None
    start_move_bps = (
        (spot_price - start_price) / start_price * 10_000.0
        if start_price is not None
        and spot_price is not None
        and start_price > 0
        else None
    )
    context["currentStartMoveBps"] = start_move_bps
    context["signalAlignedWithStartMove"] = bool(
        start_move_bps is not None
        and (
            (side == "UP" and start_move_bps > 0)
            or (side == "DOWN" and start_move_bps < 0)
        )
    )
    return context


def _open_shadow(
    store: Any,
    source: dict[str, Any],
    fee_bps: int,
    *,
    strategy: str,
    decision: dict[str, Any],
    source_context: dict[str, Any],
) -> dict[str, Any] | None:
    try:
        market_id = int(source["market_id"])
        topic_id = int(source["topic_id"])
        side = str(source["side"]).upper()
        entry = float(source["entry_price"])
        source_stake = float(source["stake"])
    except (KeyError, TypeError, ValueError):
        return None
    if (
        side not in {"UP", "DOWN"}
        or decision.get("allowed") is not True
        or not _strategy_enabled(store, strategy)
        or _trade_exists(store, strategy, market_id)
    ):
        return None

    stake = _strategy_stake(store, strategy, source_stake)
    strict = strategy == STRICT_STRATEGY
    diagnostics = source_context["diagnostics"]
    shadow_diagnostics = {
        "paper_only": True,
        "live_orders_affected": False,
        "shadow_only": True,
        "forward_only": True,
        "source_strategy": SOURCE_STRATEGY,
        "source_trade_id": source_context["sourceTradeId"],
        "source_strategy_version": source_context[
            "sourceStrategyVersion"
        ],
        "source_already_microprice_confirmed": True,
        "variant_mode": (
            "FOLLOW_CONFIRMED_IMBALANCE_STABLE_DIRECTION_STRICT"
            if strict
            else "FOLLOW_CONFIRMED_IMBALANCE_STABLE_DIRECTION_BASE"
        ),
        "selected_side": side,
        "raw_top_ask": decision.get("rawTopAsk"),
        "midpoint_delta": decision.get("midpointDelta"),
        "stable_direction_decision": decision,
        "entry_time_telemetry": {
            key: decision.get(key)
            for key in (
                "currentEffectiveCrossovers",
                "currentShortEr",
                "currentMedianEr60s",
                "currentBothSidesTouched",
                "currentStartMoveBps",
                "signalAlignedWithStartMove",
                "directionFlipCount",
                "dataQualityStatus",
                "indicatorReadiness",
                "marketMatches",
            )
        },
        "rule": {
            "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
            "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "minimumSelectedMidpointDeltaInclusive": MIN_MIDPOINT_DELTA,
            "maximumEffectiveCrossoversInclusive": (
                STRICT_MAX_EFFECTIVE_CROSSOVERS if strict else None
            ),
            "minimumShortOrMedianErInclusive": (
                STRICT_MIN_EFFICIENCY_RATIO if strict else None
            ),
            "bothSidesTouchedMustBeFalse": strict,
            "signalMustAlignWithStartMove": strict,
            "maximumDirectionFlipsInclusive": (
                STRICT_MAX_DIRECTION_FLIPS if strict else None
            ),
            "sourceMustBeMicropriceConfirm": True,
            "observerRequired": strict,
            "f1RangeScoreRequired": False,
            "missingDataFailsClosed": True,
        },
        "source_diagnostics": diagnostics,
    }
    store.open_trade(
        strategy=strategy,
        topic_id=topic_id,
        market_id=market_id,
        side=side,
        entry=entry,
        target=None,
        stake=stake,
        fee_rate_bps=int(fee_bps),
        note=(
            f"{strategy} mirrored from {SOURCE_STRATEGY} trade "
            f"#{source_context['sourceTradeId'] or '?'}; paper only"
        ),
        strategy_version=PATCH_VERSION,
        diagnostics=shadow_diagnostics,
    )
    return {
        **source,
        "strategy": strategy,
        "stake": stake,
        "paper_only": True,
        "shadow_only": True,
        "live_orders_affected": False,
        "live_forwardable_when_selected": False,
        "stable_direction_decision": decision,
        "strategy_version": PATCH_VERSION,
        "source_strategy": SOURCE_STRATEGY,
        "source_trade_id": source_context["sourceTradeId"],
    }


def open_stable_direction_shadow(
    store: Any,
    source: dict[str, Any],
    fee_bps: int,
) -> dict[str, Any] | None:
    """Backward-compatible BASE opener."""
    if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
        return None
    try:
        market_id = int(source["market_id"])
    except (KeyError, TypeError, ValueError):
        return None
    source_context = _source_context(store, market_id)
    diagnostics = source_context["diagnostics"]
    raw_top_ask = source.get("raw_top_ask", diagnostics.get("raw_top_ask"))
    midpoint_delta = source.get(
        "midpoint_delta", diagnostics.get("midpoint_delta")
    )
    decision = stable_direction_base_decision(raw_top_ask, midpoint_delta)
    return _open_shadow(
        store,
        source,
        fee_bps,
        strategy=BASE_STRATEGY,
        decision=decision,
        source_context=source_context,
    )


def open_stable_direction_strict_shadow(
    store: Any,
    source: dict[str, Any],
    fee_bps: int,
    observer_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
        return None
    try:
        market_id = int(source["market_id"])
    except (KeyError, TypeError, ValueError):
        return None
    source_context = _source_context(store, market_id)
    diagnostics = source_context["diagnostics"]
    raw_top_ask = source.get("raw_top_ask", diagnostics.get("raw_top_ask"))
    midpoint_delta = source.get(
        "midpoint_delta", diagnostics.get("midpoint_delta")
    )
    decision = stable_direction_strict_decision(
        raw_top_ask,
        midpoint_delta,
        observer_context,
    )
    return _open_shadow(
        store,
        source,
        fee_bps,
        strategy=STRICT_STRATEGY,
        decision=decision,
        source_context=source_context,
    )


def _patch_tracker_process() -> None:
    tracker_class = _variants.MicropriceVariantTracker
    original = tracker_class.process
    if getattr(original, "_stable_direction_shadow_ab_v3", False):
        return

    @wraps(original)
    def process_with_stable_direction_ab(
        self: Any,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            market_id = int(snapshot.get("market_id"))
        except (TypeError, ValueError):
            market_id = -1
        previous_market = getattr(
            self, "_stable_direction_ab_market_id", None
        )
        if previous_market != market_id:
            self._stable_direction_ab_market_id = market_id
            self._stable_direction_ab_direction_flips = 0
        before_flips = int(
            getattr(self, "rejected_direction_change", 0) or 0
        )
        opened = list(original(self, snapshot, int(fee_bps), context) or [])
        after_flips = int(
            getattr(self, "rejected_direction_change", 0) or 0
        )
        if after_flips > before_flips:
            self._stable_direction_ab_direction_flips = int(
                getattr(self, "_stable_direction_ab_direction_flips", 0)
            ) + (after_flips - before_flips)

        result = list(opened)
        for source in opened:
            if str(source.get("strategy") or "").upper() != SOURCE_STRATEGY:
                continue
            base = open_stable_direction_shadow(
                self.store, source, int(fee_bps)
            )
            if base is not None:
                result.append(base)
            try:
                source_market_id = int(source["market_id"])
                source_side = str(source["side"]).upper()
            except (KeyError, TypeError, ValueError):
                continue
            observer = _observer_context(
                self.engine,
                market_id=source_market_id,
                side=source_side,
                direction_flip_count=int(
                    getattr(
                        self,
                        "_stable_direction_ab_direction_flips",
                        0,
                    )
                ),
            )
            strict = open_stable_direction_strict_shadow(
                self.store,
                source,
                int(fee_bps),
                observer,
            )
            if strict is not None:
                result.append(strict)
        return result

    process_with_stable_direction_ab._stable_direction_shadow_ab_v3 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_stable_direction_ab


def _register_research_strategies() -> None:
    common = {
        "horizon": HORIZON_SECONDS,
        "max_ask": MAX_RAW_TOP_ASK_EXCLUSIVE,
        "min_raw_top_ask": MIN_RAW_TOP_ASK,
        "max_raw_top_ask_exclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
        "min_midpoint_delta": MIN_MIDPOINT_DELTA,
    }
    register_shadow_strategy(
        BASE_STRATEGY,
        parameters=dict(common),
        generic_signal=False,
    )
    register_shadow_strategy(
        STRICT_STRATEGY,
        parameters={
            **common,
            "max_effective_crossovers": float(
                STRICT_MAX_EFFECTIVE_CROSSOVERS
            ),
            "min_efficiency_ratio": STRICT_MIN_EFFICIENCY_RATIO,
            "max_direction_flips": float(STRICT_MAX_DIRECTION_FLIPS),
        },
        generic_signal=False,
    )


def install_microprice_confirm_stable_direction_shadow() -> None:
    """Register two derived Paper Shadows; never evaluate or forward live."""
    _register_research_strategies()
    _patch_tracker_process()
