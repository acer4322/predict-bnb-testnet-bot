from __future__ import annotations

import os

from .target_taker_live_execution_v1 import (
    BINANCE_ACCOUNT_TYPE_ENV,
    BINANCE_WALLET_ADDRESS_ENV,
    BINANCE_WALLET_ID_ENV,
    TargetTakerLiveError,
    TargetTakerLiveExecutor as _V1TargetTakerLiveExecutor,
    _market_id,
)


class TargetTakerLiveExecutor(_V1TargetTakerLiveExecutor):
    """Harden V1 venue metadata without changing the frozen EBM signal path.

    Binance Prediction Trading uses accountType=SPOT|FUNDING while MPC is the
    fundingSource passed separately to get-quote/place-order-bundle. Predict.fun
    is resolved by the exact signal market ID instead of scanning a bounded
    market list before any real-money order is signed.
    """

    def _predict_market(self, client, market_id: int) -> dict:
        payload = client._request("GET", f"/v1/markets/{int(market_id)}")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        if not isinstance(data, dict) or _market_id(data) != int(market_id):
            raise TargetTakerLiveError(
                f"Predict.fun exact market lookup did not return market {int(market_id)}"
            )
        return data

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
