from __future__ import annotations

import json
import logging
import math
import sys
import threading
from functools import wraps
from typing import Any

from .decision_strategy_rules import (
    EXCLUDED_FAMILIES,
    FAMILY_SOURCES,
    RANK1_DIRECTION_SHARE,
    RANK1_MIN_HISTORY,
    RANK1_STRATEGY,
    SOURCE_STRATEGIES,
    STAKE_USDT,
    VERSION,
    effective_break_even,
    rank1_decision as rank1_v1_decision,
)
from .research_strategy_registry_patch import register_shadow_strategy


LOGGER = logging.getLogger(__name__)
RANK1_LOGIC_VERSION = "RANK1_SIGNAL_SNAPSHOT_V2"
RANK1_V1_BASELINE_STRATEGY = "R_DECISION_RANK1_V1_SHADOW"
RANK1_V1_BASELINE_VERSION = "RANK1_ACTUAL_TRADE_OVERLAP_V1"
_BASELINE_MIGRATION_KEY = "decision_rank1_v1_shadow_initial_config"
_CAPTURE = threading.local()


def _ensure_schema(store: Any) -> None:
    if getattr(store, "_decision_rank1_snapshot_v2_schema", False):
        return
    if getattr(store, "_read_only", False):
        return
    with store.lock:
        store.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS decision_rank1_signal_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                market_id INTEGER NOT NULL,
                source_strategy TEXT NOT NULL,
                side TEXT NOT NULL CHECK(side IN ('UP','DOWN')),
                signal REAL NOT NULL,
                model_probability REAL,
                model_edge REAL,
                signal_timestamp_ns INTEGER NOT NULL,
                seconds_left REAL,
                captured_at TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                UNIQUE(market_id, source_strategy)
            );
            CREATE INDEX IF NOT EXISTS decision_rank1_signal_market_idx
                ON decision_rank1_signal_snapshots(market_id, source_strategy);
            """
        )
        store.db.commit()
    store._decision_rank1_snapshot_v2_schema = True


def _initialize_baseline_config(store: Any) -> None:
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
            (_BASELINE_MIGRATION_KEY,),
        ).fetchone()
        if marker is None:
            store.db.execute(
                """INSERT INTO config(key, value) VALUES (?, 1)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (f"strategy_{RANK1_V1_BASELINE_STRATEGY.lower()}_enabled",),
            )
            store.db.execute(
                """INSERT INTO config(key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value""",
                (
                    f"strategy_{RANK1_V1_BASELINE_STRATEGY.lower()}_stake",
                    STAKE_USDT,
                ),
            )
            store.db.execute(
                "INSERT INTO decision_strategy_install_state(key, value) VALUES (?, 'applied')",
                (_BASELINE_MIGRATION_KEY,),
            )
            store.db.commit()
            cache = getattr(store, "_config_cache", None)
            if isinstance(cache, dict):
                cache[
                    f"strategy_{RANK1_V1_BASELINE_STRATEGY.lower()}_enabled"
                ] = True
                cache[
                    f"strategy_{RANK1_V1_BASELINE_STRATEGY.lower()}_stake"
                ] = STAKE_USDT


def _record_signal_snapshot(
    store: Any,
    *,
    strategy: str,
    signal: dict[str, Any],
    current: dict[str, Any],
    snapshot: dict[str, Any],
) -> None:
    _ensure_schema(store)
    side = str(signal.get("side") or "").upper()
    if side not in {"UP", "DOWN"}:
        return
    try:
        signal_value = float(signal["signal"])
        timestamp_ns = int(float(current["timestamp_ns"]))
    except (KeyError, TypeError, ValueError):
        return
    if not math.isfinite(signal_value) or timestamp_ns <= 0:
        return

    def finite_or_none(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if math.isfinite(number) else None

    probability = finite_or_none(signal.get("model_probability"))
    edge = finite_or_none(signal.get("model_edge"))
    seconds_left = finite_or_none(current.get("seconds_left"))
    captured_at = str(snapshot.get("timestamp") or "")
    diagnostics = {
        "rank1LogicVersion": RANK1_LOGIC_VERSION,
        "captureStage": "SOURCE_SIGNAL_BEFORE_EXECUTION_CANDIDATE",
        "sourceStrategy": strategy,
        "signal": signal,
        "currentSecondsLeft": seconds_left,
        "marketId": int(snapshot["market_id"]),
        "paperEntryRequiredForVote": False,
    }
    with store.lock:
        store.db.execute(
            """INSERT INTO decision_rank1_signal_snapshots(
                   market_id, source_strategy, side, signal,
                   model_probability, model_edge, signal_timestamp_ns,
                   seconds_left, captured_at, diagnostics_json
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(market_id, source_strategy) DO UPDATE SET
                   side=excluded.side,
                   signal=excluded.signal,
                   model_probability=excluded.model_probability,
                   model_edge=excluded.model_edge,
                   signal_timestamp_ns=excluded.signal_timestamp_ns,
                   seconds_left=excluded.seconds_left,
                   captured_at=excluded.captured_at,
                   diagnostics_json=excluded.diagnostics_json
               WHERE excluded.signal_timestamp_ns >=
                     decision_rank1_signal_snapshots.signal_timestamp_ns""",
            (
                int(snapshot["market_id"]),
                strategy,
                side,
                signal_value,
                probability,
                edge,
                timestamp_ns,
                seconds_left,
                captured_at,
                json.dumps(diagnostics, sort_keys=True, default=str),
            ),
        )
        store.db.commit()


def latest_signal_snapshot(
    store: Any,
    strategy: str,
    market_id: int,
) -> dict[str, Any] | None:
    if not _table_exists(store, "decision_rank1_signal_snapshots"):
        return None
    with store.lock:
        row = store.db.execute(
            """SELECT id, market_id, source_strategy, side, signal,
                      model_probability, model_edge, signal_timestamp_ns,
                      seconds_left, captured_at
                 FROM decision_rank1_signal_snapshots
                WHERE market_id=? AND source_strategy=? LIMIT 1""",
            (int(market_id), strategy),
        ).fetchone()
    return dict(row) if row is not None else None


def _table_exists(store: Any, table: str) -> bool:
    try:
        with store.lock:
            return store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None
    except Exception:
        return False


def rank1_snapshot_decision(
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    voters: list[dict[str, Any]] = []
    for family, item in families.items():
        vote = item.get("vote")
        stats = item.get("rank1") or {}
        if vote is None or int(stats.get("n") or 0) < RANK1_MIN_HISTORY:
            continue
        utility = float(stats.get("utility") or 0.0)
        probability = float(stats.get("p") or 0.5)
        score = max(0.0, utility / STAKE_USDT) * max(
            0.0, 2.0 * probability - 1.0
        )
        voters.append(
            {
                "family": family,
                "vote": vote,
                "source": item.get("source"),
                "stats": stats,
                "score": score,
                "side": str(vote["side"]).upper(),
            }
        )

    if len(voters) < 2:
        return {
            "status": "WAITING_SIGNAL_OVERLAP",
            "reason": "fewer than two source families have same-market pre-execution signals",
            "logicVersion": RANK1_LOGIC_VERSION,
            "voterCount": len(voters),
        }

    side_weights = {
        side: sum(item["score"] for item in voters if item["side"] == side)
        for side in ("UP", "DOWN")
    }
    total_weight = sum(side_weights.values())
    if total_weight <= 0:
        return {
            "status": "NO_POSITIVE_WEIGHT",
            "reason": "no snapshot voter has positive historical reliability weight",
            "logicVersion": RANK1_LOGIC_VERSION,
            "sideWeights": side_weights,
        }

    side = max(side_weights, key=side_weights.get)
    agreement = side_weights[side] / total_weight
    same_side_voters = [item for item in voters if item["side"] == side]
    positive_supporters = [
        item for item in same_side_voters if item["score"] > 0
    ]
    if len(same_side_voters) < 2 or agreement + 1e-12 < RANK1_DIRECTION_SHARE:
        return {
            "status": "NO_CONSENSUS",
            "reason": "requires two same-side signal snapshots and 67% historical-weight agreement",
            "logicVersion": RANK1_LOGIC_VERSION,
            "agreementWeight": agreement,
            "sideWeights": side_weights,
            "sameSideVoters": [item["family"] for item in same_side_voters],
        }
    if not positive_supporters:
        return {
            "status": "NO_POSITIVE_WEIGHT",
            "reason": "same-side snapshots exist but none carries positive historical weight",
            "logicVersion": RANK1_LOGIC_VERSION,
            "agreementWeight": agreement,
        }

    selected = max(
        positive_supporters,
        key=lambda item: (
            float(item["score"]),
            int(item["vote"].get("signal_timestamp_ns") or 0),
            int(item["vote"].get("id") or 0),
        ),
    )
    source = selected.get("source") or {}
    return {
        "status": "CANDIDATE",
        "reason": "same-market signal snapshots passed reliability-weighted consensus",
        "logicVersion": RANK1_LOGIC_VERSION,
        "side": side,
        "selectedFamily": selected["family"],
        "selectedSourceSignalId": int(selected["vote"]["id"]),
        "selectedSourceTradeId": (
            int(source["id"]) if source.get("id") is not None else None
        ),
        "estimatedProbability": float(selected["stats"].get("p") or 0.5),
        "agreementWeight": agreement,
        "supporters": [item["family"] for item in same_side_voters],
        "positiveWeightSupporters": [
            item["family"] for item in positive_supporters
        ],
        "zeroWeightConfirmers": [
            item["family"]
            for item in same_side_voters
            if item["score"] <= 0
        ],
        "familyScores": {
            item["family"]: float(item["score"]) for item in voters
        },
        "sideWeights": side_weights,
        "signalSnapshotIds": {
            item["family"]: int(item["vote"]["id"]) for item in voters
        },
    }


def _install_signal_capture(store_class: type[Any]) -> None:
    server = sys.modules.get("predict_bot.server")
    if server is None:
        return

    original_evaluate = getattr(store_class, "_evaluate_research_forward", None)
    if callable(original_evaluate) and not getattr(
        original_evaluate, "_decision_rank1_snapshot_v2", False
    ):
        @wraps(original_evaluate)
        def evaluate_with_capture_context(
            self: Any,
            snapshot: dict[str, Any],
            fee_bps: int,
            cfg: dict[str, Any],
            context: dict[str, Any],
            existing: set[str],
        ) -> list[dict[str, Any]]:
            previous = getattr(_CAPTURE, "value", None)
            _CAPTURE.value = {
                "store": self,
                "snapshot": snapshot,
            }
            try:
                return original_evaluate(
                    self, snapshot, fee_bps, cfg, context, existing
                )
            finally:
                _CAPTURE.value = previous

        evaluate_with_capture_context._decision_rank1_snapshot_v2 = True  # type: ignore[attr-defined]
        store_class._evaluate_research_forward = evaluate_with_capture_context

    original_signal = getattr(server, "research_signal_for_strategy", None)
    if callable(original_signal) and not getattr(
        original_signal, "_decision_rank1_snapshot_v2", False
    ):
        @wraps(original_signal)
        def signal_with_snapshot(
            strategy: str,
            current: dict[str, Any],
            previous: dict[str, Any] | None,
            **kwargs: Any,
        ) -> dict[str, Any] | None:
            result = original_signal(strategy, current, previous, **kwargs)
            if result is None or strategy not in SOURCE_STRATEGIES:
                return result
            capture = getattr(_CAPTURE, "value", None)
            if not isinstance(capture, dict):
                return result
            store = capture.get("store")
            snapshot = capture.get("snapshot")
            if store is None or not isinstance(snapshot, dict):
                return result
            try:
                _record_signal_snapshot(
                    store,
                    strategy=strategy,
                    signal=result,
                    current=current,
                    snapshot=snapshot,
                )
            except Exception:
                LOGGER.exception(
                    "Rank 1 V2 source signal snapshot capture failed for %s",
                    strategy,
                )
            return result

        signal_with_snapshot._decision_rank1_snapshot_v2 = True  # type: ignore[attr-defined]
        server.research_signal_for_strategy = signal_with_snapshot


def _install_family_state_overlay(decision_store: Any) -> None:
    original = decision_store.family_state
    if getattr(original, "_decision_rank1_snapshot_v2", False):
        return

    @wraps(original)
    def family_state_with_votes(
        store: Any,
        market_id: int,
        as_of: str,
    ) -> dict[str, dict[str, Any]]:
        result = original(store, market_id, as_of)
        for family, strategy in FAMILY_SOURCES.items():
            item = result.get(family)
            if isinstance(item, dict):
                item["vote"] = latest_signal_snapshot(store, strategy, market_id)
        return result

    family_state_with_votes._decision_rank1_snapshot_v2 = True  # type: ignore[attr-defined]
    decision_store.family_state = family_state_with_votes
    decision_store.rank1_decision = rank1_snapshot_decision


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


def _baseline_enabled(store: Any) -> bool:
    try:
        return bool(
            store.config().get(
                f"strategy_{RANK1_V1_BASELINE_STRATEGY.lower()}_enabled",
                True,
            )
        )
    except Exception:
        return True


def _evaluate_v1_baseline(
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
    if decision_store._evaluation_exists(
        store, RANK1_V1_BASELINE_STRATEGY, trigger_id
    ):
        return []
    if not _baseline_enabled(store):
        decision_store.record_evaluation(
            store,
            controller=RANK1_V1_BASELINE_STRATEGY,
            trigger=trigger,
            status="DISABLED",
            reason="V1 baseline is disabled in Paper config",
            decision={},
            trend=None,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={"version": RANK1_V1_BASELINE_VERSION},
            created_at=as_of,
        )
        return []
    if store.has_trade(RANK1_V1_BASELINE_STRATEGY, market_id):
        decision_store.record_evaluation(
            store,
            controller=RANK1_V1_BASELINE_STRATEGY,
            trigger=trigger,
            status="ALREADY_OPEN",
            reason="one V1 baseline trade per market",
            decision={},
            trend=None,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={"version": RANK1_V1_BASELINE_VERSION},
            created_at=as_of,
        )
        return []

    families = decision_store.family_state(store, market_id, as_of)
    decision = rank1_v1_decision(families)
    if decision.get("status") != "CANDIDATE":
        decision_store.record_evaluation(
            store,
            controller=RANK1_V1_BASELINE_STRATEGY,
            trigger=trigger,
            status=str(decision.get("status") or "ABSTAIN"),
            reason=str(decision.get("reason") or "V1 baseline abstained"),
            decision=decision,
            trend=None,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={
                "version": RANK1_V1_BASELINE_VERSION,
                "baselineOf": RANK1_STRATEGY,
                "familyState": families,
                "decision": decision,
            },
            created_at=as_of,
        )
        return []

    side = str(decision["side"])
    trend = decision_store.trend_gate(store, snapshot, side)
    if trend.get("passed") is not True:
        decision_store.record_evaluation(
            store,
            controller=RANK1_V1_BASELINE_STRATEGY,
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
                "version": RANK1_V1_BASELINE_VERSION,
                "baselineOf": RANK1_STRATEGY,
                "decision": decision,
                "trend": trend,
            },
            created_at=as_of,
        )
        return []

    execution, reason = decision_store.execution_candidate(snapshot, context, side)
    if execution is None:
        decision_store.record_evaluation(
            store,
            controller=RANK1_V1_BASELINE_STRATEGY,
            trigger=trigger,
            status="BLOCK_EXECUTION",
            reason=reason,
            decision=decision,
            trend=trend,
            execution=None,
            effective_cost=None,
            model_edge=None,
            paper_trade_id=None,
            diagnostics={
                "version": RANK1_V1_BASELINE_VERSION,
                "baselineOf": RANK1_STRATEGY,
                "decision": decision,
                "trend": trend,
            },
            created_at=as_of,
        )
        return []

    controller_cost = effective_break_even(
        float(execution["entryPrice"]), float(fee_bps)
    )
    final_edge = float(decision["estimatedProbability"]) - controller_cost
    diagnostics = {
        "paper_only": True,
        "live_orders_affected": False,
        "forward_only": True,
        "derived_shadow": True,
        "version": RANK1_V1_BASELINE_VERSION,
        "baselineOf": RANK1_STRATEGY,
        "triggerSourceTradeId": trigger_id,
        "triggerSourceStrategy": str(trigger["strategy"]),
        "familyState": families,
        "decision": decision,
        "trend": trend,
        "execution": execution,
        "finalModelEdge": final_edge,
    }
    store.open_trade(
        strategy=RANK1_V1_BASELINE_STRATEGY,
        topic_id=int(snapshot["topic_id"]),
        market_id=market_id,
        side=side,
        entry=float(execution["entryPrice"]),
        target=None,
        stake=STAKE_USDT,
        fee_rate_bps=int(fee_bps),
        note="Frozen original Rank 1 actual-trade-overlap Paper baseline",
        strategy_version=RANK1_V1_BASELINE_VERSION,
        model_probability=float(decision["estimatedProbability"]),
        model_edge=final_edge,
        model_sigma=None,
        diagnostics=diagnostics,
    )
    paper = decision_store.latest_trade(
        store, RANK1_V1_BASELINE_STRATEGY, market_id
    )
    paper_trade_id = int(paper["id"]) if paper is not None else None
    evaluation_id = decision_store.record_evaluation(
        store,
        controller=RANK1_V1_BASELINE_STRATEGY,
        trigger=trigger,
        status="OPENED",
        reason=str(decision["reason"]),
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
            "strategy": RANK1_V1_BASELINE_STRATEGY,
            "topic_id": int(snapshot["topic_id"]),
            "market_id": market_id,
            "side": side,
            "entry_price": float(execution["entryPrice"]),
            "raw_top_ask": float(execution["rawTopAsk"]),
            "stake": STAKE_USDT,
            "signal_timestamp": as_of,
            "paper_only": True,
            "live_orders_affected": False,
            "rank1_v1_baseline": True,
            "decision_evaluation_id": evaluation_id,
            "paper_trade_id": paper_trade_id,
        }
    ]


def _install_tracker_overlay(decision_store: Any) -> None:
    tracker_class = decision_store.DecisionStrategyTracker
    original = tracker_class.process
    if getattr(original, "_decision_rank1_snapshot_v2", False):
        return

    @wraps(original)
    def process_with_rank1_v2(
        self: Any,
        opened: list[dict[str, Any]],
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        results = list(original(self, opened, snapshot, fee_bps, context) or [])
        trigger = _trigger_source(
            decision_store, self.store, opened, int(snapshot["market_id"])
        )
        for candidate in results:
            if str(candidate.get("strategy") or "") != RANK1_STRATEGY:
                continue
            family = str(candidate.get("selected_family") or "")
            source_strategy = FAMILY_SOURCES.get(family)
            vote = (
                latest_signal_snapshot(
                    self.store, source_strategy, int(snapshot["market_id"])
                )
                if source_strategy
                else None
            )
            candidate["rank1_logic_version"] = RANK1_LOGIC_VERSION
            candidate["trigger_source_trade_id"] = (
                int(trigger["id"]) if trigger is not None else None
            )
            candidate["selected_source_signal_id"] = (
                int(vote["id"]) if vote is not None else None
            )
        try:
            baseline = _evaluate_v1_baseline(
                decision_store,
                self,
                opened,
                snapshot,
                fee_bps,
                context,
            )
        except Exception:
            LOGGER.exception("Rank 1 V1 Paper baseline evaluation failed")
            baseline = []
        return [*results, *baseline]

    process_with_rank1_v2._decision_rank1_snapshot_v2 = True  # type: ignore[attr-defined]
    tracker_class.process = process_with_rank1_v2


def _install_dashboard_overlay(decision_store: Any) -> None:
    original = decision_store.experiment_state
    if getattr(original, "_decision_rank1_snapshot_v2", False):
        return

    @wraps(original)
    def experiment_with_rank1_v2(store: Any) -> dict[str, Any]:
        payload = original(store)
        snapshot_counts: dict[str, int] = {}
        if _table_exists(store, "decision_rank1_signal_snapshots"):
            with store.lock:
                snapshot_counts = {
                    str(row["source_strategy"]): int(row["samples"])
                    for row in store.db.execute(
                        """SELECT source_strategy, COUNT(*) AS samples
                             FROM decision_rank1_signal_snapshots
                            GROUP BY source_strategy"""
                    ).fetchall()
                }
        baseline = decision_store.strategy_stats(
            store, RANK1_V1_BASELINE_STRATEGY
        )
        baseline["mode"] = "RANK1_V1_ACTUAL_TRADE_OVERLAP"
        payload["rank1V2"] = {
            "logicVersion": RANK1_LOGIC_VERSION,
            "liveStrategy": RANK1_STRATEGY,
            "voterSource": "pre-execution source signal snapshots",
            "minimumSameSideSignals": 2,
            "minimumDirectionShare": RANK1_DIRECTION_SHARE,
            "zeroWeightConfirmersAllowed": True,
            "positiveWeightRequired": True,
            "snapshotCounts": snapshot_counts,
            "baselineStrategy": RANK1_V1_BASELINE_STRATEGY,
            "baseline": baseline,
        }
        return payload

    experiment_with_rank1_v2._decision_rank1_snapshot_v2 = True  # type: ignore[attr-defined]
    decision_store.experiment_state = experiment_with_rank1_v2


def _install_rules_overlay(decision_store: Any) -> None:
    original = decision_store.rules_payload
    if getattr(original, "_decision_rank1_snapshot_v2", False):
        return

    @wraps(original)
    def rules_with_rank1_v2() -> dict[str, Any]:
        payload = original()
        rank1 = payload.setdefault("rank1", {})
        rank1.update(
            {
                "logicVersion": RANK1_LOGIC_VERSION,
                "voterSource": "pre-execution source signal snapshots",
                "minimumSameSideSignals": 2,
                "zeroWeightConfirmersAllowed": True,
                "positiveHistoricalWeightRequired": True,
                "v1BaselineStrategy": RANK1_V1_BASELINE_STRATEGY,
            }
        )
        return payload

    rules_with_rank1_v2._decision_rank1_snapshot_v2 = True  # type: ignore[attr-defined]
    decision_store.rules_payload = rules_with_rank1_v2


def install_rank1_snapshot_v2(store: Any) -> None:
    """Upgrade the existing Rank 1 ID without changing any live whitelist.

    R_DECISION_RANK1 becomes signal-snapshot V2. The previous actual-trade-overlap
    rule is preserved as a separate Paper-only baseline strategy.
    """
    from . import decision_strategy_store as decision_store

    register_shadow_strategy(
        RANK1_V1_BASELINE_STRATEGY,
        parameters={
            "horizon": 300.0,
            "derived_only": 1.0,
            "native_event_driven": 1.0,
            "paper_baseline": 1.0,
        },
        generic_signal=False,
    )
    _ensure_schema(store)
    _initialize_baseline_config(store)
    _install_signal_capture(type(store))
    _install_family_state_overlay(decision_store)
    _install_rules_overlay(decision_store)
    _install_tracker_overlay(decision_store)
    _install_dashboard_overlay(decision_store)
