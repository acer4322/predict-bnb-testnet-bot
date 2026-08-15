from __future__ import annotations

import os
from pathlib import Path


# Set the isolated cohort identity before importing the generic collector.
os.environ["PREDICT_WALLET_MAKER_BOOK_ASSET"] = "ETH"
os.environ["PREDICT_WALLET_MAKER_BOOK_PORT"] = "8779"
root = Path(__file__).resolve().parents[2]
os.environ["PREDICT_WALLET_MAKER_BOOK_DB"] = os.environ.get(
    "PREDICT_WALLET_MAKER_BOOK_ETH5M_DB",
    str(root / "data" / "wallet_maker_book_inference_eth5m.db"),
)
# The current ETH collector still polls target matches directly, but pin the
# retained target DB to the new official ledger as well so future shared-source
# logic cannot silently fall back to the retired Wallet Shadow database.
os.environ["PREDICT_WALLET_SHADOW_DB"] = os.environ.get(
    "PREDICT_TARGET_WALLET_OFFICIAL_DB",
    str(root / "data" / "target_wallet_official_v1.db"),
)

from .predict_wallet_maker_book_inference_collector import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
