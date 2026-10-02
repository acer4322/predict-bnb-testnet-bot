from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_dataset import DEFAULT_SIGNAL_DB
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB
from predict_bot.target_taker_direct_eligibility_special_regime_v1 import (
    DEFAULT_META_OUTPUT,
    DEFAULT_OUTPUT,
    build_target_taker_direct_eligibility_special_regime_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1. "
            "One public decision row per market-second; labels are future Target Taker "
            "occurrence within 1/2/5s with same-second target events forbidden."
        )
    )
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    args = parser.parse_args()

    report = build_target_taker_direct_eligibility_special_regime_dataset(
        shadow_db_path=args.shadow_db,
        signal_db_path=args.signal_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
