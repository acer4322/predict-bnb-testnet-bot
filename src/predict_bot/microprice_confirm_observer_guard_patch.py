from __future__ import annotations

import json
import math
from functools import wraps
from typing import Any, Callable, Iterable


OBSERVER_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD"
COMBINATION_STRATEGY = OBSERVER_VERSION
SOURCE_STRATEGY = "R_MICROPRICE_CONFIRM"
PATCH_VERSION = "R_MICROPRICE_CONFIRM_OBSERVER_GUARD_V1"
MAX_HISTORICAL_RANGE_SCORE = 2
MIN_HISTORICAL_TREND_SCORE = 3
DEFAULT_MIN_SETTLED_SAMPLES = 6


def _append_unique(values: Iterable[str], item: str) -> tuple[str, ...]:
    return tuple(dict.fromkeys((*tuple(values), item)))


def _finite_number(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _extract_f1_gate(diagnostics: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(diagnostics, dict):
        return None
    context = diagnostics.get("realtime_context")
    if not isinstance(context, dict):
        context = diagnostics
    gates = context.get("m01o_observer_gates")
    if isinstance(gates, dict) and isinstance(gates.get("F1"), dict):
        return dict(gates["F1"])
    direct = context.get("m01o_observer_gate")
    return dict(direct) if isinstance(direct, dict) else None


def microprice_confirm_observer_guard_decision(
    gate: dict[str, Any] | None,
    *,
    expected_market_id: int | None = None,
) -> dict[str, Any]:
    """Evaluate the frozen causal transition guard and fail closed.

    Only the Observer's already-settled historical window is used.  The current
    market winner and the final current-round Observer values are never read.
    """

    result: dict[str, Any] = {
        "version": OBSERVER_VERSION,
        "ruleVersion": PATCH_VERSION,
        "allowed": False,
        "status": "BLOCK",
        "reason": "Observer gate is unavailable",
        "sourceStrategy": SOURCE_STRATEGY,
        "causalHistoricalSettlementsOnly": True,
        "currentRoundOutcomeUsed": False,
        "blockedCondition": {
            "historicalState": "UNCERTAIN",
            "historicalRangeScoreMaxInclusive": MAX_HISTORICAL_RANGE_SCORE,
            "historicalTrendScoreMinInclusive": MIN_HISTORICAL_TREND_SCORE,
        },
    }
    if not isinstance(gate, dict):
        return result

    profile = str(gate.get("profile") or "").strip().upper()
    quality = str(gate.get("dataQualityStatus") or "").strip().upper()
    result["profile"] = profile
    result["dataQualityStatus"] = quality
    if profile != "F1":
        result["reason"] = "Observer profile must be F1"
        return result
    if quality != "READY":
        result["reason"] = "Observer data quality is not READY"
        return result

    try:
        sample_count = int(gate["historicalSampleCount"])
        minimum_samples = int(
            gate.get("minSettledSamples") or DEFAULT_MIN_SETTLED_SAMPLES
        )
        gate_market_id = int(gate["currentMarketId"])
        range_score = int(gate["historicalRangeScore"])
        trend_score = int(gate["historicalTrendScore"])
    except (KeyError, TypeError, ValueError):
        result["reason"] = "Observer historical fields are incomplete"
        return result

    historical_state = str(gate.get("historicalState") or "").strip().upper()
    result.update(
        {
            "historicalState": historical_state,
            "historicalRangeScore": range_score,
            "historicalTrendScore": trend_score,
            "historicalSampleCount": sample_count,
            "historicalProvisional": bool(gate.get("historicalProvisional")),
            "minimumSettledSamples": minimum_samples,
            "currentMarketId": gate_market_id,
        }
    )
    if sample_count < minimum_samples:
        result["reason"] = (
            f"Observer history {sample_count}/{minimum_samples} is incomplete"
        )
        return result
    if expected_market_id is not None and gate_market_id != int(expected_market_id):
        result["reason"] = "Observer gate belongs to a different market"
        return result
    if historical_state not in {"RANGE", "UNCERTAIN", "TREND"}:
        result["reason"] = "Observer historical state is invalid"
        return result

    transition_risk = bool(
        historical_state == "UNCERTAIN"
        and range_score <= MAX_HISTORICAL_RANGE_SCORE
        and trend_score >= MIN_HISTORICAL_TREND_SCORE
    )
    result["transitionRisk"] = transition_risk
    result["allowed"] = not transition_risk
    result["status"] = "ALLOW" if not transition_risk else "BLOCK"
    result["reason"] = (
        "allowed: historical Observer is outside the trend-leaning UNCERTAIN transition zone"
        if not transition_risk
        else (
            "blocked: historical Observer is UNCERTAIN with "
            f"RANGE {range_score} <= {MAX_HISTORICAL_RANGE_SCORE} and "
            f"TREND {trend_score} >= {MIN_HISTORICAL_TREND_SCORE}"
        )
    )
    return result


def _patch_observer_dispatch(research: Any, live: Any) -> None:
    original = research.futures_lead_observer_decision
    if not getattr(original, "_microprice_confirm_observer_guard_v1", False):
        @wraps(original)
        def observer_decision_with_microprice_confirm_guard(
            version: str,
            gate: dict[str, Any] | None,
            *,
            expected_market_id: int | None = None,
        ) -> dict[str, Any]:
            normalized = str(version or "").strip().upper()
            if normalized == OBSERVER_VERSION:
                return microprice_confirm_observer_guard_decision(
                    gate,
                    expected_market_id=expected_market_id,
                )
            return original(
                version,
                gate,
                expected_market_id=expected_market_id,
            )

        observer_decision_with_microprice_confirm_guard._microprice_confirm_observer_guard_v1 = True  # type: ignore[attr-defined]
        research.futures_lead_observer_decision = (
            observer_decision_with_microprice_confirm_guard
        )

    research.FUTURES_LEAD_OBSERVER_VERSIONS = _append_unique(
        research.FUTURES_LEAD_OBSERVER_VERSIONS,
        OBSERVER_VERSION,
    )
    # live_trading imported both objects by value, so refresh its module globals.
    live.FUTURES_LEAD_OBSERVER_VERSIONS = tuple(
        research.FUTURES_LEAD_OBSERVER_VERSIONS
    )
    live.futures_lead_observer_decision = research.futures_lead_observer_decision
    live.LIVE_OBSERVER_STRATEGIES = _append_unique(
        live.LIVE_OBSERVER_STRATEGIES,
        SOURCE_STRATEGY,
    )


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


def _shadow_exists(store: Any, market_id: int) -> bool:
    try:
        row = store.db.execute(
            "SELECT 1 FROM trades WHERE strategy=? AND market_id=? LIMIT 1",
            (COMBINATION_STRATEGY, int(market_id)),
        ).fetchone()
    except Exception:
        return False
    return row is not None


def _wrap_open_trade(store_class: type[Any]) -> None:
    original = getattr(store_class, "open_trade", None)
    if not callable(original) or getattr(
        original, "_microprice_confirm_observer_guard_v1", False
    ):
        return

    @wraps(original)
    def open_trade_with_microprice_confirm_observer_guard(
        self: Any,
        *,
        strategy: str,
        topic_id: int,
        market_id: int,
        side: str,
        entry: float,
        target: float | None,
        stake: float,
        fee_rate_bps: int,
        note: str,
        strategy_version: str | None = None,
        model_probability: float | None = None,
        model_edge: float | None = None,
        model_sigma: float | None = None,
        diagnostics: dict[str, Any] | None = None,
    ) -> None:
        original(
            self,
            strategy=strategy,
            topic_id=topic_id,
            market_id=market_id,
            side=side,
            entry=entry,
            target=target,
            stake=stake,
            fee_rate_bps=fee_rate_bps,
            note=note,
            strategy_version=strategy_version,
            model_probability=model_probability,
            model_edge=model_edge,
            model_sigma=model_sigma,
            diagnostics=diagnostics,
        )

        if str(strategy or "").strip().upper() != SOURCE_STRATEGY:
            return
        gate = _extract_f1_gate(diagnostics)
        decision = microprice_confirm_observer_guard_decision(
            gate,
            expected_market_id=int(market_id),
        )
        if decision["allowed"] is not True or _shadow_exists(self, int(market_id)):
            return

        source_id = _source_trade_id(self, int(market_id))
        shadow_diagnostics = {
            "paper_only": True,
            "live_orders_affected": False,
            "shadow_only": True,
            "forward_only": True,
            "source_strategy": SOURCE_STRATEGY,
            "source_trade_id": source_id,
            "source_strategy_version": strategy_version,
            "observer_version": OBSERVER_VERSION,
            "observer_guard_version": PATCH_VERSION,
            "observer_decision": decision,
            "causal_historical_settlements_only": True,
            "current_round_outcome_used": False,
        }
        original(
            self,
            strategy=COMBINATION_STRATEGY,
            topic_id=int(topic_id),
            market_id=int(market_id),
            side=str(side).upper(),
            entry=float(entry),
            target=target,
            stake=float(stake),
            fee_rate_bps=int(fee_rate_bps),
            note=(
                f"{COMBINATION_STRATEGY} paper Shadow mirrored from "
                f"{SOURCE_STRATEGY} trade #{source_id or '?'}; never live-forwarded"
            ),
            strategy_version=PATCH_VERSION,
            model_probability=model_probability,
            model_edge=model_edge,
            model_sigma=model_sigma,
            diagnostics=shadow_diagnostics,
        )

    open_trade_with_microprice_confirm_observer_guard._microprice_confirm_observer_guard_v1 = True  # type: ignore[attr-defined]
    store_class.open_trade = open_trade_with_microprice_confirm_observer_guard


def _decode_diagnostics(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _settled(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").upper() in {
        "SETTLED_WIN",
        "SETTLED_LOSS",
    }


def _performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if _settled(row)]
    wins = sum(str(row.get("status") or "").upper() == "SETTLED_WIN" for row in settled)
    pnl = sum(float(row.get("pnl") or 0.0) for row in settled)
    return {
        "trades": len(rows),
        "open": sum(str(row.get("status") or "").upper() == "OPEN" for row in rows),
        "settled": len(settled),
        "wins": int(wins),
        "losses": len(settled) - int(wins),
        "winRatePct": (float(wins) / len(settled) * 100.0 if settled else None),
        "realizedPnl": pnl,
    }


def _database_state(store: Any) -> dict[str, Any]:
    try:
        rows = store.db.execute(
            """SELECT id, strategy, market_id, side, status, entry_price,
                      stake, pnl, opened_at, closed_at, diagnostics_json
                 FROM trades
                WHERE strategy IN (?, ?)
                ORDER BY id ASC""",
            (SOURCE_STRATEGY, COMBINATION_STRATEGY),
        ).fetchall()
    except Exception:
        rows = []
    normalized = [dict(row) for row in rows]
    sources = [row for row in normalized if row.get("strategy") == SOURCE_STRATEGY]
    shadows = [
        row for row in normalized if row.get("strategy") == COMBINATION_STRATEGY
    ]

    evaluations: list[dict[str, Any]] = []
    for row in sources:
        gate = _extract_f1_gate(_decode_diagnostics(row.get("diagnostics_json")))
        decision = microprice_confirm_observer_guard_decision(
            gate,
            expected_market_id=int(row["market_id"]),
        )
        evaluations.append({"source": row, "decision": decision})

    allowed = [item for item in evaluations if item["decision"]["allowed"] is True]
    blocked = [
        item
        for item in evaluations
        if item["decision"].get("transitionRisk") is True
    ]
    unavailable = [
        item
        for item in evaluations
        if item["decision"]["allowed"] is not True
        and item["decision"].get("transitionRisk") is not True
    ]
    blocked_source_rows = [item["source"] for item in blocked]
    return {
        "version": PATCH_VERSION,
        "observerVersion": OBSERVER_VERSION,
        "combinationStrategy": COMBINATION_STRATEGY,
        "sourceStrategy": SOURCE_STRATEGY,
        "paperOnly": True,
        "liveOrdersAffected": False,
        "liveSelectableAsObserverVersion": True,
        "registeredAsLiveStrategy": False,
        "causalHistoricalSettlementsOnly": True,
        "currentRoundOutcomeUsed": False,
        "rule": {
            "blockWhenHistoricalState": "UNCERTAIN",
            "maximumHistoricalRangeScore": MAX_HISTORICAL_RANGE_SCORE,
            "minimumHistoricalTrendScore": MIN_HISTORICAL_TREND_SCORE,
        },
        "evaluations": len(evaluations),
        "allowedEvaluations": len(allowed),
        "blockedEvaluations": len(blocked),
        "unavailableEvaluations": len(unavailable),
        "shadowPerformance": _performance(shadows),
        "blockedSourceCounterfactual": _performance(blocked_source_rows),
        "sourcePerformance": _performance(sources),
        "recentBlocked": [
            {
                "marketId": int(item["source"]["market_id"]),
                "openedAt": item["source"].get("opened_at"),
                "status": item["source"].get("status"),
                "pnl": item["source"].get("pnl"),
                "decision": item["decision"],
            }
            for item in blocked[-20:][::-1]
        ],
    }


def _inject_dashboard(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    experiment = _database_state(store)
    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceConfirmObserverGuard"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            stats = experiment["shadowPerformance"]
            strategies[COMBINATION_STRATEGY] = {
                "enabled": True,
                "stakeUsdt": None,
                "selectedBacktestParameters": {
                    **experiment["rule"],
                    "sourceStrategy": SOURCE_STRATEGY,
                    "paperOnly": True,
                    "causalHistoricalSettlementsOnly": True,
                },
                "chronologicalValidation": {
                    "status": (
                        "ANALYZABLE"
                        if int(stats["settled"]) >= 30
                        else "COLLECTING"
                    ),
                    "samples": int(stats["trades"]),
                    "settled": int(stats["settled"]),
                    "wins": int(stats["wins"]),
                    "losses": int(stats["losses"]),
                    "realizedPnl": float(stats["realizedPnl"]),
                    "minimum": 30,
                    "sourceMirrored": True,
                },
                "paperOnly": True,
                "liveOrdersAffected": False,
            }

    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        stats = experiment["shadowPerformance"]
        summaries[COMBINATION_STRATEGY] = {
            "trades": int(stats["trades"]),
            "open": int(stats["open"]),
            "wins": int(stats["wins"]),
            "losses": int(stats["losses"]),
            "realized_pnl": float(stats["realizedPnl"]),
        }
    return payload


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(
        original, "_microprice_confirm_observer_guard_v1", False
    ):
        return

    @wraps(original)
    def dashboard_with_microprice_confirm_observer_guard(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        return _inject_dashboard(payload, self)

    dashboard_with_microprice_confirm_observer_guard._microprice_confirm_observer_guard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_microprice_confirm_observer_guard


def _patch_store_on_engine_init(realtime: Any) -> None:
    engine_class = realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_microprice_confirm_observer_guard_v1", False):
        return

    @wraps(original_init)
    def init_with_microprice_confirm_observer_guard(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is None:
            return
        store_class = type(store)
        _wrap_open_trade(store_class)
        _wrap_dashboard(store_class)

    init_with_microprice_confirm_observer_guard._microprice_confirm_observer_guard_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_microprice_confirm_observer_guard


def install_microprice_confirm_observer_guard_patch() -> None:
    """Register a paper combination and a live-selectable Observer version.

    This deliberately does not add OBSERVER_VERSION to LIVE_SUPPORTED_STRATEGIES.
    Operators select R_MICROPRICE_CONFIRM as the strategy, then select this
    version in that strategy slot's existing Observer version control.
    """

    from . import live_trading as live
    from . import m_realtime as realtime
    from . import research_forward as research

    _patch_observer_dispatch(research, live)
    _patch_store_on_engine_init(realtime)
