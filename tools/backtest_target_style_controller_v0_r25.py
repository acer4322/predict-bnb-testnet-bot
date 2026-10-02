from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
TOOLS = ROOT / "tools"
for path in (SRC, TOOLS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import backtest_target_style_controller_v0 as base
from predict_bot.target_style_controller_v0 import (
    DOWN,
    UP,
    ControllerV0Config,
    PortfolioState,
    TargetStyleControllerV0,
    market_signal_from_mapping,
)

VERSION = "TARGET_STYLE_HIERARCHICAL_CONTROLLER_V0_R25_BYPASS"
DEFAULT_DB = ROOT / "data" / "simulation.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_style_controller_v0_r25_report.json"
DEFAULT_ACTIONS = ROOT / "data" / "research" / "target_style_controller_v0_r25_actions.csv"
R25_REPAIR_FRACTION = 0.25


def _replay_r25(
    rows: list[sqlite3.Row],
    *,
    split: str,
    winner: str,
    cfg: ControllerV0Config,
) -> tuple[base.MarketResult, list[base.Fill], Counter[str]]:
    """Replay LAYERED_V0 unchanged except for repair sizing.

    The bypass changes exactly one thing:
        repair_qty = abs(current net inventory gap) * 0.25

    All opportunity, desired exposure, price cap, time guard, action spacing,
    gross cap, taker fee, and recorded ask/depth execution rules remain the
    same as LAYERED_V0.
    """
    state = base._ReplayState(PortfolioState(), [])
    controller = TargetStyleControllerV0(cfg)
    intent_counts: Counter[str] = Counter()

    for row in rows:
        if not base._finite_book(row):
            continue

        signal = market_signal_from_mapping(row)
        now_ts = base._parse_ts(str(row["timestamp"])).timestamp()
        trace = controller.step(signal, state.portfolio, now_ts=now_ts)
        intent_counts[trace.intent.action] += 1

        if trace.intent.action not in {"TAKER_ADD", "TAKER_REPAIR"}:
            continue

        requested_shares = trace.intent.shares
        if trace.intent.action == "TAKER_REPAIR":
            current_net_gap = abs(trace.inventory.net_shares)
            requested_shares = min(
                current_net_gap * R25_REPAIR_FRACTION,
                trace.intent.shares,
                trace.risk.gross_remaining,
            )

        base._fill(
            state=state,
            row=row,
            strategy="V0_R25",
            action=trace.intent.action,
            side=trace.intent.side,
            requested_shares=requested_shares,
            opportunity_status=trace.opportunity.status,
            spot_move_bps=trace.opportunity.spot_move_bps,
        )

    first = rows[0]
    result = base._settle(
        strategy="V0_R25",
        split=split,
        market_id=int(first["market_id"]),
        first_observation_id=int(first["id"]),
        first_timestamp=str(first["timestamp"]),
        winner=winner,
        state=state,
    )
    return result, state.fills, intent_counts


def _run_r25_only(
    db_path: Path,
    cfg: ControllerV0Config,
) -> tuple[list[base.MarketResult], list[base.Fill], Counter[str]]:
    db = base._open_ro(db_path)
    try:
        required_tables = {
            str(r[0]) for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = {"observations", "market_settlements"} - required_tables
        if missing:
            raise RuntimeError("simulation DB missing tables: " + ", ".join(sorted(missing)))

        market_rows = db.execute(
            """SELECT o.market_id, MIN(o.id) AS first_observation_id,
                      MIN(o.timestamp) AS first_timestamp,
                      s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               GROUP BY o.market_id, s.official_winner
               ORDER BY first_observation_id"""
        ).fetchall()
        split_by_market = base._split_labels(market_rows)
        winner_by_market = {
            int(r["market_id"]): str(r["official_winner"]) for r in market_rows
        }
        if not market_rows:
            raise RuntimeError("no officially settled markets in simulation DB")

        all_results: list[base.MarketResult] = []
        all_fills: list[base.Fill] = []
        intent_counts: Counter[str] = Counter()
        current_mid: int | None = None
        current_rows: list[sqlite3.Row] = []

        def process(rows: list[sqlite3.Row]) -> None:
            if not rows:
                return
            mid = int(rows[0]["market_id"])
            result, fills, intents = _replay_r25(
                rows,
                split=split_by_market[mid],
                winner=winner_by_market[mid],
                cfg=cfg,
            )
            all_results.append(result)
            all_fills.extend(fills)
            intent_counts.update(intents)

        for row in db.execute(
            """SELECT o.*, s.official_winner
               FROM observations AS o
               JOIN market_settlements AS s ON s.market_id=o.market_id
               WHERE s.status='OFFICIAL' AND s.official_winner IN ('UP','DOWN')
               ORDER BY o.id"""
        ):
            mid = int(row["market_id"])
            if current_mid is not None and mid != current_mid:
                process(current_rows)
                current_rows = []
            current_mid = mid
            current_rows.append(row)
        process(current_rows)
    finally:
        db.close()

    return all_results, all_fills, intent_counts


def _summary_bundle(results: list[base.MarketResult]) -> dict[str, Any]:
    return {
        "overall": base._summarize(results),
        "splits": {
            split: base._summarize(r for r in results if r.split == split)
            for split in ("development", "validation", "holdout")
        },
        "regimes": {
            regime: base._summarize(r for r in results if r.regime == regime)
            for regime in ("ORDINARY", "STRESS_2026_08_16")
        },
    }


def _delta_bundle(candidate: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    return {
        "overall": base._delta(candidate["overall"], reference["overall"]),
        "splits": {
            split: base._delta(candidate["splits"][split], reference["splits"][split])
            for split in ("development", "validation", "holdout")
        },
        "regimes": {
            regime: base._delta(candidate["regimes"][regime], reference["regimes"][regime])
            for regime in ("ORDINARY", "STRESS_2026_08_16")
        },
    }


def _write_actions(path: Path, rows: list[base.Fill]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(base.Fill.__dataclass_fields__)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))


def run(db_path: Path, cfg: ControllerV0Config) -> tuple[dict[str, Any], list[base.Fill]]:
    baseline_report, baseline_fills = base.run(db_path, cfg)
    r25_results, r25_fills, r25_intents = _run_r25_only(db_path, cfg)

    direct = baseline_report["summaries"]["DIRECT_ONCE"]
    layered = baseline_report["summaries"]["LAYERED_V0"]
    r25 = _summary_bundle(r25_results)

    report = {
        "version": VERSION,
        "policy": {
            "researchOnly": True,
            "liveTradingChanges": False,
            "parameterSweep": False,
            "singleBypass": True,
            "repairFraction": R25_REPAIR_FRACTION,
            "repairFormula": "repair_qty = abs(current net inventory gap) * 0.25",
            "allOtherRules": "identical to LAYERED_V0",
        },
        "config": {**asdict(cfg), "fee_bps": base.FEE_BPS},
        "source": baseline_report["source"],
        "summaries": {
            "DIRECT_ONCE": direct,
            "LAYERED_V0": layered,
            "V0_R25": r25,
        },
        "comparisons": {
            "LAYERED_V0_minus_DIRECT_ONCE": _delta_bundle(layered, direct),
            "V0_R25_minus_DIRECT_ONCE": _delta_bundle(r25, direct),
            "V0_R25_minus_LAYERED_V0": _delta_bundle(r25, layered),
        },
        "intentCounts": {
            "LAYERED_V0": baseline_report.get("layeredIntentCounts", {}),
            "V0_R25": dict(r25_intents),
        },
        "marketResults": [
            *baseline_report["marketResults"],
            *[asdict(r) for r in r25_results],
        ],
    }
    return report, [*baseline_fills, *r25_fills]


def main() -> None:
    p = argparse.ArgumentParser(
        description="Single-bypass V0-R25 research replay versus DIRECT_ONCE and LAYERED_V0"
    )
    p.add_argument("--database", type=Path, default=DEFAULT_DB)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--actions", type=Path, default=DEFAULT_ACTIONS)
    args = p.parse_args()

    cfg = ControllerV0Config()
    report, fills = run(args.database, cfg)

    out = args.report.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_actions(args.actions, fills)

    print(json.dumps({
        "version": VERSION,
        "repairFormula": report["policy"]["repairFormula"],
        "candidateMarkets": report["source"]["candidateMarkets"],
        "DIRECT_ONCE": report["summaries"]["DIRECT_ONCE"]["overall"],
        "LAYERED_V0": report["summaries"]["LAYERED_V0"]["overall"],
        "V0_R25": report["summaries"]["V0_R25"]["overall"],
        "V0_R25_minus_DIRECT_ONCE": report["comparisons"]["V0_R25_minus_DIRECT_ONCE"]["overall"],
        "V0_R25_minus_LAYERED_V0": report["comparisons"]["V0_R25_minus_LAYERED_V0"]["overall"],
        "report": str(out),
        "actions": str(args.actions.expanduser().resolve()),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
