from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import numpy as np

from predict_bot.core import taker_fee


HORIZONS = (180, 120, 60, 30)
LAGS = (3, 5, 10, 20)
STAKE = 10.0
FEE_BPS = 200
SLIPPAGE_BPS = 50
MAX_BOOK_AGE_MS = 2000.0
MAX_BOOK_SKEW_MS = 500.0
MAX_SPREAD = 0.03
MIN_ENTRY = 0.05

SOURCES = {
    "MICROPRICE": "https://papers.ssrn.com/sol3/papers.cfm?abstract_id=2970694",
    "OFI": "https://arxiv.org/abs/1011.6402",
    "FUTURES_LEAD": "https://doi.org/10.1016/j.ribaf.2019.101116",
    "CALIBRATED_VALUE": "https://www.nber.org/papers/w12200",
    "CONSENSUS": "https://arxiv.org/abs/2601.07852",
}


@dataclass(frozen=True)
class Trade:
    strategy: str
    split: str
    market_id: int
    timestamp: str
    horizon_seconds: int
    side: str
    winner: str
    raw_ask: float
    entry_price: float
    shares: float
    fee: float
    pnl: float
    signal: float


def _connect_read_only(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=60
    )
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=60000")
    return db


def _finite(*values: Any) -> bool:
    try:
        return all(math.isfinite(float(value)) for value in values)
    except (TypeError, ValueError):
        return False


def _snapshot_at(rows: list[sqlite3.Row], target: float) -> sqlite3.Row | None:
    for row in rows:
        seconds_left = row["seconds_left"]
        if seconds_left is None:
            continue
        seconds = float(seconds_left)
        if seconds <= target:
            return row if target - seconds <= 3.0 else None
    return None


def _book_valid(row: sqlite3.Row) -> bool:
    values = [
        row[name]
        for name in (
            "up_ask",
            "up_bid",
            "down_ask",
            "down_bid",
            "up_ask_size",
            "up_bid_size",
            "down_ask_size",
            "down_bid_size",
            "book_age_ms",
            "book_skew_ms",
        )
    ]
    if not _finite(*values):
        return False
    up_ask, up_bid = float(row["up_ask"]), float(row["up_bid"])
    down_ask, down_bid = float(row["down_ask"]), float(row["down_bid"])
    return (
        0 < up_bid <= up_ask < 1
        and 0 < down_bid <= down_ask < 1
        and float(row["up_ask_size"]) > 0
        and float(row["up_bid_size"]) > 0
        and float(row["down_ask_size"]) > 0
        and float(row["down_bid_size"]) > 0
        and 0 <= float(row["book_age_ms"]) <= MAX_BOOK_AGE_MS
        and 0 <= float(row["book_skew_ms"]) <= MAX_BOOK_SKEW_MS
    )


def _mid(row: sqlite3.Row, side: str) -> float:
    prefix = side.lower()
    return (float(row[f"{prefix}_bid"]) + float(row[f"{prefix}_ask"])) / 2


def _microprice_imbalance(row: sqlite3.Row, side: str) -> float:
    prefix = side.lower()
    bid_size = float(row[f"{prefix}_bid_size"])
    ask_size = float(row[f"{prefix}_ask_size"])
    return (bid_size - ask_size) / (bid_size + ask_size)


def _microprice_score(row: sqlite3.Row) -> float:
    return _microprice_imbalance(row, "UP") - _microprice_imbalance(row, "DOWN")


def _ofi(previous: sqlite3.Row, current: sqlite3.Row, side: str) -> float:
    prefix = side.lower()
    old_bid = float(previous[f"{prefix}_bid"])
    new_bid = float(current[f"{prefix}_bid"])
    old_ask = float(previous[f"{prefix}_ask"])
    new_ask = float(current[f"{prefix}_ask"])
    old_bid_size = float(previous[f"{prefix}_bid_size"])
    new_bid_size = float(current[f"{prefix}_bid_size"])
    old_ask_size = float(previous[f"{prefix}_ask_size"])
    new_ask_size = float(current[f"{prefix}_ask_size"])
    value = (
        (new_bid_size if new_bid >= old_bid else 0.0)
        - (old_bid_size if new_bid <= old_bid else 0.0)
        - (new_ask_size if new_ask <= old_ask else 0.0)
        + (old_ask_size if new_ask >= old_ask else 0.0)
    )
    depth = old_bid_size + new_bid_size + old_ask_size + new_ask_size
    return value / depth if depth > 0 else 0.0


def _log_return(previous: Any, current: Any) -> float | None:
    if not _finite(previous, current):
        return None
    old, new = float(previous), float(current)
    if old <= 0 or new <= 0:
        return None
    return math.log(new / old)


def _timestamp_ns(value: str) -> int:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return int(parsed.timestamp() * 1_000_000_000)


def _load_microstructure(
    path: Path,
) -> dict[int, list[tuple[int, float, float]]]:
    db = _connect_read_only(path)
    by_market: dict[int, list[tuple[int, float, float]]] = {}
    try:
        for row in db.execute(
            """SELECT timestamp_ns, market_id, spot_price, futures_price
                 FROM microstructure_snapshots
                WHERE market_id IS NOT NULL
                  AND spot_price IS NOT NULL
                  AND futures_price IS NOT NULL
                ORDER BY timestamp_ns"""
        ):
            by_market.setdefault(int(row["market_id"]), []).append(
                (
                    int(row["timestamp_ns"]),
                    float(row["spot_price"]),
                    float(row["futures_price"]),
                )
            )
    finally:
        db.close()
    return by_market


def _nearest_micro(
    rows: list[tuple[int, float, float]], target_ns: int
) -> tuple[int, float, float] | None:
    timestamps = [row[0] for row in rows]
    index = bisect.bisect_right(timestamps, target_ns) - 1
    if index < 0:
        return None
    selected = rows[index]
    return selected if target_ns - selected[0] <= 1_500_000_000 else None


def _extract_features(
    database: Path, microstructure_database: Path,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    microstructure = _load_microstructure(microstructure_database)
    db = _connect_read_only(database)
    try:
        market_rows = db.execute(
            """SELECT o.market_id, MIN(o.id) AS first_observation_id
                 FROM observations AS o
                 JOIN market_settlements AS s ON s.market_id=o.market_id
                WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
                GROUP BY o.market_id ORDER BY first_observation_id"""
        ).fetchall()
        all_market_ids = [int(row["market_id"]) for row in market_rows]
        market_ids = [market_id for market_id in all_market_ids if market_id in microstructure]
        development_end = math.ceil(len(market_ids) * 0.60)
        validation_end = math.ceil(len(market_ids) * 0.80)
        split_by_market = {
            market_id: (
                "development"
                if index < development_end
                else "validation" if index < validation_end else "holdout"
            )
            for index, market_id in enumerate(market_ids)
        }
        snapshot = db.execute(
            """SELECT MIN(timestamp) AS first_timestamp,
                      MAX(timestamp) AS last_timestamp,
                      COUNT(*) AS observation_rows
                 FROM observations"""
        ).fetchone()

        features: list[dict[str, Any]] = []

        def process_market(rows: list[sqlite3.Row]) -> None:
            if not rows:
                return
            market_id = int(rows[0]["market_id"])
            if market_id not in split_by_market:
                return
            winner = str(rows[0]["official_winner"])
            for horizon in HORIZONS:
                current = _snapshot_at(rows, horizon)
                if current is None or not _book_valid(current):
                    continue
                current_micro = _nearest_micro(
                    microstructure[market_id], _timestamp_ns(str(current["timestamp"]))
                )
                if current_micro is None:
                    continue
                lagged: dict[int, dict[str, float]] = {}
                for lag in LAGS:
                    previous = _snapshot_at(rows, horizon + lag)
                    if previous is None or not _book_valid(previous):
                        continue
                    previous_micro = _nearest_micro(
                        microstructure[market_id],
                        _timestamp_ns(str(previous["timestamp"])),
                    )
                    if previous_micro is None:
                        continue
                    spot_return = _log_return(previous_micro[1], current_micro[1])
                    futures_return = _log_return(previous_micro[2], current_micro[2])
                    up_mid_change = _mid(current, "UP") - _mid(previous, "UP")
                    if spot_return is None:
                        continue
                    lagged[lag] = {
                        "spotReturn": spot_return,
                        "futuresReturn": futures_return,
                        "upMidChange": up_mid_change,
                        "ofiScore": _ofi(previous, current, "UP")
                        - _ofi(previous, current, "DOWN"),
                    }
                up_mid = _mid(current, "UP")
                down_mid = _mid(current, "DOWN")
                probability = up_mid / (up_mid + down_mid)
                features.append(
                    {
                        "marketId": market_id,
                        "split": split_by_market[market_id],
                        "winner": winner,
                        "timestamp": str(current["timestamp"]),
                        "horizon": horizon,
                        "row": current,
                        "marketProbability": probability,
                        "micropriceScore": _microprice_score(current),
                        "lags": lagged,
                    }
                )

        current_market: int | None = None
        current_rows: list[sqlite3.Row] = []
        for row in db.execute(
            """SELECT o.*, s.official_winner
                 FROM observations AS o
                 JOIN market_settlements AS s ON s.market_id=o.market_id
                WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
                ORDER BY o.id"""
        ):
            market_id = int(row["market_id"])
            if current_market is not None and market_id != current_market:
                process_market(current_rows)
                current_rows = []
            current_market = market_id
            current_rows.append(row)
        process_market(current_rows)
        return features, {
            "firstObservation": snapshot["first_timestamp"],
            "lastObservation": snapshot["last_timestamp"],
            "observationRows": int(snapshot["observation_rows"]),
            "officialMarkets": len(market_ids),
            "allOfficialMarkets": len(all_market_ids),
            "marketUniverse": "official markets with synchronous spot/futures microstructure coverage",
            "splitMarkets": {
                "development": development_end,
                "validation": validation_end - development_end,
                "holdout": len(market_ids) - validation_end,
            },
        }
    finally:
        db.close()


def _execute(
    feature: dict[str, Any], strategy: str, side: str, signal: float, max_ask: float
) -> Trade | None:
    row = feature["row"]
    prefix = side.lower()
    raw_ask = float(row[f"{prefix}_ask"])
    bid = float(row[f"{prefix}_bid"])
    if not (MIN_ENTRY <= raw_ask <= max_ask and raw_ask - bid <= MAX_SPREAD):
        return None
    entry_price = raw_ask * (1 + SLIPPAGE_BPS / 10_000)
    if entry_price >= 1:
        return None
    shares = STAKE / entry_price
    if float(row[f"{prefix}_ask_size"]) + 1e-12 < shares:
        return None
    fee = taker_fee(shares, entry_price, FEE_BPS)
    pnl = (shares if side == feature["winner"] else 0.0) - STAKE - fee
    return Trade(
        strategy=strategy,
        split=str(feature["split"]),
        market_id=int(feature["marketId"]),
        timestamp=str(feature["timestamp"]),
        horizon_seconds=int(feature["horizon"]),
        side=side,
        winner=str(feature["winner"]),
        raw_ask=raw_ask,
        entry_price=entry_price,
        shares=shares,
        fee=fee,
        pnl=pnl,
        signal=signal,
    )


def _summarize(trades: list[Trade], candidate_markets: int) -> dict[str, Any]:
    wins = sum(trade.pnl > 0 for trade in trades)
    pnl = sum(trade.pnl for trade in trades)
    cost = sum(STAKE + trade.fee for trade in trades)
    gross_profit = sum(max(0.0, trade.pnl) for trade in trades)
    gross_loss = sum(max(0.0, -trade.pnl) for trade in trades)
    equity = peak = max_drawdown = 0.0
    streak = max_streak = 0
    for trade in trades:
        equity += trade.pnl
        peak = max(peak, equity)
        max_drawdown = max(max_drawdown, peak - equity)
        if trade.pnl <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    return {
        "candidateMarkets": candidate_markets,
        "trades": len(trades),
        "coverageRate": len(trades) / candidate_markets if candidate_markets else None,
        "wins": wins,
        "losses": len(trades) - wins,
        "winRate": wins / len(trades) if trades else None,
        "realizedPnl": pnl,
        "roi": pnl / cost if cost else None,
        "profitFactor": gross_profit / gross_loss if gross_loss else None,
        "maxDrawdown": max_drawdown,
        "maxConsecutiveLosses": max_streak,
        "averageEntryPrice": (
            sum(trade.entry_price for trade in trades) / len(trades) if trades else None
        ),
    }


def _fit_logistic(features: Iterable[dict[str, Any]]) -> tuple[float, float]:
    rows = list(features)
    x = np.array(
        [
            [
                1.0,
                math.log(
                    min(1 - 1e-6, max(1e-6, row["marketProbability"]))
                    / (1 - min(1 - 1e-6, max(1e-6, row["marketProbability"])))
                ),
            ]
            for row in rows
        ],
        dtype=float,
    )
    y = np.array([row["winner"] == "UP" for row in rows], dtype=float)
    beta = np.array([0.0, 1.0], dtype=float)
    ridge = np.diag([0.05, 0.25])
    for _ in range(50):
        z = np.clip(x @ beta, -30, 30)
        p = 1 / (1 + np.exp(-z))
        weights = np.maximum(p * (1 - p), 1e-8)
        gradient = x.T @ (y - p) - ridge @ beta
        hessian = (x.T * weights) @ x + ridge
        step = np.linalg.solve(hessian, gradient)
        beta += step
        if float(np.max(np.abs(step))) < 1e-9:
            break
    return float(beta[0]), float(beta[1])


def _calibrated_probability(feature: dict[str, Any], beta: tuple[float, float]) -> float:
    probability = min(1 - 1e-6, max(1e-6, feature["marketProbability"]))
    logit = math.log(probability / (1 - probability))
    z = max(-30.0, min(30.0, beta[0] + beta[1] * logit))
    return 1 / (1 + math.exp(-z))


def _trade_microprice(feature: dict[str, Any], config: dict[str, Any]) -> Trade | None:
    score = float(feature["micropriceScore"])
    if abs(score) < config["threshold"]:
        return None
    return _execute(
        feature,
        "MICROPRICE",
        "UP" if score > 0 else "DOWN",
        score,
        config["maxAsk"],
    )


def _trade_ofi(feature: dict[str, Any], config: dict[str, Any]) -> Trade | None:
    lag = feature["lags"].get(config["lag"])
    if lag is None:
        return None
    score = float(lag["ofiScore"])
    if abs(score) < config["threshold"]:
        return None
    return _execute(
        feature, "OFI", "UP" if score > 0 else "DOWN", score, config["maxAsk"]
    )


def _trade_futures(feature: dict[str, Any], config: dict[str, Any]) -> Trade | None:
    lag = feature["lags"].get(config["lag"])
    if lag is None or lag["futuresReturn"] is None:
        return None
    futures_return = float(lag["futuresReturn"])
    spot_return = float(lag["spotReturn"])
    lead = abs(futures_return) - abs(spot_return)
    if abs(futures_return) < 1e-12 or lead * 10_000 < config["minLeadBps"]:
        return None
    return _execute(
        feature,
        "FUTURES_LEAD",
        "UP" if futures_return > 0 else "DOWN",
        math.copysign(lead * 10_000, futures_return),
        config["maxAsk"],
    )


def _trade_calibrated(
    feature: dict[str, Any], config: dict[str, Any]
) -> Trade | None:
    probability = _calibrated_probability(feature, tuple(config["beta"]))
    row = feature["row"]
    choices = []
    for side, chance in (("UP", probability), ("DOWN", 1 - probability)):
        raw_ask = float(row[f"{side.lower()}_ask"])
        entry = raw_ask * (1 + SLIPPAGE_BPS / 10_000)
        per_share_fee = taker_fee(1.0, entry, FEE_BPS)
        choices.append((chance - entry - per_share_fee, side))
    edge, side = max(choices)
    if edge < config["minEdge"]:
        return None
    return _execute(feature, "CALIBRATED_VALUE", side, edge, config["maxAsk"])


def _trade_consensus(feature: dict[str, Any], config: dict[str, Any]) -> Trade | None:
    lag = feature["lags"].get(config["lag"])
    if lag is None or lag["futuresReturn"] is None:
        return None
    signals = [
        float(feature["micropriceScore"]),
        float(lag["ofiScore"]),
        float(lag["futuresReturn"]),
        float(lag["spotReturn"]),
        float(lag["upMidChange"]),
    ]
    up_votes = sum(value > 0 for value in signals)
    down_votes = sum(value < 0 for value in signals)
    votes = max(up_votes, down_votes)
    if votes < config["votes"]:
        return None
    side = "UP" if up_votes > down_votes else "DOWN"
    return _execute(
        feature,
        "CONSENSUS",
        side,
        float(up_votes - down_votes),
        config["maxAsk"],
    )


def _configs_by_family(features: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    configs: dict[str, list[dict[str, Any]]] = {
        "MICROPRICE": [
            {"horizon": horizon, "threshold": threshold, "maxAsk": max_ask}
            for horizon in HORIZONS
            for threshold in (0.10, 0.20, 0.35, 0.50)
            for max_ask in (0.55, 0.70, 0.85)
        ],
        "OFI": [
            {
                "horizon": horizon,
                "lag": lag,
                "threshold": threshold,
                "maxAsk": max_ask,
            }
            for horizon in HORIZONS
            for lag in (5, 10, 20)
            for threshold in (0.03, 0.06, 0.10, 0.20)
            for max_ask in (0.55, 0.70, 0.85)
        ],
        "FUTURES_LEAD": [
            {
                "horizon": horizon,
                "lag": lag,
                "minLeadBps": lead,
                "maxAsk": max_ask,
            }
            for horizon in HORIZONS
            for lag in (3, 5, 10)
            for lead in (0.25, 0.50, 1.00, 2.00)
            for max_ask in (0.55, 0.70, 0.85)
        ],
        "CONSENSUS": [
            {"horizon": horizon, "lag": lag, "votes": votes, "maxAsk": max_ask}
            for horizon in HORIZONS
            for lag in (5, 10, 20)
            for votes in (4, 5)
            for max_ask in (0.55, 0.70, 0.85)
        ],
    }
    calibrated = []
    for horizon in HORIZONS:
        development = [
            feature
            for feature in features
            if feature["split"] == "development" and feature["horizon"] == horizon
        ]
        beta = _fit_logistic(development)
        for edge in (0.01, 0.02, 0.03, 0.05, 0.08):
            for max_ask in (0.55, 0.70, 0.85):
                calibrated.append(
                    {
                        "horizon": horizon,
                        "minEdge": edge,
                        "maxAsk": max_ask,
                        "beta": list(beta),
                    }
                )
    configs["CALIBRATED_VALUE"] = calibrated
    return configs


TRADERS: dict[str, Callable[[dict[str, Any], dict[str, Any]], Trade | None]] = {
    "MICROPRICE": _trade_microprice,
    "OFI": _trade_ofi,
    "FUTURES_LEAD": _trade_futures,
    "CALIBRATED_VALUE": _trade_calibrated,
    "CONSENSUS": _trade_consensus,
}


def _run_config(
    family: str, config: dict[str, Any], features: list[dict[str, Any]]
) -> list[Trade]:
    trader = TRADERS[family]
    trades = []
    for feature in features:
        if feature["horizon"] != config["horizon"]:
            continue
        trade = trader(feature, config)
        if trade is not None:
            trades.append(trade)
    return trades


def _select_config(
    family: str,
    configs: list[dict[str, Any]],
    features: list[dict[str, Any]],
    development_markets: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    ranking = []
    for config in configs:
        trades = [
            trade
            for trade in _run_config(family, config, features)
            if trade.split == "development"
        ]
        metrics = _summarize(trades, development_markets)
        enough = metrics["trades"] >= 60
        stable_return = (
            metrics["realizedPnl"] / metrics["maxDrawdown"]
            if metrics["maxDrawdown"] > 0
            else -math.inf
        )
        rank_key = (
            enough,
            metrics["realizedPnl"] > 0,
            stable_return,
            metrics["realizedPnl"],
            -metrics["maxConsecutiveLosses"],
        )
        ranking.append((rank_key, config, metrics))
    ranking.sort(key=lambda item: item[0], reverse=True)
    selected = ranking[0]
    top = [
        {"config": config, "development": metrics}
        for _, config, metrics in ranking[:10]
    ]
    return selected[1], top


def run_backtest(database: Path, microstructure_database: Path) -> dict[str, Any]:
    features, snapshot = _extract_features(database, microstructure_database)
    configs = _configs_by_family(features)
    summaries: dict[str, Any] = {}
    selected_trades: list[Trade] = []
    for family in (
        "MICROPRICE",
        "OFI",
        "FUTURES_LEAD",
        "CALIBRATED_VALUE",
        "CONSENSUS",
    ):
        selected, top = _select_config(
            family,
            configs[family],
            features,
            snapshot["splitMarkets"]["development"],
        )
        trades = _run_config(family, selected, features)
        selected_trades.extend(trades)
        summaries[family] = {
            "source": SOURCES[family],
            "selectedConfig": selected,
            "selectionRule": "development only; >=60 trades preferred; positive PnL then PnL/maxDrawdown",
            "validationAndHoldoutExcludedFromSelection": True,
            "overall": _summarize(trades, snapshot["officialMarkets"]),
            "splits": {
                split: _summarize(
                    [trade for trade in trades if trade.split == split],
                    snapshot["splitMarkets"][split],
                )
                for split in ("development", "validation", "holdout")
            },
            "developmentTop10": top,
        }
        sensitivity_configs = []
        for candidate_config in configs[family]:
            candidate_trades = _run_config(family, candidate_config, features)
            candidate_splits = {
                split: _summarize(
                    [trade for trade in candidate_trades if trade.split == split],
                    snapshot["splitMarkets"][split],
                )
                for split in ("development", "validation", "holdout")
            }
            if (
                candidate_splits["development"]["trades"] >= 40
                and candidate_splits["development"]["realizedPnl"] > 0
            ):
                sensitivity_configs.append(
                    {
                        "config": candidate_config,
                        "splits": candidate_splits,
                        "positiveValidationAndHoldout": (
                            candidate_splits["validation"]["realizedPnl"] > 0
                            and candidate_splits["holdout"]["realizedPnl"] > 0
                        ),
                    }
                )
        summaries[family]["parameterSensitivity"] = {
            "rule": "development trades >=40 and development PnL >0; validation/holdout used only as robustness audit",
            "developmentPositiveConfigs": len(sensitivity_configs),
            "positiveValidationAndHoldoutConfigs": sum(
                item["positiveValidationAndHoldout"] for item in sensitivity_configs
            ),
            "passingConfigs": [
                item for item in sensitivity_configs
                if item["positiveValidationAndHoldout"]
            ],
        }
    eligible = [
        family
        for family, value in summaries.items()
        if value["overall"]["trades"] >= 100
        and value["splits"]["validation"]["realizedPnl"] > 0
        and value["splits"]["holdout"]["realizedPnl"] > 0
        and value["splits"]["validation"]["profitFactor"] is not None
        and value["splits"]["validation"]["profitFactor"] > 1
        and value["splits"]["holdout"]["profitFactor"] is not None
        and value["splits"]["holdout"]["profitFactor"] > 1
    ]
    selected_candidate = min(
        eligible,
        key=lambda family: (
            summaries[family]["overall"]["maxDrawdown"],
            summaries[family]["overall"]["maxConsecutiveLosses"],
        ),
        default=None,
    )
    selected_daily: dict[str, dict[str, Any]] = {}
    if selected_candidate:
        taipei = timezone(timedelta(hours=8))
        for trade in selected_trades:
            if trade.strategy != selected_candidate:
                continue
            day = datetime.fromisoformat(
                trade.timestamp.replace("Z", "+00:00")
            ).astimezone(taipei).date().isoformat()
            values = selected_daily.setdefault(
                day, {"trades": 0, "wins": 0, "realizedPnl": 0.0}
            )
            values["trades"] += 1
            values["wins"] += int(trade.pnl > 0)
            values["realizedPnl"] += trade.pnl
        for values in selected_daily.values():
            values["winRate"] = values["wins"] / values["trades"]
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "sourceDatabase": str(database.resolve()),
        "microstructureDatabase": str(microstructure_database.resolve()),
        "dataSnapshot": snapshot,
        "splitPolicy": "official markets chronological 60/20/20",
        "executionModel": {
            "singleLegOnly": True,
            "stake": STAKE,
            "feeBps": FEE_BPS,
            "slippageBps": SLIPPAGE_BPS,
            "fullTopOfBookDepthRequired": True,
            "maxSpread": MAX_SPREAD,
            "maxBookAgeMs": MAX_BOOK_AGE_MS,
            "maxBookSkewMs": MAX_BOOK_SKEW_MS,
            "exit": "official settlement",
            "queuePositionModeled": False,
        },
        "summaries": summaries,
        "selection": {
            "eligible": eligible,
            "selectedCandidate": selected_candidate,
            "rule": "positive validation and holdout PnL and profit factor, >=100 overall trades; lowest drawdown then losing streak",
            "selectedDailyTaipei": selected_daily,
        },
        "trades": [asdict(trade) for trade in selected_trades],
    }


def _percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def render_markdown(report: dict[str, Any]) -> str:
    snapshot = report["dataSnapshot"]
    lines = [
        "# 五種全新單腿策略回測",
        "",
        f"資料截止（UTC）：`{snapshot['lastObservation']}`；"
        f"`{snapshot['observationRows']:,}` 筆觀察、`{snapshot['allOfficialMarkets']:,}` 個正式結算市場。",
        f"因 futures 微結構從較晚才開始保存，公平共同回測宇宙為其中 "
        f"`{snapshot['officialMarkets']:,}` 個同時有 spot／futures 微結構資料的市場。",
        "",
        "五種策略均為本輪新研究規則，不呼叫既有 F1/F2/K/M7 等進場邏輯。每筆固定 10 USDT，"
        "含 200 bps fee 與 50 bps 逆向滑價，要求第一檔完整深度，持有至正式結算。",
        "",
        "| 新策略 | 交易 | 勝率 | 收益 USDT | ROI | 最大回撤 | 最大連敗 | Val 收益 | Holdout 收益 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for family, value in report["summaries"].items():
        overall = value["overall"]
        splits = value["splits"]
        lines.append(
            f"| {family} | {overall['trades']} | {_percent(overall['winRate'])} | "
            f"{overall['realizedPnl']:.2f} | {_percent(overall['roi'])} | "
            f"{overall['maxDrawdown']:.2f} | {overall['maxConsecutiveLosses']} | "
            f"{splits['validation']['realizedPnl']:.2f} | {splits['holdout']['realizedPnl']:.2f} |"
        )
    lines.extend(["", "## Development 凍結參數", ""])
    for family, value in report["summaries"].items():
        lines.append(f"- `{family}`：`{json.dumps(value['selectedConfig'], ensure_ascii=False)}`")
    selected = report["selection"]["selectedCandidate"]
    if selected:
        sensitivity = report["summaries"][selected]["parameterSensitivity"]
        lines.extend(
            [
                "",
                "## 參數敏感度",
                "",
                f"`{selected}` 有 `{sensitivity['developmentPositiveConfigs']}` 組 development 正收益鄰近設定，"
                f"其中 `{sensitivity['positiveValidationAndHoldoutConfigs']}` 組在 validation 與 holdout 也同為正。",
                "",
                "台北日別收益："
                + "；".join(
                    f"`{day}` {values['realizedPnl']:.2f} USDT"
                    for day, values in report["selection"]["selectedDailyTaipei"].items()
                )
                + "。",
            ]
        )
    lines.extend(
        [
            "",
            "## 判定",
            "",
            (
                f"`{selected}` 通過 validation 與 holdout 正收益門檻，只能作 frozen forward-paper 候選。"
                if selected
                else "沒有新策略同時通過 validation 與 holdout 正收益門檻；本輪不應升級任何策略。"
            ),
            "",
            "這是 top-of-book paper replay；未模擬真實排隊順位、下單延遲期間撤單或多檔衝擊。",
            "",
        ]
    )
    return "\n".join(lines)


def write_report(report: dict[str, Any], output_dir: Path) -> tuple[Path, Path, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"new-five-strategy-{stamp}.json"
    markdown_path = output_dir / f"new-five-strategy-{stamp}.md"
    csv_path = output_dir / f"new-five-strategy-{stamp}-trades.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    trades = report["trades"]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(Trade.__dataclass_fields__))
        writer.writeheader()
        writer.writerows(trades)
    return json_path, markdown_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/simulation.db"))
    parser.add_argument(
        "--microstructure-database",
        type=Path,
        default=Path("data/microstructure.db"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("data/backtests"))
    args = parser.parse_args()
    report = run_backtest(args.database, args.microstructure_database)
    paths = write_report(report, args.output_dir)
    print(json.dumps({
        "selection": report["selection"],
        "summaries": {
            family: {
                "config": value["selectedConfig"],
                "overall": value["overall"],
                "validation": value["splits"]["validation"],
                "holdout": value["splits"]["holdout"],
            }
            for family, value in report["summaries"].items()
        },
    }, ensure_ascii=False, indent=2))
    print(f"JSON: {paths[0]}")
    print(f"Markdown: {paths[1]}")
    print(f"CSV: {paths[2]}")


if __name__ == "__main__":
    main()
