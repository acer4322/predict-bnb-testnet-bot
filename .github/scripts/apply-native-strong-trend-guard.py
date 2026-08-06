from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if old not in text:
        raise RuntimeError(f"anchor not found in {path}: {old[:120]!r}")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")


BACKEND = r'''from __future__ import annotations

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
            normalized = _normalized_source_pnl(row)
            source_status = str(row["source_status"] or "")
            if source_status in {"SETTLED_WIN", "SETTLED_LOSS"} and normalized is not None:
                stats["blockedSettled"] += 1
                if normalized > 0:
                    stats["blockedWins"] += 1
                    stats["sacrificedProfitUsdt"] += normalized
                else:
                    stats["blockedLosses"] += 1
                    stats["avoidedLossUsdt"] += -normalized

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
'''

STRONG_PANEL = r'''"use client";

type StrategyStats = {
  sourceStrategy?: string;
  shadowStrategy?: string;
  sourceTrades?: number;
  allowed?: number;
  blocked?: number;
  notEvaluable?: number;
  shadowOpened?: number;
  trades?: number;
  open?: number;
  settled?: number;
  wins?: number;
  losses?: number;
  winRate?: number | null;
  realizedPnl?: number;
  roi?: number | null;
  blockedSettled?: number;
  blockedWins?: number;
  blockedLosses?: number;
  blockedWinRate?: number | null;
  avoidedLossUsdt?: number;
  sacrificedProfitUsdt?: number;
  netProtectionUsdt?: number;
};

type Decision = {
  id?: number;
  openedAt?: string;
  marketId?: number;
  sourceStrategy?: string;
  shadowStrategy?: string;
  side?: string;
  startMoveBps?: number | null;
  pathEfficiencyRatio?: number | null;
  elapsedSeconds?: number | null;
  decision?: string;
  reason?: string;
  sourceStatus?: string | null;
  shadowStatus?: string | null;
};

type Experiment = {
  version?: string;
  status?: string;
  paperOnly?: boolean;
  liveOrdersAffected?: boolean;
  forwardOnly?: boolean;
  normalizedStakeUsdt?: number;
  rules?: Record<string, unknown>;
  decisions?: number;
  strategies?: Record<string, StrategyStats>;
  recentDecisions?: Decision[];
  runtime?: Decision | null;
};

const CARDS = [
  ["R_STRONG_TREND_GUARD_M01", "M01 逆強趨勢阻擋", "M01"],
  ["R_STRONG_TREND_GUARD_M01F", "M01F 逆強趨勢阻擋", "M01F"],
  ["R_STRONG_TREND_GUARD_M01T180", "M01T180 逆強趨勢阻擋", "M01T180"],
  ["R_STRONG_TREND_GUARD_M01O_F1", "M01O F1 逆強趨勢阻擋", "M01O_F1"],
  ["R_STRONG_TREND_GUARD_M01R", "M01R 逆強趨勢阻擋", "M01R"],
  ["R_STRONG_TREND_GUARD_MICROPRICE", "Microprice 逆強趨勢阻擋", "R_MICROPRICE"],
  ["R_STRONG_TREND_GUARD_FUTURES_LEAD", "Futures Lead 逆強趨勢阻擋", "R_FUTURES_LEAD"],
  ["R_STRONG_TREND_GUARD_CONSENSUS", "Consensus 逆強趨勢阻擋", "R_CONSENSUS"],
] as const;

function money(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `$${value.toFixed(2)}`;
}
function pct(value: number | null | undefined) {
  return value == null || !Number.isFinite(value) ? "—" : `${(value * 100).toFixed(1)}%`;
}
function decimal(value: number | null | undefined, digits = 3) {
  return value == null || !Number.isFinite(value) ? "—" : value.toFixed(digits);
}
function compact(rules: Record<string, unknown> | undefined) {
  return Object.entries(rules ?? {}).map(([key, value]) => `${key}=${String(value)}`).join(" · ");
}

export default function StrongTrendGuardPanel({ experiment }: { experiment?: Experiment | null }) {
  const runtime = experiment?.runtime;
  const strategies = experiment?.strategies ?? {};
  const recent = experiment?.recentDecisions ?? [];
  return <div role="tabpanel" id="strong-trend-guard-panel" aria-labelledby="strong-trend-guard-tab" className="m-exit-experiment research-forward-panel strong-trend-guard-panel">
    <style>{`
      .strong-trend-guard-panel .stg-runtime{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
      .strong-trend-guard-panel .stg-runtime article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
      .strong-trend-guard-panel .stg-runtime span,.strong-trend-guard-panel .stg-runtime small{display:block;color:#91a0bb}
      .strong-trend-guard-panel .stg-runtime strong{display:block;margin:4px 0}
      .strong-trend-guard-panel .stg-protection{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:12px}
      .strong-trend-guard-panel .stg-protection div{padding:9px;border-radius:10px;background:rgba(126,145,178,.08)}
      .strong-trend-guard-panel .stg-protection span,.strong-trend-guard-panel .stg-protection strong{display:block}
      @media(max-width:720px){.strong-trend-guard-panel .stg-protection{grid-template-columns:1fr}}
    `}</style>
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">STRONG OPPOSING TREND GUARD · NATIVE FORWARD PAPER</span><h3>逆強趨勢阻擋 · 八組獨立 Paper Shadow</h3></div>
      <p>每個來源策略真正開立 paper 單後才評估；只使用該筆進場時間以前的 Spot observations。原策略完全不變，八組 Shadow 固定 5 USDT、永久不轉送實單。</p>
    </section>
    <section className="m-exit-rules" aria-label="逆強趨勢阻擋規則">
      <div className="m-exit-rules-head"><div><span className="eyebrow">CAUSAL OBSERVATIONS · FAIL OPEN FOR MISSING DATA</span><h3>Guard 即時狀態</h3></div><span className="m-exit-api-state live">{experiment?.status ?? "WAITING"}</span></div>
      <div className="stg-runtime">
        <article><span>最近市場／來源</span><strong>#{runtime?.marketId ?? "—"} · {runtime?.sourceStrategy ?? "—"}</strong><small>{runtime?.side ?? "—"} · {runtime?.decision ?? "等待來源交易"}</small></article>
        <article><span>Start move</span><strong>{decimal(runtime?.startMoveBps, 3)} bps</strong><small>門檻 |move| ≥ {String(experiment?.rules?.minimumAbsoluteStartMoveBpsInclusive ?? 2.5)} bps</small></article>
        <article><span>路徑 ER／已開盤</span><strong>{decimal(runtime?.pathEfficiencyRatio, 3)} · {decimal(runtime?.elapsedSeconds, 1)}s</strong><small>ER ≥ {String(experiment?.rules?.minimumPathEfficiencyRatioInclusive ?? 0.4)} · elapsed ≥ {String(experiment?.rules?.minimumElapsedSecondsInclusive ?? 30)}s</small></article>
        <article><span>決策總數／版本</span><strong>{experiment?.decisions ?? 0}</strong><small>{experiment?.version ?? "等待後端"}</small></article>
      </div>
      <small>{compact(experiment?.rules)}</small>
    </section>
    <div className="m-exit-summary-grid research-strategy-grid">
      {CARDS.map(([id, title, source]) => {
        const stats = strategies[id] ?? {};
        return <article className="m-exit-card cyan" key={id} data-strong-trend-strategy={id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{id}</span><h3>{title}</h3></div><div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div></div>
          <div className="m-exit-primary-stats">
            <div><span>Guard 後收益</span><strong className={(stats.realizedPnl ?? 0) >= 0 ? "positive" : "negative"}>{money(stats.realizedPnl)}</strong></div>
            <div><span>Guard 後勝率</span><strong>{pct(stats.winRate)}</strong></div>
            <div><span>ROI</span><strong>{pct(stats.roi)}</strong></div>
          </div>
          <div className={`continuous-calibration-state ${(stats.settled ?? 0) >= 30 ? "ready" : "warmup"}`}>
            <span>來源 {source} · 固定 5 USDT</span>
            <strong>來源 {stats.sourceTrades ?? 0} · 放行 {stats.allowed ?? 0} · 阻擋 {stats.blocked ?? 0}</strong>
            <small>無法評估 {stats.notEvaluable ?? 0} · Shadow {stats.shadowOpened ?? 0} · 未結算 {stats.open ?? 0}</small>
          </div>
          <div className="stg-protection">
            <div><span>避免虧損</span><strong className="positive">{money(stats.avoidedLossUsdt)}</strong></div>
            <div><span>犧牲獲利</span><strong className="negative">{money(stats.sacrificedProfitUsdt)}</strong></div>
            <div><span>淨保護</span><strong className={(stats.netProtectionUsdt ?? 0) >= 0 ? "positive" : "negative"}>{money(stats.netProtectionUsdt)}</strong></div>
          </div>
          <p>已阻擋結算 {stats.blockedSettled ?? 0} · 原本勝率 {pct(stats.blockedWinRate)} · 勝 {stats.blockedWins ?? 0}／敗 {stats.blockedLosses ?? 0}</p>
          <small>Forward-only；資料缺失採 ALLOW_NOT_EVALUABLE，不把缺資料誤算成危險趨勢。</small>
        </article>;
      })}
    </div>
    <section className="shadow-tag-live-orders">
      <div><span className="eyebrow">RECENT CAUSAL DECISIONS</span><h3>最近 40 筆 Guard 決策</h3></div>
      <div className="table-scroll"><table><thead><tr><th>時間／市場</th><th>來源／方向</th><th>Move／ER</th><th>決策</th><th>來源／Shadow 結果</th></tr></thead><tbody>
        {recent.length === 0 ? <tr><td colSpan={5} className="empty">等待八個來源策略建立新的 forward paper 交易。</td></tr> : recent.map(row => <tr key={row.id}>
          <td>{row.openedAt ?? "—"}<small>#{row.marketId ?? "—"}</small></td>
          <td>{row.sourceStrategy ?? "—"} · {row.side ?? "—"}<small>{row.shadowStrategy ?? "—"}</small></td>
          <td>{decimal(row.startMoveBps, 3)} bps<small>ER {decimal(row.pathEfficiencyRatio, 3)} · {decimal(row.elapsedSeconds, 1)}s</small></td>
          <td>{row.decision ?? "—"}<small>{row.reason ?? ""}</small></td>
          <td>{row.sourceStatus ?? "—"}<small>Shadow {row.shadowStatus ?? "—"}</small></td>
        </tr>)}
      </tbody></table></div>
    </section>
  </div>;
}
'''

CALIBRATED_PANEL = r'''"use client";

const STRATEGIES = [
  ["R_CALIBRATED_VALUE_IMMEDIATE_CONTROL", "Calibrated Value 即時進場對照", "INITIAL EVENT CONTROL", "第一個直接雙 token REST 事件若淨 edge ≥0.015，就使用當時直接 Ask 開立 5 USDT paper 單，不等待價格確認。", "purple"],
  ["R_CALIBRATED_VALUE_CONFIRM_V2", "Calibrated Value 多事件確認順勢 V2", "FOLLOW CONFIRMED REPRICING", "至少第二個不同事件、持續 ≥150ms；方向不變、midpoint 同向、最終 edge 合格才沿原方向進場。", "cyan"],
  ["R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE", "Calibrated Value 多事件確認反向 V2", "REVERSE CONFIRMED REPRICING", "與順勢 V2 使用相同市場與確認事件，但買入相反方向。", "coral"],
  ["R_CALIBRATED_VALUE_CONFIRM_RANGE12", "Calibrated Value 確認 · Range 1–2", "CONFIRM V2 + RANGE SCORE 1–2", "沿用 Confirm V2，另外要求當輪 Range score 為 1 或 2，且有效穿越不超過 2 次。", "green"],
  ["R_CALIBRATED_VALUE_LOWTAIL_CONFIRM", "Calibrated Value 低價肥尾確認", "STRICT LOW-PRICE TAIL", "確認後 entry 介於 0.10～0.221，使用更嚴格事件數、延遲、book freshness 與 edge retention。", "amber"],
] as const;

function money(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `$${n.toFixed(2)}` : "—"; }
function ratio(value: unknown) { const n = Number(value); return Number.isFinite(n) ? `${(n * 100).toFixed(1)}%` : "—"; }
function decimal(value: unknown, digits = 3) { const n = Number(value); return Number.isFinite(n) ? n.toFixed(digits) : "—"; }
function compact(value: unknown) { return value && typeof value === "object" ? Object.entries(value as Record<string, unknown>).slice(0, 14).map(([key, item]) => `${key}=${String(item)}`).join(" · ") : ""; }
function rowText(row: Record<string, unknown> | undefined, key: string) { return row?.[key] == null ? "—" : String(row[key]); }

export default function CalibratedConfirmationLab({ payload }: { payload: any }) {
  const experiment = payload?.researchForward?.calibratedValueConfirmationExperiment;
  const runtime = experiment?.runtime;
  const decision = runtime?.lastDecision;
  const recent = experiment?.recentCohorts ?? [];
  return <div role="tabpanel" id="calibrated-value-confirmation-panel" aria-labelledby="calibrated-value-confirmation-tab" className="m-exit-experiment research-forward-panel calibrated-value-confirmation-lab">
    <style>{`
      .calibrated-value-confirmation-lab .cv-confirm-runtime{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px;margin:14px 0}
      .calibrated-value-confirmation-lab .cv-confirm-runtime article{padding:12px;border:1px solid rgba(126,145,178,.24);border-radius:14px;background:rgba(13,18,29,.72)}
      .calibrated-value-confirmation-lab .cv-confirm-runtime span,.calibrated-value-confirmation-lab .cv-confirm-runtime small{display:block;color:#91a0bb}
      .calibrated-value-confirmation-lab .cv-confirm-runtime strong{display:block;margin:4px 0}
      .calibrated-value-confirmation-lab .cv-confirm-reason{margin:12px 0 18px;padding:12px 14px;border-left:3px solid #7ee3f5;background:rgba(74,196,219,.08)}
    `}</style>
    <section className="strategy-family-intro m-exit-intro">
      <div><span className="eyebrow">CALIBRATED VALUE CONFIRMATION LAB · NATIVE FORWARD PAPER</span><h3>Calibrated Value 五組前向確認測試</h3></div>
      <p>已改為主頁原生 React 頁籤：不再使用 Portal、MutationObserver、DOM 插入或額外輪詢。五組前向帳本與原有後端規則保持不變。</p>
    </section>
    <section className="m-exit-rules">
      <div className="m-exit-rules-head"><div><span className="eyebrow">DIRECT DUAL-TOKEN REST · FIXED COHORT</span><h3>確認引擎狀態</h3></div><span className="m-exit-api-state live">{decision?.status ?? runtime?.status ?? "WAITING"}</span></div>
      <div className="cv-confirm-runtime">
        <article><span>目前市場</span><strong>#{runtime?.currentMarketId ?? "—"}</strong><small>來源 {experiment?.sourceStrategy ?? "R_CALIBRATED_VALUE"}</small></article>
        <article><span>初始方向／edge</span><strong>{runtime?.currentInitialSide ?? "—"} · {decimal(runtime?.currentInitialEdge, 4)}</strong><small>midpoint {decimal(runtime?.currentInitialMidpoint, 4)}</small></article>
        <article><span>確認事件</span><strong>{runtime?.currentConfirmations ?? 0} / {String(runtime?.rules?.minimumConfirmations ?? 2)}</strong><small>至少 {String(runtime?.rules?.minimumConfirmationMs ?? 150)}ms</small></article>
        <article><span>即時／確認市場</span><strong>{experiment?.immediateMarkets ?? 0} / {experiment?.pairedMarkets ?? 0}</strong><small>完整三組 {experiment?.completeCohorts ?? 0} · Range12 {experiment?.range12Markets ?? 0} · Lowtail {experiment?.lowtailMarkets ?? 0}</small></article>
      </div>
      <p className="cv-confirm-reason"><strong>{decision?.reason ?? "等待合格的直接 REST book 事件"}</strong><br />市場 #{decision?.marketId ?? runtime?.currentMarketId ?? "—"} · 初始 edge {decimal(decision?.initialEdge, 4)} · 最終 edge {decimal(decision?.finalEdge, 4)} · 保留 {ratio(decision?.retainedEdgeRatio)} · midpoint Δ {decimal(decision?.chosenMidpointDelta, 4)}</p>
      <small>{compact(runtime?.rules)}</small>
    </section>
    <div className="m-exit-summary-grid research-strategy-grid">
      {STRATEGIES.map(([id, title, kicker, rule, tone]) => {
        const summary = payload?.summaries?.[id] ?? {};
        const stats = experiment?.strategies?.[id] ?? {};
        const wins = summary.wins ?? stats.wins ?? 0;
        const losses = summary.losses ?? stats.losses ?? 0;
        const settled = wins + losses;
        const pnl = summary.realized_pnl ?? stats.realizedPnl ?? 0;
        const validation = payload?.researchForward?.strategies?.[id]?.chronologicalValidation;
        const runtimeDecision = experiment?.filterRuntime?.lastDecisions?.[id];
        return <article className={`m-exit-card ${tone}`} key={id} data-calibrated-strategy={id}>
          <div className="m-exit-card-head"><div><span className="eyebrow">{id} · {kicker}</span><h3>{title}</h3></div><div className="m-exit-card-actions"><span className="m-exit-id">PAPER ONLY</span></div></div>
          <div className="m-exit-primary-stats"><div><span>已實現收益</span><strong className={pnl >= 0 ? "positive" : "negative"}>{money(pnl)}</strong></div><div><span>勝率</span><strong>{settled ? ratio(wins / settled) : "—"}</strong></div><div><span>交易／未結算</span><strong>{summary.trades ?? stats.trades ?? 0} / {summary.open ?? stats.open ?? 0}</strong></div></div>
          <div className={`continuous-calibration-state ${settled >= 30 ? "ready" : "warmup"}`}><span>{stats.mode ?? kicker}</span><strong>已結算 {settled} / 30 · 勝 {wins} · 敗 {losses}</strong><small>平均進場價 {decimal(stats.averageEntryPrice)}</small></div>
          <p>{rule}</p><small>狀態 {validation?.status ?? "COLLECTING"} · Forward-only · 每筆 5 USDT · 不回填、不轉送實單。</small>
          {runtimeDecision && <small>Runtime：{String(runtimeDecision.status ?? "WAITING")} · {String(runtimeDecision.reason ?? "")}</small>}
        </article>;
      })}
    </div>
    <section className="shadow-tag-live-orders"><div><span className="eyebrow">MATCHED COHORT AUDIT</span><h3>最近完整確認 cohort</h3></div><div className="table-scroll"><table><thead><tr><th>市場</th><th>即時</th><th>順勢</th><th>反向</th><th>Range／Lowtail</th></tr></thead><tbody>
      {recent.length === 0 ? <tr><td colSpan={5} className="empty">等待新的前向確認事件。</td></tr> : recent.map((item: any) => <tr key={item.marketId}><td>#{item.marketId ?? "—"}</td><td>{rowText(item.immediate, "side")} · {rowText(item.immediate, "status")}</td><td>{rowText(item.confirm, "side")} · {rowText(item.confirm, "status")}</td><td>{rowText(item.reverse, "side")} · {rowText(item.reverse, "status")}</td><td>{rowText(item.range12, "status")} / {rowText(item.lowtail, "status")}</td></tr>)}
    </tbody></table></div></section>
  </div>;
}
'''

PYTEST = r'''from __future__ import annotations

from datetime import datetime, timedelta, timezone

from predict_bot import server
from predict_bot.research_forward import GENERIC_SIGNAL_RESEARCH_STRATEGIES
from predict_bot.strong_trend_guard_shadows import (
    SOURCE_TO_SHADOW,
    STRATEGIES,
    evaluate_strong_trend,
)


def rows(prices: list[float], *, side_seconds: float = 250.0, age: float = 20.0):
    return [
        {
            "start_price": 100.0,
            "spot_price": price,
            "seconds_left": side_seconds,
            "spot_age_ms": age,
        }
        for price in prices
    ]


def test_decision_boundaries_and_direction():
    blocked = evaluate_strong_trend(rows([99.99, 99.98, 99.96]), "UP")
    assert blocked["decision"] == "BLOCK_STRONG_OPPOSING_TREND"
    assert blocked["startMoveBps"] <= -2.5
    assert blocked["pathEfficiencyRatio"] >= 0.40

    aligned = evaluate_strong_trend(rows([99.99, 99.98, 99.96]), "DOWN")
    assert aligned["decision"] == "ALLOW"

    early = evaluate_strong_trend(rows([99.99, 99.98, 99.96], side_seconds=280.1), "UP")
    assert early["decision"] == "ALLOW"

    stale = evaluate_strong_trend(rows([99.99, 99.98, 99.96], age=3000.0), "UP")
    assert stale["decision"] == "ALLOW_NOT_EVALUABLE"


def insert_observations(store, market_id: int, prices: list[float], seconds_left: float = 250.0):
    now = datetime.now(timezone.utc) - timedelta(seconds=10)
    with store.lock:
        for index, price in enumerate(prices):
            store.db.execute(
                """INSERT INTO observations(
                       timestamp, topic_id, market_id, title, start_price, spot_price,
                       spot_age_ms, seconds_left, up_ask, up_bid, down_ask, down_bid,
                       up_ask_size, up_bid_size, down_ask_size, down_bid_size,
                       book_skew_ms, book_age_ms
                   ) VALUES (?, 1, ?, 'test', 100.0, ?, 10.0, ?, .20, .19, .80, .79,
                             100, 100, 100, 100, 10, 10)""",
                ((now + timedelta(seconds=index)).isoformat(), market_id, price, seconds_left),
            )
        store.db.commit()


def test_store_creates_allow_shadow_and_records_block(tmp_path):
    store = server.Store(tmp_path / "simulation.db")

    insert_observations(store, 101, [99.99, 99.98, 99.96])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=101,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="source",
    )
    blocked = store.db.execute(
        "SELECT * FROM strong_trend_guard_decisions WHERE market_id=101"
    ).fetchone()
    assert blocked["decision"] == "BLOCK_STRONG_OPPOSING_TREND"
    assert blocked["shadow_trade_id"] is None

    insert_observations(store, 102, [99.99, 100.00, 100.04])
    store.open_trade(
        strategy="M01",
        topic_id=1,
        market_id=102,
        side="UP",
        entry=.20,
        target=None,
        stake=10.0,
        fee_rate_bps=200,
        note="source",
    )
    allowed = store.db.execute(
        "SELECT * FROM strong_trend_guard_decisions WHERE market_id=102"
    ).fetchone()
    assert allowed["decision"] == "ALLOW"
    shadow = store.db.execute(
        "SELECT * FROM trades WHERE strategy='R_STRONG_TREND_GUARD_M01' AND market_id=102"
    ).fetchone()
    assert shadow is not None
    assert shadow["side"] == "UP"
    assert shadow["entry_price"] == .20
    assert shadow["stake"] == 5.0


def test_eight_strategies_are_derived_paper_shadows():
    assert len(SOURCE_TO_SHADOW) == 8
    assert len(STRATEGIES) == 8
    assert not (set(STRATEGIES) & set(GENERIC_SIGNAL_RESEARCH_STRATEGIES))
    for strategy in STRATEGIES:
        assert server.DEFAULT_CONFIG[f"strategy_{strategy.lower()}_enabled"] is True
        assert server.DEFAULT_CONFIG[f"strategy_{strategy.lower()}_stake"] == 5.0
'''

NODE_TEST = r'''import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";

const page = fs.readFileSync(new URL("../app/page.tsx", import.meta.url), "utf8");
const layout = fs.readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
const strong = fs.readFileSync(new URL("../app/strong-trend-guard-panel.tsx", import.meta.url), "utf8");
const calibrated = fs.readFileSync(new URL("../app/calibrated-confirmation-lab.tsx", import.meta.url), "utf8");

const banned = ["MutationObserver", "createPortal", "document.querySelector", "document.createElement", "setInterval", "/api/state"];

test("Strong Trend Guard is a native page tab with no injector or polling", () => {
  assert.match(page, /type StrategyView = [^;]*"strong-trend-guard"/s);
  assert.match(page, /id="strong-trend-guard-tab"/);
  assert.match(page, /<StrongTrendGuardPanel/);
  for (const token of banned) assert.equal(strong.includes(token), false, token);
});

test("Calibrated five-way confirmation is native and unmounted from layout", () => {
  assert.match(page, /id="calibrated-value-confirmation-tab"/);
  assert.match(page, /<CalibratedConfirmationLab payload=\{state\}/);
  assert.equal(layout.includes("CalibratedConfirmationLab"), false);
  for (const token of banned) assert.equal(calibrated.includes(token), false, token);
});

test("Reliability mirror remains the existing native React panel", () => {
  assert.match(page, /function ReliabilityShadowPanel/);
  assert.match(page, /strategyView === "reliability-shadow" \? <ReliabilityShadowPanel/);
  assert.equal(layout.includes("ReliabilityShadow"), false);
});
'''

(ROOT / "src/predict_bot/strong_trend_guard_shadows.py").write_text(BACKEND, encoding="utf-8")
(ROOT / "dashboard/app/strong-trend-guard-panel.tsx").write_text(STRONG_PANEL, encoding="utf-8")
(ROOT / "dashboard/app/calibrated-confirmation-lab.tsx").write_text(CALIBRATED_PANEL, encoding="utf-8")
(ROOT / "tests/test_strong_trend_guard_shadows.py").write_text(PYTEST, encoding="utf-8")
(ROOT / "dashboard/tests/native-strong-trend-guard.test.mjs").write_text(NODE_TEST, encoding="utf-8")

server = ROOT / "src/predict_bot/server.py"
replace_once(
    server,
    "\n\ndef main() -> None:\n",
    "\n\nfrom .strong_trend_guard_shadows import install_strong_trend_guard_shadows\n\ninstall_strong_trend_guard_shadows(globals())\n\n\ndef main() -> None:\n",
)

layout = ROOT / "dashboard/app/layout.tsx"
text = layout.read_text(encoding="utf-8")
text = text.replace('import CalibratedConfirmationLab from "./calibrated-confirmation-lab";\n', "")
text = text.replace("      <CalibratedConfirmationLab />\n", "")
layout.write_text(text, encoding="utf-8")

page = ROOT / "dashboard/app/page.tsx"
replace_once(
    page,
    'import type { ReactNode } from "react";\n',
    'import type { ReactNode } from "react";\nimport CalibratedConfirmationLab from "./calibrated-confirmation-lab";\nimport StrongTrendGuardPanel from "./strong-trend-guard-panel";\n',
)
replace_once(
    page,
    'type StrategyView = "live-m0w" | "research" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";',
    'type StrategyView = "live-m0w" | "research" | "calibrated-confirmation" | "strong-trend-guard" | "reliability-shadow" | "lead-observer" | "m-series" | "pair-arb" | "legacy" | "paused";',
)
replace_once(
    page,
    '  const isNonConfigView = isLiveView || isReliabilityView;\n',
    '  const isNonConfigView = isLiveView || isReliabilityView || strategyView === "calibrated-confirmation" || strategyView === "strong-trend-guard";\n',
)
replace_once(
    page,
    '  const visibleTrades = strategyView === "lead-observer" ? observerTradePage.trades : state.trades.filter(trade => {\n    if (isPausedView) return stoppedStrategySet.has(trade.strategy);',
    '  const visibleTrades = strategyView === "lead-observer" ? observerTradePage.trades : state.trades.filter(trade => {\n    if (strategyView === "strong-trend-guard") return trade.strategy.startsWith("R_STRONG_TREND_GUARD_");\n    if (strategyView === "calibrated-confirmation") return ["R_CALIBRATED_VALUE_IMMEDIATE_CONTROL", "R_CALIBRATED_VALUE_CONFIRM_V2", "R_CALIBRATED_VALUE_CONFIRM_V2_REVERSE", "R_CALIBRATED_VALUE_CONFIRM_RANGE12", "R_CALIBRATED_VALUE_LOWTAIL_CONFIRM"].includes(String(trade.strategy));\n    if (isPausedView) return stoppedStrategySet.has(trade.strategy);',
)
replace_once(
    page,
    ': strategyView === "reliability-shadow" ? "MODEL RELIABILITY · SHADOW TAGS"',
    ': strategyView === "calibrated-confirmation" ? "CALIBRATED VALUE · NATIVE CONFIRMATION" : strategyView === "strong-trend-guard" ? "STRONG OPPOSING TREND · EIGHT SHADOWS" : strategyView === "reliability-shadow" ? "MODEL RELIABILITY · SHADOW TAGS"',
)
replace_once(
    page,
    ': strategyView === "reliability-shadow" ? "模型可靠／失準研究標籤"',
    ': strategyView === "calibrated-confirmation" ? "Calibrated Value 五組前向確認測試" : strategyView === "strong-trend-guard" ? "逆強趨勢阻擋測試" : strategyView === "reliability-shadow" ? "模型可靠／失準研究標籤"',
)
reliability_tab = '          <button type="button" role="tab" id="reliability-shadow-tab"'
if reliability_tab not in page.read_text(encoding="utf-8"):
    raise RuntimeError("reliability tab anchor missing")
text = page.read_text(encoding="utf-8")
insert_tabs = '''          <button type="button" role="tab" id="calibrated-value-confirmation-tab" aria-controls="calibrated-value-confirmation-panel" aria-selected={strategyView === "calibrated-confirmation"} className={strategyView === "calibrated-confirmation" ? "active" : ""} onClick={() => setStrategyView("calibrated-confirmation")}><strong>Calibrated 確認</strong><span>五組前向測試 · 原生頁籤</span></button>\n          <button type="button" role="tab" id="strong-trend-guard-tab" aria-controls="strong-trend-guard-panel" aria-selected={strategyView === "strong-trend-guard"} className={strategyView === "strong-trend-guard" ? "active shadow-tag" : "shadow-tag"} onClick={() => setStrategyView("strong-trend-guard")}><strong>逆強趨勢阻擋</strong><span>8 組來源策略 · Forward Paper A/B</span></button>\n'''
page.write_text(text.replace(reliability_tab, insert_tabs + reliability_tab, 1), encoding="utf-8")
replace_once(
    page,
    'onRulesSave={saveLiveRules} /> : strategyView === "reliability-shadow" ? <ReliabilityShadowPanel',
    'onRulesSave={saveLiveRules} /> : strategyView === "calibrated-confirmation" ? <CalibratedConfirmationLab payload={state} /> : strategyView === "strong-trend-guard" ? <StrongTrendGuardPanel experiment={(state.researchForward as any)?.strongTrendGuardExperiment} /> : strategyView === "reliability-shadow" ? <ReliabilityShadowPanel',
)

print("native Strong Trend Guard patch applied")
