from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from functools import wraps
from typing import Any, Iterable

from . import live_trading as _live
from . import m_realtime as _realtime
from .core import taker_fee
from .research_strategy_registry_patch import register_primary_strategy


RANK1_STRATEGY = "R_DECISION_RANK1"
RANK2_STRATEGY = "R_DECISION_RANK2"
DECISION_STRATEGIES = (RANK1_STRATEGY, RANK2_STRATEGY)
DECISION_VERSION = "DECISION_STRATEGY_FORWARD_V1"

FAMILY_SOURCES = {
    "FUTURES_LEAD": "R_FUTURES_LEAD",
    "CALIBRATED_VALUE": "R_CALIBRATED_VALUE",
    "CONSENSUS": "R_CONSENSUS",
}
EXCLUDED_FAMILIES = ("M01", "MICROPRICE", "OFI")
HISTORY_LIMIT = 60
HISTORY_HALF_LIFE = 20.0
MIN_HISTORY = 20
MIN_FAMILIES = 2
RANK1_AGREEMENT = 0.67
RANK1_FAMILY_CAP = 0.45
RANK2_FAMILY_CAP = 0.50
RANK2_CAP_WINDOW = 20
MIN_NET_EDGE = 0.03
STAKE_USDT = 5.0
SLIPPAGE_BPS = 50.0
MAX_BOOK_AGE_MS = 2_000.0
MAX_BOOK_SKEW_MS = 500.0
MAX_SPREAD = 0.03
MAX_ENTRY_PRICE = 0.95

TREND_MIN_ELAPSED_SECONDS = 30.0
TREND_MIN_MOVE_BPS = 2.5
TREND_MIN_PATH_ER = 0.40
TREND_MIN_SAMPLES = 3
TREND_MAX_SPOT_AGE_MS = 2_000.0

_ACTIVE_TRACKER: DecisionStrategyTracker | None = None


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _normalized(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip().upper() for value in values if str(value).strip()))


def _register_strategy_surfaces() -> None:
    for strategy in DECISION_STRATEGIES:
        register_primary_strategy(strategy, generic_signal=False)

    _live.LIVE_RESEARCH_STRATEGIES = _normalized((*_live.LIVE_RESEARCH_STRATEGIES, *DECISION_STRATEGIES))
    _live.LIVE_SUPPORTED_STRATEGIES = _normalized((*_live.LIVE_SUPPORTED_STRATEGIES, *DECISION_STRATEGIES))
    _live.LIVE_RESEARCH_REPRICE_GAPS = {
        **_live.LIVE_RESEARCH_REPRICE_GAPS,
        RANK1_STRATEGY: _live.Decimal("0.05"),
        RANK2_STRATEGY: _live.Decimal("0.05"),
    }


def _cap_weights(raw: dict[str, float], requested_cap: float) -> dict[str, float]:
    positive = {key: max(0.0, float(value)) for key, value in raw.items() if float(value) > 0}
    if not positive:
        return {}
    minimum_feasible_cap = 1.0 / len(positive)
    cap = max(float(requested_cap), minimum_feasible_cap)
    remaining = set(positive)
    result: dict[str, float] = {}
    remaining_mass = 1.0
    remaining_raw = sum(positive.values())
    while remaining and remaining_raw > 0:
        changed = False
        for key in tuple(remaining):
            proportional = remaining_mass * positive[key] / remaining_raw
            if proportional > cap + 1e-12:
                result[key] = cap
                remaining.remove(key)
                remaining_mass -= cap
                remaining_raw -= positive[key]
                changed = True
        if not changed:
            for key in remaining:
                result[key] = remaining_mass * positive[key] / remaining_raw
            break
    total = sum(result.values())
    return {key: value / total for key, value in result.items()} if total > 0 else {}


def _max_drawdown(pnls: list[float]) -> float:
    equity = 0.0
    peak = 0.0
    drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        drawdown = min(drawdown, equity - peak)
    return drawdown


def _safe_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


class DecisionStrategyTracker:
    def __init__(self, engine: Any, store: Any) -> None:
        self.engine = engine
        self.store = store
        self.last_decision: dict[str, dict[str, Any]] = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        db = getattr(self.store, "db", None)
        if db is None:
            return
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS decision_strategy_meta (
                singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                forward_started_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS decision_strategy_decisions (
                strategy TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                topic_id INTEGER,
                side TEXT,
                status TEXT NOT NULL,
                reason TEXT NOT NULL,
                selected_family TEXT,
                selected_source_strategy TEXT,
                selected_source_trade_id INTEGER,
                agreement_weight REAL,
                estimated_probability REAL,
                effective_cost REAL,
                model_edge REAL,
                trend_status TEXT,
                evaluations INTEGER NOT NULL DEFAULT 1,
                first_evaluated_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                opened_at TEXT,
                shadow_trade_id INTEGER,
                family_snapshot_json TEXT NOT NULL DEFAULT '{}',
                history_snapshot_json TEXT NOT NULL DEFAULT '{}',
                trend_json TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY (strategy, market_id)
            );
            CREATE INDEX IF NOT EXISTS idx_decision_strategy_status
                ON decision_strategy_decisions(strategy, status, updated_at);
            """
        )
        db.execute(
            "INSERT OR IGNORE INTO decision_strategy_meta(singleton, forward_started_at) VALUES (1, ?)",
            (_utc_now(),),
        )
        db.commit()

    def _source_trade(self, market_id: int, source_strategy: str) -> dict[str, Any] | None:
        row = self.store.db.execute(
            """SELECT id, strategy, topic_id, market_id, side, entry_price, stake,
                      model_probability, model_edge, opened_at, diagnostics_json
                 FROM trades
                WHERE market_id=? AND strategy=?
                ORDER BY id DESC LIMIT 1""",
            (market_id, source_strategy),
        ).fetchone()
        return dict(row) if row is not None else None

    def _history(self, source_strategy: str, market_id: int) -> dict[str, Any]:
        rows = self.store.db.execute(
            """SELECT id, market_id, stake, pnl, entry_price, closed_at
                 FROM trades
                WHERE strategy=? AND market_id<>?
                  AND closed_at IS NOT NULL AND pnl IS NOT NULL
                ORDER BY id DESC LIMIT ?""",
            (source_strategy, market_id, HISTORY_LIMIT),
        ).fetchall()
        normalized = [dict(row) for row in rows]
        weighted_pnl = 0.0
        weighted_win = 0.0
        total_weight = 0.0
        for age, row in enumerate(normalized):
            stake = _finite(row.get("stake"))
            pnl = _finite(row.get("pnl"))
            if stake is None or stake <= 0 or pnl is None:
                continue
            weight = math.exp(-math.log(2.0) * age / HISTORY_HALF_LIFE)
            weighted_pnl += weight * max(-1.02, min(2.0, pnl / stake))
            weighted_win += weight * (1.0 if pnl > 0 else 0.0)
            total_weight += weight
        samples = len(normalized)
        utility = weighted_pnl / total_weight if total_weight > 0 else 0.0
        posterior_probability = (weighted_win + 2.0) / (total_weight + 4.0) if total_weight > 0 else 0.5
        return {
            "samples": samples,
            "ready": samples >= MIN_HISTORY,
            "utility": utility,
            "posteriorProbability": posterior_probability,
            "effectiveWeight": total_weight,
        }

    def _execution(self, snapshot: dict[str, Any], side: str, fee_bps: int) -> dict[str, float] | None:
        prefix = side.lower()
        ask = _finite(snapshot.get(f"{prefix}_ask"))
        bid = _finite(snapshot.get(f"{prefix}_bid"))
        ask_size = _finite(snapshot.get(f"{prefix}_ask_size"))
        age = _finite(snapshot.get("book_age_ms"))
        skew = _finite(snapshot.get("book_skew_ms"))
        if (
            ask is None
            or bid is None
            or ask_size is None
            or age is None
            or skew is None
            or not 0 < ask <= MAX_ENTRY_PRICE
            or not 0 <= bid <= ask
            or ask - bid > MAX_SPREAD
            or ask_size <= 0
            or age < 0
            or age > MAX_BOOK_AGE_MS
            or skew < 0
            or skew > MAX_BOOK_SKEW_MS
        ):
            return None
        entry = ask * (1.0 + SLIPPAGE_BPS / 10_000.0)
        if not 0 < entry < 1:
            return None
        requested_shares = STAKE_USDT / entry
        if ask_size + 1e-12 < requested_shares:
            return None
        fee = taker_fee(1.0, entry, int(fee_bps))
        return {
            "ask": ask,
            "bid": bid,
            "entry": entry,
            "feePerShare": fee,
            "effectiveCost": entry + fee,
            "requestedShares": requested_shares,
            "bookAgeMs": age,
            "bookSkewMs": skew,
        }

    def _trend(self, snapshot: dict[str, Any], side: str) -> dict[str, Any]:
        try:
            market_id = int(snapshot["market_id"])
            seconds_left = float(snapshot["seconds_left"])
        except (KeyError, TypeError, ValueError):
            return {"status": "NOT_EVALUABLE", "passed": False, "reason": "missing market clock"}
        elapsed = max(0.0, 300.0 - seconds_left)
        if elapsed < TREND_MIN_ELAPSED_SECONDS:
            return {
                "status": "NOT_APPLICABLE",
                "passed": True,
                "reason": "strong-trend window has not started",
                "elapsedSeconds": elapsed,
            }
        start_price = _finite(snapshot.get("start_price"))
        spot_price = _finite(snapshot.get("spot_price"))
        spot_age = _finite(snapshot.get("spot_age_ms"))
        if (
            start_price is None
            or start_price <= 0
            or spot_price is None
            or spot_price <= 0
            or spot_age is None
            or spot_age < 0
            or spot_age > TREND_MAX_SPOT_AGE_MS
        ):
            return {"status": "NOT_EVALUABLE", "passed": False, "reason": "spot/start data missing or stale"}
        rows = self.store.db.execute(
            """SELECT spot_price FROM observations
                 WHERE market_id=? AND spot_price IS NOT NULL
                 ORDER BY id ASC""",
            (market_id,),
        ).fetchall()
        path = [float(row[0]) for row in rows if _finite(row[0]) is not None]
        if len(path) < TREND_MIN_SAMPLES:
            return {
                "status": "NOT_EVALUABLE",
                "passed": False,
                "reason": "too few path samples",
                "samples": len(path),
            }
        distance = abs(path[-1] - path[0])
        travel = sum(abs(current - previous) for previous, current in zip(path, path[1:]))
        path_er = distance / travel if travel > 0 else 0.0
        move_bps = (spot_price - start_price) / start_price * 10_000.0
        strong = abs(move_bps) >= TREND_MIN_MOVE_BPS and path_er >= TREND_MIN_PATH_ER
        trend_side = "UP" if move_bps > 0 else ("DOWN" if move_bps < 0 else None)
        opposes = strong and trend_side is not None and side != trend_side
        return {
            "status": "BLOCK_OPPOSES_STRONG_TREND" if opposes else ("PASS_STRONG_TREND" if strong else "PASS_NOT_STRONG"),
            "passed": not opposes,
            "reason": "decision opposes strong current path" if opposes else "trend gate passed",
            "elapsedSeconds": elapsed,
            "moveBps": move_bps,
            "pathEr": path_er,
            "samples": len(path),
            "trendSide": trend_side,
            "signalSide": side,
            "spotAgeMs": spot_age,
        }

    def _family_state(self, snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
        market_id = int(snapshot["market_id"])
        result: dict[str, dict[str, Any]] = {}
        for family, source_strategy in FAMILY_SOURCES.items():
            source = self._source_trade(market_id, source_strategy)
            history = self._history(source_strategy, market_id)
            result[family] = {
                "family": family,
                "sourceStrategy": source_strategy,
                "source": source,
                "history": history,
            }
        return result

    def _rank1(self, families: dict[str, dict[str, Any]]) -> dict[str, Any]:
        eligible = {
            family: item
            for family, item in families.items()
            if item["source"] is not None
            and item["history"]["ready"]
            and float(item["history"]["utility"]) > 0
        }
        if len(eligible) < MIN_FAMILIES:
            return {"status": "WAITING", "reason": "fewer than two positive-history source families"}
        weights = _cap_weights(
            {family: float(item["history"]["utility"]) for family, item in eligible.items()},
            RANK1_FAMILY_CAP,
        )
        side_weights: dict[str, float] = {"UP": 0.0, "DOWN": 0.0}
        for family, weight in weights.items():
            side = str(eligible[family]["source"]["side"]).upper()
            if side in side_weights:
                side_weights[side] += weight
        side, agreement = max(side_weights.items(), key=lambda item: item[1])
        if agreement + 1e-12 < RANK1_AGREEMENT:
            return {
                "status": "NO_CONSENSUS",
                "reason": "same-direction utility weight below 67%",
                "agreementWeight": agreement,
                "weights": weights,
                "sideWeights": side_weights,
            }
        agreeing = [
            (family, item)
            for family, item in eligible.items()
            if str(item["source"]["side"]).upper() == side
        ]
        probability_weight = sum(weights[family] for family, _ in agreeing)
        probability = (
            sum(weights[family] * float(item["history"]["posteriorProbability"]) for family, item in agreeing)
            / probability_weight
        )
        selected_family, selected = max(
            agreeing,
            key=lambda pair: (
                float(pair[1]["history"]["utility"]),
                float(pair[1]["history"]["posteriorProbability"]),
            ),
        )
        return {
            "status": "CANDIDATE",
            "reason": "utility-weighted family consensus passed",
            "side": side,
            "selectedFamily": selected_family,
            "selectedSource": selected["source"],
            "estimatedProbability": probability,
            "agreementWeight": agreement,
            "weights": weights,
            "sideWeights": side_weights,
        }

    def _rank2_usage(self) -> dict[str, int]:
        rows = self.store.db.execute(
            """SELECT selected_family FROM decision_strategy_decisions
                 WHERE strategy=? AND status='OPENED' AND selected_family IS NOT NULL
                 ORDER BY opened_at DESC LIMIT ?""",
            (RANK2_STRATEGY, RANK2_CAP_WINDOW),
        ).fetchall()
        counts: dict[str, int] = {}
        for row in rows:
            family = str(row[0])
            counts[family] = counts.get(family, 0) + 1
        counts["__TOTAL__"] = len(rows)
        return counts

    def _rank2(self, families: dict[str, dict[str, Any]]) -> dict[str, Any]:
        eligible = [
            (family, item)
            for family, item in families.items()
            if item["source"] is not None
            and item["history"]["ready"]
            and float(item["history"]["utility"]) > 0
        ]
        if not eligible:
            return {"status": "WAITING", "reason": "no positive-history source family is currently active"}
        eligible.sort(
            key=lambda pair: (
                float(pair[1]["history"]["utility"]),
                float(pair[1]["history"]["posteriorProbability"]),
            ),
            reverse=True,
        )
        usage = self._rank2_usage()
        total = usage.pop("__TOTAL__", 0)
        selected: tuple[str, dict[str, Any]] | None = None
        for family, item in eligible:
            projected_share = (usage.get(family, 0) + 1) / (total + 1)
            if total < 4 or projected_share <= RANK2_FAMILY_CAP + 1e-12:
                selected = (family, item)
                break
        if selected is None:
            return {
                "status": "FAMILY_CAP",
                "reason": "all currently eligible champions exceed the 50% recent-family cap",
                "familyUsage": usage,
                "usageWindow": total,
            }
        family, item = selected
        source = item["source"]
        return {
            "status": "CANDIDATE",
            "reason": "recent payoff-aware champion passed family cap",
            "side": str(source["side"]).upper(),
            "selectedFamily": family,
            "selectedSource": source,
            "estimatedProbability": float(item["history"]["posteriorProbability"]),
            "agreementWeight": 1.0,
            "familyUsage": usage,
            "usageWindow": total,
        }

    def _record(
        self,
        strategy: str,
        snapshot: dict[str, Any],
        decision: dict[str, Any],
        families: dict[str, dict[str, Any]],
        trend: dict[str, Any] | None = None,
        execution: dict[str, Any] | None = None,
        trade_id: int | None = None,
    ) -> None:
        now = _utc_now()
        market_id = int(snapshot["market_id"])
        topic_id = int(snapshot.get("topic_id") or 0)
        source = decision.get("selectedSource") or {}
        status = str(decision.get("status") or "WAITING")
        reason = str(decision.get("reason") or "decision unavailable")
        existing = self.store.db.execute(
            "SELECT status, evaluations, first_evaluated_at FROM decision_strategy_decisions WHERE strategy=? AND market_id=?",
            (strategy, market_id),
        ).fetchone()
        if existing is not None and str(existing[0]) == "OPENED":
            return
        evaluations = int(existing[1]) + 1 if existing is not None else 1
        first = str(existing[2]) if existing is not None else now
        opened_at = now if status == "OPENED" else None
        self.store.db.execute(
            """INSERT INTO decision_strategy_decisions(
                       strategy, market_id, topic_id, side, status, reason,
                       selected_family, selected_source_strategy, selected_source_trade_id,
                       agreement_weight, estimated_probability, effective_cost, model_edge,
                       trend_status, evaluations, first_evaluated_at, updated_at, opened_at,
                       shadow_trade_id, family_snapshot_json, history_snapshot_json, trend_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(strategy, market_id) DO UPDATE SET
                       topic_id=excluded.topic_id,
                       side=excluded.side,
                       status=excluded.status,
                       reason=excluded.reason,
                       selected_family=excluded.selected_family,
                       selected_source_strategy=excluded.selected_source_strategy,
                       selected_source_trade_id=excluded.selected_source_trade_id,
                       agreement_weight=excluded.agreement_weight,
                       estimated_probability=excluded.estimated_probability,
                       effective_cost=excluded.effective_cost,
                       model_edge=excluded.model_edge,
                       trend_status=excluded.trend_status,
                       evaluations=excluded.evaluations,
                       updated_at=excluded.updated_at,
                       opened_at=COALESCE(decision_strategy_decisions.opened_at, excluded.opened_at),
                       shadow_trade_id=COALESCE(decision_strategy_decisions.shadow_trade_id, excluded.shadow_trade_id),
                       family_snapshot_json=excluded.family_snapshot_json,
                       history_snapshot_json=excluded.history_snapshot_json,
                       trend_json=excluded.trend_json""",
            (
                strategy,
                market_id,
                topic_id,
                decision.get("side"),
                status,
                reason,
                decision.get("selectedFamily"),
                source.get("strategy"),
                source.get("id"),
                _finite(decision.get("agreementWeight")),
                _finite(decision.get("estimatedProbability")),
                _finite((execution or {}).get("effectiveCost")),
                _finite(decision.get("modelEdge")),
                (trend or {}).get("status"),
                evaluations,
                first,
                now,
                opened_at,
                trade_id,
                json.dumps(
                    {
                        family: {
                            "sourceStrategy": item["sourceStrategy"],
                            "sourceTradeId": (item["source"] or {}).get("id"),
                            "side": (item["source"] or {}).get("side"),
                        }
                        for family, item in families.items()
                    },
                    sort_keys=True,
                ),
                json.dumps({family: item["history"] for family, item in families.items()}, sort_keys=True),
                json.dumps(trend or {}, sort_keys=True),
            ),
        )
        self.store.db.commit()
        self.last_decision[strategy] = {
            "strategy": strategy,
            "marketId": market_id,
            "status": status,
            "reason": reason,
            "side": decision.get("side"),
            "selectedFamily": decision.get("selectedFamily"),
            "agreementWeight": decision.get("agreementWeight"),
            "estimatedProbability": decision.get("estimatedProbability"),
            "effectiveCost": (execution or {}).get("effectiveCost"),
            "modelEdge": decision.get("modelEdge"),
            "trend": trend,
            "updatedAt": now,
        }

    def _evaluate_one(
        self,
        strategy: str,
        snapshot: dict[str, Any],
        fee_bps: int,
        families: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        market_id = int(snapshot["market_id"])
        existing = self.store.db.execute(
            "SELECT status FROM decision_strategy_decisions WHERE strategy=? AND market_id=?",
            (strategy, market_id),
        ).fetchone()
        if existing is not None and str(existing[0]) == "OPENED":
            return []
        decision = self._rank1(families) if strategy == RANK1_STRATEGY else self._rank2(families)
        if decision.get("status") != "CANDIDATE":
            self._record(strategy, snapshot, decision, families)
            return []
        side = str(decision["side"])
        execution = self._execution(snapshot, side, int(fee_bps))
        if execution is None:
            decision.update(status="BLOCKED_EXECUTION", reason="current direct book fails age, skew, spread, price, or depth checks")
            self._record(strategy, snapshot, decision, families)
            return []
        probability = float(decision["estimatedProbability"])
        edge = probability - float(execution["effectiveCost"])
        decision["modelEdge"] = edge
        if edge < MIN_NET_EDGE:
            decision.update(status="EDGE_TOO_SMALL", reason="posterior probability does not clear effective cost by 3pp")
            self._record(strategy, snapshot, decision, families, execution=execution)
            return []
        trend = self._trend(snapshot, side)
        if not bool(trend.get("passed")):
            decision.update(status="BLOCKED_TREND", reason=str(trend.get("reason") or "trend gate blocked"))
            self._record(strategy, snapshot, decision, families, trend=trend, execution=execution)
            return []

        source = decision.get("selectedSource") or {}
        diagnostics = {
            "paper_only": True,
            "forward_only": True,
            "live_whitelist_eligible": True,
            "live_execution_requires_selected_strategy": True,
            "decision_strategy": True,
            "decision_version": DECISION_VERSION,
            "decision_mode": "UTILITY_WEIGHTED_MAJORITY" if strategy == RANK1_STRATEGY else "RECENT_CONTEXT_CHAMPION",
            "included_families": list(FAMILY_SOURCES),
            "excluded_families": list(EXCLUDED_FAMILIES),
            "selected_family": decision.get("selectedFamily"),
            "selected_source_strategy": source.get("strategy"),
            "selected_source_trade_id": source.get("id"),
            "agreement_weight": decision.get("agreementWeight"),
            "estimated_probability": probability,
            "effective_cost": execution["effectiveCost"],
            "model_edge": edge,
            "strong_trend_gate": trend,
            "family_histories": {family: item["history"] for family, item in families.items()},
            "rank1_weights": decision.get("weights"),
            "rank2_family_usage": decision.get("familyUsage"),
            "rule": {
                "historyLimit": HISTORY_LIMIT,
                "historyHalfLife": HISTORY_HALF_LIFE,
                "minimumHistory": MIN_HISTORY,
                "minimumFamilies": MIN_FAMILIES if strategy == RANK1_STRATEGY else 1,
                "sameDirectionWeight": RANK1_AGREEMENT if strategy == RANK1_STRATEGY else None,
                "familyCap": RANK1_FAMILY_CAP if strategy == RANK1_STRATEGY else RANK2_FAMILY_CAP,
                "minimumNetEdge": MIN_NET_EDGE,
                "stakeUsdt": STAKE_USDT,
                "slippageBps": SLIPPAGE_BPS,
                "eventDriven": True,
                "oneTradePerMarket": True,
                "trendMissingDataPolicy": "BLOCK",
            },
        }
        self.store.open_trade(
            strategy=strategy,
            topic_id=int(snapshot["topic_id"]),
            market_id=market_id,
            side=side,
            entry=float(execution["entry"]),
            target=None,
            stake=STAKE_USDT,
            fee_rate_bps=int(fee_bps),
            note=f"{strategy} forward-only decision paper; native event-driven; live only when explicitly selected",
            strategy_version=DECISION_VERSION,
            model_probability=probability,
            model_edge=edge,
            model_sigma=None,
            diagnostics=diagnostics,
        )
        trade = self.store.db.execute(
            "SELECT id FROM trades WHERE strategy=? AND market_id=? ORDER BY id DESC LIMIT 1",
            (strategy, market_id),
        ).fetchone()
        trade_id = int(trade[0]) if trade is not None else None
        decision.update(status="OPENED", reason="decision, execution, payoff, and strong-trend gates passed")
        self._record(strategy, snapshot, decision, families, trend=trend, execution=execution, trade_id=trade_id)
        return [
            {
                "strategy": strategy,
                "topic_id": int(snapshot["topic_id"]),
                "market_id": market_id,
                "side": side,
                "entry_price": float(execution["entry"]),
                "raw_top_ask": float(execution["ask"]),
                "stake": STAKE_USDT,
                "seconds_left": float(snapshot["seconds_left"]),
                "book_age_ms": snapshot.get("book_age_ms"),
                "fee_bps": int(fee_bps),
                "signal_timestamp": str(snapshot.get("timestamp") or ""),
                "paper_only": True,
                "live_orders_affected": False,
                "market_data_integrity_ok": True,
                "research_signal": edge,
                "model_probability": probability,
                "model_edge": edge,
                "decision_strategy": True,
                "decision_mode": diagnostics["decision_mode"],
                "selected_family": decision.get("selectedFamily"),
                "source_strategy": source.get("strategy"),
                "source_trade_id": source.get("id"),
                "strong_trend_gate": trend,
            }
        ]

    def process(
        self,
        snapshot: dict[str, Any],
        fee_bps: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        try:
            market_id = int(snapshot["market_id"])
            seconds_left = float(snapshot["seconds_left"])
        except (KeyError, TypeError, ValueError):
            return []
        if market_id <= 0 or not 0 < seconds_left < 300:
            return []
        if context.get("execution_eligible") is not True:
            return []
        if context.get("prediction_data_source") != "dual_token_rest":
            return []
        families = self._family_state(snapshot)
        opened: list[dict[str, Any]] = []
        for strategy in DECISION_STRATEGIES:
            opened.extend(self._evaluate_one(strategy, snapshot, int(fee_bps), families))
        return opened

    def state(self) -> dict[str, Any]:
        return {
            "version": DECISION_VERSION,
            "paperOnly": True,
            "forwardOnly": True,
            "nativeEventDriven": True,
            "liveSelectable": True,
            "strategies": list(DECISION_STRATEGIES),
            "includedFamilies": list(FAMILY_SOURCES),
            "excludedFamilies": list(EXCLUDED_FAMILIES),
            "lastDecisions": dict(self.last_decision),
        }


def _strategy_stats(store: Any, strategy: str) -> dict[str, Any]:
    try:
        reset = store.db.execute(
            "SELECT COALESCE(MAX(cutoff_trade_id), 0) FROM strategy_measurement_resets WHERE strategy=?",
            (strategy,),
        ).fetchone()
        cutoff = int(reset[0] or 0) if reset is not None else 0
    except (sqlite3.Error, TypeError, ValueError):
        cutoff = 0
    rows = store.db.execute(
        """SELECT id, market_id, side, status, entry_price, stake, pnl, opened_at, closed_at
             FROM trades WHERE strategy=? AND id>? ORDER BY id ASC""",
        (strategy, cutoff),
    ).fetchall()
    trades = [dict(row) for row in rows]
    settled = [row for row in trades if row.get("closed_at") is not None and _finite(row.get("pnl")) is not None]
    pnls = [float(row["pnl"]) for row in settled]
    wins = sum(1 for pnl in pnls if pnl > 0)
    losses = sum(1 for pnl in pnls if pnl <= 0)
    entries = [float(row["entry_price"]) for row in trades if _finite(row.get("entry_price")) is not None]
    return {
        "strategy": strategy,
        "trades": len(trades),
        "open": len(trades) - len(settled),
        "settled": len(settled),
        "wins": wins,
        "losses": losses,
        "winRate": wins / len(settled) if settled else None,
        "realizedPnl": sum(pnls),
        "maxDrawdown": _max_drawdown(pnls),
        "averageEntryPrice": sum(entries) / len(entries) if entries else None,
        "recentTrades": trades[-20:][::-1],
        "measurementCutoffTradeId": cutoff,
    }


def _decision_summary(store: Any, tracker: DecisionStrategyTracker | None) -> dict[str, Any]:
    try:
        meta = store.db.execute("SELECT forward_started_at FROM decision_strategy_meta WHERE singleton=1").fetchone()
        forward_started_at = str(meta[0]) if meta is not None else None
    except sqlite3.Error:
        forward_started_at = None
    strategies: dict[str, Any] = {}
    for strategy in DECISION_STRATEGIES:
        stats = _strategy_stats(store, strategy)
        try:
            decision_rows = store.db.execute(
                """SELECT strategy, market_id, topic_id, side, status, reason,
                          selected_family, agreement_weight, estimated_probability,
                          effective_cost, model_edge, trend_status, evaluations,
                          updated_at, opened_at, shadow_trade_id
                     FROM decision_strategy_decisions
                    WHERE strategy=? ORDER BY updated_at DESC LIMIT 50""",
                (strategy,),
            ).fetchall()
        except sqlite3.Error:
            decision_rows = []
        decisions = [dict(row) for row in decision_rows]
        reason_counts: dict[str, int] = {}
        for row in decisions:
            key = str(row.get("status") or "UNKNOWN")
            reason_counts[key] = reason_counts.get(key, 0) + 1
        strategies[strategy] = {
            **stats,
            "mode": "UTILITY_WEIGHTED_MAJORITY" if strategy == RANK1_STRATEGY else "RECENT_CONTEXT_CHAMPION",
            "displayName": "Rank 1 · Utility Weighted Majority" if strategy == RANK1_STRATEGY else "Rank 2 · Recent Context Champion",
            "liveSelectable": True,
            "paperOnly": True,
            "forwardOnly": True,
            "statusCounts": reason_counts,
            "currentPreview": (tracker.last_decision.get(strategy) if tracker is not None else None),
            "recentDecisions": decisions,
        }
    return {
        "version": DECISION_VERSION,
        "paperOnly": True,
        "forwardOnly": True,
        "nativeEventDriven": True,
        "liveSelectable": True,
        "forwardStartedAt": forward_started_at,
        "includedFamilies": list(FAMILY_SOURCES),
        "excludedFamilies": list(EXCLUDED_FAMILIES),
        "rules": {
            "historyLimit": HISTORY_LIMIT,
            "historyHalfLife": HISTORY_HALF_LIFE,
            "minimumHistory": MIN_HISTORY,
            "rank1MinimumFamilies": MIN_FAMILIES,
            "rank1AgreementWeight": RANK1_AGREEMENT,
            "rank1FamilyCap": RANK1_FAMILY_CAP,
            "rank2FamilyCap": RANK2_FAMILY_CAP,
            "rank2CapWindow": RANK2_CAP_WINDOW,
            "minimumNetEdge": MIN_NET_EDGE,
            "stakeUsdt": STAKE_USDT,
            "slippageBps": SLIPPAGE_BPS,
            "maximumBookAgeMs": MAX_BOOK_AGE_MS,
            "maximumBookSkewMs": MAX_BOOK_SKEW_MS,
            "maximumSpread": MAX_SPREAD,
            "oneTradePerMarket": True,
            "entryMode": "NATIVE_EVENT_DRIVEN",
            "trendGate": {
                "minimumElapsedSeconds": TREND_MIN_ELAPSED_SECONDS,
                "minimumMoveBps": TREND_MIN_MOVE_BPS,
                "minimumPathEr": TREND_MIN_PATH_ER,
                "minimumSamples": TREND_MIN_SAMPLES,
                "maximumSpotAgeMs": TREND_MAX_SPOT_AGE_MS,
                "missingDataPolicy": "BLOCK",
            },
        },
        "strategies": strategies,
    }


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(original, "_decision_strategy_dashboard_v1", False):
        return

    @wraps(original)
    def dashboard_with_decision_strategies(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        payload = original(self, *args, **kwargs)
        if not isinstance(payload, dict):
            return payload
        tracker = _ACTIVE_TRACKER if isinstance(_ACTIVE_TRACKER, DecisionStrategyTracker) else None
        experiment = _decision_summary(self, tracker)
        payload["decisionStrategyTest"] = experiment
        summaries = payload.get("summaries")
        if isinstance(summaries, dict):
            for strategy, stats in experiment["strategies"].items():
                summaries[strategy] = {
                    "trades": stats["trades"],
                    "open": stats["open"],
                    "wins": stats["wins"],
                    "losses": stats["losses"],
                    "realized_pnl": stats["realizedPnl"],
                }
        research = payload.get("researchForward")
        if isinstance(research, dict) and isinstance(research.get("strategies"), dict):
            for strategy, stats in experiment["strategies"].items():
                research["strategies"][strategy] = {
                    "enabled": True,
                    "stakeUsdt": STAKE_USDT,
                    "paperOnly": True,
                    "liveOrdersAffected": False,
                    "liveSelectable": True,
                    "selectedBacktestParameters": experiment["rules"],
                    "chronologicalValidation": {
                        "status": "ANALYZABLE" if stats["settled"] >= 30 else "COLLECTING",
                        "samples": stats["trades"],
                        "settled": stats["settled"],
                        "wins": stats["wins"],
                        "losses": stats["losses"],
                        "realizedPnl": stats["realizedPnl"],
                        "minimum": 30,
                        "fixedCohort": True,
                    },
                }
        return payload

    dashboard_with_decision_strategies._decision_strategy_dashboard_v1 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_decision_strategies


def _sync_server_registry() -> None:
    server = sys.modules.get("predict_bot.server")
    if server is None:
        return
    supported = getattr(server, "SUPPORTED_STRATEGIES", ())
    if isinstance(supported, tuple):
        server.SUPPORTED_STRATEGIES = _normalized((*supported, *DECISION_STRATEGIES))
    default_config = getattr(server, "DEFAULT_CONFIG", None)
    if isinstance(default_config, dict):
        for strategy in DECISION_STRATEGIES:
            default_config.setdefault(f"strategy_{strategy.lower()}_enabled", True)
            default_config.setdefault(f"strategy_{strategy.lower()}_stake", STAKE_USDT)


def _wrap_store(engine: Any, store: Any) -> None:
    global _ACTIVE_TRACKER
    if getattr(store, "_decision_strategy_wrapped_v1", False):
        tracker = getattr(store, "_decision_strategy_tracker_v1", None)
        if isinstance(tracker, DecisionStrategyTracker):
            tracker.engine = engine
            _ACTIVE_TRACKER = tracker
        engine.decision_strategy_tracker = tracker
        _wrap_dashboard(type(store))
        _sync_server_registry()
        return
    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    tracker = DecisionStrategyTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_decision_strategies(
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opened = original(snapshot, fee_bps, realtime_context=realtime_context)
        decisions = tracker.process(snapshot, int(fee_bps), dict(realtime_context or {}))
        return [*(opened or []), *decisions]

    store.maybe_enter_m_series = maybe_enter_with_decision_strategies
    store._decision_strategy_wrapped_v1 = True
    store._decision_strategy_tracker_v1 = tracker
    engine.decision_strategy_tracker = tracker
    _ACTIVE_TRACKER = tracker
    _wrap_dashboard(type(store))
    _sync_server_registry()


def install_decision_strategy_shadows() -> None:
    _register_strategy_surfaces()
    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if not getattr(original_init, "_decision_strategy_shadows_v1", False):

        @wraps(original_init)
        def init_with_decision_strategies(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            _wrap_store(self, self.store)

        init_with_decision_strategies._decision_strategy_shadows_v1 = True  # type: ignore[attr-defined]
        engine_class.__init__ = init_with_decision_strategies

    original_state = engine_class.state
    if not getattr(original_state, "_decision_strategy_shadows_v1", False):

        @wraps(original_state)
        def state_with_decision_strategies(self: Any) -> dict[str, Any]:
            state = original_state(self)
            tracker = getattr(self, "decision_strategy_tracker", None)
            state["decisionStrategyTest"] = (
                tracker.state()
                if isinstance(tracker, DecisionStrategyTracker)
                else {
                    "version": DECISION_VERSION,
                    "paperOnly": True,
                    "forwardOnly": True,
                    "nativeEventDriven": True,
                    "liveSelectable": True,
                    "status": "UNAVAILABLE",
                }
            )
            return state

        state_with_decision_strategies._decision_strategy_shadows_v1 = True  # type: ignore[attr-defined]
        engine_class.state = state_with_decision_strategies
