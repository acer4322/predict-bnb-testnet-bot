from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_dataset import DEFAULT_MAKER_DB
from predict_bot.target_maker_ebm_v3_dataset import DEFAULT_OUTPUT as DEFAULT_MAKER_DATASET
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB
from predict_bot.target_maker_taker_state_link_v3 import (
    DEFAULT_META_OUTPUT,
    DEFAULT_OUTPUT,
    build_state_link_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build the timestamp-safe Target Maker/Taker State Link V3 dataset using high-resolution "
            "8778 Maker fill anchors and second-bucket Target Taker labels."
        )
    )
    parser.add_argument("--maker-dataset", type=Path, default=DEFAULT_MAKER_DATASET)
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    args = parser.parse_args()

    report = build_state_link_dataset(
        maker_dataset_path=args.maker_dataset,
        maker_db_path=args.maker_db,
        shadow_db_path=args.shadow_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
