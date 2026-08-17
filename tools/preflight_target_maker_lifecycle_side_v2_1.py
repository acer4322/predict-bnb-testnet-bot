from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_MAKER_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_lifecycle_side_preflight_v2_1.json"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"

MIN_PARENT_CONFIDENCE = 0.70
MIN_PLACEMENT_COVERAGE = 0.80
MIN_FILL_COVERAGE = 0.80


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _epoch_ms(value: str) -> int:
    return int(datetime.fromisoformat(value).timestamp() * 1000)


def _quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"p10": None, "median": None, "p90": None}
    ordered = sorted(float(v) for v in values)

    def pick(q: float) -> float:
        return ordered[min(len(ordered) - 1, max(0, int(round((len(ordered) - 1) * q))))]

    return {"p10": pick(0.10), "median": float(statistics.median(ordered)), "p90": pick(0.90)}


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _read_behavior(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        rows = [dict(row) for row in csv.DictReader(handle)]
    required = {"parent_id", "market_id", "target_side", "placement_first_ms", "first_fill_ms"}
    fields = set(rows[0]) if rows else set()
    missing = sorted(required - fields)
    if missing:
        raise RuntimeError("behavior dataset missing columns: " + ", ".join(missing))
    output: list[dict[str, Any]] = []
    for row in rows:
        placement_ms = _int(row.get("placement_first_ms"))
        first_fill_ms = _int(row.get("first_fill_ms"))
        market_id = _int(row.get("market_id"))
        side = str(row.get("target_side") or "").upper()
        if placement_ms is None or market_id is None or side not in {"UP", "DOWN"}:
            continue
        output.append({
            "parent_id": str(row.get("parent_id") or ""),
            "market_id": market_id,
            "side": side,
            "placement_ms": placement_ms,
            "event_ms": first_fill_ms,
        })
    return output


def _load_db_eligible_prior_rows(path: Path) -> list[dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    try:
        table = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='maker_book_inference_v21_parent_lifecycles'"
        ).fetchone()
        if table is None:
            raise RuntimeError("maker_book_inference_v21_parent_lifecycles is missing")
        rows = db.execute(
            """SELECT parent_id,market_id,target_side,placement_first_ms,first_target_ms,
                      confidence,placement_coverage,fill_allocation_coverage
                 FROM maker_book_inference_v21_parent_lifecycles
                WHERE placement_first_ms IS NOT NULL
                  AND first_target_ms IS NOT NULL
                  AND confidence>=?
                  AND placement_coverage>=?
                  AND fill_allocation_coverage>=?
                ORDER BY market_id,first_target_ms,parent_id""",
            (MIN_PARENT_CONFIDENCE, MIN_PLACEMENT_COVERAGE, MIN_FILL_COVERAGE),
        ).fetchall()
    finally:
        db.close()
    output: list[dict[str, Any]] = []
    for raw in rows:
        side = str(raw["target_side"] or "").upper()
        if side not in {"UP", "DOWN"}:
            continue
        output.append({
            "parent_id": str(raw["parent_id"] or ""),
            "market_id": int(raw["market_id"]),
            "side": side,
            "placement_ms": int(raw["placement_first_ms"]),
            "event_ms": int(raw["first_target_ms"]),
        })
    return output


def _market_blocked_rate(records: list[dict[str, Any]], key: str) -> float | None:
    by_market: dict[int, list[int]] = defaultdict(list)
    for row in records:
        value = row.get(key)
        if value is None:
            continue
        by_market[int(row["market_id"])].append(int(bool(value)))
    if not by_market:
        return None
    market_rates = [sum(values) / len(values) for values in by_market.values() if values]
    return sum(market_rates) / len(market_rates) if market_rates else None


def _analyze(target_rows: list[dict[str, Any]], prior_rows: list[dict[str, Any]]) -> dict[str, Any]:
    prior_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in prior_rows:
        if row.get("event_ms") is None:
            continue
        prior_by_market[int(row["market_id"])].append(row)
    for values in prior_by_market.values():
        values.sort(key=lambda r: (int(r["event_ms"]), str(r["parent_id"])))

    targets_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in target_rows:
        targets_by_market[int(row["market_id"])].append(row)
    for values in targets_by_market.values():
        values.sort(key=lambda r: (int(r["placement_ms"]), str(r["parent_id"])))

    records: list[dict[str, Any]] = []
    own_fill_before_placement = 0
    for market_id, targets in targets_by_market.items():
        priors = prior_by_market.get(market_id, [])
        cursor = 0
        active: list[dict[str, Any]] = []
        up_count = 0
        down_count = 0
        for target in targets:
            placement_ms = int(target["placement_ms"])
            if target.get("event_ms") is not None and int(target["event_ms"]) < placement_ms:
                own_fill_before_placement += 1
            while cursor < len(priors) and int(priors[cursor]["event_ms"]) < placement_ms:
                item = priors[cursor]
                cursor += 1
                if str(item.get("parent_id") or "") == str(target.get("parent_id") or ""):
                    continue
                active.append(item)
                if item["side"] == "UP":
                    up_count += 1
                else:
                    down_count += 1
            # Exclude the current parent if it somehow entered active earlier due to anomalous timestamps.
            current_id = str(target.get("parent_id") or "")
            visible = [item for item in active if str(item.get("parent_id") or "") != current_id]
            if len(visible) != len(active):
                up_count = sum(int(item["side"] == "UP") for item in visible)
                down_count = sum(int(item["side"] == "DOWN") for item in visible)
                active = visible
            if not active:
                records.append({"market_id": market_id, "has_prior": False})
                continue
            last = max(active, key=lambda r: (int(r["event_ms"]), str(r["parent_id"])))
            same_as_last = target["side"] == last["side"]
            if up_count > down_count:
                toward_minority = target["side"] == "DOWN"
                delta_sign = "PRIOR_UP_GREATER"
            elif down_count > up_count:
                toward_minority = target["side"] == "UP"
                delta_sign = "PRIOR_DOWN_GREATER"
            else:
                toward_minority = None
                delta_sign = "PRIOR_TIE"
            records.append({
                "market_id": market_id,
                "has_prior": True,
                "prior_count": len(active),
                "prior_up": up_count,
                "prior_down": down_count,
                "last_side": last["side"],
                "next_side": target["side"],
                "next_up": int(target["side"] == "UP"),
                "same_as_last": same_as_last,
                "toward_minority": toward_minority,
                "delta_sign": delta_sign,
                "last_age_ms": placement_ms - int(last["event_ms"]),
            })

    with_prior = [row for row in records if row.get("has_prior")]
    transitions = {"UP->UP": 0, "UP->DOWN": 0, "DOWN->UP": 0, "DOWN->DOWN": 0}
    for row in with_prior:
        transitions[f"{row['last_side']}->{row['next_side']}"] += 1

    conditional: dict[str, dict[str, Any]] = {}
    for key in ("PRIOR_UP_GREATER", "PRIOR_TIE", "PRIOR_DOWN_GREATER"):
        subset = [row for row in with_prior if row["delta_sign"] == key]
        conditional[key] = {
            "rows": len(subset),
            "nextUpRate": _rate(sum(int(row["next_up"]) for row in subset), len(subset)),
        }
    up_heavy = conditional["PRIOR_UP_GREATER"]["nextUpRate"]
    down_heavy = conditional["PRIOR_DOWN_GREATER"]["nextUpRate"]
    rebalance_contrast = (
        float(down_heavy) - float(up_heavy)
        if up_heavy is not None and down_heavy is not None else None
    )
    minority_rows = [row for row in with_prior if row.get("toward_minority") is not None]
    same_count = sum(int(row["same_as_last"]) for row in with_prior)
    next_up_count = sum(int(row["next_up"]) for row in with_prior)
    prior_counts = [float(row["prior_count"]) for row in with_prior]
    ages = [float(row["last_age_ms"]) for row in with_prior]

    return {
        "rows": len(records),
        "markets": len(targets_by_market),
        "rowsWithPrior": len(with_prior),
        "priorCoverage": _rate(len(with_prior), len(records)),
        "rowsWithAtLeast": {
            "1": len(with_prior),
            "2": sum(int(row["prior_count"] >= 2) for row in with_prior),
            "3": sum(int(row["prior_count"] >= 3) for row in with_prior),
            "5": sum(int(row["prior_count"] >= 5) for row in with_prior),
        },
        "nextUpRateWithPrior": _rate(next_up_count, len(with_prior)),
        "sameAsLastRate": _rate(same_count, len(with_prior)),
        "marketBlockedSameAsLastRate": _market_blocked_rate(with_prior, "same_as_last"),
        "transitions": transitions,
        "towardMinoritySide": {
            "rows": len(minority_rows),
            "rate": _rate(sum(int(row["toward_minority"]) for row in minority_rows), len(minority_rows)),
            "marketBlockedRate": _market_blocked_rate(minority_rows, "toward_minority"),
        },
        "nextUpByPriorCountImbalance": conditional,
        "rebalanceContrast": rebalance_contrast,
        "priorCountQuantiles": _quantiles(prior_counts),
        "lastMakerFillAgeMsQuantiles": _quantiles(ages),
        "ownParentFillBeforeOwnPlacementRows": own_fill_before_placement,
        "strictPastBoundary": "Only prior parent first-fill events with event_ms < current placement_first_ms are admitted. Same/future events are excluded.",
    }


def _cohort_rows(rows: list[dict[str, Any]], special_start_ms: int) -> dict[str, list[dict[str, Any]]]:
    return {
        "ALL": rows,
        "ORDINARY_PRE_SPECIAL": [row for row in rows if int(row["placement_ms"]) < special_start_ms],
        "SPECIAL": [row for row in rows if int(row["placement_ms"]) >= special_start_ms],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Strict-past Maker lifecycle-side descriptive preflight")
    parser.add_argument("--behavior", type=Path, default=DEFAULT_BEHAVIOR)
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    args = parser.parse_args()

    behavior = _read_behavior(args.behavior)
    db_prior = _load_db_eligible_prior_rows(args.maker_db)
    special_start_ms = _epoch_ms(args.special_start)
    behavior_prior = [row for row in behavior if row.get("event_ms") is not None]
    cohorts = _cohort_rows(behavior, special_start_ms)

    analyses: dict[str, Any] = {}
    for cohort_name, target_rows in cohorts.items():
        analyses[cohort_name] = {
            "BEHAVIOR_COHORT_PRIOR": _analyze(target_rows, behavior_prior),
            "DB_ELIGIBLE_PRIOR": _analyze(target_rows, db_prior),
        }

    payload = {
        "version": "TARGET_MAKER_LIFECYCLE_SIDE_PREFLIGHT_V2_1_STRICT_PAST",
        "paperResearchOnly": True,
        "automaticModelTraining": False,
        "behaviorDataset": str(args.behavior.expanduser().resolve()),
        "makerDb": str(args.maker_db.expanduser().resolve()),
        "specialStart": args.special_start,
        "behaviorRows": len(behavior),
        "behaviorMarkets": len({int(row["market_id"]) for row in behavior}),
        "dbEligiblePriorParents": len(db_prior),
        "dbEligiblePriorMarkets": len({int(row["market_id"]) for row in db_prior}),
        "thresholds": {
            "minParentConfidence": MIN_PARENT_CONFIDENCE,
            "minPlacementCoverage": MIN_PLACEMENT_COVERAGE,
            "minFillAllocationCoverage": MIN_FILL_COVERAGE,
        },
        "analyses": analyses,
        "interpretationGuide": {
            "sameAsLastRate": "Deviation from 0.5 suggests short-memory persistence (>0.5) or alternation (<0.5).",
            "towardMinoritySideRate": "Above 0.5 suggests count-level rebalancing toward the less represented prior Maker side.",
            "rebalanceContrast": "nextUpRate(PRIOR_DOWN_GREATER) - nextUpRate(PRIOR_UP_GREATER); positive values support count-level rebalancing.",
            "nextStep": "Do not train EBM unless prior coverage is substantial and both prior-state sources show a consistent non-trivial directional effect.",
        },
    }

    resolved = args.output.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
