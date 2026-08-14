from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_dataset import DEFAULT_SIGNAL_DB
from predict_bot.target_maker_taker_link import DEFAULT_SHADOW_DB
from predict_bot.target_taker_behavior_v1 import (
    DEFAULT_META_OUTPUT,
    DEFAULT_OUTPUT,
    MAX_SIGNAL_AGE_MS,
    build_target_taker_behavior_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build a timestamp-safe direct Target Taker dataset. Taker side/size features are taken "
            "strictly before the reported event-second bucket to prevent same-second leakage."
        )
    )
    parser.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    parser.add_argument("--max-signal-age-ms", type=int, default=MAX_SIGNAL_AGE_MS)
    args = parser.parse_args()
    report = build_target_taker_behavior_dataset(
        shadow_db_path=args.shadow_db,
        signal_db_path=args.signal_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
        max_signal_age_ms=max(250, int(args.max_signal_age_ms)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
