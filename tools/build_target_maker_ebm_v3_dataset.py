from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_maker_ebm_dataset import DEFAULT_MAKER_DB, DEFAULT_OUTPUT as DEFAULT_V1_OUTPUT, DEFAULT_SIGNAL_DB
from predict_bot.target_maker_ebm_v3_dataset import DEFAULT_META_OUTPUT, DEFAULT_OUTPUT, build_v3_dataset


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Augment the target Maker EBM dataset with strict historical trajectories, fill paths, signed reprice labels, and delay labels."
    )
    parser.add_argument("--v1-dataset", type=Path, default=DEFAULT_V1_OUTPUT)
    parser.add_argument("--maker-db", type=Path, default=DEFAULT_MAKER_DB)
    parser.add_argument("--signal-db", type=Path, default=DEFAULT_SIGNAL_DB)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    parser.add_argument("--trajectory-snapshot-max-age-ms", type=int, default=600)
    args = parser.parse_args()

    meta = build_v3_dataset(
        v1_dataset_path=args.v1_dataset,
        maker_db_path=args.maker_db,
        signal_db_path=args.signal_db,
        output_path=args.output,
        meta_output_path=args.meta_output,
        trajectory_snapshot_max_age_ms=max(0, args.trajectory_snapshot_max_age_ms),
    )
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
