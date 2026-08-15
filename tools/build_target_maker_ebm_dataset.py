from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_dataset import (
    DEFAULT_MAKER_DB,
    DEFAULT_META_OUTPUT,
    DEFAULT_OUTPUT,
    DEFAULT_SIGNAL_DB,
    build_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build an offline EBM training table from 8778 target Maker parent lifecycles joined to strict as-of 8777 signals."
    )
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    parser.add_argument("--max-signal-age-ms", type=int, default=2000)
    parser.add_argument("--min-parent-confidence", type=float, default=0.70)
    parser.add_argument("--min-placement-coverage", type=float, default=0.80)
    parser.add_argument("--min-fill-allocation-coverage", type=float, default=0.80)
    args = parser.parse_args()

    meta = build_dataset(
        maker_db_path=args.maker_db,
        signal_db_path=args.signal_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
        max_signal_age_ms=max(0, args.max_signal_age_ms),
        min_parent_confidence=args.min_parent_confidence,
        min_placement_coverage=args.min_placement_coverage,
        min_fill_allocation_coverage=args.min_fill_allocation_coverage,
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
