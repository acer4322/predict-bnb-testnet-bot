from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BEHAVIOR = ROOT / "data" / "research" / "target_maker_direct_behavior_v1.csv"
DEFAULT_MAKER_DB = ROOT / "data" / "wallet_maker_book_inference.db"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_lifecycle_side_v2_3.csv"
DEFAULT_META = ROOT / "data" / "research" / "target_maker_lifecycle_side_v2_3.meta.json"
DATASET_VERSION = "TARGET_MAKER_LIFECYCLE_SIDE_V2_3_CLEAN_TARGET_STRICT_PAST"

MIN_PARENT_CONFIDENCE = 0.70
MIN_PLACEMENT_COVERAGE = 0.80
MIN_FILL_COVERAGE = 0.80

LIFECYCLE_FEATURES = [
    "prior_maker_parent_count",
    "prior_maker_up_parent_count",
    "prior_maker_down_parent_count",
    "prior_maker_count_delta",
    "prior_maker_count_abs_delta",
    "prior_maker_up_fraction",
    "last_maker_side_up",
    "previous_maker_side_up",
    "last_maker_age_ms",
    "last_two_maker_same",
    "prior_maker_same_side_run_length",
]


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    number = _num(value)
    return int(number) if number is not None else None


def _read_behavior(path: Path) -> tuple[list[str], list[dict[str, Any]]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    required = {"parent_id", "market_id", "target_side", "placement_first_ms", "first_fill_ms", "label_side_up"}
    missing = sorted(required - set(fields))
    if missing:
        raise RuntimeError("behavior dataset missing columns: " + ", ".join(missing))
    return fields, rows


def _load_prior_parents(path: Path) -> list[dict[str, Any]]:
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
            """SELECT parent_id,market_id,target_side,first_target_ms,
                      confidence,placement_coverage,fill_allocation_coverage
                 FROM maker_book_inference_v21_parent_lifecycles
                WHERE first_target_ms IS NOT NULL
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
            "event_ms": int(raw["first_target_ms"]),
        })
    return output


def _write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: row.get(column, "") for column in columns})
    temp.replace(resolved)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _features(active: list[dict[str, Any]], placement_ms: int) -> dict[str, Any]:
    if not active:
        return {feature: "" for feature in LIFECYCLE_FEATURES}
    up = sum(int(item["side"] == "UP") for item in active)
    down = len(active) - up
    last = active[-1]
    previous = active[-2] if len(active) >= 2 else None
    run = 1
    for index in range(len(active) - 2, -1, -1):
        if active[index]["side"] != last["side"]:
            break
        run += 1
    total = len(active)
    return {
        "prior_maker_parent_count": total,
        "prior_maker_up_parent_count": up,
        "prior_maker_down_parent_count": down,
        "prior_maker_count_delta": up - down,
        "prior_maker_count_abs_delta": abs(up - down),
        "prior_maker_up_fraction": up / total if total else "",
        "last_maker_side_up": int(last["side"] == "UP"),
        "previous_maker_side_up": int(previous["side"] == "UP") if previous is not None else "",
        "last_maker_age_ms": int(placement_ms) - int(last["event_ms"]),
        "last_two_maker_same": int(previous is not None and previous["side"] == last["side"]),
        "prior_maker_same_side_run_length": run,
    }


def build_dataset(
    *,
    behavior_path: Path = DEFAULT_BEHAVIOR,
    maker_db_path: Path = DEFAULT_MAKER_DB,
    output_path: Path = DEFAULT_OUTPUT,
    meta_path: Path = DEFAULT_META,
) -> dict[str, Any]:
    fields, behavior = _read_behavior(behavior_path)
    priors = _load_prior_parents(maker_db_path)

    clean_targets: list[dict[str, Any]] = []
    dirty_targets = 0
    for row in behavior:
        placement_ms = _int(row.get("placement_first_ms"))
        fill_ms = _int(row.get("first_fill_ms"))
        market_id = _int(row.get("market_id"))
        if placement_ms is None or fill_ms is None or market_id is None:
            continue
        if fill_ms < placement_ms:
            dirty_targets += 1
            continue
        clean = dict(row)
        clean["market_id"] = market_id
        clean["placement_first_ms"] = placement_ms
        clean["first_fill_ms"] = fill_ms
        clean_targets.append(clean)

    prior_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in priors:
        prior_by_market[int(row["market_id"])].append(row)
    for values in prior_by_market.values():
        values.sort(key=lambda item: (int(item["event_ms"]), str(item["parent_id"])))

    targets_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in clean_targets:
        targets_by_market[int(row["market_id"])].append(row)
    for values in targets_by_market.values():
        values.sort(key=lambda item: (int(item["placement_first_ms"]), str(item.get("parent_id") or "")))

    output: list[dict[str, Any]] = []
    rows_with_prior = 0
    for market_id, targets in targets_by_market.items():
        market_priors = prior_by_market.get(market_id, [])
        cursor = 0
        active: list[dict[str, Any]] = []
        for target in targets:
            placement_ms = int(target["placement_first_ms"])
            current_id = str(target.get("parent_id") or "")
            while cursor < len(market_priors) and int(market_priors[cursor]["event_ms"]) < placement_ms:
                item = market_priors[cursor]
                cursor += 1
                if str(item.get("parent_id") or "") != current_id:
                    active.append(item)
            visible = [item for item in active if str(item.get("parent_id") or "") != current_id]
            if len(visible) != len(active):
                active = visible
            enriched = dict(target)
            enriched.update(_features(active, placement_ms))
            enriched["lifecycle_dataset_version"] = DATASET_VERSION
            enriched["clean_target"] = 1
            enriched["strict_past_prior_count"] = len(active)
            rows_with_prior += int(bool(active))
            output.append(enriched)

    output.sort(key=lambda row: (int(row["placement_first_ms"]), int(row["market_id"]), str(row.get("parent_id") or "")))
    columns = list(fields)
    for column in ["lifecycle_dataset_version", "clean_target", "strict_past_prior_count"] + LIFECYCLE_FEATURES:
        if column not in columns:
            columns.append(column)
    _write_csv(output_path, columns, output)

    meta = {
        "datasetVersion": DATASET_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "behaviorInput": str(behavior_path.expanduser().resolve()),
        "makerDb": str(maker_db_path.expanduser().resolve()),
        "output": str(output_path.expanduser().resolve()),
        "inputBehaviorRows": len(behavior),
        "cleanTargetRows": len(output),
        "dirtyTargetRowsExcluded": dirty_targets,
        "cleanTargetRate": len(output) / len(behavior) if behavior else None,
        "markets": len({int(row["market_id"]) for row in output}),
        "rowsWithPrior": rows_with_prior,
        "priorCoverage": rows_with_prior / len(output) if output else None,
        "eligiblePriorParents": len(priors),
        "eligiblePriorMarkets": len({int(row["market_id"]) for row in priors}),
        "thresholds": {
            "minParentConfidence": MIN_PARENT_CONFIDENCE,
            "minPlacementCoverage": MIN_PLACEMENT_COVERAGE,
            "minFillAllocationCoverage": MIN_FILL_COVERAGE,
        },
        "lifecycleFeatures": LIFECYCLE_FEATURES,
        "targetRule": "first_fill_ms >= placement_first_ms",
        "priorBoundary": "only eligible Maker parent first_target_ms strictly earlier than current placement_first_ms; current parent id excluded",
    }
    _write_json(meta_path, meta)
    print(json.dumps(meta, ensure_ascii=False, indent=2), flush=True)
    return meta


def main() -> int:
    parser = argparse.ArgumentParser(description="Build clean strict-past Maker lifecycle-side V2.3 dataset")
    parser.add_argument("--behavior", type=Path, default=DEFAULT_BEHAVIOR)
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta", type=Path, default=DEFAULT_META)
    args = parser.parse_args()
    build_dataset(
        behavior_path=args.behavior,
        maker_db_path=args.maker_db,
        output_path=args.output,
        meta_path=args.meta,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
