from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_dataset import DEFAULT_MAKER_DB
from predict_bot.target_maker_ebm_v3_dataset import DEFAULT_OUTPUT as DEFAULT_MAKER_DATASET
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB
from predict_bot.target_maker_taker_link_v2 import DEFAULT_REPORT, analyze_link_v2


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Analyze target Maker/Taker timing with second-bucket-aware Taker timestamps and high-resolution 8778 Maker fill evidence."
        )
    )
    parser.add_argument("--maker-dataset", type=Path, default=DEFAULT_MAKER_DATASET)
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--bootstrap-samples", type=int, default=2000)
    args = parser.parse_args()

    report = analyze_link_v2(
        maker_dataset_path=args.maker_dataset,
        maker_db_path=args.maker_db,
        shadow_db_path=args.shadow_db,
        report_path=args.report,
        bootstrap_samples=max(100, int(args.bootstrap_samples)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
