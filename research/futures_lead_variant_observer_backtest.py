from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from research.legacy_observer_backtest import (
    SPLITS,
    VERSIONS,
    LegacyTrade,
    _assign_segments,
    _connect_read_only,
    _decision_rank,
    _replay_observer,
    _signal_timestamp,
    _stability_assessment,
    _summarize,
)


SOURCE_STRATEGY = "R_FUTURES_LEAD"
DISTANCE_STRATEGY = "R_FUTURES_LEAD_DISTANCE"
VARIANTS = (
    "R_FUTURES_LEAD_DISTANCE",
    "R_FUTURES_LEAD_SIGNAL_100",
    "R_FUTURES_LEAD_MIN_ENTRY_020",
)


def _load_source_trades(
    db: sqlite3.Connection,
) -> tuple[list[LegacyTrade], dict[int, dict[str, Any]]]:
    rows = db.execute(
        """SELECT id, strategy, market_id, side, status, entry_price,
                  stake, fees, pnl, opened_at, diagnostics_json
             FROM trades
            WHERE strategy IN (?, ?)
              AND status<>'OPEN' AND pnl IS NOT NULL
            ORDER BY opened_at, id""",
        (SOURCE_STRATEGY, DISTANCE_STRATEGY),
    ).fetchall()
    trades: list[LegacyTrade] = []
    diagnostics_by_id: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            diagnostics = json.loads(str(row["diagnostics_json"] or "{}"))
        except (TypeError, json.JSONDecodeError):
            diagnostics = {}
        signal_at, source = _signal_timestamp(
            str(row["opened_at"]), row["diagnostics_json"]
        )
        trade = LegacyTrade(
            id=int(row["id"]),
            strategy=str(row["strategy"]),
            market_id=int(row["market_id"]),
            side=str(row["side"]),
            status=str(row["status"]),
            entry_price=float(row["entry_price"]),
            stake=float(row["stake"]),
            fees=float(row["fees"] or 0.0),
            pnl=float(row["pnl"]),
            opened_at=str(row["opened_at"]),
            signal_at=signal_at,
            signal_time_source=source,
        )
        trades.append(trade)
        diagnostics_by_id[trade.id] = diagnostics if isinstance(diagnostics, dict) else {}
    return trades, diagnostics_by_id


def _cohorts(
    trades: list[LegacyTrade], diagnostics_by_id: dict[int, dict[str, Any]]
) -> dict[str, list[LegacyTrade]]:
    lead = [trade for trade in trades if trade.strategy == SOURCE_STRATEGY]
    distance = [trade for trade in trades if trade.strategy == DISTANCE_STRATEGY]

    def source_signal(trade: LegacyTrade) -> float | None:
        try:
            return float(diagnostics_by_id[trade.id]["signal"])
        except (KeyError, TypeError, ValueError):
            return None

    return {
        "R_FUTURES_LEAD_DISTANCE": distance,
        "R_FUTURES_LEAD_SIGNAL_100": [
            trade
            for trade in lead
            if source_signal(trade) is not None
            and abs(float(source_signal(trade))) >= 1.0
        ],
        "R_FUTURES_LEAD_MIN_ENTRY_020": [
            trade for trade in lead if trade.entry_price > 0.20
        ],
    }


def _variant_report(
    strategy: str,
    trades: list[LegacyTrade],
    evaluations_by_id: dict[int, dict[str, Any]],
) -> dict[str, Any]:
    split_by_id, fold_by_id = _assign_segments(trades)
    baseline = _summarize(
        trades, baseline_count=len(trades), fold_by_id=fold_by_id
    )
    versions: dict[str, dict[str, Any]] = {}
    for version in VERSIONS:
        selected = (
            trades
            if version == "BASE"
            else [
                trade
                for trade in trades
                if evaluations_by_id[trade.id][f"{version}_allowed"] is True
            ]
        )
        overall = _summarize(
            selected, baseline_count=len(trades), fold_by_id=fold_by_id
        )
        splits = {
            split: _summarize(
                [trade for trade in selected if split_by_id[trade.id] == split],
                baseline_count=sum(value == split for value in split_by_id.values()),
            )
            for split in SPLITS
        }
        item = {
            "strategy": strategy,
            "observerVersion": version,
            "overall": overall,
            "splits": splits,
            "stability": _stability_assessment(overall, splits, baseline),
            "pnlDeltaVsBase": overall["realizedPnl"] - baseline["realizedPnl"],
            "drawdownReductionVsBase": (
                baseline["maxDrawdown"] - overall["maxDrawdown"]
            ),
        }
        versions[version] = item
    ranked = sorted(versions.values(), key=_decision_rank, reverse=True)
    return {
        "sourceTrades": len(trades),
        "versions": versions,
        "bestByFrozenStabilityRanking": ranked[0]["observerVersion"],
        "ranking": [item["observerVersion"] for item in ranked],
    }


def run(db_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db = _connect_read_only(db_path)
    try:
        db.execute("BEGIN")
        observation = db.execute(
            """SELECT MIN(timestamp) AS first_timestamp,
                      MAX(timestamp) AS last_timestamp,
                      COUNT(*) AS rows, COUNT(DISTINCT market_id) AS markets
                 FROM observations"""
        ).fetchone()
        trades, diagnostics_by_id = _load_source_trades(db)
        evaluations, coverage = _replay_observer(db, trades)
        evaluations_by_id = {int(item["id"]): item for item in evaluations}
        cohorts = _cohorts(trades, diagnostics_by_id)
        variants = {
            strategy: _variant_report(
                strategy, cohorts[strategy], evaluations_by_id
            )
            for strategy in VARIANTS
        }
        report = {
            "schemaVersion": 1,
            "generatedAt": datetime.now(timezone.utc).isoformat(),
            "sourceDatabase": str(db_path.resolve()),
            "dataSnapshot": {
                "firstObservation": observation["first_timestamp"],
                "lastObservation": observation["last_timestamp"],
                "observationRows": int(observation["rows"]),
                "observedMarkets": int(observation["markets"]),
                **coverage,
            },
            "method": {
                "observerVersions": list(VERSIONS),
                "causalObserverReplay": True,
                "singleReadTransactionSnapshot": True,
                "distanceCohort": "recorded realized R_FUTURES_LEAD_DISTANCE paper fills",
                "signal100Cohort": "recorded realized R_FUTURES_LEAD fills with abs(signal) >= 1.00 bps",
                "minEntry020Cohort": "recorded realized R_FUTURES_LEAD fills with entry_price > 0.20",
                "pnlSource": "recorded realized pnl; blocked rows omitted",
                "splitPolicy": "per-cohort chronological 60/20/20",
                "foldPolicy": "per-cohort chronological five equal-count folds",
                "warning": (
                    "Signal and entry filters are recorded-fill counterfactuals; "
                    "Distance uses its actual paper history. No result activates live trading."
                ),
            },
            "variants": variants,
        }
        return report, evaluations
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=Path("data/simulation.db"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("artifacts/futures_lead_variant_observer_backtest.json"),
    )
    args = parser.parse_args()
    report, _ = run(args.db)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["variants"], ensure_ascii=False, indent=2))
    print(f"report={args.output.resolve()}")


if __name__ == "__main__":
    main()
