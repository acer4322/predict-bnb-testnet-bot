from __future__ import annotations

import json
import logging
import math
import sys
from functools import wraps
from typing import Any, Iterable

from . import m_realtime as _realtime
from .core import taker_fee
from .research_strategy_registry_patch import register_shadow_strategy


LOGGER = logging.getLogger(__name__)
VERSION = "DECISION_STRATEGY_NATIVE_V2"
RANK1_STRATEGY = "R_DECISION_RANK1"
RANK2_STRATEGY = "R_DECISION_RANK2"
STRATEGIES = (RANK1_STRATEGY, RANK2_STRATEGY)
FAMILY_SOURCES = {
    "FUTURES_LEAD": "R_FUTURES_LEAD",
    "CALIBRATED_VALUE": "R_CALIBRATED_VALUE",
    "CONSENSUS": "R_CONSENSUS",
}
EXCLUDED_FAMILIES = ("M01", "MICROPRICE", "OFI")
SOURCE_STRATEGIES = frozenset(FAMILY_SOURCES.values())
STAKE_USDT = 5.0
SLIPPAGE_BPS = 50.0
MAX_BOOK_AGE_MS = 2_000.0
MAX_BOOK_SKEW_MS = 500.0
MAX_SPREAD = 0.03
MAX_ENTRY = 0.95
PRIOR_WINS = 2.0
PRIOR_LOSSES = 2.0
RANK1_HISTORY = 60
RANK1_HALF_LIFE = 20.0
RANK1_MIN_HISTORY = 10
RANK1_MIN_SUPPORTERS = 2
RANK1_DIRECTION_SHARE = 0.67
RANK2_HISTORY = 30
RANK2_HALF_LIFE = 10.0
RANK2_MIN_CONTEXT_HISTORY = 8
RANK2_EDGE_MARGIN = 0.03
RANK2_CLEAR_CHAMPION_PROBABILITY = 0.72
RANK2_CLEAR_CHAMPION_EDGE_LEAD = 0.05
RANK2_FAMILY_CAP = 0.50
TREND_MIN_ELAPSED_SECONDS = 30.0
TREND_MIN_MOVE_BPS = 2.5
TREND_MIN_PATH_ER = 0.40
TREND_MIN_SAMPLES = 3
TREND_MAX_SPOT_AGE_MS = 2_000.0
DASHBOARD_RECENT_LIMIT = 40
DASHBOARD_TRADE_LIMIT = 5_000

_ACTIVE_TRACKER: DecisionStrategyTracker | None = None


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


def _context_key(
    *,
    side: str,
    entry_price: float,
    elapsed_seconds: float,
    start_move_bps: float,
    path_er: float,
) -> tuple[str, str, str, str]:
    phase = (
        "EARLY"
        if elapsed_seconds < 60.0
        else "MID"
        if elapsed_seconds < 180.0
        else "LATE"
    )
    if abs(start_move_bps) < 0.75:
        alignment = "FLAT"
    elif (side == "UP" and start_move_bps > 0) or (
        side == "DOWN" and start_move_bps < 0
    ):
        alignment = "ALIGNED"
    else:
        alignment = "OPPOSED"
    price_bucket = (
        "P_LT030"
        if entry_price < 0.30
        else "P_030_055"
        if entry_price < 0.55
        else "P_055_075"
        if entry_price < 0.75
        else "P_GE075"
    )
    er_bucket = (
        "ER_LOW"
        if path_er < 0.30
        else "ER_MID"
        if path_er < 0.60
        else "ER_HIGH"
    )
    return phase, alignment, price_bucket, er_bucket


def _normalized_pnl(row: Any) -> float | None:
    stake = _finite(row["stake"])
    pnl = _finite(row["pnl"])
    if stake is None or stake <= 0 or pnl is None:
        return None
    return max(-5.10, min(10.0, pnl * STAKE_USDT / stake))


def _weighted_stats(
    rows: Iterable[Any],
    *,
    limit: int,
    half_life: float,
) -> dict[str, Any]:
    usable: list[float] = []
    for row in list(rows)[-limit:]:
        pnl = _normalized_pnl(row)
        if pnl is not None:
            usable.append(pnl)
    if not usable:
        return {
            "n": 0,
            "utility": 0.0,
            "p": 0.5,
            "weightSum": 0.0,
        }
    weights = [
        0.5 ** ((len(usable) - 1 - index) / half_life)
        for index in range(len(usable))
    ]
    weight_sum = sum(weights)
    weighted_wins = sum(
        weight for weight, pnl in zip(weights, usable) if pnl > 0
    )
    probability = (weighted_wins + PRIOR_WINS) / (
        weight_sum + PRIOR_WINS + PRIOR_LOSSES
    )
    utility = sum(
        weight * pnl for weight, pnl in zip(weights, usable)
    ) / weight_sum
    return {
        "n": len(usable),
        "utility": utility,
        "p": probability,
        "weightSum": weight_sum,
    }


def _effective_break_even(entry_price: float, fee_rate_bps: float) -> float:
    return entry_price + min(entry_price, 1.0 - entry_price) * (
        fee_rate_bps / 10_000.0
    )


def _cap_family_weights(
    weights: dict[str, float],
) -> dict[str, float]:
    positive = {
        family: max(0.0, float(weight))
        for family, weight in weights.items()
        if float(weight) > 0
    }
    if len(positive) <= 1:
        return positive
    total = sum(positive.values())
    if total <= 0:
        return positive
    result = dict(positive)
    for family, weight in positive.items():
        others = total - weight
        # With a 50% cap, one family may contribute no more than all other
        # positive families combined.
        result[family] = min(weight, others)
    return result


def _rank1_decision(
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    scored: list[dict[str, Any]] = []
    for family, item in families.items():
        source = item.get("source")
        stats = item.get("rank1") or {}
        if source is None or int(stats.get("n") or 0) < RANK1_MIN_HISTORY:
            continue
        utility = float(stats.get("utility") or 0.0)
        probability = float(stats.get("p") or 0.5)
        score = max(0.0, utility / STAKE_USDT) * max(
            0.0, 2.0 * probability - 1.0
        )
        scored.append(
            {
                "family": family,
                "source": source,
                "stats": stats,
                "score": score,
                "side": str(source["side"]).upper(),
            }
        )
    if len(scored) < RANK1_MIN_SUPPORTERS:
        return {
            "status": "WAITING_HISTORY",
            "reason": "fewer than two families have ten causal settled samples",
        }
    side_weights = {
        side: sum(
            item["score"] for item in scored if item["side"] == side
        )
        for side in ("UP", "DOWN")
    }
    total_weight = sum(side_weights.values())
    if total_weight <= 0:
        return {
            "status": "NO_POSITIVE_WEIGHT",
            "reason": "utility and shrunk hit rate produce no positive weight",
            "sideWeights": side_weights,
        }
    side = max(side_weights, key=side_weights.get)
    agreement = side_weights[side] / total_weight
    supporters = [
        item
        for item in scored
        if item["side"] == side and item["score"] > 0
    ]
    if (
        len(supporters) < RANK1_MIN_SUPPORTERS
        or agreement + 1e-12 < RANK1_DIRECTION_SHARE
    ):
        return {
            "status": "NO_CONSENSUS",
            "reason": "requires two positive supporters and 67% direction weight",
            "agreementWeight": agreement,
            "sideWeights": side_weights,
        }
    mean_utility = sum(
        float(item["stats"].get("utility") or 0.0)
        for item in supporters
    ) / len(supporters)
    if mean_utility <= 0:
        return {
            "status": "NON_POSITIVE_SUPPORT_UTILITY",
            "reason": "supporter mean utility is not positive",
            "agreementWeight": agreement,
        }
    selected = max(
        supporters,
        key=lambda item: (
            float(item["score"]),
            str(item["source"].get("opened_at") or ""),
            int(item["source"].get("id") or 0),
        ),
    )
    return {
        "status": "CANDIDATE",
        "reason": "utility-weighted family consensus passed",
        "side": side,
        "selectedFamily": selected["family"],
        "selectedSourceTradeId": int(selected["source"]["id"]),
        "estimatedProbability": float(selected["stats"].get("p") or 0.5),
        "agreementWeight": agreement,
        "supporters": [item["family"] for item in supporters],
        "familyScores": {
            item["family"]: float(item["score"]) for item in scored
        },
        "sideWeights": side_weights,
        "meanSupportUtility": mean_utility,
    }


def _rank2_decision(
    families: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    eligible: list[dict[str, Any]] = []
    for family, item in families.items():
        source = item.get("source")
        source_context = item.get("sourceContext")
        stats = item.get("rank2") or {}
        if (
            source is None
            or source_context is None
            or int(stats.get("n") or 0) < RANK2_MIN_CONTEXT_HISTORY
        ):
            continue
        probability = float(stats.get("p") or 0.5)
        entry = float(source["entry_price"])
        fee_bps = float(source.get("fee_rate_bps") or 200.0)
        break_even = _effective_break_even(entry, fee_bps)
        edge = probability - break_even
        if edge + 1e-12 < RANK2_EDGE_MARGIN:
            continue
        eligible.append(
            {
                "family": family,
                "source": source,
                "stats": stats,
                "probability": probability,
                "breakEven": break_even,
                "edge": edge,
                "side": str(source["side"]).upper(),
                "context": source_context,
            }
        )
    if not eligible:
        return {
            "status": "WAITING_CONTEXT_EDGE",
            "reason": "no family has eight same-context samples and 3pp net edge",
        }

    raw_weights = {item["family"]: item["edge"] for item in eligible}
    capped_weights = _cap_family_weights(raw_weights)
    side_weights = {
        side: sum(
            capped_weights.get(item["family"], 0.0)
            for item in eligible
            if item["side"] == side
        )
        for side in ("UP", "DOWN")
    }
    side = max(side_weights, key=side_weights.get)
    supporters = [item for item in eligible if item["side"] == side]
    total_weight = sum(side_weights.values())
    agreement = side_weights[side] / total_weight if total_weight > 0 else 0.0

    ordered = sorted(
        eligible,
        key=lambda item: (
            item["probability"],
            item["edge"],
            str(item["source"].get("opened_at") or ""),
        ),
        reverse=True,
    )
    champion = ordered[0]
    runner_edge = ordered[1]["edge"] if len(ordered) > 1 else 0.0
    clear_champion = bool(
        champion["probability"] >= RANK2_CLEAR_CHAMPION_PROBABILITY
        and champion["edge"] - runner_edge + 1e-12
        >= RANK2_CLEAR_CHAMPION_EDGE_LEAD
    )
    two_same_side = len(supporters) >= 2
    if not two_same_side and not clear_champion:
        return {
            "status": "NO_CONTEXT_CHAMPION",
            "reason": "requires two same-side families or a 72% clear champion",
            "agreementWeight": agreement,
            "rawWeights": raw_weights,
            "cappedWeights": capped_weights,
        }
    selected = (
        champion
        if clear_champion and not two_same_side
        else max(
            supporters,
            key=lambda item: (
                capped_weights.get(item["family"], 0.0),
                item["probability"],
                item["edge"],
            ),
        )
    )
    return {
        "status": "CANDIDATE",
        "reason": (
            "72% clear same-context champion passed"
            if clear_champion and not two_same_side
            else "two same-side context-qualified families passed"
        ),
        "side": str(selected["side"]),
        "selectedFamily": selected["family"],
        "selectedSourceTradeId": int(selected["source"]["id"]),
        "estimatedProbability": float(selected["probability"]),
        "agreementWeight": agreement,
        "supporters": [item["family"] for item in supporters],
        "clearChampion": clear_champion,
        "rawWeights": raw_weights,
        "cappedWeights": capped_weights,
        "selectedContext": list(selected["context"]),
        "selectedSourceBreakEven": float(selected["breakEven"]),
        "selectedSourceEdge": float(selected["edge"]),
    }


def _table_exists(store: Any, table: str) -> bool:
    try:
        return store.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,),
        ).fetchone() is not None
    except Exception:
        return False


def _ensure_schema(store: Any) -> None:
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


def _install_config(store: Any) -> None:
    if getattr(store, "_read_only", False):
        return
    server = sys.modules.get("predict_bot.server")
    defaults = getattr(server, "DEFAULT_CONFIG", None) if server else None
    for strategy in STRATEGIES:
        enabled_key = f"strategy_{strategy.lower()}_enabled"
        stake_key = f"strategy_{strategy.lower()}_stake"
        if isinstance(defaults, dict):
            defaults.setdefault(enabled_key, True)
            defaults.setdefault(stake_key, STAKE_USDT)
        with store.lock:
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
            cache.setdefault(enabled_key, True)
            cache.setdefault(stake_key, STAKE_USDT)


def _latest_source_trade(
    store: Any,
    strategy: str,
    market_id: int,
) -> dict[str, Any] | None:
    row = store.db.execute(
        """SELECT id, strategy, topic_id, market_id, side, entry_price,
                  stake, fee_rate_bps, opened_at, diagnostics_json
             FROM trades
            WHERE strategy=? AND market_id=?
            ORDER BY id DESC LIMIT 1""",
        (strategy, int(market_id)),
    ).fetchone()
    return dict(row) if row is not None else None


def _causal_path(
    store: Any,
    snapshot: dict[str, Any],
) -> dict[str, Any] | None:
    market_id = int(snapshot["market_id"])
    timestamp = str(snapshot.get("timestamp") or "")
    start_price = _finite(snapshot.get("start_price"))
    current_spot = _finite(snapshot.get("spot_price"))
    seconds_left = _finite(snapshot.get("seconds_left"))
    spot_age_ms = _finite(snapshot.get("spot_age_ms"))
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
            """SELECT timestamp, spot_price
                 FROM observations
                WHERE market_id=? AND timestamp<=?
                  AND spot_price IS NOT NULL
                ORDER BY id ASC""",
            (market_id, timestamp),
        ).fetchall()
    prices = [start_price]
    for row in rows:
        value = _finite(row["spot_price"])
        if value is not None and value > 0:
            prices.append(value)
    if not prices or abs(prices[-1] - current_spot) > 1e-12:
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


def _record_source_context(
    store: Any,
    source: dict[str, Any],
    snapshot: dict[str, Any],
) -> tuple[str, str, str, str] | None:
    _ensure_schema(store)
    existing = store.db.execute(
        "SELECT phase, alignment, price_bucket, er_bucket "
        "FROM decision_strategy_source_contexts WHERE source_trade_id=?",
        (int(source["id"]),),
    ).fetchone()
    if existing is not None:
        return tuple(str(existing[key]) for key in existing.keys())  # type: ignore[return-value]
    path = _causal_path(store, snapshot)
    entry = _finite(source.get("entry_price"))
    side = str(source.get("side") or "").upper()
    if path is None or entry is None or side not in {"UP", "DOWN"}:
        return None
    context = _context_key(
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


def _history_rows(
    store: Any,
    strategy: str,
    as_of: str,
    limit: int,
) -> list[Any]:
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


def _families(
    store: Any,
    market_id: int,
    as_of: str,
) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for family, strategy in FAMILY_SOURCES.items():
        source = _latest_source_trade(store, strategy, market_id)
        context = None
        if source is not None:
            row = store.db.execute(
                """SELECT phase, alignment, price_bucket, er_bucket
                     FROM decision_strategy_source_contexts
                    WHERE source_trade_id=?""",
                (int(source["id"]),),
            ).fetchone()
            if row is not None:
                context = tuple(str(row[key]) for key in row.keys())
        rank1 = _weighted_stats(
            _history_rows(store, strategy, as_of, RANK1_HISTORY),
            limit=RANK1_HISTORY,
            half_life=RANK1_HALF_LIFE,
        )
        rank2 = (
            _weighted_stats(
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


def _trend_gate(
    store: Any,
    snapshot: dict[str, Any],
    side: str,
) -> dict[str, Any]:
    path = _causal_path(store, snapshot)
    if path is None:
        return {
            "passed": False,
            "status": "NOT_EVALUABLE",
            "reason": "causal Spot path is unavailable",
        }
    age = _finite(path.get("spotAgeMs"))
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
        "status": (
            "BLOCK_OPPOSES_STRONG_TREND" if blocked else "PASS"
        ),
        "reason": (
            "controller side opposes an established causal Spot trend"
            if blocked
            else "strong opposing trend rule did not fully match"
        ),
        "strongTrend": strong,
        "opposingDirection": opposing,
    }


def _execution(
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


def _evaluation_exists(
    store: Any,
    controller: str,
    trigger_trade_id: int,
) -> bool:
    return store.db.execute(
        """SELECT 1 FROM decision_strategy_evaluations
            WHERE controller=? AND trigger_source_trade_id=? LIMIT 1""",
        (controller, int(trigger_trade_id)),
    ).fetchone() is not None


def _record_evaluation(
    store: Any,
    *,
    controller: str,
    trigger: dict[str, Any],
    status: str,
    reason: str,
    decision: dict[str, Any],
    trend: dict[str, Any] | None,
    execution: dict[str, Any] | None,
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
                (
                    _effective_break_even(
                        float(execution["entryPrice"]),
                        float(trigger.get("fee_rate_bps") or 200.0),
                    )
                    if execution
                    else None
                ),
                diagnostics.get("finalModelEdge"),
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
        _ensure_schema(store)
        _install_config(store)

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
            source = _latest_source_trade(self.store, strategy, market_id)
            if source is None:
                continue
            _record_source_context(self.store, source, snapshot)
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
        families = _families(self.store, market_id, as_of)
        results: list[dict[str, Any]] = []
        for controller, rule in (
            (RANK1_STRATEGY, _rank1_decision),
            (RANK2_STRATEGY, _rank2_decision),
        ):
            if _evaluation_exists(self.store, controller, int(trigger["id"])):
                continue
            if not self._enabled(controller):
                _record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="DISABLED",
                    reason="controller is disabled in Paper config",
                    decision={},
                    trend=None,
                    execution=None,
                    paper_trade_id=None,
                    diagnostics={"version": VERSION},
                    created_at=as_of,
                )
                continue
            if self.store.has_trade(controller, market_id):
                _record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="ALREADY_OPEN",
                    reason="one controller trade per market",
                    decision={},
                    trend=None,
                    execution=None,
                    paper_trade_id=None,
                    diagnostics={"version": VERSION},
                    created_at=as_of,
                )
                continue
            decision = rule(families)
            if decision.get("status") != "CANDIDATE":
                _record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status=str(decision.get("status") or "ABSTAIN"),
                    reason=str(decision.get("reason") or "controller abstained"),
                    decision=decision,
                    trend=None,
                    execution=None,
                    paper_trade_id=None,
                    diagnostics={
                        "version": VERSION,
                        "includedFamilies": list(FAMILY_SOURCES),
                        "familyState": families,
                        "decision": decision,
                    },
                    created_at=as_of,
                )
                continue
            side = str(decision["side"])
            trend = _trend_gate(self.store, snapshot, side)
            if trend.get("passed") is not True:
                _record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status=str(trend.get("status") or "BLOCK_TREND"),
                    reason=str(trend.get("reason") or "trend gate blocked"),
                    decision=decision,
                    trend=trend,
                    execution=None,
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
            execution, execution_reason = _execution(snapshot, context, side)
            if execution is None:
                _record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="BLOCK_EXECUTION",
                    reason=execution_reason,
                    decision=decision,
                    trend=trend,
                    execution=None,
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
            effective_cost = _effective_break_even(
                float(execution["entryPrice"]),
                float(fee_bps),
            )
            final_edge = float(decision["estimatedProbability"]) - effective_cost
            if controller == RANK2_STRATEGY and final_edge + 1e-12 < RANK2_EDGE_MARGIN:
                diagnostics = {
                    "version": VERSION,
                    "familyState": families,
                    "decision": decision,
                    "trend": trend,
                    "execution": execution,
                    "finalModelEdge": final_edge,
                }
                _record_evaluation(
                    self.store,
                    controller=controller,
                    trigger=trigger,
                    status="BLOCK_FINAL_EDGE",
                    reason="controller execution cost leaves less than 3pp edge",
                    decision=decision,
                    trend=trend,
                    execution=execution,
                    paper_trade_id=None,
                    diagnostics=diagnostics,
                    created_at=as_of,
                )
                continue
            diagnostics = {
                "paper_only": True,
                "live_orders_affected": False,
                "forward_only": True,
                "derived_shadow": True,
                "decision_strategy_controller": True,
                "version": VERSION,
                "controller": controller,
                "includedFamilies": list(FAMILY_SOURCES),
                "excludedFamilies": list(EXCLUDED_FAMILIES),
                "triggerSourceTradeId": int(trigger["id"]),
                "triggerSourceStrategy": str(trigger["strategy"]),
                "familyState": families,
                "decision": decision,
                "trend": trend,
                "execution": execution,
                "finalModelEdge": final_edge,
                "rules": _rules(),
                "realtimeContext": json.loads(_json(context)),
            }
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
            paper = _latest_source_trade(self.store, controller, market_id)
            paper_trade_id = int(paper["id"]) if paper is not None else None
            evaluation_id = _record_evaluation(
                self.store,
                controller=controller,
                trigger=trigger,
                status="OPENED",
                reason=str(decision["reason"]),
                decision=decision,
                trend=trend,
                execution=execution,
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
                    "selected_family": decision["selectedFamily"],
                    "selected_source_trade_id": decision["selectedSourceTradeId"],
                    "estimated_probability": decision["estimatedProbability"],
                    "agreement_weight": decision["agreementWeight"],
                    "model_edge": final_edge,
                    "trend_status": trend["status"],
                }
            )
        return results


def _rules() -> dict[str, Any]:
    return {
        "entryMode": "NATIVE_EVENT_DRIVEN",
        "stakeUsdt": STAKE_USDT,
        "slippageBps": SLIPPAGE_BPS,
        "maximumBookAgeMs": MAX_BOOK_AGE_MS,
        "maximumBookSkewMs": MAX_BOOK_SKEW_MS,
        "maximumSpread": MAX_SPREAD,
        "oneTradePerMarket": True,
        "rank1": {
            "history": RANK1_HISTORY,
            "halfLife": RANK1_HALF_LIFE,
            "minimumHistory": RANK1_MIN_HISTORY,
            "minimumSupporters": RANK1_MIN_SUPPORTERS,
            "minimumDirectionShare": RANK1_DIRECTION_SHARE,
            "score": "max(0,utility/5)*max(0,2p-1)",
        },
        "rank2": {
            "history": RANK2_HISTORY,
            "halfLife": RANK2_HALF_LIFE,
            "minimumSameContextHistory": RANK2_MIN_CONTEXT_HISTORY,
            "minimumNetEdge": RANK2_EDGE_MARGIN,
            "familyCap": RANK2_FAMILY_CAP,
            "clearChampionProbability": RANK2_CLEAR_CHAMPION_PROBABILITY,
            "clearChampionEdgeLead": RANK2_CLEAR_CHAMPION_EDGE_LEAD,
            "context": ["phase", "alignment", "entryPrice", "pathEr"],
            "historyScope": "forward contexts recorded after deployment",
        },
        "trendGate": {
            "minimumElapsedSeconds": TREND_MIN_ELAPSED_SECONDS,
            "minimumMoveBps": TREND_MIN_MOVE_BPS,
            "minimumPathEr": TREND_MIN_PATH_ER,
            "minimumSamples": TREND_MIN_SAMPLES,
            "maximumSpotAgeMs": TREND_MAX_SPOT_AGE_MS,
            "missingDataPolicy": "BLOCK",
        },
    }


def _strategy_stats(store: Any, strategy: str) -> dict[str, Any]:
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
    settled = [row for row in rows if row.get("pnl") is not None]
    wins = sum(float(row["pnl"]) > 0 for row in settled)
    cumulative = 0.0
    peak = 0.0
    max_drawdown = 0.0
    for row in settled:
        cumulative += float(row["pnl"])
        peak = max(peak, cumulative)
        max_drawdown = max(max_drawdown, peak - cumulative)
    evaluation_rows = [
        dict(row)
        for row in store.db.execute(
            """SELECT status FROM decision_strategy_evaluations
                WHERE controller=? AND (? IS NULL OR created_at>=?)
                ORDER BY id DESC LIMIT ?""",
            (
                strategy,
                str(reset["reset_at"]) if reset is not None else None,
                str(reset["reset_at"]) if reset is not None else None,
                DASHBOARD_TRADE_LIMIT,
            ),
        ).fetchall()
    ]
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
        "resetAt": str(reset["reset_at"]) if reset is not None else None,
        "statusCounts": status_counts,
    }


def _experiment_state(store: Any) -> dict[str, Any]:
    if not _table_exists(store, "decision_strategy_evaluations"):
        return {
            "version": VERSION,
            "status": "WAITING_FOR_EXECUTION_STORE",
            "paperOnly": True,
            "liveOrdersAffected": False,
            "forwardOnly": True,
            "includedFamilies": list(FAMILY_SOURCES),
            "excludedFamilies": list(EXCLUDED_FAMILIES),
            "rules": _rules(),
            "strategies": {},
            "recentDecisions": [],
        }
    strategies = {
        strategy: _strategy_stats(store, strategy) for strategy in STRATEGIES
    }
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
    current: dict[str, dict[str, Any] | None] = {}
    for strategy in STRATEGIES:
        row = next(
            (item for item in recent_rows if item["controller"] == strategy),
            None,
        )
        current[strategy] = row
        strategies[strategy]["currentPreview"] = row
    context_counts = {
        str(row["source_strategy"]): int(row["samples"])
        for row in store.db.execute(
            """SELECT source_strategy, COUNT(*) AS samples
                 FROM decision_strategy_source_contexts
                GROUP BY source_strategy"""
        ).fetchall()
    }
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
        "rules": _rules(),
        "contextSamples": context_counts,
        "rank2Warmup": (
            "same-context history is collected prospectively; no startup scan "
            "of the historical observations table"
        ),
        "strategies": strategies,
        "current": current,
        "recentDecisions": recent_rows,
    }


def _wrap_dashboard(store_class: type[Any]) -> None:
    original = getattr(store_class, "dashboard", None)
    if not callable(original) or getattr(
        original, "_decision_strategy_native_v2", False
    ):
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
            experiment = _experiment_state(self)
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
                "rules": _rules(),
                "strategies": {},
                "recentDecisions": [],
            }
        research = payload.get("researchForward")
        if isinstance(research, dict):
            research["decisionStrategyExperiment"] = experiment
        return payload

    dashboard_with_decision_strategy._decision_strategy_native_v2 = True  # type: ignore[attr-defined]
    store_class.dashboard = dashboard_with_decision_strategy


def _wrap_store(engine: Any, store: Any) -> None:
    global _ACTIVE_TRACKER
    existing = getattr(store, "_decision_strategy_native_v2_tracker", None)
    if isinstance(existing, DecisionStrategyTracker):
        existing.engine = engine
        engine.decision_strategy_tracker = existing
        _ACTIVE_TRACKER = existing
        _wrap_dashboard(type(store))
        return
    original = getattr(store, "maybe_enter_m_series", None)
    if not callable(original):
        return
    tracker = DecisionStrategyTracker(engine, store)

    @wraps(original)
    def maybe_enter_with_decision_strategy(
        snapshot: dict[str, Any],
        fee_bps: int,
        *,
        realtime_context: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        opened = list(
            original(
                snapshot,
                fee_bps,
                realtime_context=realtime_context,
            )
            or []
        )
        if not any(
            str(item.get("strategy") or "").upper() in SOURCE_STRATEGIES
            for item in opened
            if isinstance(item, dict)
        ):
            return opened
        try:
            derived = tracker.process(
                opened,
                snapshot,
                int(fee_bps),
                dict(realtime_context or {}),
            )
        except Exception:
            LOGGER.exception(
                "Decision strategy source-trigger failed; returning base candidates unchanged"
            )
            return opened
        return [*opened, *derived]

    maybe_enter_with_decision_strategy._decision_strategy_native_v2 = True  # type: ignore[attr-defined]
    store.maybe_enter_m_series = maybe_enter_with_decision_strategy
    store._decision_strategy_native_v2_tracker = tracker
    engine.decision_strategy_tracker = tracker
    _ACTIVE_TRACKER = tracker
    _wrap_dashboard(type(store))


def install_decision_strategy_native_v2() -> None:
    for strategy in STRATEGIES:
        register_shadow_strategy(
            strategy,
            parameters={
                "horizon": 300.0,
                "derived_only": 1.0,
                "native_event_driven": 1.0,
            },
            generic_signal=False,
        )

    engine_class = _realtime.MSeriesRealtimeEngine
    original_init = engine_class.__init__
    if getattr(original_init, "_decision_strategy_native_v2", False):
        return

    @wraps(original_init)
    def init_with_decision_strategy(
        self: Any,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        original_init(self, *args, **kwargs)
        _wrap_store(self, self.store)

    init_with_decision_strategy._decision_strategy_native_v2 = True  # type: ignore[attr-defined]
    engine_class.__init__ = init_with_decision_strategy
