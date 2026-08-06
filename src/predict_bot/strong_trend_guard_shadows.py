from __future__ import annotations

import json
import math
import sqlite3
from functools import wraps
from typing import Any, Iterable

from .research_strategy_registry_patch import register_shadow_strategy


VERSION = "STRONG_TREND_GUARD_FORWARD_PAPER_V1"
NORMALIZED_STAKE_USDT = 5.0
MIN_ELAPSED_SECONDS = 30.0
MIN_ABS_START_MOVE_BPS = 2.5
MIN_PATH_EFFICIENCY_RATIO = 0.40
MIN_PATH_SAMPLES = 3
MAX_SPOT_AGE_MS = 2_000.0

SOURCE_TO_SHADOW = {
    "M01": "R_STRONG_TREND_GUARD_M01",
    "M01F": "R_STRONG_TREND_GUARD_M01F",
    "M01T180": "R_STRONG_TREND_GUARD_M01T180",
    "M01O_F1": "R_STRONG_TREND_GUARD_M01O_F1",
    "M01R": "R_STRONG_TREND_GUARD_M01R",
    "R_MICROPRICE": "R_STRONG_TREND_GUARD_MICROPRICE",
    "R_FUTURES_LEAD": "R_STRONG_TREND_GUARD_FUTURES_LEAD",
    "R_CONSENSUS": "R_STRONG_TREND_GUARD_CONSENSUS",
}
STRATEGIES = tuple(SOURCE_TO_SHADOW.values())


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _value(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except (KeyError, TypeError, IndexError):
        return None


def evaluate_strong_trend(
    observations: Iterable[Any],
    side: str,
) -> dict[str, Any]:
    rows = list(observations)
    normalized_side = str(side or "").upper()
    base = {
        "version": VERSION,
        "side": normalized_side,
        "minimumElapsedSeconds": MIN_ELAPSED_SECONDS,
        "minimumAbsoluteStartMoveBps": MIN_ABS_START_MOVE_BPS,
        "minimumPathEfficiencyRatio": MIN_PATH_EFFICIENCY_RATIO,
        "minimumPathSamples": MIN_PATH_SAMPLES,
        "maximumSpotAgeMs": MAX_SPOT_AGE_MS,
        "paperOnly": True,
        "liveOrdersAffected": False,
    }
    if normalized_side not in {"UP", "DOWN"}:
        return {**base, "decision": "ALLOW_NOT_EVALUABLE", "reason": "invalid source side"}
    if len(rows) < MIN_PATH_SAMPLES:
        return {
            **base,
            "decision": "ALLOW_NOT_EVALUABLE",
            "reason": f"only {len(rows)} causal observations; need >= {MIN_PATH_SAMPLES}",
            "pathSamples": len(rows),
        }

    latest = rows[-1]
    start_price = _finite(_value(latest, "start_price"))
    spot_price = _finite(_value(latest, "spot_price"))
    seconds_left = _finite(_value(latest, "seconds_left"))
    spot_age_ms = _finite(_value(latest, "spot_age_ms"))
    if start_price is None or start_price <= 0 or spot_price is None:
        return {**base, "decision": "ALLOW_NOT_EVALUABLE", "reason": "missing start or spot price"}
    if seconds_left is None:
        return {**base, "decision": "ALLOW_NOT_EVALUABLE", "reason": "missing seconds_left"}
    if spot_age_ms is None or spot_age_ms < 0 or spot_age_ms > MAX_SPOT_AGE_MS:
        return {
            **base,
            "decision": "ALLOW_NOT_EVALUABLE",
            "reason": "spot observation is missing or stale",
            "spotAgeMs": spot_age_ms,
        }

    prices = [start_price]
    for row in rows:
        value = _finite(_value(row, "spot_price"))
        if value is not None and value > 0:
            prices.append(value)
    if len(prices) - 1 < MIN_PATH_SAMPLES:
        return {
            **base,
            "decision": "ALLOW_NOT_EVALUABLE",
            "reason": "insufficient finite spot path samples",
            "pathSamples": len(prices) - 1,
        }

    path_distance = sum(abs(current - previous) for previous, current in zip(prices, prices[1:]))
    net_move = spot_price - start_price
    path_er = abs(net_move) / path_distance if path_distance > 0 else None
    if path_er is None or not math.isfinite(path_er):
        return {
            **base,
            "decision": "ALLOW_NOT_EVALUABLE",
            "reason": "spot path has no measurable distance",
            "pathSamples": len(prices) - 1,
            "pathTotalDistance": path_distance,
        }

    elapsed_seconds = max(0.0, 300.0 - seconds_left)
    start_move_bps = net_move / start_price * 10_000.0
    opposing = (normalized_side == "UP" and start_move_bps < 0) or (
        normalized_side == "DOWN" and start_move_bps > 0
    )
    strong_trend = bool(
        elapsed_seconds >= MIN_ELAPSED_SECONDS
        and abs(start_move_bps) >= MIN_ABS_START_MOVE_BPS
        and path_er >= MIN_PATH_EFFICIENCY_RATIO
    )
    blocked = bool(strong_trend and opposing)
    return {
        **base,
        "decision": "BLOCK_STRONG_OPPOSING_TREND" if blocked else "ALLOW",
        "reason": (
            "source side opposes an established causal spot trend"
            if blocked
            else "strong opposing trend rule did not fully match"
        ),
        "elapsedSeconds": elapsed_seconds,
        "startPrice": start_price,
        "spotPrice": spot_price,
        "spotAgeMs": spot_age_ms,
        "startMoveBps": start_move_bps,
        "pathEfficiencyRatio": path_er,
        "pathSamples": len(prices) - 1,
        "pathTotalDistance": path_distance,
        "pathNetMove": net_move,
        "opposingDirection": opposing,
        "strongTrend": strong_trend,
    }


def _ensure_schema(store: Any) -> None:
    if getattr(store, "_strong_trend_guard_schema_ready", False):
        return
    if getattr(store, "_read_only", False):
        return
    with store.lock:
        store.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS strong_trend_guard_decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_trade_id INTEGER NOT NULL UNIQUE,
                source_strategy TEXT NOT NULL,
                shadow_strategy TEXT NOT NULL,
                shadow_trade_id INTEGER,
                market_id INTEGER NOT NULL,
                topic_id INTEGER NOT NULL,
                side TEXT NOT NULL,
                source_entry_price REAL NOT NULL,
                normalized_stake REAL NOT NULL,
                opened_at TEXT NOT NULL,
                observation_id INTEGER,
                observation_timestamp TEXT,
                elapsed_seconds REAL,
                start_price REAL,
                spot_price REAL,
                spot_age_ms REAL,
                start_move_bps REAL,
                path_er REAL,
                path_samples INTEGER,
                path_total_distance REAL,
                path_net_move REAL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                shadow_opened INTEGER NOT NULL DEFAULT 0,
                strategy_version TEXT NOT NULL,
                diagnostics_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS strong_trend_guard_shadow_idx
                ON strong_trend_guard_decisions(shadow_strategy, id DESC);
            CREATE INDEX IF NOT EXISTS strong_trend_guard_market_idx
                ON strong_trend_guard_decisions(market_id, id DESC);
            """
        )
        store.db.commit()
    store._strong_trend_guard_schema_ready = True


def _table_exists(store: Any) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='strong_trend_guard_decisions'"
        ).fetchone() is not None
    except Exception:
        return False


def _causal_observations(store: Any, market_id: int, opened_at: str) -> list[Any]:
    with store.lock:
        return store.db.execute(
            """SELECT id, timestamp, start_price, spot_price, spot_age_ms, seconds_left
                 FROM observations
                WHERE market_id=? AND timestamp<=?
                ORDER BY id ASC""",
            (int(market_id), str(opened_at)),
        ).fetchall()


def _enabled(store: Any, strategy: str) -> bool:
    try:
        config = store.config()
    except Exception:
        return True
    return bool(config.get(f"strategy_{strategy.lower()}_enabled", True))


def _latest_trade(store: Any, strategy: str, market_id: int) -> Any:
    with store.lock:
        return store.db.execute(
            "SELECT * FROM trades WHERE strategy=? AND market_id=? ORDER BY id DESC LIMIT 1",
            (strategy, int(market_id)),
        ).fetchone()


def _trade_id(store: Any, strategy: str, market_id: int) -> int | None:
    row = _latest_trade(store, strategy, market_id)
    return int(row["id"]) if row is not None else None


def _decision_exists(store: Any, source_trade_id: int) -> bool:
    if not _table_exists(store):
        return False
    with store.lock:
        return store.db.execute(
            "SELECT 1 FROM strong_trend_guard_decisions WHERE source_trade_id=?",
            (int(source_trade_id),),
        ).fetchone() is not None


def _record_decision(
    store: Any,
    source: Any,
    shadow_strategy: str,
    shadow_trade_id: int | None,
    decision: dict[str, Any],
    created_at: str,
) -> None:
    latest_observation = None
    try:
        observations = _causal_observations(store, int(source["market_id"]), str(source["opened_at"]))
        latest_observation = observations[-1] if observations else None
    except Exception:
        latest_observation = None
    with store.lock:
        store.db.execute(
            """INSERT OR IGNORE INTO strong_trend_guard_decisions(
                   source_trade_id, source_strategy, shadow_strategy, shadow_trade_id,
                   market_id, topic_id, side, source_entry_price, normalized_stake,
                   opened_at, observation_id, observation_timestamp, elapsed_seconds,
                   start_price, spot_price, spot_age_ms, start_move_bps, path_er,
                   path_samples, path_total_distance, path_net_move, decision, reason,
                   shadow_opened, strategy_version, diagnostics_json, created_at
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                int(source["id"]),
                str(source["strategy"]),
                shadow_strategy,
                shadow_trade_id,
                int(source["market_id"]),
                int(source["topic_id"]),
                str(source["side"]),
                float(source["entry_price"]),
                NORMALIZED_STAKE_USDT,
                str(source["opened_at"]),
                int(latest_observation["id"]) if latest_observation is not None else None,
                str(latest_observation["timestamp"]) if latest_observation is not None else None,
                decision.get("elapsedSeconds"),
                decision.get("startPrice"),
                decision.get("spotPrice"),
                decision.get("spotAgeMs"),
                decision.get("startMoveBps"),
                decision.get("pathEfficiencyRatio"),
                decision.get("pathSamples"),
                decision.get("pathTotalDistance"),
                decision.get("pathNetMove"),
                str(decision["decision"]),
                str(decision["reason"]),
                1 if shadow_trade_id is not None else 0,
                VERSION,
                json.dumps(decision, sort_keys=True),
                created_at,
            ),
        )
        store.db.commit()


def _process_source_trade(store: Any, source: Any, original_open: Any, utc_iso: Any) -> None:
    source_strategy = str(source["strategy"]).upper()
    shadow_strategy = SOURCE_TO_SHADOW.get(source_strategy)
    if shadow_strategy is None or _decision_exists(store, int(source["id"])):
        return
    observations = _causal_observations(store, int(source["market_id"]), str(source["opened_at"]))
    decision = evaluate_strong_trend(observations, str(source["side"]))
    shadow_trade_id = None
    if decision["decision"] != "BLOCK_STRONG_OPPOSING_TREND" and _enabled(store, shadow_strategy):
        existing = _trade_id(store, shadow_strategy, int(source["market_id"]))
        if existing is None:
            diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "shadow_only": True,
                "forward_only": True,
                "source_strategy": source_strategy,
                "source_trade_id": int(source["id"]),
                "normalized_stake_usdt": NORMALIZED_STAKE_USDT,
                "strong_trend_guard_decision": decision,
                "rule": {
                    "minimumElapsedSecondsInclusive": MIN_ELAPSED_SECONDS,
                    "minimumAbsoluteStartMoveBpsInclusive": MIN_ABS_START_MOVE_BPS,
                    "minimumPathEfficiencyRatioInclusive": MIN_PATH_EFFICIENCY_RATIO,
                    "minimumPathSamples": MIN_PATH_SAMPLES,
                    "maximumSpotAgeMs": MAX_SPOT_AGE_MS,
                    "missingData": "ALLOW_NOT_EVALUABLE",
                },
            }
            store._strong_trend_guard_internal = True
            try:
                original_open(
                    store,
                    strategy=shadow_strategy,
                    topic_id=int(source["topic_id"]),
                    market_id=int(source["market_id"]),
                    side=str(source["side"]),
                    entry=float(source["entry_price"]),
                    target=None,
                    stake=NORMALIZED_STAKE_USDT,
                    fee_rate_bps=int(source["fee_rate_bps"] or 200),
                    note=(
                        f"{shadow_strategy} forward-only normalized Paper Shadow from "
                        f"{source_strategy} trade #{int(source['id'])}; never live-forwarded"
                    ),
                    strategy_version=VERSION,
                    diagnostics=diagnostics,
                )
            finally:
                store._strong_trend_guard_internal = False
            shadow_trade_id = _trade_id(store, shadow_strategy, int(source["market_id"]))
        else:
            shadow_trade_id = existing
    _record_decision(store, source, shadow_strategy, shadow_trade_id, decision, utc_iso())


def _normalized_source_pnl(row: Any) -> float | None:
    pnl = _finite(row["source_pnl"])
    stake = _finite(row["source_stake"])
    if pnl is None or stake is None or stake <= 0:
        return None
    return pnl * NORMALIZED_STAKE_USDT / stake


def _experiment_state(store: Any) -> dict[str, Any]:
    empty = {
        strategy: {
            "sourceStrategy": source,
            "shadowStrategy": strategy,
            "sourceTrades": 0,
            "allowed": 0,
            "blocked": 0,
            "notEvaluable": 0,
            "shadowOpened": 0,
            "trades": 0,
            "open": 0,
            "settled": 0,
            "wins": 0,
            "losses": 0,
            "winRate": None,
            "realizedPnl": 0.0,
            "roi": None,
            "blockedSettled": 0,
            "blockedPending": 0,
            "blockedFlat": 0,
            "blockedWins": 0,
            "blockedLosses": 0,
            "blockedWinRate": None,
            "avoidedLossUsdt": 0.0,
            "sacrificedProfitUsdt": 0.0,
            "netProtectionUsdt": 0.0,
        }
        for source, strategy in SOURCE_TO_SHADOW.items()
    }
    if not _table_exists(store):
        return {
            "version": VERSION,
            "status": "WAITING_FOR_SCHEMA",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "rules": _rules(),
            "strategies": empty,
            "recentDecisions": [],
            "runtime": None,
        }
    try:
        with store.lock:
            rows = store.db.execute(
                """SELECT d.*,
                          source.status AS source_status,
                          source.pnl AS source_pnl,
                          source.stake AS source_stake,
                          shadow.status AS shadow_status,
                          shadow.pnl AS shadow_pnl,
                          shadow.stake AS shadow_stake
                     FROM strong_trend_guard_decisions AS d
                     JOIN trades AS source ON source.id=d.source_trade_id
                     LEFT JOIN trades AS shadow ON shadow.id=d.shadow_trade_id
                    ORDER BY d.id ASC"""
            ).fetchall()
    except sqlite3.DatabaseError:
        rows = []

    for row in rows:
        strategy = str(row["shadow_strategy"])
        stats = empty.setdefault(strategy, {})
        stats["sourceTrades"] += 1
        decision = str(row["decision"])
        if decision == "BLOCK_STRONG_OPPOSING_TREND":
            stats["blocked"] += 1
        elif decision == "ALLOW_NOT_EVALUABLE":
            stats["notEvaluable"] += 1
            stats["allowed"] += 1
        else:
            stats["allowed"] += 1
        if bool(row["shadow_opened"]):
            stats["shadowOpened"] += 1
            stats["trades"] += 1
        shadow_status = str(row["shadow_status"] or "")
        if shadow_status == "OPEN":
            stats["open"] += 1
        if shadow_status in {"SETTLED_WIN", "SETTLED_LOSS"}:
            stats["settled"] += 1
            if shadow_status == "SETTLED_WIN":
                stats["wins"] += 1
            else:
                stats["losses"] += 1
            stats["realizedPnl"] += float(row["shadow_pnl"] or 0.0)

        if decision == "BLOCK_STRONG_OPPOSING_TREND":
            source_status = str(row["source_status"] or "").upper()
            normalized = _normalized_source_pnl(row)
            # Paper trades remain OPEN until an exit/official settlement writes a
            # realized PnL.  Do not restrict counterfactual accounting to only
            # SETTLED_WIN/SETTLED_LOSS: target fills, stop exits and timeout exits
            # are also finalized source outcomes and must affect protection PnL.
            source_finalized = source_status not in {"", "OPEN"} and normalized is not None
            if source_finalized:
                stats["blockedSettled"] += 1
                if normalized > 0:
                    stats["blockedWins"] += 1
                    stats["sacrificedProfitUsdt"] += normalized
                elif normalized < 0:
                    stats["blockedLosses"] += 1
                    stats["avoidedLossUsdt"] += -normalized
                else:
                    stats["blockedFlat"] += 1
            else:
                stats["blockedPending"] += 1

    for stats in empty.values():
        settled = int(stats["settled"])
        blocked_settled = int(stats["blockedSettled"])
        stats["winRate"] = stats["wins"] / settled if settled else None
        stats["roi"] = stats["realizedPnl"] / (settled * NORMALIZED_STAKE_USDT) if settled else None
        stats["blockedWinRate"] = stats["blockedWins"] / blocked_settled if blocked_settled else None
        stats["netProtectionUsdt"] = stats["avoidedLossUsdt"] - stats["sacrificedProfitUsdt"]

    recent = []
    for row in reversed(rows[-40:]):
        recent.append({
            "id": int(row["id"]),
            "openedAt": row["opened_at"],
            "marketId": int(row["market_id"]),
            "sourceStrategy": row["source_strategy"],
            "shadowStrategy": row["shadow_strategy"],
            "side": row["side"],
            "startMoveBps": row["start_move_bps"],
            "pathEfficiencyRatio": row["path_er"],
            "elapsedSeconds": row["elapsed_seconds"],
            "decision": row["decision"],
            "reason": row["reason"],
            "sourceStatus": row["source_status"],
            "sourcePnl": row["source_pnl"],
            "sourceStake": row["source_stake"],
            "sourceOutcomeFinalized": (
                str(row["source_status"] or "").upper() not in {"", "OPEN"}
                and _finite(row["source_pnl"]) is not None
            ),
            "shadowStatus": row["shadow_status"],
        })
    runtime = recent[0] if recent else None
    return {
        "version": VERSION,
        "status": "COLLECTING" if rows else "WAITING_FOR_SOURCE_TRADES",
        "paperOnly": True,
        "liveOrdersAffected": False,
        "forwardOnly": True,
        "normalizedStakeUsdt": NORMALIZED_STAKE_USDT,
        "rules": _rules(),
        "sourceStrategies": SOURCE_TO_SHADOW,
        "decisions": len(rows),
        "strategies": empty,
        "recentDecisions": recent,
        "runtime": runtime,
    }


def _rules() -> dict[str, Any]:
    return {
        "minimumElapsedSecondsInclusive": MIN_ELAPSED_SECONDS,
        "minimumAbsoluteStartMoveBpsInclusive": MIN_ABS_START_MOVE_BPS,
        "minimumPathEfficiencyRatioInclusive": MIN_PATH_EFFICIENCY_RATIO,
        "minimumPathSamples": MIN_PATH_SAMPLES,
        "maximumSpotAgeMs": MAX_SPOT_AGE_MS,
        "missingDataDecision": "ALLOW_NOT_EVALUABLE",
        "normalizedStakeUsdt": NORMALIZED_STAKE_USDT,
    }


def install_strong_trend_guard_shadows(namespace: dict[str, Any]) -> None:
    for strategy in STRATEGIES:
        register_shadow_strategy(
            strategy,
            parameters={
                "horizon": 300.0,
                "minimum_elapsed_seconds": MIN_ELAPSED_SECONDS,
                "minimum_abs_start_move_bps": MIN_ABS_START_MOVE_BPS,
                "minimum_path_er": MIN_PATH_EFFICIENCY_RATIO,
            },
            generic_signal=False,
        )

    default_config = namespace.get("DEFAULT_CONFIG")
    if isinstance(default_config, dict):
        for strategy in STRATEGIES:
            default_config[f"strategy_{strategy.lower()}_enabled"] = True
            default_config[f"strategy_{strategy.lower()}_stake"] = NORMALIZED_STAKE_USDT

    store_class = namespace["Store"]
    utc_iso = namespace["utc_iso"]

    original_init = store_class.__init__
    if not getattr(original_init, "_strong_trend_guard_v1", False):
        @wraps(original_init)
        def init_with_strong_trend_guard(self: Any, *args: Any, **kwargs: Any) -> None:
            original_init(self, *args, **kwargs)
            _ensure_schema(self)

        init_with_strong_trend_guard._strong_trend_guard_v1 = True  # type: ignore[attr-defined]
        store_class.__init__ = init_with_strong_trend_guard

    original_open = store_class.open_trade
    if not getattr(original_open, "_strong_trend_guard_v1", False):
        @wraps(original_open)
        def open_with_strong_trend_guard(self: Any, *args: Any, **kwargs: Any) -> Any:
            strategy = str(kwargs.get("strategy") or "").upper()
            market_id = kwargs.get("market_id")
            before_id = None
            if strategy in SOURCE_TO_SHADOW and market_id is not None and not getattr(self, "_strong_trend_guard_internal", False):
                before_id = _trade_id(self, strategy, int(market_id))
            result = original_open(self, *args, **kwargs)
            if strategy not in SOURCE_TO_SHADOW or market_id is None or getattr(self, "_strong_trend_guard_internal", False):
                return result
            source = _latest_trade(self, strategy, int(market_id))
            if source is None or (before_id is not None and int(source["id"]) <= before_id):
                return result
            _ensure_schema(self)
            _process_source_trade(self, source, original_open, utc_iso)
            return result

        open_with_strong_trend_guard._strong_trend_guard_v1 = True  # type: ignore[attr-defined]
        store_class.open_trade = open_with_strong_trend_guard

    original_dashboard = store_class.dashboard
    if not getattr(original_dashboard, "_strong_trend_guard_v1", False):
        @wraps(original_dashboard)
        def dashboard_with_strong_trend_guard(self: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
            payload = original_dashboard(self, *args, **kwargs)
            research = payload.get("researchForward")
            if not isinstance(research, dict):
                research = {}
                payload["researchForward"] = research
            research["strongTrendGuardExperiment"] = _experiment_state(self)
            return payload

        dashboard_with_strong_trend_guard._strong_trend_guard_v1 = True  # type: ignore[attr-defined]
        store_class.dashboard = dashboard_with_strong_trend_guard
