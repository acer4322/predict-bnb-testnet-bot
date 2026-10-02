from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_v3_dataset import DEFAULT_OUTPUT as DEFAULT_MAKER_DATASET
from predict_bot.target_maker_taker_link import DEFAULT_REPORT, DEFAULT_SHADOW_DB, analyze_link


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Measure observational timing/side/inventory associations between target Maker parent lifecycles and target Taker parents."
    )
    parser.add_argument("--maker-dataset", type=Path, default=DEFAULT_MAKER_DATASET)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--min-regime-rows", type=int, default=30)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()

    report = analyze_link(
        maker_dataset_path=args.maker_dataset,
        shadow_db_path=args.shadow_db,
        report_path=args.report,
        min_regime_rows=max(10, args.min_regime_rows),
        bootstrap_samples=max(100, args.bootstrap_samples),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
