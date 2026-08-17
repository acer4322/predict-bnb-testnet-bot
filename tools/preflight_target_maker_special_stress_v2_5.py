from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from predict_bot import target_maker_direct_placement_v1 as direct
import build_target_maker_lifecycle_side_v2_3 as lifecycle_builder

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
DEFAULT_HAZARD = RESEARCH / "target_maker_special_source_hazard_v2_5.csv"
DEFAULT_BEHAVIOR = RESEARCH / "target_maker_special_source_behavior_v2_5.csv"
DEFAULT_DIRECT_META = RESEARCH / "target_maker_special_source_v2_5.meta.json"
DEFAULT_LIFECYCLE = RESEARCH / "target_maker_special_lifecycle_v2_5.csv"
DEFAULT_LIFECYCLE_META = RESEARCH / "target_maker_special_lifecycle_v2_5.meta.json"
DEFAULT_REPORT = RESEARCH / "target_maker_special_stress_preflight_v2_5.json"
VERSION = "TARGET_MAKER_SPECIAL_STRESS_PREFLIGHT_V2_5_REBUILD_ONLY"


def _epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return list(reader.fieldnames or []), [dict(row) for row in reader]


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "median": None, "p90": None}
    ordered = sorted(values)
    def pick(q: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))]
    return {"p10": pick(.10), "median": float(statistics.median(ordered)), "p90": pick(.90)}


def _market_blocked_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    grouped: dict[int, list[int]] = defaultdict(list)
    for row in rows:
        value = row.get(key)
        if value is None:
            continue
        grouped[int(row["market_id"])].append(int(bool(value)))
    rates = [sum(values) / len(values) for values in grouped.values() if values]
    return statistics.fmean(rates) if rates else None


def _summarize_special(rows: list[dict[str, Any]], special_start_ms: int) -> dict[str, Any]:
    special = [row for row in rows if int(float(row["placement_first_ms"])) >= special_start_ms]
    with_prior = [row for row in special if _num(row.get("last_maker_side_up")) is not None]
    same_rows: list[dict[str, Any]] = []
    for row in with_prior:
        last_up = int(float(row["last_maker_side_up"]))
        next_up = int(float(row["label_side_up"]))
        item = dict(row)
        item["same_as_last"] = int(last_up == next_up)
        same_rows.append(item)
    ages = [float(value) for row in with_prior if (value := _num(row.get("last_maker_age_ms"))) is not None]
    runs = [float(value) for row in with_prior if (value := _num(row.get("prior_maker_same_side_run_length"))) is not None]
    rows_by_phase = {"OPEN_GT180": [], "MID_60_180": [], "TAIL_LE60": []}
    for row in special:
        seconds = _num(row.get("seconds_left"))
        if seconds is None:
            continue
        if seconds > 180:
            rows_by_phase["OPEN_GT180"].append(row)
        elif seconds > 60:
            rows_by_phase["MID_60_180"].append(row)
        else:
            rows_by_phase["TAIL_LE60"].append(row)
    phase = {}
    for name, values in rows_by_phase.items():
        prior = [row for row in values if _num(row.get("last_maker_side_up")) is not None]
        same = sum(int(int(float(row["last_maker_side_up"])) == int(float(row["label_side_up"]))) for row in prior)
        phase[name] = {
            "rows": len(values),
            "markets": len({int(float(row["market_id"])) for row in values}),
            "rowsWithPrior": len(prior),
            "sameAsLastRate": same / len(prior) if prior else None,
        }
    return {
        "rows": len(special),
        "markets": len({int(float(row["market_id"])) for row in special}),
        "firstPlacementMs": min((int(float(row["placement_first_ms"])) for row in special), default=None),
        "lastPlacementMs": max((int(float(row["placement_first_ms"])) for row in special), default=None),
        "rowsWithPrior": len(with_prior),
        "priorCoverage": len(with_prior) / len(special) if special else None,
        "sameAsLastRate": sum(int(row["same_as_last"]) for row in same_rows) / len(same_rows) if same_rows else None,
        "marketBlockedSameAsLastRate": _market_blocked_rate(same_rows, "same_as_last"),
        "lastMakerAgeMsQuantiles": _quantiles(ages),
        "sameSideRunLengthQuantiles": _quantiles(runs),
        "phases": phase,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild and preflight 2026-08-16+ Target Maker special cohort")
    parser.add_argument("--maker-db", type=Path, default=direct.DEFAULT_MAKER_DB)
    parser.add_argument("--signal-db", type=Path, default=direct.DEFAULT_SIGNAL_DB)
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    args = parser.parse_args()

    started = time.perf_counter()
    print(f"[{datetime.now():%H:%M:%S}] V2.5 rebuild direct Maker behavior", flush=True)
    direct_meta = direct.build_datasets(
        maker_db_path=args.maker_db,
        signal_db_path=args.signal_db,
        hazard_output_path=DEFAULT_HAZARD,
        behavior_output_path=DEFAULT_BEHAVIOR,
        meta_output_path=DEFAULT_DIRECT_META,
        max_signal_age_ms=direct.MAX_SIGNAL_AGE_MS,
        min_parent_confidence=direct.MIN_PARENT_CONFIDENCE,
        min_placement_coverage=direct.MIN_PLACEMENT_COVERAGE,
        min_fill_coverage=direct.MIN_FILL_COVERAGE,
    )
    print(f"[{datetime.now():%H:%M:%S}] V2.5 build clean strict-past lifecycle", flush=True)
    lifecycle_meta = lifecycle_builder.build_dataset(
        behavior_path=DEFAULT_BEHAVIOR,
        maker_db_path=args.maker_db,
        output_path=DEFAULT_LIFECYCLE,
        meta_path=DEFAULT_LIFECYCLE_META,
    )

    _, behavior_rows = _read_csv(DEFAULT_BEHAVIOR)
    _, lifecycle_rows = _read_csv(DEFAULT_LIFECYCLE)
    special_start_ms = _epoch_ms(args.special_start)
    behavior_special = [row for row in behavior_rows if int(float(row["placement_first_ms"])) >= special_start_ms]
    lifecycle_summary = _summarize_special(lifecycle_rows, special_start_ms)
    behavior_special_markets = {int(float(row["market_id"])) for row in behavior_special}
    clean_special_markets = {
        int(float(row["market_id"])) for row in lifecycle_rows
        if int(float(row["placement_first_ms"])) >= special_start_ms
    }

    clean_rate = lifecycle_summary["rows"] / len(behavior_special) if behavior_special else None
    direct_behavior_meta = direct_meta.get("behavior", {}) if isinstance(direct_meta, dict) else {}
    gate_reasons: list[str] = []
    if len(behavior_special) < 500:
        gate_reasons.append("special behavior rows < 500")
    if len(behavior_special_markets) < 15:
        gate_reasons.append("special behavior markets < 15")
    if clean_rate is None or clean_rate < 0.40:
        gate_reasons.append("special clean-target rate < 40%")
    if lifecycle_summary.get("priorCoverage") is None or float(lifecycle_summary["priorCoverage"]) < 0.70:
        gate_reasons.append("special strict-past prior coverage < 70%")
    decision = "PROCEED_FROZEN_SPECIAL_STRESS" if not gate_reasons else "STOP_SPECIAL_COHORT_INSUFFICIENT"

    report = {
        "version": VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "specialFit": False,
        "specialStart": args.special_start,
        "specialStartMs": special_start_ms,
        "sources": {
            "makerDb": str(args.maker_db.expanduser().resolve()),
            "signalDb": str(args.signal_db.expanduser().resolve()),
        },
        "directRebuild": {
            "meta": direct_meta,
            "behaviorSpecialRows": len(behavior_special),
            "behaviorSpecialMarkets": len(behavior_special_markets),
            "behaviorSpecialCleanMarkets": len(clean_special_markets),
            "missingStrictPrePlacementSnapshotAll": direct_behavior_meta.get("missingStrictPrePlacementSnapshot"),
        },
        "cleanLifecycle": lifecycle_meta,
        "specialLifecycleSummary": lifecycle_summary,
        "specialCleanTargetRate": clean_rate,
        "decisionGate": {
            "decision": decision,
            "reasons": gate_reasons,
            "rule": "special >=500 behavior rows, >=15 markets, clean-target rate >=40%, strict-past prior coverage >=70%",
            "next": "train on ordinary only and evaluate frozen PUBLIC/LAST_MAKER/LIFECYCLE on special; never fit special",
        },
        "elapsedSeconds": time.perf_counter() - started,
    }
    path = args.report.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    print(f"[{datetime.now():%H:%M:%S}] STOP GATE {decision} elapsed={time.perf_counter() - started:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
