from __future__ import annotations

"""BTC Maker inference bootstrap.

The V2.1 inference implementation is preserved verbatim in the sibling _impl
module.  Set the target truth source before importing it so 8778 consumes the
new TARGET_WALLET_OFFICIAL_V1 ledger instead of the retired Wallet Shadow DB.
"""

import os
from pathlib import Path

root = Path(__file__).resolve().parents[2]
os.environ["PREDICT_WALLET_SHADOW_DB"] = os.environ.get(
    "PREDICT_TARGET_WALLET_OFFICIAL_DB",
    str(root / "data" / "target_wallet_official_v1.db"),
)

from .predict_wallet_maker_book_inference_collector_v2_1_impl import *  # noqa: E402,F401,F403
from .predict_wallet_maker_book_inference_collector_v2_1_impl import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
