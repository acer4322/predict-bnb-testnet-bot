from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_taker_reentry_v1_analysis import (
    DEFAULT_DATASET,
    DEFAULT_REPORT,
    build_reentry_v1_walk_forward_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train/compare REENTRY_V1 5s same-side continuation and opposite-side hazard EBMs "
            "with whole-market walk-forward evaluation. Research only; no runtime wiring."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--min-train-markets", type=int, default=80)
    parser.add_argument("--min-calibration-markets", type=int, default=12)
    parser.add_argument("--calibration-fraction", type=float, default=0.15)
    parser.add_argument("--test-markets", type=int, default=40)
    parser.add_argument("--max-folds", type=int, default=3)
    parser.add_argument("--interactions", type=int, default=0)
    parser.add_argument("--max-rounds", type=int, default=500)
    parser.add_argument("--outer-bags", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--n-jobs", type=int, default=-2)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Laptop-friendly smoke analysis: 2 folds, 300 rounds, 2 outer bags unless explicitly lower.",
    )
    args = parser.parse_args()

    max_folds = args.max_folds
    max_rounds = args.max_rounds
    outer_bags = args.outer_bags
    if args.quick:
        max_folds = min(max_folds, 2)
        max_rounds = min(max_rounds, 300)
        outer_bags = min(outer_bags, 2)

    report = build_reentry_v1_walk_forward_report(
        dataset_path=args.dataset,
        report_path=args.report,
        min_train_markets=args.min_train_markets,
        min_calibration_markets=args.min_calibration_markets,
        calibration_fraction=args.calibration_fraction,
        test_markets=args.test_markets,
        max_folds=max_folds,
        interactions=args.interactions,
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=args.seed,
        n_jobs=args.n_jobs,
    )

    summary = {
        "reportVersion": report["reportVersion"],
        "datasetRows": report["datasetRows"],
        "reportPath": report.get("reportPath") or str(args.report),
        "tasks": {},
    }
    for task_name, task in report["tasks"].items():
        summary["tasks"][task_name] = {
            "status": task.get("status"),
            "cohort": task.get("cohort"),
            "comparison": task.get("comparison"),
            "aggregates": {
                family: details.get("aggregate")
                for family, details in (task.get("featureSets") or {}).items()
            },
        }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
