from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_eth_taker_behavior_v1 import DEFAULT_ETH_TARGET_DB, DEFAULT_PREDICT_DB
from predict_bot.target_eth_taker_hazard_v1 import (
    DEFAULT_META_OUTPUT,
    DEFAULT_OUTPUT,
    build_target_eth_taker_hazard_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a uniform one-second ETH Taker hazard dataset. Every covered ETH market second is a decision "
            "point and labels ask whether a Target Taker parent begins in the next 1/2/5 full seconds."
        )
    )
    parser.add_argument("--target-db", type=Path, default=DEFAULT_ETH_TARGET_DB)
    parser.add_argument("--predict-db", type=Path, default=DEFAULT_PREDICT_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    args = parser.parse_args()
    report = build_target_eth_taker_hazard_dataset(
        target_db_path=args.target_db,
        predict_db_path=args.predict_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
