from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_direct_placement_v1 import (
    DEFAULT_BEHAVIOR_OUTPUT,
    DEFAULT_HAZARD_OUTPUT,
    DEFAULT_MAKER_DB,
    DEFAULT_META_OUTPUT,
    DEFAULT_SIGNAL_DB,
    MAX_SIGNAL_AGE_MS,
    MIN_FILL_COVERAGE,
    MIN_PARENT_CONFIDENCE,
    MIN_PLACEMENT_COVERAGE,
    build_datasets,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build strict-pre-inferred-placement Target Maker direct EBM datasets")
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--hazard-output", type=Path, default=DEFAULT_HAZARD_OUTPUT)
    parser.add_argument("--behavior-output", type=Path, default=DEFAULT_BEHAVIOR_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    parser.add_argument("--max-signal-age-ms", type=int, default=MAX_SIGNAL_AGE_MS)
    parser.add_argument("--min-parent-confidence", type=float, default=MIN_PARENT_CONFIDENCE)
    parser.add_argument("--min-placement-coverage", type=float, default=MIN_PLACEMENT_COVERAGE)
    parser.add_argument("--min-fill-coverage", type=float, default=MIN_FILL_COVERAGE)
    args = parser.parse_args()
    report = build_datasets(
        maker_db_path=args.maker_db,
        signal_db_path=args.signal_db,
        hazard_output_path=args.hazard_output,
        behavior_output_path=args.behavior_output,
        meta_output_path=args.meta_output,
        max_signal_age_ms=max(250, int(args.max_signal_age_ms)),
        min_parent_confidence=max(0.0, min(1.0, float(args.min_parent_confidence))),
        min_placement_coverage=max(0.0, min(1.0, float(args.min_placement_coverage))),
        min_fill_coverage=max(0.0, min(1.0, float(args.min_fill_coverage))),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
