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

from .predict_wallet_maker_book_inference_collector import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
