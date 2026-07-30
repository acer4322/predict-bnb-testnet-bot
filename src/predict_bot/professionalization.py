from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import NormalDist
from typing import Any, Iterable, Sequence

from .core import taker_fee


@dataclass(frozen=True)
class CompositeDefinition:
    strategy: str
    observer_version: str
    replay_version: str
    drawdown_enabled: bool
    two_loss_cooldown: bool

    @property
    def composite_id(self) -> str:
        suffix = "+2L1" if self.two_loss_cooldown else ""
        return f"{self.strategy}+{self.replay_version}{suffix}"


FROZEN_COMPOSITES: tuple[CompositeDefinition, ...] = (
    CompositeDefinition("R_FUTURES_LEAD", "V2", "V2", False, False),
    CompositeDefinition(
        "R_CALIBRATED_VALUE", "V6", "V6+DD20", True, False
    ),
    CompositeDefinition("R_MICROPRICE", "V6", "V6+DD20", True, True),
)
COMPOSITE_BY_STRATEGY = {
    definition.strategy: definition for definition in FROZEN_COMPOSITES
}

CALIBRATION_STRATEGY = "R_CALIBRATED_VALUE"
CALIBRATION_MIN_BIN_SAMPLES = 20
CALIBRATION_Z = 1.959963984540054
CALIBRATED_VALUE_MIN_EDGE = 0.01
DEFAULT_FEE_BPS = 200
DEFAULT_SLIPPAGE_BPS = 50.0
HORIZON_SECONDS = (3, 10, 30, 60, 120)


def utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stable_hash(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.resolve()
    connection = sqlite3.connect(
        f"file:{resolved.as_posix()}?mode=ro", uri=True
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    return connection


def selected_composite_trades(report: dict[str, Any]) -> list[dict[str, Any]]:
    selected = [
        trade
        for trade in report.get("trades", [])
        if (
            str(trade.get("strategy")) in COMPOSITE_BY_STRATEGY
            and str(trade.get("observer_version"))
            == COMPOSITE_BY_STRATEGY[str(trade["strategy"])].replay_version
        )
    ]
    selected = sorted(
        selected,
        key=lambda trade: (
            int(trade["observation_id"]),
            str(trade["strategy"]),
        ),
    )
    kept, _ = apply_two_loss_skip_one(selected)
    return kept


def apply_two_loss_skip_one(
    trades: Sequence[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply the frozen MP cooldown causally without changing other composites."""

    ordered = sorted(
        trades,
        key=lambda trade: (
            int(trade["observation_id"]),
            str(trade["strategy"]),
        ),
    )
    consecutive_losses = 0
    kept: list[dict[str, Any]] = []
    blocked: list[dict[str, Any]] = []
    for trade in ordered:
        if str(trade["strategy"]) != "R_MICROPRICE":
            kept.append(trade)
            continue
        if consecutive_losses >= 2:
            blocked.append(trade)
            consecutive_losses = 0
            continue
        kept.append(trade)
        if float(trade["pnl"]) < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0
    return kept, blocked


def infer_selected_side_probability(trade: dict[str, Any]) -> float | None:
    if str(trade.get("strategy")) != CALIBRATION_STRATEGY:
        return None
    try:
        shares = float(trade["shares"])
        fee_per_share = float(trade["fee"]) / shares
        probability = (
            float(trade["signal"])
            + float(trade["entry_price"])
            + fee_per_share
        )
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return None
    if not math.isfinite(probability):
        return None
    return min(1 - 1e-12, max(1e-12, probability))


def wilson_interval(
    wins: int, count: int, z: float = CALIBRATION_Z
) -> tuple[float | None, float | None]:
    if count <= 0:
        return None, None
    probability = wins / count
    denominator = 1 + z * z / count
    center = (probability + z * z / (2 * count)) / denominator
    half_width = (
        z
        * math.sqrt(
            probability * (1 - probability) / count
            + z * z / (4 * count * count)
        )
        / denominator
    )
    return max(0.0, center - half_width), min(1.0, center + half_width)


def calibration_report(
    records: Sequence[dict[str, Any]], *, bins: int = 10
) -> dict[str, Any]:
    usable = []
    for record in records:
        probability = record.get("model_probability")
        outcome = record.get("outcome")
        try:
            probability = float(probability)
            outcome = int(outcome)
        except (TypeError, ValueError):
            continue
        if not (0 < probability < 1 and outcome in (0, 1)):
            continue
        usable.append((probability, outcome))
    if not usable:
        return {
            "status": "UNAVAILABLE",
            "reason": "strategy does not emit a recoverable probability",
            "samples": 0,
            "bins": [],
        }

    total = len(usable)
    brier = sum((probability - outcome) ** 2 for probability, outcome in usable) / total
    log_loss = -sum(
        outcome * math.log(probability)
        + (1 - outcome) * math.log(1 - probability)
        for probability, outcome in usable
    ) / total
    grouped: dict[int, list[tuple[float, int]]] = defaultdict(list)
    for probability, outcome in usable:
        index = min(bins - 1, int(probability * bins))
        grouped[index].append((probability, outcome))
    bucket_rows = []
    expected_calibration_error = 0.0
    maximum_calibration_error = 0.0
    for index in range(bins):
        values = grouped.get(index, [])
        if not values:
            continue
        count = len(values)
        wins = sum(outcome for _, outcome in values)
        predicted = sum(probability for probability, _ in values) / count
        actual = wins / count
        error = abs(predicted - actual)
        lower, upper = wilson_interval(wins, count)
        expected_calibration_error += error * count / total
        maximum_calibration_error = max(maximum_calibration_error, error)
        bucket_rows.append({
            "bucket": index,
            "lowerProbability": index / bins,
            "upperProbability": (index + 1) / bins,
            "samples": count,
            "predictedMean": predicted,
            "actualWinRate": actual,
            "wilsonLower95": lower,
            "wilsonUpper95": upper,
            "absoluteCalibrationError": error,
            "enoughForConservativeGate": count >= CALIBRATION_MIN_BIN_SAMPLES,
        })
    return {
        "status": "READY",
        "samples": total,
        "wins": sum(outcome for _, outcome in usable),
        "brierScore": brier,
        "logLoss": log_loss,
        "expectedCalibrationError": expected_calibration_error,
        "maximumCalibrationError": maximum_calibration_error,
        "bins": bucket_rows,
    }


def trade_probability_records(
    trades: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    records = []
    for trade in trades:
        probability = infer_selected_side_probability(trade)
        if probability is None:
            continue
        records.append({
            "strategy": trade["strategy"],
            "composite_id": COMPOSITE_BY_STRATEGY[
                str(trade["strategy"])
            ].composite_id,
            "market_id": int(trade["market_id"]),
            "observation_id": int(trade["observation_id"]),
            "timestamp": str(trade["timestamp"]),
            "split": str(trade["split"]),
            "side": str(trade["side"]),
            "model_probability": probability,
            "quoted_ask_probability": float(trade["raw_ask"]),
            "effective_entry_probability": float(trade["entry_price"])
            + float(trade["fee"]) / float(trade["shares"]),
            "model_edge": float(trade["signal"]),
            "outcome": int(str(trade["side"]) == str(trade["winner"])),
        })
    return records


class ProfessionalizationLedger:
    """Research-only ledger; never point this class at a production database."""

    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self._initialize()

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> "ProfessionalizationLedger":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _initialize(self) -> None:
        self.db.executescript(
            """
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS experiments(
                experiment_id TEXT PRIMARY KEY,
                created_at TEXT NOT NULL,
                dataset_sha256 TEXT NOT NULL,
                config_sha256 TEXT NOT NULL,
                config_json TEXT NOT NULL,
                composite_json TEXT NOT NULL,
                selection_policy TEXT NOT NULL,
                prior_holdout_reused INTEGER NOT NULL CHECK(prior_holdout_reused IN (0,1)),
                promotion_status TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS probability_forecasts(
                experiment_id TEXT NOT NULL,
                composite_id TEXT NOT NULL,
                strategy TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                split TEXT NOT NULL,
                side TEXT NOT NULL,
                model_probability REAL NOT NULL,
                quoted_ask_probability REAL NOT NULL,
                effective_entry_probability REAL NOT NULL,
                model_edge REAL NOT NULL,
                outcome INTEGER NOT NULL CHECK(outcome IN (0,1)),
                PRIMARY KEY(experiment_id, composite_id, market_id, observation_id)
            );
            CREATE TABLE IF NOT EXISTS execution_audits(
                experiment_id TEXT NOT NULL,
                composite_id TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                split TEXT NOT NULL,
                scenario TEXT NOT NULL,
                fill_ratio REAL NOT NULL,
                stressed_entry REAL,
                stressed_pnl REAL,
                rejected_reason TEXT,
                PRIMARY KEY(experiment_id, composite_id, market_id, observation_id, scenario)
            );
            CREATE TABLE IF NOT EXISTS horizon_markouts(
                experiment_id TEXT NOT NULL,
                composite_id TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                split TEXT NOT NULL,
                horizon_seconds INTEGER NOT NULL,
                observed_at TEXT,
                bid REAL,
                bid_size REAL,
                full_depth INTEGER,
                exit_pnl REAL,
                unavailable_reason TEXT,
                PRIMARY KEY(experiment_id, composite_id, market_id, observation_id, horizon_seconds)
            );
            CREATE TABLE IF NOT EXISTS blocked_counterfactuals(
                experiment_id TEXT NOT NULL,
                composite_id TEXT NOT NULL,
                market_id INTEGER NOT NULL,
                observation_id INTEGER NOT NULL,
                split TEXT NOT NULL,
                blocker TEXT NOT NULL,
                counterfactual_result TEXT NOT NULL,
                counterfactual_pnl REAL NOT NULL,
                PRIMARY KEY(experiment_id, composite_id, market_id, observation_id, blocker)
            );
            CREATE TABLE IF NOT EXISTS platform_risks(
                experiment_id TEXT NOT NULL,
                risk_id TEXT NOT NULL,
                component TEXT NOT NULL,
                failure_mode TEXT NOT NULL,
                evidence_available INTEGER NOT NULL CHECK(evidence_available IN (0,1)),
                current_control TEXT NOT NULL,
                gap TEXT NOT NULL,
                PRIMARY KEY(experiment_id, risk_id)
            );
            """
        )
        self.db.commit()

    def register_experiment(
        self,
        *,
        dataset_sha256: str,
        config: dict[str, Any],
        prior_holdout_reused: bool,
    ) -> str:
        config_sha256 = stable_hash(config)
        composites = [asdict(value) for value in FROZEN_COMPOSITES]
        experiment_id = stable_hash({
            "dataset": dataset_sha256,
            "config": config_sha256,
            "composites": composites,
        })[:24]
        self.db.execute(
            """INSERT OR IGNORE INTO experiments(
                   experiment_id, created_at, dataset_sha256, config_sha256,
                   config_json, composite_json, selection_policy,
                   prior_holdout_reused, promotion_status
               ) VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                experiment_id,
                utc_iso(),
                dataset_sha256,
                config_sha256,
                json.dumps(config, sort_keys=True),
                json.dumps(composites, sort_keys=True),
                "development-only selection; later splits are audit only",
                int(prior_holdout_reused),
                "RESEARCH_ONLY_NEEDS_NEW_FORWARD"
                if prior_holdout_reused
                else "RESEARCH_ONLY",
            ),
        )
        self.db.commit()
        return experiment_id

    def insert_forecasts(
        self, experiment_id: str, records: Iterable[dict[str, Any]]
    ) -> int:
        before = self.db.total_changes
        self.db.executemany(
            """INSERT OR REPLACE INTO probability_forecasts VALUES(
                   :experiment_id, :composite_id, :strategy, :market_id,
                   :observation_id, :timestamp, :split, :side,
                   :model_probability, :quoted_ask_probability,
                   :effective_entry_probability, :model_edge, :outcome
               )""",
            ({**record, "experiment_id": experiment_id} for record in records),
        )
        self.db.commit()
        return self.db.total_changes - before

    def insert_execution_audits(
        self, experiment_id: str, records: Iterable[dict[str, Any]]
    ) -> int:
        before = self.db.total_changes
        self.db.executemany(
            """INSERT OR REPLACE INTO execution_audits VALUES(
                   :experiment_id, :composite_id, :market_id, :observation_id,
                   :split, :scenario, :fill_ratio, :stressed_entry,
                   :stressed_pnl, :rejected_reason
               )""",
            ({**record, "experiment_id": experiment_id} for record in records),
        )
        self.db.commit()
        return self.db.total_changes - before

    def insert_markouts(
        self, experiment_id: str, records: Iterable[dict[str, Any]]
    ) -> int:
        before = self.db.total_changes
        self.db.executemany(
            """INSERT OR REPLACE INTO horizon_markouts VALUES(
                   :experiment_id, :composite_id, :market_id, :observation_id,
                   :split, :horizon_seconds, :observed_at, :bid, :bid_size,
                   :full_depth, :exit_pnl, :unavailable_reason
               )""",
            ({**record, "experiment_id": experiment_id} for record in records),
        )
        self.db.commit()
        return self.db.total_changes - before

    def insert_counterfactuals(
        self, experiment_id: str, records: Iterable[dict[str, Any]]
    ) -> int:
        before = self.db.total_changes
        self.db.executemany(
            """INSERT OR REPLACE INTO blocked_counterfactuals VALUES(
                   :experiment_id, :composite_id, :market_id, :observation_id,
                   :split, :blocker, :counterfactual_result,
                   :counterfactual_pnl
               )""",
            ({**record, "experiment_id": experiment_id} for record in records),
        )
        self.db.commit()
        return self.db.total_changes - before

    def insert_platform_risks(
        self, experiment_id: str, records: Iterable[dict[str, Any]]
    ) -> int:
        before = self.db.total_changes
        self.db.executemany(
            """INSERT OR REPLACE INTO platform_risks VALUES(
                   :experiment_id, :risk_id, :component, :failure_mode,
                   :evidence_available, :current_control, :gap
               )""",
            ({**record, "experiment_id": experiment_id} for record in records),
        )
        self.db.commit()
        return self.db.total_changes - before

    def counts(self, experiment_id: str) -> dict[str, int]:
        tables = (
            "probability_forecasts",
            "execution_audits",
            "horizon_markouts",
            "blocked_counterfactuals",
            "platform_risks",
        )
        return {
            table: int(
                self.db.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE experiment_id=?",
                    (experiment_id,),
                ).fetchone()[0]
            )
            for table in tables
        }


def data_coverage_audit(
    simulation_db: Path, *, end_at: str | None = None
) -> dict[str, Any]:
    observation_where = "WHERE timestamp <= ?" if end_at else ""
    settlement_where = (
        "WHERE official_winner IN ('UP','DOWN') AND official_settled_at <= ?"
        if end_at
        else "WHERE official_winner IN ('UP','DOWN')"
    )
    parameters = (end_at,) if end_at else ()
    with connect_read_only(simulation_db) as connection:
        observation = connection.execute(
            f"""SELECT MIN(timestamp) AS first_at,
                      MAX(timestamp) AS last_at,
                      COUNT(*) AS rows,
                      COUNT(DISTINCT market_id) AS markets,
                      SUM(CASE WHEN book_age_ms IS NULL THEN 1 ELSE 0 END) AS missing_age,
                      SUM(CASE WHEN book_skew_ms IS NULL THEN 1 ELSE 0 END) AS missing_skew,
                      SUM(CASE WHEN book_age_ms > 1000 THEN 1 ELSE 0 END) AS stale_rows,
                      SUM(CASE WHEN book_skew_ms > 500 THEN 1 ELSE 0 END) AS skewed_rows,
                      SUM(CASE WHEN up_ask-up_bid > .05 OR down_ask-down_bid > .05
                               THEN 1 ELSE 0 END) AS wide_spread_rows
                 FROM observations
                 {observation_where}""",
            parameters,
        ).fetchone()
        settlement = connection.execute(
            f"""SELECT COUNT(*) AS official_markets,
                      MIN(official_settled_at) AS first_settlement,
                      MAX(official_settled_at) AS last_settlement
                 FROM market_settlements
                {settlement_where}""",
            parameters,
        ).fetchone()
    first_at = parse_timestamp(str(observation["first_at"]))
    last_at = parse_timestamp(str(observation["last_at"]))
    observed_days = (last_at - first_at).total_seconds() / 86_400
    rows = int(observation["rows"] or 0)
    return {
        "status": (
            "READY_LONG_HORIZON"
            if observed_days >= 30
            else "INSUFFICIENT_LONG_HORIZON_DATA"
        ),
        "minimumRequiredDays": 30,
        "preferredDays": 90,
        "observedDays": observed_days,
        "firstObservation": observation["first_at"],
        "lastObservation": observation["last_at"],
        "fixedEndInclusive": end_at,
        "observationRows": rows,
        "distinctMarkets": int(observation["markets"] or 0),
        "officialMarkets": int(settlement["official_markets"] or 0),
        "stressCoverage": {
            "missingBookAgeRows": int(observation["missing_age"] or 0),
            "missingBookSkewRows": int(observation["missing_skew"] or 0),
            "bookAgeOver1000msRows": int(observation["stale_rows"] or 0),
            "bookSkewOver500msRows": int(observation["skewed_rows"] or 0),
            "spreadOver005Rows": int(observation["wide_spread_rows"] or 0),
            "bookAgeOver1000msRate": (
                int(observation["stale_rows"] or 0) / rows if rows else None
            ),
            "bookSkewOver500msRate": (
                int(observation["skewed_rows"] or 0) / rows if rows else None
            ),
        },
    }


def _stress_scenarios() -> tuple[dict[str, Any], ...]:
    return (
        {"name": "recorded_50bps", "slippage_bps": 50, "depth_haircut": 1.0, "latency_ms": 0},
        {"name": "slippage_100bps", "slippage_bps": 100, "depth_haircut": 1.0, "latency_ms": 250},
        {"name": "slippage_200bps", "slippage_bps": 200, "depth_haircut": 1.0, "latency_ms": 500},
        {"name": "depth_half", "slippage_bps": 100, "depth_haircut": 0.5, "latency_ms": 250},
        {"name": "depth_quarter_stale", "slippage_bps": 200, "depth_haircut": 0.25, "latency_ms": 750},
    )


def execution_stress_records(
    trades: Sequence[dict[str, Any]], fee_bps: int = DEFAULT_FEE_BPS
) -> list[dict[str, Any]]:
    records = []
    for trade in trades:
        strategy = str(trade["strategy"])
        composite_id = COMPOSITE_BY_STRATEGY[strategy].composite_id
        raw_ask = float(trade["raw_ask"])
        # Preserve the recorded order quantity. Recomputing it from raw ask would
        # make the baseline order larger than the recorded slippage-aware order.
        requested_shares = float(trade["shares"])
        for scenario in _stress_scenarios():
            entry = raw_ask * (1 + scenario["slippage_bps"] / 10_000)
            available = float(trade["visible_ask_size"]) * scenario["depth_haircut"]
            fill_shares = min(requested_shares, max(0.0, available))
            fill_ratio = fill_shares / requested_shares if requested_shares else 0.0
            rejected = None
            if entry >= 1:
                rejected = "STRESSED_ENTRY_AT_OR_ABOVE_ONE"
                fill_ratio = 0.0
            elif float(trade["book_age_ms"]) + scenario["latency_ms"] > 1500:
                rejected = "QUOTE_EXPIRY_PROXY_BOOK_TOO_OLD"
                fill_ratio = 0.0
            if fill_ratio <= 0:
                stressed_pnl = None
                stressed_entry = None
            else:
                filled_stake = fill_shares * entry
                fee = taker_fee(fill_shares, entry, fee_bps)
                payout = fill_shares if trade["side"] == trade["winner"] else 0.0
                stressed_pnl = payout - filled_stake - fee
                stressed_entry = entry
            records.append({
                "composite_id": composite_id,
                "market_id": int(trade["market_id"]),
                "observation_id": int(trade["observation_id"]),
                "split": str(trade["split"]),
                "scenario": str(scenario["name"]),
                "fill_ratio": fill_ratio,
                "stressed_entry": stressed_entry,
                "stressed_pnl": stressed_pnl,
                "rejected_reason": rejected,
            })
    return records


def summarize_execution_stress(records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["composite_id"], record["split"], record["scenario"])].append(record)
    result: dict[str, Any] = {}
    for (composite_id, split, scenario), values in grouped.items():
        executed = [value for value in values if value["stressed_pnl"] is not None]
        result.setdefault(composite_id, {}).setdefault(split, {})[scenario] = {
            "candidates": len(values),
            "executed": len(executed),
            "executionRate": len(executed) / len(values),
            "averageFillRatio": sum(value["fill_ratio"] for value in values) / len(values),
            "realizedPnl": sum(float(value["stressed_pnl"]) for value in executed),
            "rejectedReasons": dict(Counter(
                value["rejected_reason"] for value in values if value["rejected_reason"]
            )),
        }
    return result


def _sizing_metrics(pnls: Sequence[float], stakes: Sequence[float]) -> dict[str, Any]:
    equity = 0.0
    peak = 0.0
    maximum_drawdown = 0.0
    for pnl in pnls:
        equity += pnl
        peak = max(peak, equity)
        maximum_drawdown = max(maximum_drawdown, peak - equity)
    positive = sum(pnl for pnl in pnls if pnl > 0)
    negative = -sum(pnl for pnl in pnls if pnl < 0)
    stake = sum(stakes)
    return {
        "trades": len(pnls),
        "totalStake": stake,
        "realizedPnl": sum(pnls),
        "roiOnStake": sum(pnls) / stake if stake else None,
        "profitFactor": positive / negative if negative else None,
        "maxDrawdown": maximum_drawdown,
    }


def strategy_local_sizing_research(
    trades: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Compare predeclared strategy-local sizing rules without selecting one."""

    result: dict[str, Any] = {}
    for definition in FROZEN_COMPOSITES:
        values = sorted(
            (
                trade
                for trade in trades
                if str(trade["strategy"]) == definition.strategy
            ),
            key=lambda trade: int(trade["observation_id"]),
        )
        scenario_rows: dict[str, list[tuple[str, float, float]]] = {
            "recordedStake": [],
            "flat1": [],
            "rolling1To4Start2": [],
        }
        rolling_stake = 2.0
        for trade in values:
            original_stake = float(trade["stake"])
            unit_return = float(trade["pnl"]) / original_stake
            split = str(trade["split"])
            scenario_rows["recordedStake"].append(
                (split, float(trade["pnl"]), original_stake)
            )
            scenario_rows["flat1"].append((split, unit_return, 1.0))
            scenario_rows["rolling1To4Start2"].append(
                (split, unit_return * rolling_stake, rolling_stake)
            )
            if float(trade["pnl"]) > 0:
                rolling_stake = min(4.0, rolling_stake + 1.0)
            else:
                rolling_stake = max(1.0, rolling_stake - 1.0)
        scenario_result: dict[str, Any] = {}
        for scenario, rows in scenario_rows.items():
            scenario_result[scenario] = {
                "all": _sizing_metrics(
                    [row[1] for row in rows], [row[2] for row in rows]
                ),
                "splits": {
                    split: _sizing_metrics(
                        [row[1] for row in rows if row[0] == split],
                        [row[2] for row in rows if row[0] == split],
                    )
                    for split in ("development", "validation", "holdout")
                },
            }
        result[definition.composite_id] = {
            "status": "RESEARCH_ONLY_NO_SELECTION_NO_LIVE_FORWARDING",
            "scenarios": scenario_result,
            "limitation": (
                "PnL is scaled linearly from recorded fills; larger-order depth and "
                "slippage must be re-simulated before any sizing decision"
            ),
        }
    return result


def recorded_horizon_markouts(
    simulation_db: Path,
    trades: Sequence[dict[str, Any]],
    *,
    horizons: Sequence[int] = HORIZON_SECONDS,
    fee_bps: int = DEFAULT_FEE_BPS,
    end_at: str | None = None,
) -> list[dict[str, Any]]:
    market_ids = sorted({int(trade["market_id"]) for trade in trades})
    if not market_ids:
        return []
    observations: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with connect_read_only(simulation_db) as connection:
        for start in range(0, len(market_ids), 500):
            chunk = market_ids[start : start + 500]
            placeholders = ",".join("?" for _ in chunk)
            end_clause = "AND timestamp <= ?" if end_at else ""
            parameters: list[Any] = list(chunk)
            if end_at:
                parameters.append(end_at)
            rows = connection.execute(
                f"""SELECT market_id, timestamp, seconds_left,
                           up_bid, up_bid_size, down_bid, down_bid_size
                     FROM observations
                     WHERE market_id IN ({placeholders})
                       {end_clause}
                     ORDER BY market_id, timestamp""",
                parameters,
            )
            for row in rows:
                observations[int(row["market_id"])].append(dict(row))
    timestamps = {
        market_id: [parse_timestamp(str(row["timestamp"])) for row in rows]
        for market_id, rows in observations.items()
    }
    records = []
    for trade in trades:
        strategy = str(trade["strategy"])
        composite_id = COMPOSITE_BY_STRATEGY[strategy].composite_id
        market_id = int(trade["market_id"])
        entry_at = parse_timestamp(str(trade["timestamp"]))
        shares = float(trade["shares"])
        entry_cost = float(trade["stake"]) + float(trade["fee"])
        rows = observations.get(market_id, [])
        market_timestamps = timestamps.get(market_id, [])
        for horizon in horizons:
            target = entry_at + timedelta(seconds=int(horizon))
            index = bisect_left(market_timestamps, target)
            unavailable = None
            row = rows[index] if index < len(rows) else None
            if row is None:
                unavailable = "NO_RECORDED_SNAPSHOT_AT_OR_AFTER_HORIZON"
            elif market_timestamps[index] - target > timedelta(seconds=5):
                unavailable = "RECORDED_SNAPSHOT_GAP_OVER_5_SECONDS"
            elif float(row["seconds_left"]) <= 0:
                unavailable = "SNAPSHOT_AT_OR_AFTER_SETTLEMENT"
            bid_key = f"{str(trade['side']).lower()}_bid"
            size_key = f"{str(trade['side']).lower()}_bid_size"
            bid = float(row[bid_key]) if row is not None and row[bid_key] is not None else None
            bid_size = float(row[size_key]) if row is not None and row[size_key] is not None else None
            if unavailable is None and (bid is None or bid_size is None or bid <= 0 or bid_size <= 0):
                unavailable = "INVALID_OR_MISSING_EXECUTABLE_BID"
            full_depth = bool(
                unavailable is None and bid_size is not None and bid_size + 1e-12 >= shares
            )
            if unavailable is None and not full_depth:
                unavailable = "INSUFFICIENT_BID_DEPTH_FOR_FULL_EXIT"
            exit_pnl = None
            if unavailable is None and bid is not None:
                proceeds = shares * bid
                exit_fee = taker_fee(shares, bid, fee_bps)
                exit_pnl = proceeds - exit_fee - entry_cost
            records.append({
                "composite_id": composite_id,
                "market_id": market_id,
                "observation_id": int(trade["observation_id"]),
                "split": str(trade["split"]),
                "horizon_seconds": int(horizon),
                "observed_at": str(row["timestamp"]) if row is not None else None,
                "bid": bid,
                "bid_size": bid_size,
                "full_depth": int(full_depth),
                "exit_pnl": exit_pnl,
                "unavailable_reason": unavailable,
            })
    return records


def summarize_markouts(
    records: Sequence[dict[str, Any]], trades: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    settlement = {
        (
            COMPOSITE_BY_STRATEGY[str(trade["strategy"])].composite_id,
            int(trade["market_id"]),
            int(trade["observation_id"]),
        ): float(trade["pnl"])
        for trade in trades
    }
    grouped: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["composite_id"], record["split"], record["horizon_seconds"])].append(record)
    result: dict[str, Any] = {}
    for (composite_id, split, horizon), values in grouped.items():
        executable = [value for value in values if value["exit_pnl"] is not None]
        settlement_for_covered = [
            settlement[(composite_id, value["market_id"], value["observation_id"])]
            for value in executable
        ]
        result.setdefault(composite_id, {}).setdefault(split, {})[str(horizon)] = {
            "candidates": len(values),
            "fullDepthExecutable": len(executable),
            "coverageRate": len(executable) / len(values),
            "exitPnl": sum(float(value["exit_pnl"]) for value in executable),
            "coveredSettlementPnl": sum(settlement_for_covered),
            "pnlDeltaVsCoveredSettlement": (
                sum(float(value["exit_pnl"]) for value in executable)
                - sum(settlement_for_covered)
            ),
            "averageExitPnl": (
                sum(float(value["exit_pnl"]) for value in executable) / len(executable)
                if executable
                else None
            ),
            "unavailableReasons": dict(Counter(
                value["unavailable_reason"]
                for value in values
                if value["unavailable_reason"]
            )),
        }
    return result


def _trade_key(trade: dict[str, Any]) -> tuple[int, int]:
    return int(trade["market_id"]), int(trade["observation_id"])


def _counterfactual_record(
    trade: dict[str, Any], composite_id: str, blocker: str
) -> dict[str, Any]:
    return {
        "composite_id": composite_id,
        "market_id": int(trade["market_id"]),
        "observation_id": int(trade["observation_id"]),
        "split": str(trade["split"]),
        "blocker": blocker,
        "counterfactual_result": "WIN" if float(trade["pnl"]) > 0 else "LOSS",
        "counterfactual_pnl": float(trade["pnl"]),
    }


def blocked_counterfactual_records(report: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    all_trades = report.get("trades", [])
    records: list[dict[str, Any]] = []
    for definition in FROZEN_COMPOSITES:
        strategy_trades = [
            trade for trade in all_trades if trade.get("strategy") == definition.strategy
        ]
        source = {
            _trade_key(trade): trade
            for trade in strategy_trades
            if trade.get("observer_version") == "BASE"
        }
        observer = {
            _trade_key(trade): trade
            for trade in strategy_trades
            if trade.get("observer_version") == definition.observer_version
        }
        final = {
            _trade_key(trade): trade
            for trade in strategy_trades
            if trade.get("observer_version") == definition.replay_version
        }
        for key in sorted(source.keys() - observer.keys()):
            records.append(_counterfactual_record(
                source[key], definition.composite_id, "OBSERVER"
            ))
        if definition.drawdown_enabled:
            for key in sorted(observer.keys() - final.keys()):
                records.append(_counterfactual_record(
                    observer[key], definition.composite_id, "DRAWDOWN"
                ))
        if definition.two_loss_cooldown:
            _, blocked = apply_two_loss_skip_one(list(final.values()))
            for trade in blocked:
                records.append(_counterfactual_record(
                    trade, definition.composite_id, "TWO_LOSS_COOLDOWN"
                ))
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        grouped[(record["composite_id"], record["split"], record["blocker"])].append(record)
    summary: dict[str, Any] = {}
    for (composite_id, split, blocker), values in grouped.items():
        summary.setdefault(composite_id, {}).setdefault(split, {})[blocker] = {
            "blocked": len(values),
            "counterfactualWins": sum(value["counterfactual_result"] == "WIN" for value in values),
            "counterfactualLosses": sum(value["counterfactual_result"] == "LOSS" for value in values),
            "counterfactualPnl": sum(value["counterfactual_pnl"] for value in values),
        }
    summary["unsupportedAttribution"] = {
        "PRICE_AND_EXECUTION": (
            "the replay report contains executed candidates only; rejected price/depth/quote "
            "candidates require a future all-candidate ledger"
        ),
        "OVERLAPPING_BLOCKERS": (
            "sequential replay attributes the first blocker only; future forward logging must "
            "record all evaluated blockers"
        ),
    }
    return records, summary


def conservative_calibrated_value_shadow(
    records: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    development = [record for record in records if record["split"] == "development"]
    report = calibration_report(development)
    buckets = {row["bucket"]: row for row in report.get("bins", [])}
    decisions = []
    for record in records:
        bucket = min(9, int(float(record["model_probability"]) * 10))
        evidence = buckets.get(bucket)
        allowed = False
        reason = "DEVELOPMENT_BIN_UNAVAILABLE"
        conservative_probability = None
        conservative_edge = None
        if evidence is not None and evidence["samples"] >= CALIBRATION_MIN_BIN_SAMPLES:
            conservative_probability = min(
                float(record["model_probability"]),
                float(evidence["wilsonLower95"]),
            )
            conservative_edge = (
                conservative_probability
                - float(record["effective_entry_probability"])
            )
            allowed = conservative_edge >= CALIBRATED_VALUE_MIN_EDGE
            reason = "ALLOW" if allowed else "CONSERVATIVE_EDGE_BELOW_MINIMUM"
        decisions.append({
            **record,
            "developmentBucketSamples": evidence["samples"] if evidence else 0,
            "conservativeProbability": conservative_probability,
            "conservativeEdge": conservative_edge,
            "allowed": allowed,
            "reason": reason,
        })
    splits: dict[str, Any] = {}
    for split in ("development", "validation", "holdout"):
        values = [value for value in decisions if value["split"] == split]
        allowed = [value for value in values if value["allowed"]]
        splits[split] = {
            "candidates": len(values),
            "allowed": len(allowed),
            "coverageRate": len(allowed) / len(values) if values else None,
            "wins": sum(value["outcome"] for value in allowed),
            "winRate": (
                sum(value["outcome"] for value in allowed) / len(allowed)
                if allowed
                else None
            ),
            "status": "AUDIT_ONLY_REUSED" if split != "development" else "DEVELOPMENT",
        }
    return {
        "status": (
            "CANDIDATE_AVAILABLE"
            if any(value["allowed"] for value in decisions)
            else "INSUFFICIENT_CALIBRATION_EVIDENCE"
        ),
        "selectionPolicy": "fixed probability deciles fitted on development only",
        "minimumBinSamples": CALIBRATION_MIN_BIN_SAMPLES,
        "minimumConservativeEdge": CALIBRATED_VALUE_MIN_EDGE,
        "splits": splits,
        "decisions": decisions,
    }


def _quantile(values: Sequence[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _fingerprint_value(trade: dict[str, Any], feature: str) -> float:
    if feature == "signal_strength":
        return abs(float(trade["signal"]))
    if feature == "visible_depth_log":
        return math.log1p(float(trade["visible_ask_size"]))
    return float(trade[feature])


def advantage_fingerprints(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    features = (
        "signal_strength",
        "entry_price",
        "visible_depth_log",
        "book_age_ms",
    )
    result: dict[str, Any] = {}
    for definition in FROZEN_COMPOSITES:
        values = [trade for trade in trades if trade["strategy"] == definition.strategy]
        development = [trade for trade in values if trade["split"] == "development"]
        if len(development) < 20:
            result[definition.composite_id] = {
                "status": "INSUFFICIENT_DEVELOPMENT_TRADES",
                "developmentTrades": len(development),
                "features": {},
            }
            continue
        composite = {
            "status": "READY_DESCRIPTIVE_NOT_A_TRADING_GATE",
            "developmentTrades": len(development),
            "features": {},
        }
        for feature in features:
            cuts = [
                _quantile([_fingerprint_value(trade, feature) for trade in development], fraction)
                for fraction in (0.25, 0.5, 0.75)
            ]
            split_rows: dict[str, Any] = {}
            for split in ("development", "validation", "holdout"):
                split_values = [trade for trade in values if trade["split"] == split]
                buckets = []
                for bucket in range(4):
                    bucket_values = [
                        trade
                        for trade in split_values
                        if sum(_fingerprint_value(trade, feature) > cut for cut in cuts) == bucket
                    ]
                    stake = sum(float(trade["stake"]) for trade in bucket_values)
                    pnl = sum(float(trade["pnl"]) for trade in bucket_values)
                    buckets.append({
                        "bucket": bucket,
                        "trades": len(bucket_values),
                        "wins": sum(float(trade["pnl"]) > 0 for trade in bucket_values),
                        "winRate": (
                            sum(float(trade["pnl"]) > 0 for trade in bucket_values)
                            / len(bucket_values)
                            if bucket_values
                            else None
                        ),
                        "realizedPnl": pnl,
                        "roiOnStake": pnl / stake if stake else None,
                    })
                split_rows[split] = buckets
            composite["features"][feature] = {
                "developmentCuts": cuts,
                "splits": split_rows,
            }
        result[definition.composite_id] = composite
    return result


def edge_realization_waterfall(trades: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for definition in FROZEN_COMPOSITES:
        values = [trade for trade in trades if trade["strategy"] == definition.strategy]
        probability_values = [infer_selected_side_probability(trade) for trade in values]
        probability_values = [value for value in probability_values if value is not None]
        total_shares = sum(float(trade["shares"]) for trade in values)
        result[definition.composite_id] = {
            "trades": len(values),
            "averageAbsoluteSignal": (
                sum(abs(float(trade["signal"])) for trade in values) / len(values)
                if values
                else None
            ),
            "averageModelProbability": (
                sum(probability_values) / len(probability_values)
                if probability_values
                else None
            ),
            "averageRawAsk": (
                sum(float(trade["raw_ask"]) for trade in values) / len(values)
                if values
                else None
            ),
            "averageSimulatedEntry": (
                sum(float(trade["entry_price"]) for trade in values) / len(values)
                if values
                else None
            ),
            "slippagePaid": sum(
                (float(trade["entry_price"]) - float(trade["raw_ask"]))
                * float(trade["shares"])
                for trade in values
            ),
            "entryFeesPaid": sum(float(trade["fee"]) for trade in values),
            "settlementPnl": sum(float(trade["pnl"]) for trade in values),
            "pnlPerShare": (
                sum(float(trade["pnl"]) for trade in values) / total_shares
                if total_shares
                else None
            ),
            "probabilityEdgeAvailable": bool(probability_values),
            "availabilityNote": (
                "selected-side probability reconstructed exactly from recorded model edge"
                if probability_values
                else "strategy emits a directional score, not a calibrated probability"
            ),
        }
    return result


def quote_edge_stress(probability_records: Sequence[dict[str, Any]]) -> dict[str, Any]:
    result = {}
    for extra_bps in (0, 50, 100, 200, 500):
        rows = []
        for record in probability_records:
            quoted = float(record["quoted_ask_probability"]) * (1 + extra_bps / 10_000)
            fee = taker_fee(1.0, quoted, DEFAULT_FEE_BPS)
            edge = float(record["model_probability"]) - quoted - fee
            rows.append({**record, "stressedQuote": quoted, "stressedEdge": edge, "allowed": edge >= CALIBRATED_VALUE_MIN_EDGE})
        result[str(extra_bps)] = {
            "candidates": len(rows),
            "edgePreserved": sum(row["allowed"] for row in rows),
            "edgePreservationRate": (
                sum(row["allowed"] for row in rows) / len(rows) if rows else None
            ),
            "averageStressedEdge": (
                sum(row["stressedEdge"] for row in rows) / len(rows) if rows else None
            ),
        }
    return result


def platform_risk_registry() -> list[dict[str, Any]]:
    return [
        {
            "risk_id": "API_TRANSPORT",
            "component": "Binance Prediction API",
            "failure_mode": "timeouts, transport errors, ambiguous placement response",
            "evidence_available": 1,
            "current_control": "segmented latency, bounded requote, consecutive-error watchdog",
            "gap": "no controlled historical outage injection in this research baseline",
        },
        {
            "risk_id": "THIRD_PARTY_PROTOCOL",
            "component": "Predict.fun integration",
            "failure_mode": "protocol availability or behavior changes outside the bot",
            "evidence_available": 0,
            "current_control": "fail-closed API and market metadata checks",
            "gap": "formal dependency/version/change register is missing",
        },
        {
            "risk_id": "ORACLE_SETTLEMENT",
            "component": "official market settlement",
            "failure_mode": "delayed, disputed, missing, or revised winner",
            "evidence_available": 1,
            "current_control": "official settlement is stored separately from proxy winner",
            "gap": "no explicit dispute/revision lifecycle in the research ledger",
        },
        {
            "risk_id": "WALLET_REDEEM",
            "component": "wallet balance and redeem flow",
            "failure_mode": "balance drift, redeem delay, uncertain transaction result",
            "evidence_available": 1,
            "current_control": "wallet share checks, separate redeem records, uncertain-result handling",
            "gap": "no periodic end-to-end reconciliation report in this branch baseline",
        },
        {
            "risk_id": "CLOCK_AND_AUTH",
            "component": "signed request authentication",
            "failure_mode": "timestamp skew or credential rejection",
            "evidence_available": 1,
            "current_control": "preflight and explicit quote access status",
            "gap": "server-time drift is not yet a first-class longitudinal metric",
        },
    ]


def select_development_exit_candidate(
    markout_summary: dict[str, Any], composite_id: str
) -> dict[str, Any]:
    development = markout_summary.get(composite_id, {}).get("development", {})
    candidates = []
    for horizon, values in development.items():
        if values["fullDepthExecutable"] < 30 or values["coverageRate"] < 0.6:
            continue
        candidates.append((values["pnlDeltaVsCoveredSettlement"], int(horizon), values))
    if not candidates:
        return {
            "status": "INSUFFICIENT_DEVELOPMENT_EXIT_EVIDENCE",
            "minimumExecutableTrades": 30,
            "minimumCoverageRate": 0.6,
            "selectedHorizonSeconds": None,
        }
    candidates.sort(reverse=True)
    best_delta, horizon, values = candidates[0]
    if best_delta <= 0:
        return {
            "status": "NO_BENEFICIAL_DEVELOPMENT_EXIT_CANDIDATE",
            "minimumExecutableTrades": 30,
            "minimumCoverageRate": 0.6,
            "selectedHorizonSeconds": None,
            "bestObservedHorizonSeconds": horizon,
            "bestObservedPnlDeltaVsCoveredSettlement": best_delta,
        }
    return {
        "status": "DEVELOPMENT_CANDIDATE_AUDIT_ONLY",
        "selectedHorizonSeconds": horizon,
        "development": values,
        "validation": markout_summary.get(composite_id, {}).get("validation", {}).get(str(horizon)),
        "holdout": markout_summary.get(composite_id, {}).get("holdout", {}).get(str(horizon)),
        "promotionStatus": "NEEDS_NEW_FORWARD_DATA_BECAUSE_LATER_SPLITS_ARE_REUSED",
    }
