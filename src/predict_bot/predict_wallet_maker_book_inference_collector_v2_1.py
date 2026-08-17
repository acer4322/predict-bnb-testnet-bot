from __future__ import annotations

"""BTC Maker inference bootstrap.

The V2.1 inference implementation is preserved verbatim in the sibling _impl
module. Lock the BTC service identity before importing it so 8778 cannot inherit
stale ETH/generic Maker-book environment values from the dashboard process.
Target truth still comes from the shared read-only TARGET_WALLET_OFFICIAL_V1
ledger; only BTC market ids selected by this process are admitted downstream.
"""

import os
from pathlib import Path

root = Path(__file__).resolve().parents[2]

# Hard service boundary: 8778 is always BTC and always writes its own BTC DB.
# Use the BTC-specific override only; never reuse the generic DB/asset/port vars
# because those can be present in a long-lived Windows/dashboard environment.
os.environ["PREDICT_WALLET_MAKER_BOOK_ASSET"] = "BTC"
os.environ["PREDICT_WALLET_MAKER_BOOK_PORT"] = "8778"
os.environ["PREDICT_WALLET_MAKER_BOOK_DB"] = os.environ.get(
    "PREDICT_WALLET_MAKER_BOOK_BTC5M_DB",
    str(root / "data" / "wallet_maker_book_inference.db"),
)
os.environ["PREDICT_WALLET_SHADOW_DB"] = os.environ.get(
    "PREDICT_TARGET_WALLET_OFFICIAL_DB",
    str(root / "data" / "target_wallet_official_v1.db"),
)

from .predict_wallet_maker_book_inference_collector_v2_1_impl import *  # noqa: E402,F401,F403
from .predict_wallet_maker_book_inference_collector_v2_1_responsive import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
