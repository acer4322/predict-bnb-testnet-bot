from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_taker_reentry_v1_dataset import (
    DEFAULT_LEGACY_DB,
    DEFAULT_META_OUTPUT,
    DEFAULT_MIN_SNAPSHOT_STEP_MS,
    DEFAULT_OFFICIAL_DB,
    DEFAULT_OUTPUT,
    DEFAULT_SIGNAL_DB,
    build_target_taker_reentry_v1_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build the strict-causal Target Taker REENTRY_V1 risk-set dataset."
    )
    parser.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_DB)
    parser.add_argument("--legacy-db", type=Path, default=DEFAULT_LEGACY_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    parser.add_argument(
        "--min-snapshot-step-ms",
        type=int,
        default=DEFAULT_MIN_SNAPSHOT_STEP_MS,
        help="Downsample public risk-set observations to at least this spacing (default: 250ms).",
    )
    args = parser.parse_args()
    report = build_target_taker_reentry_v1_dataset(
        official_db_path=args.official_db,
        legacy_db_path=args.legacy_db,
        signal_db_path=args.signal_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
        min_snapshot_step_ms=max(0, int(args.min_snapshot_step_ms)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
