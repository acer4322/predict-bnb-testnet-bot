# Echtgeld Engine V2 — 4310 balance + PnL compatibility

## Binance balance

The old live-control/XPAIR implementation used the signed Binance Prediction
`balance/payment-options` response and discovered the funded Prediction source
under `accountType: CeDeFi` while SPOT/FUNDING could be zero.

V2 restores that **monitoring/display** path:

- parse enabled `availableBalanceDisplay` rows;
- prefer `CeDeFi` unless `PREDICT_ECHTGELD_BINANCE_BALANCE_ACCOUNT_TYPE` selects another enabled type;
- sum all enabled rows matching the selected account type, matching the legacy preflight behavior;
- expose the selected amount as `balance.availableUsdt` with source
  `binance_prediction.payment-options`.

The hardened V4 MPC wallet check remains separate. V2 also exposes
`mpcWalletAvailableUsdt` from the exact configured `wallet/list` wallet plus BSC
USDT `balanceOf`. Restoring the 4310 display therefore does **not** weaken or
replace the current live execution preflight.

## PnL

V2 mirrors official Target Taker settlement winners from the persistent Wallet
Shadow DB table `wallet_target_taker_public_side_v1_results` into the Echtgeld
DB. Settlement synchronization is observation-only and cannot submit/retry an
order.

Only Echtgeld rows with:

- `status == SUBMITTED`;
- official winner `UP` or `DOWN`;
- positive reconciled `submitted_usdt`;
- positive reconciled `shares`;

are counted as settled live PnL.

For each counted order:

```text
payout = shares, if side == winner, otherwise 0
PnL    = payout - submitted_usdt
ROI    = PnL / submitted_usdt
```

The monitor reports attempts, submitted/rejected/ambiguous, W/L, win rate,
settled stake, net PnL, net ROI, max drawdown, current loss streak and longest
loss streak. This is tracked strategy accounting, not a Binance account
statement.
