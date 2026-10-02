from __future__ import annotations

import argparse
import json
from pathlib import Path

import preflight_target_maker_lifecycle_side_v2_1 as v21

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_maker_lifecycle_side_preflight_v2_2.json"


def _write_json(path: Path, payload: dict) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _analyze_pair(target_rows: list[dict], behavior_prior: list[dict], db_prior: list[dict]) -> dict:
    return {
        "BEHAVIOR_COHORT_PRIOR": v21._analyze(target_rows, behavior_prior),
        "DB_ELIGIBLE_PRIOR": v21._analyze(target_rows, db_prior),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Strict-past Maker lifecycle-side V2.2 preflight with clean target timestamp gate"
    )
    parser.add_argument("--behavior", type=Path, default=v21.DEFAULT_BEHAVIOR)
    parser.add_argument("--maker-db", type=Path, default=v21.DEFAULT_MAKER_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--special-start", default=v21.DEFAULT_SPECIAL_START)
    args = parser.parse_args()

    behavior = v21._read_behavior(args.behavior)
    db_prior = v21._load_db_eligible_prior_rows(args.maker_db)
    behavior_prior = [row for row in behavior if row.get("event_ms") is not None]
    special_start_ms = v21._epoch_ms(args.special_start)

    ordinary = [row for row in behavior if int(row["placement_ms"]) < special_start_ms]
    special = [row for row in behavior if int(row["placement_ms"]) >= special_start_ms]

    clean = [
        row for row in behavior
        if row.get("event_ms") is not None and int(row["event_ms"]) >= int(row["placement_ms"])
    ]
    anomalous = [
        row for row in behavior
        if row.get("event_ms") is not None and int(row["event_ms"]) < int(row["placement_ms"])
    ]
    missing_fill = [row for row in behavior if row.get("event_ms") is None]

    clean_ordinary = [row for row in clean if int(row["placement_ms"]) < special_start_ms]
    clean_special = [row for row in clean if int(row["placement_ms"]) >= special_start_ms]

    payload = {
        "version": "TARGET_MAKER_LIFECYCLE_SIDE_PREFLIGHT_V2_2_CLEAN_TARGET_STRICT_PAST",
        "paperResearchOnly": True,
        "automaticModelTraining": False,
        "behaviorDataset": str(args.behavior.expanduser().resolve()),
        "makerDb": str(args.maker_db.expanduser().resolve()),
        "specialStart": args.special_start,
        "targetTimestampQuality": {
            "totalRows": len(behavior),
            "cleanRows": len(clean),
            "cleanRate": len(clean) / len(behavior) if behavior else None,
            "ownFillBeforeInferredPlacementRows": len(anomalous),
            "ownFillBeforeInferredPlacementRate": len(anomalous) / len(behavior) if behavior else None,
            "missingFirstFillRows": len(missing_fill),
            "cleanTargetRule": "current parent first_fill_ms must be >= current inferred placement_first_ms; rows violating this are excluded only as targets, not automatically removed from strict-past prior history",
            "reason": "avoid evaluating a placement-side decision at an inferred placement timestamp that occurs after the same parent fill timestamp",
        },
        "dbEligiblePriorParents": len(db_prior),
        "dbEligiblePriorMarkets": len({int(row["market_id"]) for row in db_prior}),
        "thresholds": {
            "minParentConfidence": v21.MIN_PARENT_CONFIDENCE,
            "minPlacementCoverage": v21.MIN_PLACEMENT_COVERAGE,
            "minFillAllocationCoverage": v21.MIN_FILL_COVERAGE,
        },
        "analyses": {
            "ORDINARY_ALL": _analyze_pair(ordinary, behavior_prior, db_prior),
            "ORDINARY_CLEAN_TARGET": _analyze_pair(clean_ordinary, behavior_prior, db_prior),
            "SPECIAL_ALL": _analyze_pair(special, behavior_prior, db_prior),
            "SPECIAL_CLEAN_TARGET": _analyze_pair(clean_special, behavior_prior, db_prior),
        },
        "decisionGate": {
            "proceedToQuickEbmIf": [
                "ORDINARY_CLEAN_TARGET rowsWithPrior remains substantial",
                "both prior sources keep sameAsLastRate materially above 0.5",
                "market-blocked same-side persistence agrees in direction",
            ],
            "doNotFitSpecial": True,
            "specialUse": "stress-test only after ordinary lifecycle-side model is established",
        },
    }

    _write_json(args.output, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
