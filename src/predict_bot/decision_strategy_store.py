from __future__ import annotations

import json
import logging
import math
import sys
from functools import wraps
from typing import Any

from .decision_strategy_rules import (
    EXCLUDED_FAMILIES,
    FAMILY_SOURCES,
    MAX_BOOK_AGE_MS,
    MAX_BOOK_SKEW_MS,
    MAX_ENTRY,
    MAX_SPREAD,
    RANK1_HALF_LIFE,
    RANK1_HISTORY,
    RANK1_STRATEGY,
    RANK2_EDGE_MARGIN,
    RANK2_HALF_LIFE,
    RANK2_HISTORY,
    RANK2_STRATEGY,
    SLIPPAGE_BPS,
    SOURCE_STRATEGIES,
    STAKE_USDT,
    STRATEGIES,
    TREND_MAX_SPOT_AGE_MS,
    TREND_MIN_ELAPSED_SECONDS,
    TREND_MIN_MOVE_BPS,
    TREND_MIN_PATH_ER,
    TREND_MIN_SAMPLES,
    VERSION,
    context_key,
    effective_break_even,
    finite,
    rank1_decision,
    rank2_decision,
    rules_payload,
    weighted_stats,
)


LOGGER = logging.getLogger(__name__)
DASHBOARD_RECENT_LIMIT = 40
DASHBOARD_TRADE_LIMIT = 5_000


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def table_exists(store: Any, table: str) -> bool:
    try:
        with store.lock:
            return store.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone() is not None
    except Exception:
        return False


def ensure_schema(store: Any) -> None:
    if getattr(store, "_decision_strategy_native_v2_schema", False):
        return
    if getattr(store, "_read_only", False):
        return
    with store.lock:
        store.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS decision_strategy_source_contexts (
                source_trade_id INTEGER PRIMARY KEY,
                source_strategy TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                side TEXT NOT NULL CHECK(side IN ('UP','DOWN')),
                entry_price REAL NOT NULL,
                opened_at TEXT NOT NULL,
                elapsed_seconds REAL NOT NULL,
                start_move_bps REAL NOT NULL,
                path_er REAL NOT NULL,
                phase TEXT NOT NULL,
                alignment TEXT NOT NULL,
                price_bucket TEXT NOT NULL,
                er_bucket TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(source_trade_id) REFERENCES trades(id)
            );
            CREATE INDEX IF NOT EXISTS decision_source_context_lookup_idx
                ON decision_strategy_source_contexts(
                    source_strategy, phase, alignment, price_bucket, er_bucket,
                    source_trade_id DESC
                );
            CREATE INDEX IF NOT EXISTS decision_source_context_market_idx
                ON decision_strategy_source_contexts(market_id, source_strategy);

            CREATE TABLE IF NOT EXISTS decision_strategy_evaluations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                controller TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                trigger_source_trade_id INTEGER NOT NULL,
                trigger_source_strategy TEXT NOT NULL,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                side TEXT,
                selected_family TEXT,
                selected_source_trade_id INTEGER,
                raw_top_ask REAL,
                entry_price REAL,
                estimated_probability REAL,
                effective_cost REAL,
                model_edge REAL,
                agreement_weight REAL,
                trend_status TEXT,
                paper_trade_id INTEGER,
                diagnostics_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(controller, trigger_source_trade_id),
                FOREIGN KEY(trigger_source_trade_id) REFERENCES trades(id),
                FOREIGN KEY(selected_source_trade_id) REFERENCES trades(id),
                FOREIGN KEY(paper_trade_id) REFERENCES trades(id)
            );
            CREATE INDEX IF NOT EXISTS decision_evaluations_controller_idx
                ON decision_strategy_evaluations(controller, id DESC);
            CREATE INDEX IF NOT EXISTS decision_evaluations_market_idx
                ON decision_strategy_evaluations(market_id, controller, id DESC);
            """
        )
        store.db.commit()
    store._decision_strategy_native_v2_schema = True


def install_config(store: Any) -> None:
    if getattr(store, "_read_only", False):
        return
    server = sys.modules.get("predict_bot.server")
    defaults = getattr(server, "DEFAULT_CONFIG", None) if server else None
    with store.lock:
        for strategy in STRATEGIES:
            enabled_key = f"strategy_{strategy.lower()}_enabled"
            stake_key = f"strategy_{strategy.lower()}_stake"
            if isinstance(defaults, dict):
                defaults.setdefault(enabled_key, True)
                defaults.setdefault(stake_key, STAKE_USDT)
            store.db.execute(
                "INSERT OR IGNORE INTO config(key, value) VALUES (?, 1)",
                (enabled_key,),
            )
            store.db.execute(
                "INSERT OR IGNORE INTO config(key, value) VALUES (?, ?)",
                (stake_key, STAKE_USDT),
            )
        store.db.commit()
    cache = getattr(store, "_config_cache", None)
    if isinstance(cache, dict):
        for strategy in STRATEGIES:
            cache.setdefault(f"strategy_{strategy.lower()}_enabled", True)
            cache.setdefault(f"strategy_{strategy.lower()}_stake", STAKE_USDT)


def latest_trade(store: Any, strategy: str, market_id: int) -> dict[str, Any] | None:
    with store.lock:
        row = store.db.execute(
            """SELECT id, strategy, topic_id, market_id, side, entry_price,
                      stake, fee_rate_bps, opened_at
                 FROM trades
                WHERE strategy=? AND market_id=?
                ORDER BY id DESC LIMIT 1""",
            (strategy, int(market_id)),
        ).fetchone()
    return dict(row) if row is not None else None


def causal_path(store: Any, snapshot: dict[str, Any]) -> dict[str, Any] | None:
    market_id = int(snapshot["market_id"])
    timestamp = str(snapshot.get("timestamp") or "")
    start_price = finite(snapshot.get("start_price"))
    current_spot = finite(snapshot.get("spot_price"))
    seconds_left = finite(snapshot.get("seconds_left"))
    spot_age_ms = finite(snapshot.get("spot_age_ms"))
    if (
        not timestamp
        or start_price is None
        or start_price <= 0
        or current_spot is None
        or current_spot <= 0
        or seconds_left is None
    ):
        return None
    with store.lock:
        rows = store.db.execute(
            """SELECT spot_price
                 FROM observations
                WHERE market_id=? AND timestamp<=?
                  AND spot_price IS NOT NULL
                ORDER BY id ASC""",
            (market_id, timestamp),
        ).fetchall()
    prices = [start_price]
    for row in rows:
        value = finite(row["spot_price"])
        if value is not None and value > 0:
            prices.append(value)
    if abs(prices[-1] - current_spot) > 1e-12:
        prices.append(current_spot)
    path_distance = sum(
        abs(current - previous)
        for previous, current in zip(prices, prices[1:])
    )
    net_move = current_spot - start_price
    path_er = abs(net_move) / path_distance if path_distance > 0 else None
    if path_er is None or not math.isfinite(path_er):
        return None
    return {
        "startPrice": start_price,
        "spotPrice": current_spot,
        "spotAgeMs": spot_age_ms,
        "secondsLeft": seconds_left,
        "elapsedSeconds": max(0.0, 300.0 - seconds_left),
        "startMoveBps": net_move / start_price * 10_000.0,
        "pathEr": path_er,
        "pathSamples": max(0, len(prices) - 1),
        "pathDistance": path_distance,
        "netMove": net_move,
    }


def record_source_context(
    store: Any,
    source: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    ensure_schema(store)
    with store.lock:
        existing = store.db.execute(
            """SELECT phase, alignment, price_bucket, er_bucket
                 FROM decision_strategy_source_contexts
                WHERE source_trade_id=?""",
            (int(source["id"]),),
        ).fetchone()
    if existing is not None:
        return (
            str(existing["phase"]),
            str(existing["alignment"]),
            str(existing["price_bucket"]),
            str(existing["er_bucket"]),
        )
    path = causal_path(store, snapshot)
    entry = finite(source.get("entry_price"))
    side = str(source.get("side") or "").upper()
    if path is None or entry is None or side not in {"UP", "DOWN"}:
        return None
    context = context_key(
        side=side,
        entry_price=entry,
        elapsed_seconds=float(path["elapsedSeconds"]),
        start_move_bps=float(path["startMoveBps"]),
        path_er=float(path["pathEr"]),
    )
    created_at = str(snapshot.get("timestamp") or source.get("opened_at") or "")
    with store.lock:
        store.db.execute(
            """INSERT OR IGNORE INTO decision_strategy_source_contexts(
                   source_trade_id, source_strategy, market_id, side,
                   entry_price, opened_at, elapsed_seconds, start_move_bps,
                   path_er, phase, alignment, price_bucket, er_bucket,
                   diagnostics_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(source["id"]),
                str(source["strategy"]),
                int(source["market_id"]),
                side,
                entry,
                str(source["opened_at"]),
                float(path["elapsedSeconds"]),
                float(path["startMoveBps"]),
                float(path["pathEr"]),
                *context,
                _json({"version": VERSION, "causalPath": path}),
                created_at,
            ),
        )
        store.db.commit()
    return context


def _history_rows(store: Any, strategy: str, as_of: str, limit: int) -> list[Any]:
    with store.lock:
        rows = store.db.execute(
            """SELECT t.id, t.stake, t.pnl, t.closed_at
                 FROM trades AS t
                 JOIN market_settlements AS s ON s.market_id=t.market_id
                WHERE t.strategy=?
                  AND t.closed_at IS NOT NULL AND t.closed_at<?
                  AND t.status IN ('SETTLED_WIN','SETTLED_LOSS')
                  AND t.pnl IS NOT NULL AND t.stake>0
                  AND s.status='OFFICIAL'
                ORDER BY t.closed_at DESC, t.id DESC LIMIT ?""",
            (strategy, as_of, int(limit)),
        ).fetchall()
    return list(reversed(rows))


def _context_history_rows(
    store: Any,
    strategy: str,
    context: tuple[str, str, str, str],
    as_of: str,
) -> list[Any]:
    with store.lock:
        rows = store.db.execute(
            """SELECT t.id, t.stake, t.pnl, t.closed_at
                 FROM decision_strategy_source_contexts AS c
                 JOIN trades AS t ON t.id=c.source_trade_id
                 JOIN market_settlements AS s ON s.market_id=t.market_id
                WHERE t.strategy=?
                  AND c.phase=? AND c.alignment=?
                  AND c.price_bucket=? AND c.er_bucket=?
                  AND t.closed_at IS NOT NULL AND t.closed_at<?
                  AND t.status IN ('SETTLED_WIN','SETTLED_LOSS')
                  AND t.pnl IS NOT NULL AND t.stake>0
                  AND s.status='OFFICIAL'
                ORDER BY t.closed_at DESC, t.id DESC LIMIT ?""",
            (strategy, *context, as_of, RANK2_HISTORY),
        ).fetchall()
    return list(reversed(rows))


def family_state(
    store: Any,
    market_id: int,
    as_of: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for family, strategy in FAMILY_SOURCES.items():
        source = latest_trade(store, strategy, market_id)
        context = None
        if source is not None:
            with store.lock:
                row = store.db.execute(
                    """SELECT phase, alignment, price_bucket, er_bucket
                         FROM decision_strategy_source_contexts
                        WHERE source_trade_id=?""",
                    (int(source["id"]),),
                ).fetchone()
            if row is not None:
                context = (
                    str(row["phase"]),
                    str(row["alignment"]),
                    str(row["price_bucket"]),
                    str(row["er_bucket"]),
                )
        rank1 = weighted_stats(
            _history_rows(store, strategy, as_of, RANK1_HISTORY),
            limit=RANK1_HISTORY,
            half_life=RANK1_HALF_LIFE,
        )
        rank2 = (
            weighted_stats(
                _context_history_rows(store, strategy, context, as_of),
                limit=RANK2_HISTORY,
                half_life=RANK2_HALF_LIFE,
            )
            if context is not None
            else {"n": 0, "utility": 0.0, "p": 0.5, "weightSum": 0.0}
        )
        result[family] = {
            "sourceStrategy": strategy,
            "source": source,
            "sourceContext": context,
            "rank1": rank1,
            "rank2": rank2,
        }
    return result


def trend_gate(store: Any, snapshot: dict[str, Any], side: str) -> dict[str, Any]:
    path = causal_path(store, snapshot)
    if path is None:
        return {
            "passed": False,
            "status": "NOT_EVALUABLE",
            "reason": "causal Spot path is unavailable",
        }
    age = finite(path.get("spotAgeMs"))
    if age is None or age < 0 or age > TREND_MAX_SPOT_AGE_MS:
        return {
            **path,
            "passed": False,
            "status": "NOT_EVALUABLE",
            "reason": "Spot observation is missing or stale",
        }
    if int(path["pathSamples"]) < TREND_MIN_SAMPLES:
        return {
            **path,
            "passed": False,
            "status": "NOT_EVALUABLE",
            "reason": "fewer than three causal Spot path samples",
        }
    move = float(path["startMoveBps"])
    strong = bool(
        float(path["elapsedSeconds"]) >= TREND_MIN_ELAPSED_SECONDS
        and abs(move) >= TREND_MIN_MOVE_BPS
        and float(path["pathEr"]) >= TREND_MIN_PATH_ER
    )
    opposing = (side == "UP" and move < 0) or (side == "DOWN" and move > 0)
    blocked = strong and opposing
    return {
        **path,
        "passed": not blocked,
        "status": "BLOCK_OPPOSES_STRONG_TREND" if blocked else "PASS",
        "reason": (
            "controller side opposes an established causal Spot trend"
            if blocked
            else "strong opposing trend rule did not fully match"
        ),
        "strongTrend": strong,
        "opposingDirection": opposing,
    }


def execution_candidate(
    snapshot: dict[str, Any],
    context: dict[str, Any],
    side: str,
) -> tuple[dict[str, Any] | None, str]:
    if (
        context.get("signal_event_type") != "prediction"
        or context.get("execution_eligible") is not True
        or context.get("prediction_data_source") != "dual_token_rest"
        or context.get("market_data_integrity_ok", True) is not True
    ):
        return None, "current event is not an executable direct dual-token book"
    try:
        book = {
            key: float(snapshot[key])
            for key in ("up_ask", "up_bid", "down_ask", "down_bid")
        }
        ask_size = float(snapshot[f"{side.lower()}_ask_size"])
        book_age = float(snapshot["book_age_ms"])
        book_skew = float(snapshot["book_skew_ms"])
    except (KeyError, TypeError, ValueError):
        return None, "current direct book is incomplete"
    if not (
        all(math.isfinite(value) for value in book.values())
        and 0 < book["up_ask"] < 1
        and 0 <= book["up_bid"] <= book["up_ask"]
        and 0 < book["down_ask"] < 1
        and 0 <= book["down_bid"] <= book["down_ask"]
        and math.isfinite(ask_size)
        and ask_size > 0
        and 0 <= book_age <= MAX_BOOK_AGE_MS
        and 0 <= book_skew <= MAX_BOOK_SKEW_MS
    ):
        return None, "current direct book failed freshness, skew, or price checks"
    raw_ask = book[f"{side.lower()}_ask"]
    raw_bid = book[f"{side.lower()}_bid"]
    if raw_ask - raw_bid > MAX_SPREAD:
        return None, "selected-side spread exceeds 0.03"
    entry = raw_ask * (1.0 + SLIPPAGE_BPS / 10_000.0)
    if entry > MAX_ENTRY or entry >= 1:
        return None, "slippage-adjusted entry exceeds 0.95"
    required_shares = STAKE_USDT / entry
    if ask_size + 1e-12 < required_shares:
        return None, "top-level Ask depth does not cover 5 USDT"
    return {
        "rawTopAsk": raw_ask,
        "rawTopBid": raw_bid,
        "entryPrice": entry,
        "visibleAskSize": ask_size,
        "requiredShares": required_shares,
        "bookAgeMs": book_age,
        "bookSkewMs": book_skew,
        "spread": raw_ask - raw_bid,
    }, ""


def _evaluation_exists(store: Any, controller: str, trigger_trade_id: int) -> bool:
    with store.lock:
        return store.db.execute(
            """SELECT 1 FROM decision_strategy_evaluations
                WHERE controller=? AND trigger_source_trade_id=? LIMIT 1""",
            (controller, int(trigger_trade_id)),
        ).fetchone() is not None


def record_evaluation(
    store: Any,
    *,
    controller: str,
    trigger: dict[str, Any],
    status: str,
    reason: str,
    decision: dict[str, Any],
    trend: dict[str, Any] | None,
    execution: dict[str, Any] | None,
    effective_cost: float | None,
    model_edge: float | None,
    paper_trade_id: int | None,
    diagnostics: dict[str, Any],
    created_at: str,
) -> int | None:
    with store.lock:
        cursor = store.db.execute(
            """INSERT OR IGNORE INTO decision_strategy_evaluations(
                   controller, market_id, topic_id, trigger_source_trade_id,
                   trigger_source_strategy, status, reason, side,
                   selected_family, selected_source_trade_id, raw_top_ask,
                   entry_price, estimated_probability, effective_cost,
                   model_edge, agreement_weight, trend_status, paper_trade_id,
                   diagnostics_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                controller,
                int(trigger["market_id"]),
                int(trigger["topic_id"]),
                int(trigger["id"]),
                str(trigger["strategy"]),
                status,
                reason,
                decision.get("side"),
                decision.get("selectedFamily"),
                decision.get("selectedSourceTradeId"),
                execution.get("rawTopAsk") if execution else None,
                execution.get("entryPrice") if execution else None,
                decision.get("estimatedProbability"),
                effective_cost,
                model_edge,
                decision.get("agreementWeight"),
                trend.get("status") if trend else None,
                paper_trade_id,
                _json(diagnostics),
                created_at,
            ),
        )
        store.db.commit()
        return int(cursor.lastrowid) if cursor.rowcount else None


class DecisionStrategyTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine = engine
        self.store = store
        ensure_schema(store)
        install_config(store)

    def _enabled(self, strategy: str) -> bool:
        try:
            return bool(
                self.store.config().get(
                    f"strategy_{strategy.lower()}_enabled",
                    True,
                )
            )
        except Exception:
            return True

    def _source_rows(
        self,
        opened: list[dict[str, Any]],
        snapshot: dict[str, Any],
    ) -> list[dict[str, Any]]:
        market_id = int(snapshot["market_id"])
        rows: dict[int, dict[str, Any]] = {}
        for candidate in opened:
            strategy = str(candidate.get("strategy") or "").upper()
            if strategy not in SOURCE_STRATEGIES:
                continue
            source = latest_trade(self.store, strategy, market_id)
            if source is None:
                continue
            record_source_context(self.store, source, snapshot)
            rows[int(source["id"])] = source
        return sorted(rows.values(), key=lambda row: int(row["id"]))

    def process(
        self,
        opened: list[dict[str, Any]],
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        source_rows = self._source_rows(opened, snapshot)
        if not source_rows:
            return []
        trigger = source_rows[-1]
        market_id = int(snapshot["market_id"])
        as_of = str(snapshot.get("timestamp") or trigger["opened_at"])
        families = family_state(self.store, market_id, as_of)
        results: list[dict[str, Any]] = []
        for controller, rule in (
            (RANK1_STRATEGY, rank1_decision),
            (RANK2_STRATEGY, rank2_decision),
        ):
            if _evaluation_exists(self.store, controller, int(trigger["id"])):
                continue
            if not self._enabled(controller):
                record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="DISABLED",
                    reason="controller is disabled in Paper config",
                    decision={},
                    trend=None,
                    execution=None,
                    effective_cost=None,
                    model_edge=None,
                    paper_trade_id=None,
                    diagnostics={"version": VERSION},
                    created_at=as_of,
                )
                continue
            if self.store.has_trade(controller, market_id):
                record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="ALREADY_OPEN",
                    reason="one controller trade per market",
                    decision={},
                    trend=None,
                    execution=None,
                    effective_cost=None,
                    model_edge=None,
                    paper_trade_id=None,
                    diagnostics={"version": VERSION},
                    created_at=as_of,
                )
                continue
            decision = rule(families)
            if decision.get("status") != "CANDIDATE":
                record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status=str(decision.get("status") or "ABSTAIN"),
                    reason=str(decision.get("reason") or "controller abstained"),
                    decision=decision,
                    trend=None,
                    execution=None,
                    effective_cost=None,
                    model_edge=None,
                    paper_trade_id=None,
                    diagnostics={
                        "version": VERSION,
                        "familyState": families,
                        "decision": decision,
                    },
                    created_at=as_of,
                )
                continue
            side = str(decision["side"])
            trend = trend_gate(self.store, snapshot, side)
            if trend.get("passed") is not True:
                record_evaluation(
                    self.store,
                    controller=controller,
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
                        "version": VERSION,
                        "familyState": families,
                        "decision": decision,
                        "trend": trend,
                    },
                    created_at=as_of,
                )
                continue
            execution, execution_reason = execution_candidate(snapshot, context, side)
            if execution is None:
                record_evaluation(
                    self.store,
                    controller=controller,
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
                        "version": VERSION,
                        "familyState": families,
                        "decision": decision,
                        "trend": trend,
                    },
                    created_at=as_of,
                )
                continue
            controller_cost = effective_break_even(
                float(execution["entryPrice"]), float(fee_bps)
            )
            final_edge = float(decision["estimatedProbability"]) - controller_cost
            diagnostics = {
                "version": VERSION,
                "familyState": families,
                "decision": decision,
                "trend": trend,
                "execution": execution,
                "controllerFeeBps": int(fee_bps),
                "effectiveCost": controller_cost,
                "finalModelEdge": final_edge,
            }
            if controller == RANK2_STRATEGY and final_edge + 1e-12 < RANK2_EDGE_MARGIN:
                record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="BLOCK_FINAL_EDGE",
                    reason="controller execution cost leaves less than 3pp edge",
                    decision=decision,
                    trend=trend,
                    execution=execution,
                    effective_cost=controller_cost,
                    model_edge=final_edge,
                    paper_trade_id=None,
                    diagnostics=diagnostics,
                    created_at=as_of,
                )
                continue
            diagnostics.update(
                {
                    "paper_only": True,
                    "live_orders_affected": False,
                    "forward_only": True,
                    "derived_shadow": True,
                    "decision_strategy_controller": True,
                    "controller": controller,
                    "includedFamilies": list(FAMILY_SOURCES),
                    "excludedFamilies": list(EXCLUDED_FAMILIES),
                    "triggerSourceTradeId": int(trigger["id"]),
                    "triggerSourceStrategy": str(trigger["strategy"]),
                    "rules": rules_payload(),
                    "realtimeContext": json.loads(_json(context)),
                }
            )
            self.store.open_trade(
                strategy=controller,
                topic_id=int(snapshot["topic_id"]),
                market_id=market_id,
                side=side,
                entry=float(execution["entryPrice"]),
                target=None,
                stake=STAKE_USDT,
                fee_rate_bps=int(fee_bps),
                note=(
                    f"{controller} native event-driven derived Paper controller; "
                    "M01/raw Microprice/OFI excluded"
                ),
                strategy_version=VERSION,
                model_probability=float(decision["estimatedProbability"]),
                model_edge=final_edge,
                model_sigma=None,
                diagnostics=diagnostics,
            )
            paper = latest_trade(self.store, controller, market_id)
            paper_trade_id = int(paper["id"]) if paper is not None else None
            evaluation_id = record_evaluation(
                self.store,
                controller=controller,
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
            results.append(
                {
                    "strategy": controller,
                    "topic_id": int(snapshot["topic_id"]),
                    "market_id": market_id,
                    "side": side,
                    "entry_price": float(execution["entryPrice"]),
                    "raw_top_ask": float(execution["rawTopAsk"]),
                    "stake": STAKE_USDT,
                    "seconds_left": float(snapshot["seconds_left"]),
                    "book_age_ms": execution["bookAgeMs"],
                    "fee_bps": int(fee_bps),
                    "signal_timestamp": as_of,
                    "paper_only": True,
                    "live_orders_affected": False,
                    "market_data_integrity_ok": True,
                    "decision_strategy_controller": True,
                    "decision_strategy_version": VERSION,
                    "decision_evaluation_id": evaluation_id,
                    "paper_trade_id": paper_trade_id,
                    "selected_family": decision["selectedFamily"],
                    "selected_source_trade_id": decision["selectedSourceTradeId"],
                    "estimated_probability": decision["estimatedProbability"],
                    "effective_cost": controller_cost,
                    "agreement_weight": decision["agreementWeight"],
                    "model_edge": final_edge,
                    "trend_status": trend["status"],
                }
            )
        return results


def strategy_stats(store: Any, strategy: str) -> dict[str, Any]:
    with store.lock:
        reset = store.db.execute(
            """SELECT cutoff_trade_id, reset_at
                 FROM strategy_measurement_resets
                WHERE strategy=? ORDER BY id DESC LIMIT 1""",
            (strategy,),
        ).fetchone()
        cutoff = int(reset["cutoff_trade_id"]) if reset is not None else 0
        rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT id, market_id, side, status, entry_price, stake, pnl,
                          opened_at, closed_at
                     FROM trades
                    WHERE strategy=? AND id>?
                    ORDER BY id ASC LIMIT ?""",
                (strategy, cutoff, DASHBOARD_TRADE_LIMIT),
            ).fetchall()
        ]
        reset_at = str(reset["reset_at"]) if reset is not None else None
        evaluation_rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT status FROM decision_strategy_evaluations
                    WHERE controller=? AND (? IS NULL OR created_at>=?)
                    ORDER BY id DESC LIMIT ?""",
                (strategy, reset_at, reset_at, DASHBOARD_TRADE_LIMIT),
            ).fetchall()
        ]
    settled = [row for row in rows if row.get("pnl") is not None]
    wins = sum(float(row["pnl"]) > 0 for row in settled)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in settled:
        cumulative += float(row["pnl"])
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    status_counts: dict[str, int] = {}
    for row in evaluation_rows:
        key = str(row["status"])
        status_counts[key] = status_counts.get(key, 0) + 1
    return {
        "strategy": strategy,
        "mode": (
            "UTILITY_WEIGHTED_MAJORITY"
            if strategy == RANK1_STRATEGY
            else "RECENT_CONTEXT_CHAMPION"
        ),
        "trades": len(rows),
        "open": sum(str(row["status"]) == "OPEN" for row in rows),
        "settled": len(settled),
        "wins": int(wins),
        "losses": len(settled) - int(wins),
        "winRate": wins / len(settled) if settled else None,
        "realizedPnl": sum(float(row["pnl"]) for row in settled),
        "maxDrawdown": -max_drawdown,
        "averageEntryPrice": (
            sum(float(row["entry_price"]) for row in rows) / len(rows)
            if rows
            else None
        ),
        "resetAt": reset_at,
        "statusCounts": status_counts,
    }


def experiment_state(store: Any) -> dict[str, Any]:
    if not table_exists(store, "decision_strategy_evaluations"):
        return {
            "version": VERSION,
            "status": "WAITING_FOR_EXECUTION_STORE",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "includedFamilies": list(FAMILY_SOURCES),
            "excludedFamilies": list(EXCLUDED_FAMILIES),
            "rules": rules_payload(),
            "strategies": {},
            "recentDecisions": [],
        }
    strategies = {strategy: strategy_stats(store, strategy) for strategy in STRATEGIES}
    with store.lock:
        recent_rows = [
            dict(row)
            for row in store.db.execute(
                """SELECT id, controller, market_id, trigger_source_strategy,
                          status, reason, side, selected_family,
                          selected_source_trade_id, raw_top_ask, entry_price,
                          estimated_probability, effective_cost, model_edge,
                          agreement_weight, trend_status, paper_trade_id,
                          created_at
                     FROM decision_strategy_evaluations
                    ORDER BY id DESC LIMIT ?""",
                (DASHBOARD_RECENT_LIMIT,),
            ).fetchall()
        ]
        context_counts = {
            str(row["source_strategy"]): int(row["samples"])
            for row in store.db.execute(
                """SELECT source_strategy, COUNT(*) AS samples
                     FROM decision_strategy_source_contexts
                    GROUP BY source_strategy"""
            ).fetchall()
        }
    current: dict[str, dict[str, Any] | None] = {}
    for strategy in STRATEGIES:
        row = next(
            (item for item in recent_rows if item["controller"] == strategy),
            None,
        )
        current[strategy] = row
        strategies[strategy]["currentPreview"] = row
    return {
        "version": VERSION,
        "status": "COLLECTING",
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "nativeEventDriven": True,
        "includedFamilies": list(FAMILY_SOURCES),
        "familySources": dict(FAMILY_SOURCES),
        "excludedFamilies": list(EXCLUDED_FAMILIES),
        "rules": rules_payload(),
        "contextSamples": context_counts,
        "rank2Warmup": (
            "same-context history is collected prospectively; no startup scan "
            "of the historical observations table"
        ),
        "strategies": strategies,
        "current": current,
        "recentDecisions": recent_rows,
    }


def wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(original, "_decision_strategy_native_v2", False):
        return

    @wraps(original)
    def dashboard_with_decision_strategy(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        try:
            experiment = experiment_state(self)
        except Exception as exc:
            LOGGER.exception(
                "Decision strategy dashboard summary failed; preserving base payload"
            )
            experiment = {
                "version": VERSION,
                "status": "UNAVAILABLE",
                "paperOnly": True,
                "liveOrdersAffected": False,
                "forwardOnly": True,
                "error": f"{type(exc).__name__}: {exc}",
                "includedFamilies": list(FAMILY_SOURCES),
                "excludedFamilies": list(EXCLUDED_FAMILIES),
                "rules": rules_payload(),
                "strategies": {},
                "recentDecisions": [],
            }
        research = payload.get("researchForward")
        if isinstance(research, dict):
            research["decisionStrategyExperiment"] = experiment
        return payload

    dashboard_with_decision_strategy._decision_strategy_native_v2 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_decision_strategy
