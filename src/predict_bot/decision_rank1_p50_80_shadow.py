from __future__ import annotations

import json
import logging
from functools import wraps
from typing import Any

from .decision_rank1_snapshot_v2 import rank1_snapshot_decision
from .decision_strategy_rules import (
    EXCLUDED_FAMILIES,
    FAMILY_SOURCES,
    SOURCE_STRATEGIES,
    STAKE_USDT,
    effective_break_even,
)
from .research_strategy_registry_patch import register_shadow_strategy


LOGGER = logging.getLogger(__name__)
RANK1_P50_80_STRATEGY = "R_DECISION_RANK1_P50_80"
RANK1_P50_80_VERSION = "RANK1_SIGNAL_SNAPSHOT_V2_P50_80"
RANK1_P50_80_MIN_ENTRY = 0.50
RANK1_P50_80_MAX_ENTRY = 0.80
_CONFIG_MIGRATION_KEY = "decision_rank1_p50_80_shadow_initial_config"


def _initialize_config(store: Any) -> None:
    if getattr(store, "_read_only", False):
        return
    with store.lock:
        store.db.execute(
            """CREATE TABLE IF NOT EXISTS decision_strategy_install_state (
                   key TEXT PRIMARY KEY,
                   value TEXT NOT NULL,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        marker = store.db.execute(
            "SELECT 1 FROM decision_strategy_install_state WHERE key=?",
            (_CONFIG_MIGRATION_KEY,),
        ).fetchone()
        if marker is None:
            enabled_key = f"strategy_{RANK1_P50_80_STRATEGY.lower()}_enabled"
            stake_key = f"strategy_{RANK1_P50_80_STRATEGY.lower()}_stake"
            store.db.execute(
                """INSERT INTO config(key, value) VALUES (?, 1)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (enabled_key,),
            )
            store.db.execute(
                """INSERT INTO config(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (stake_key, STAKE_USDT),
            )
            store.db.execute(
                "INSERT INTO decision_strategy_install_state(key, value) VALUES (?, 'applied')",
                (_CONFIG_MIGRATION_KEY,),
            )
            store.db.commit()
            cache = getattr(store, "_config_cache", None)
            if isinstance(cache, dict):
                cache[enabled_key] = True
                cache[stake_key] = STAKE_USDT


def _enabled(store: Any) -> bool:
    try:
        return bool(
            store.config().get(
                f"strategy_{RANK1_P50_80_STRATEGY.lower()}_enabled",
                True,
            )
        )
    except Exception:
        return True


def _trigger_source(
    decision_store: Any,
    store: Any,
    opened: list[dict[str, Any]],
    market_id: int,
) -> dict[str, Any] | None:
    rows: dict[int, dict[str, Any]] = {}
    for candidate in opened:
        strategy = str(candidate.get("strategy") or "").upper()
        if strategy not in SOURCE_STRATEGIES:
            continue
        source = decision_store.latest_trade(store, strategy, market_id)
        if source is not None:
            rows[int(source["id"])] = source
    return max(rows.values(), key=lambda row: int(row["id"])) if rows else None


def _evaluate(
    decision_store: Any,
    tracker: Any,
    opened: list[dict[str, Any]],
    snapshot: dict[str, Any],
    fee_bps: int,
    context: dict[str, Any],
) -> list[dict[str, Any]]:
    store = tracker.store
    market_id = int(snapshot["market_id"])
    trigger = _trigger_source(decision_store, store, opened, market_id)
    if trigger is None:
        return []
    trigger_id = int(trigger["id"])
    as_of = str(snapshot.get("timestamp") or trigger["opened_at"])

    if decision_store._evaluation_exists(store, RANK1_P50_80_STRATEGY, trigger_id):
        return []
    if not _enabled(store):
        decision_store.record_evaluation(
            store,
            controller=RANK1_P50_80_STRATEGY,
            trigger=trigger,
            status="DISABLED",
            reason="P50_80 Paper shadow is disabled",
            decision={},
            trend=None,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={"version": RANK1_P50_80_VERSION},
            created_at=as_of,
        )
        return []
    if store.has_trade(RANK1_P50_80_STRATEGY, market_id):
        decision_store.record_evaluation(
            store,
            controller=RANK1_P50_80_STRATEGY,
            trigger=trigger,
            status="ALREADY_OPEN",
            reason="one P50_80 Paper trade per market",
            decision={},
            trend=None,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={"version": RANK1_P50_80_VERSION},
            created_at=as_of,
        )
        return []

    families = decision_store.family_state(store, market_id, as_of)
    decision = rank1_snapshot_decision(families)
    if decision.get("status") != "CANDIDATE":
        decision_store.record_evaluation(
            store,
            controller=RANK1_P50_80_STRATEGY,
            trigger=trigger,
            status=str(decision.get("status") or "ABSTAIN"),
            reason=str(decision.get("reason") or "P50_80 shadow abstained"),
            decision=decision,
            trend=None,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={
                "version": RANK1_P50_80_VERSION,
                "familyState": families,
                "decision": decision,
                "entryRange": [RANK1_P50_80_MIN_ENTRY, RANK1_P50_80_MAX_ENTRY],
            },
            created_at=as_of,
        )
        return []

    side = str(decision["side"])
    trend = decision_store.trend_gate(store, snapshot, side)
    if trend.get("passed") is not True:
        decision_store.record_evaluation(
            store,
            controller=RANK1_P50_80_STRATEGY,
            trigger=trigger,
            status=str(trend.get("status") or "BLOCK_TREND"),
            reason=str(trend.get("reason") or "trend gate blocked"),
            decision=decision,
            trend=trend,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={
                "version": RANK1_P50_80_VERSION,
                "decision": decision,
                "trend": trend,
                "entryRange": [RANK1_P50_80_MIN_ENTRY, RANK1_P50_80_MAX_ENTRY],
            },
            created_at=as_of,
        )
        return []

    execution, execution_reason = decision_store.execution_candidate(
        snapshot, context, side
    )
    if execution is None:
        decision_store.record_evaluation(
            store,
            controller=RANK1_P50_80_STRATEGY,
            trigger=trigger,
            status="BLOCK_EXECUTION",
            reason=execution_reason,
            decision=decision,
            trend=trend,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={
                "version": RANK1_P50_80_VERSION,
                "decision": decision,
                "trend": trend,
                "entryRange": [RANK1_P50_80_MIN_ENTRY, RANK1_P50_80_MAX_ENTRY],
            },
            created_at=as_of,
        )
        return []

    entry = float(execution["entryPrice"])
    controller_cost = effective_break_even(entry, float(fee_bps))
    final_edge = float(decision["estimatedProbability"]) - controller_cost
    diagnostics = {
        "paper_only": True,
        "live_orders_affected": False,
        "forward_only": True,
        "derived_shadow": True,
        "decision_strategy_controller": True,
        "version": RANK1_P50_80_VERSION,
        "controller": RANK1_P50_80_STRATEGY,
        "baselineOf": "R_DECISION_RANK1",
        "entryRange": {
            "minimumInclusive": RANK1_P50_80_MIN_ENTRY,
            "maximumExclusive": RANK1_P50_80_MAX_ENTRY,
            "appliedTo": "slippage_adjusted_entry_price",
        },
        "triggerSourceTradeId": trigger_id,
        "triggerSourceStrategy": str(trigger["strategy"]),
        "includedFamilies": list(FAMILY_SOURCES),
        "excludedFamilies": list(EXCLUDED_FAMILIES),
        "familyState": families,
        "decision": decision,
        "trend": trend,
        "execution": execution,
        "effectiveCost": controller_cost,
        "finalModelEdge": final_edge,
        "realtimeContext": json.loads(json.dumps(context, default=str)),
    }

    if not (
        RANK1_P50_80_MIN_ENTRY <= entry < RANK1_P50_80_MAX_ENTRY
    ):
        decision_store.record_evaluation(
            store,
            controller=RANK1_P50_80_STRATEGY,
            trigger=trigger,
            status="BLOCK_ENTRY_RANGE",
            reason="slippage-adjusted entry is outside [0.50, 0.80)",
            decision=decision,
            trend=trend,
            execution=execution,
            effective_cost=controller_cost,
            model_edge=final_edge,
            paper_trade_id=None,
            diagnostics=diagnostics,
            created_at=as_of,
        )
        return []

    store.open_trade(
        strategy=RANK1_P50_80_STRATEGY,
        topic_id=int(snapshot["topic_id"]),
        market_id=market_id,
        side=side,
        entry=entry,
        target=None,
        stake=STAKE_USDT,
        fee_rate_bps=int(fee_bps),
        note="Rank 1 V2 P50_80 Paper-only forward validation shadow",
        strategy_version=RANK1_P50_80_VERSION,
        model_probability=float(decision["estimatedProbability"]),
        model_edge=final_edge,
        model_sigma=None,
        diagnostics=diagnostics,
    )
    paper = decision_store.latest_trade(store, RANK1_P50_80_STRATEGY, market_id)
    paper_trade_id = int(paper["id"]) if paper is not None else None
    evaluation_id = decision_store.record_evaluation(
        store,
        controller=RANK1_P50_80_STRATEGY,
        trigger=trigger,
        status="OPENED",
        reason="Rank 1 V2 candidate passed the [0.50, 0.80) Paper entry filter",
        decision=decision,
        trend=trend,
        execution=execution,
        effective_cost=controller_cost,
        model_edge=final_edge,
        paper_trade_id=paper_trade_id,
        diagnostics=diagnostics,
        created_at=as_of,
    )
    return [
        {
            "strategy": RANK1_P50_80_STRATEGY,
            "topic_id": int(snapshot["topic_id"]),
            "market_id": market_id,
            "side": side,
            "entry_price": entry,
            "raw_top_ask": float(execution["rawTopAsk"]),
            "stake": STAKE_USDT,
            "seconds_left": float(snapshot["seconds_left"]),
            "book_age_ms": execution["bookAgeMs"],
            "fee_bps": int(fee_bps),
            "signal_timestamp": as_of,
            "paper_only": True,
            "live_orders_affected": False,
            "rank1_p50_80_shadow": True,
            "decision_strategy_controller": True,
            "decision_strategy_version": RANK1_P50_80_VERSION,
            "decision_evaluation_id": evaluation_id,
            "paper_trade_id": paper_trade_id,
            "selected_family": decision["selectedFamily"],
            "selected_source_trade_id": decision.get("selectedSourceTradeId"),
            "selected_source_signal_id": decision.get("selectedSourceSignalId"),
            "estimated_probability": decision["estimatedProbability"],
            "effective_cost": controller_cost,
            "agreement_weight": decision["agreementWeight"],
            "model_edge": final_edge,
            "trend_status": trend["status"],
        }
    ]


def _install_tracker_overlay(decision_store: Any) -> None:
    tracker_class = decision_store.DecisionStrategyTracker
    original = tracker_class.process
    if getattr(original, "_decision_rank1_p50_80", False):
        return

    @wraps(original)
    def process_with_p50_80(
        self: Any,
        opened: list[dict[str, Any]],
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results = list(original(self, opened, snapshot, fee_bps, context) or [])
        try:
            shadow = _evaluate(
                decision_store,
                self,
                opened,
                snapshot,
                fee_bps,
                context,
            )
        except Exception:
            LOGGER.exception("Rank 1 P50_80 Paper shadow evaluation failed")
            shadow = []
        return [*results, *shadow]

    process_with_p50_80._decision_rank1_p50_80 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_p50_80


def _install_dashboard_overlay(decision_store: Any) -> None:
    original = decision_store.experiment_state
    if getattr(original, "_decision_rank1_p50_80", False):
        return

    @wraps(original)
    def experiment_with_p50_80(store: Any) -> dict[str, Any]:
        payload = original(store)
        rank1_v2 = payload.setdefault("rank1V2", {})
        stats = decision_store.strategy_stats(store, RANK1_P50_80_STRATEGY)
        stats["mode"] = "RANK1_V2_P50_80_FORWARD_SHADOW"
        rank1_v2["p50_80Strategy"] = RANK1_P50_80_STRATEGY
        rank1_v2["p50_80EntryRange"] = {
            "minimumInclusive": RANK1_P50_80_MIN_ENTRY,
            "maximumExclusive": RANK1_P50_80_MAX_ENTRY,
            "appliedTo": "slippage_adjusted_entry_price",
        }
        rank1_v2["p50_80"] = stats
        return payload

    experiment_with_p50_80._decision_rank1_p50_80 = True  # type: ignore[attr-defined]
    decision_store.experiment_state = experiment_with_p50_80


def install_rank1_p50_80_shadow(store: Any) -> None:
    """Install the Paper-only Rank 1 V2 [0.50, 0.80) forward-validation shadow."""
    from . import decision_strategy_store as decision_store

    register_shadow_strategy(
        RANK1_P50_80_STRATEGY,
        parameters={
            "horizon": 300.0,
            "derived_only": 1.0,
            "native_event_driven": 1.0,
            "paper_shadow": 1.0,
            "minimum_entry": RANK1_P50_80_MIN_ENTRY,
            "maximum_entry_exclusive": RANK1_P50_80_MAX_ENTRY,
        },
        generic_signal=False,
    )
    _initialize_config(store)
    _install_tracker_overlay(decision_store)
    _install_dashboard_overlay(decision_store)
