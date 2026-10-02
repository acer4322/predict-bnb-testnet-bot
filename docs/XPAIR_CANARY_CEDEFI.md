# XPAIR Canary CeDeFi Compatibility

The Prediction payment-options endpoint may expose the Prediction wallet as
`accountType: CeDeFi`, while SPOT and FUNDING both report zero. The original
canary only accepted SPOT or FUNDING, so it incorrectly rejected a funded
Prediction wallet during preflight.

This branch adds two compatibility entry points:

```powershell
python -m predict_bot.xpair_btc_eth_canary_cedefi --mode quote-only
python -m predict_bot.xpair_canary_dashboard_server_cedefi
```

The dashboard wrapper always routes the one-shot canary through the exact
`CeDeFi` account type. The dashboard form also exposes
`CeDeFi / Prediction Wallet` and selects it by default.

Preflight now checks only the selected payment source. It does not add balances
from unrelated account types, because the same account type is sent to the
place-order endpoint. Existing active-order and active-position checks remain
unchanged.

For the current 2.00 USDT pair budget and 0.10 USDT buffer, the required CeDeFi
available balance remains 2.10 USDT.
