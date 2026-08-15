from __future__ import annotations

import os

from .target_taker_live_execution_v1 import (
    BINANCE_ACCOUNT_TYPE_ENV,
    BINANCE_WALLET_ADDRESS_ENV,
    BINANCE_WALLET_ID_ENV,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V1TargetTakerLiveExecutor,
)


class TargetTakerLiveExecutor(_V1TargetTakerLiveExecutor):
    """V1 executor with Binance accountType/fundingSource kept distinct.

    Binance Prediction Trading uses accountType=SPOT|FUNDING while MPC is the
    fundingSource passed separately to get-quote/place-order-bundle. Keeping
    this correction in a tiny compatibility layer avoids destabilizing the
    already-reviewed venue logic in V1.
    """

    def _binance_wallet(self) -> tuple[str, str, str]:
        address = str(os.environ.get(BINANCE_WALLET_ADDRESS_ENV) or "").strip()
        wallet_id = str(os.environ.get(BINANCE_WALLET_ID_ENV) or "").strip()
        account_type = str(os.environ.get(BINANCE_ACCOUNT_TYPE_ENV) or "SPOT").strip().upper()
        if not address or not wallet_id:
            raise TargetTakerLiveError(
                f"Binance Target Taker requires {BINANCE_WALLET_ADDRESS_ENV} and {BINANCE_WALLET_ID_ENV}; "
                "wallet selection is intentionally never guessed for real-money orders"
            )
        if account_type not in {"SPOT", "FUNDING"}:
            raise TargetTakerLiveError(
                f"{BINANCE_ACCOUNT_TYPE_ENV} must be SPOT or FUNDING; MPC is a fundingSource, not accountType"
            )
        return address, wallet_id, account_type
