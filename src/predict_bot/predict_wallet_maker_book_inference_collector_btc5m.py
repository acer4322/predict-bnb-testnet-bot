from __future__ import annotations

import os
from pathlib import Path


# Keep the mature V2.1 BTC inference implementation, but switch its retained
# target-fill source from the retired Wallet Shadow DB to the new 8776 official
# target ledger. The new ledger preserves the old target-event compatibility
# tables and seeds rowids after the legacy DB max so the forward cursor remains
# monotonic across the cutover.
root = Path(__file__).resolve().parents[2]
os.environ["PREDICT_WALLET_SHADOW_DB"] = os.environ.get(
    "PREDICT_TARGET_WALLET_OFFICIAL_DB",
    str(root / "data" / "target_wallet_official_v1.db"),
)

from .predict_wallet_maker_book_inference_collector_v2_1 import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
