from __future__ import annotations

import argparse
import json
from pathlib import Path

from predict_bot.target_eth_taker_behavior_v1 import (
    DEFAULT_ETH_SIGNAL_DB,
    DEFAULT_ETH_TARGET_DB,
    DEFAULT_META_OUTPUT,
    DEFAULT_OUTPUT,
    DEFAULT_PREDICT_DB,
    MAX_SIGNAL_AGE_MS,
    build_target_eth_taker_dataset,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build strict-pre-event Target ETH Taker side/size research data from the isolated 8779 ETH wallet "
            "events. Historical mode uses 8771 ETH Predict.fun trajectory; enriched mode uses forward 8780 "
            "ETH spot/futures microstructure while still excluding the entire target event second."
        )
    )
    parser.add_argument("--target-db", type=Path, default=DEFAULT_ETH_TARGET_DB)
    parser.add_argument("--predict-db", type=Path, default=DEFAULT_PREDICT_DB)
    parser.add_argument("--eth-signal-db", type=Path, default=DEFAULT_ETH_SIGNAL_DB)
    parser.add_argument("--signal-source", choices=("predict", "enriched"), default="predict")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META_OUTPUT)
    parser.add_argument("--max-signal-age-ms", type=int, default=MAX_SIGNAL_AGE_MS)
    args = parser.parse_args()
    report = build_target_eth_taker_dataset(
        target_db_path=args.target_db,
        predict_db_path=args.predict_db,
        eth_signal_db_path=args.eth_signal_db,
        signal_source=args.signal_source,
        output_path=args.output,
        meta_output_path=args.meta_output,
        max_signal_age_ms=max(500, int(args.max_signal_age_ms)),
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
