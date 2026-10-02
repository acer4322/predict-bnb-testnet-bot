from __future__ import annotations

import json
from functools import wraps
from typing import Any

from .microprice_confirm_stable_consensus_guard import (
    MAX_RAW_TOP_ASK_EXCLUSIVE,
    MIN_RAW_TOP_ASK,
    OBSERVER_VERSION,
    PATCH_VERSION,
    SOURCE_STRATEGY,
    STRATEGY,
    stable_consensus_signal_decision,
)


DASHBOARD_VERSION = "MICROPRICE_CONFIRM_STABLE_CONSENSUS_DASHBOARD_V1"


def _decode_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        decoded = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _f1_gate(diagnostics: dict[str, Any]) -> dict[str, Any] | None:
    contexts = [diagnostics]
    realtime = diagnostics.get("realtime_context")
    if isinstance(realtime, dict):
        contexts.insert(0, realtime)
    for context in contexts:
        gates = context.get("m01o_observer_gates")
        if isinstance(gates, dict) and isinstance(gates.get("F1"), dict):
            return dict(gates["F1"])
        for key in (
            "strategy_observer_gate",
            "m01o_observer_gate",
            "observer_gate",
        ):
            gate = context.get(key)
            if isinstance(gate, dict):
                return dict(gate)
    return None


def _raw_top_ask(diagnostics: dict[str, Any]) -> Any:
    for context in (
        diagnostics,
        diagnostics.get("source_diagnostics"),
        diagnostics.get("realtime_context"),
    ):
        if isinstance(context, dict) and context.get("raw_top_ask") is not None:
            return context.get("raw_top_ask")
    return None


def _is_settled(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").upper() in {
        "SETTLED_WIN",
        "SETTLED_LOSS",
    }


def _performance(rows: list[dict[str, Any]]) -> dict[str, Any]:
    settled = [row for row in rows if _is_settled(row)]
    wins = sum(
        str(row.get("status") or "").upper() == "SETTLED_WIN"
        for row in settled
    )
    pnl = sum(float(row.get("pnl") or 0.0) for row in settled)
    return {
        "trades": len(rows),
        "open": sum(
            str(row.get("status") or "").upper() == "OPEN"
            for row in rows
        ),
        "settled": len(settled),
        "wins": int(wins),
        "losses": len(settled) - int(wins),
        "winRatePct": (
            float(wins) / len(settled) * 100.0
            if settled
            else None
        ),
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
            (SOURCE_STRATEGY, STRATEGY),
        ).fetchall()
    except Exception:
        rows = []

    normalized = [dict(row) for row in rows]
    sources = [row for row in normalized if row.get("strategy") == SOURCE_STRATEGY]
    shadows = [row for row in normalized if row.get("strategy") == STRATEGY]

    evaluations: list[dict[str, Any]] = []
    for row in sources:
        diagnostics = _decode_json(row.get("diagnostics_json"))
        gate = _f1_gate(diagnostics)
        decision = stable_consensus_signal_decision(
            gate,
            _raw_top_ask(diagnostics),
            expected_market_id=int(row["market_id"]),
        )
        evaluations.append(
            {
                "source": row,
                "decision": decision,
            }
        )

    allowed = [
        item for item in evaluations
        if item["decision"].get("allowed") is True
    ]
    transition_blocked = [
        item for item in evaluations
        if item["decision"].get("transitionRisk") is True
    ]
    price_blocked = [
        item for item in evaluations
        if item["decision"].get("allowed") is not True
        and item["decision"].get("transitionRisk") is not True
        and item["decision"].get("priceBandPassed") is False
    ]
    unavailable = [
        item for item in evaluations
        if item["decision"].get("allowed") is not True
        and item not in transition_blocked
        and item not in price_blocked
    ]
    blocked = [*transition_blocked, *price_blocked]
    blocked_rows = [item["source"] for item in blocked]

    return {
        "version": DASHBOARD_VERSION,
        "strategyVersion": PATCH_VERSION,
        "strategy": STRATEGY,
        "observerVersion": OBSERVER_VERSION,
        "sourceStrategy": SOURCE_STRATEGY,
        "paperOnly": True,
        "liveSelectable": True,
        "observerSelectable": True,
        "liveOrdersAffected": False,
        "rule": {
            "minimumRawTopAskInclusive": MIN_RAW_TOP_ASK,
            "maximumRawTopAskExclusive": MAX_RAW_TOP_ASK_EXCLUSIVE,
            "historicalTransitionGuard": True,
            "causalHistoricalSettlementsOnly": True,
            "missingDataFailsClosed": True,
        },
        "evaluations": len(evaluations),
        "allowedEvaluations": len(allowed),
        "blockedEvaluations": len(blocked),
        "transitionBlockedEvaluations": len(transition_blocked),
        "priceBandBlockedEvaluations": len(price_blocked),
        "unavailableEvaluations": len(unavailable),
        "shadowPerformance": _performance(shadows),
        "blockedSourceCounterfactual": _performance(blocked_rows),
        "sourcePerformance": _performance(sources),
        "recentDecisions": [
            {
                "marketId": int(item["source"]["market_id"]),
                "openedAt": item["source"].get("opened_at"),
                "side": item["source"].get("side"),
                "entryPrice": item["source"].get("entry_price"),
                "status": item["source"].get("status"),
                "pnl": item["source"].get("pnl"),
                "decision": item["decision"],
            }
            for item in evaluations[-20:][::-1]
        ],
    }


def _inject_dashboard(payload: dict[str, Any], store: Any) -> dict[str, Any]:
    experiment = _database_state(store)

    research = payload.get("researchForward")
    if isinstance(research, dict):
        research["micropriceConfirmStableConsensusGuard"] = experiment
        strategies = research.get("strategies")
        if isinstance(strategies, dict):
            stats = experiment["shadowPerformance"]
            strategies[STRATEGY] = {
                "enabled": True,
                "stakeUsdt": None,
                "paperOnly": True,
                "liveOrdersAffected": False,
                "selectedBacktestParameters": {
                    **experiment["rule"],
                    "sourceStrategy": SOURCE_STRATEGY,
                    "observerVersion": OBSERVER_VERSION,
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
            }

    summaries = payload.get("summaries")
    if isinstance(summaries, dict):
        stats = experiment["shadowPerformance"]
        summaries[STRATEGY] = {
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
        original,
        "_stable_consensus_dashboard_v1",
        False,
    ):
        return

    @wraps(original)
    def dashboard_with_stable_consensus(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        return _inject_dashboard(payload, self)

    dashboard_with_stable_consensus._stable_consensus_dashboard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_stable_consensus


def _patch_store_on_engine_init(realtime: Any) -> None:
    engine_class = realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_stable_consensus_dashboard_v1", False):
        return

    @wraps(original_init)
    def init_with_stable_consensus_dashboard(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        store = getattr(self, "store", None)
        if store is not None:
            _wrap_dashboard(type(store))

    init_with_stable_consensus_dashboard._stable_consensus_dashboard_v1 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_stable_consensus_dashboard


def install_microprice_confirm_stable_consensus_dashboard_patch() -> None:
    from . import m_realtime as realtime

    _patch_store_on_engine_init(realtime)
