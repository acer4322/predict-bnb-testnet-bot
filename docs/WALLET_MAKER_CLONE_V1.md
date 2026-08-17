# Wallet Maker Clone V1/V2

## Goal

`R_WALLET_MAKER_CLONE` reproduces the execution pattern observed from wallet
`0x9ddbdc8bdf41ec79cd4f5c4dec14050f4b591a18` without claiming that its private
directional signal has been recovered.

The first live experiment focuses on the repeatable parts already supported by
our evidence:

- participate in every ETH/BNB 5-minute market;
- maintain both UP and DOWN passive BUY orders;
- quote near Binance Prediction best Bid;
- size each leg by target potential profit `shares * (1 - price)`;
- use LIMIT/GTC orders and wait for fills;
- keep filled shares to resolution instead of auto-selling;
- observe partial fills, pair asymmetry and adverse selection separately.

## Services

| Asset | Port | SQLite |
|---|---:|---|
| ETH clone | 8774 | `data/wallet_maker_clone_eth.db` |
| BNB clone | 8775 | `data/wallet_maker_clone_bnb.db` |

`predict_bot.multi_asset_live_supervisor` starts both clone services alongside
8770/8772/8773. The V2 safety wrapper is the entry point.

## Safety defaults

Every clone process force-pauses `runtimeEnabled` on startup. Starting Dashboard
V2 therefore cannot submit clone orders by itself.

Before Resume, the clone checks the corresponding normal live service:

- ETH clone checks 8772;
- BNB clone checks 8773.

The check is fail-closed. If normal live is enabled, has an active round, or its
state cannot be verified, the clone is blocked. If a conflict appears while the
clone is running, new clone orders are paused and recorded resting clone orders
are cancelled.

A clone pair submits UP and DOWN concurrently, but the two Binance requests are
still independent. If only one leg is confirmed or placement becomes ambiguous,
the engine attempts to cancel every known submitted clone order and auto-pauses
for operator review.

## Soft post-only

The Binance Prediction LIMIT/GTC wrapper used by this project does not expose an
explicit post-only flag. Clone V1 therefore implements a soft post-only rule:

1. plan a BUY at current best Bid minus `bidOffsetTicks`;
2. require target price `< current Ask`;
3. request a signed LIMIT quote;
4. immediately fetch the direct token order book again;
5. require target price `< latest Ask` again;
6. only then place LIMIT/GTC.

This greatly reduces accidental taker execution but is not equivalent to an
exchange-enforced post-only order.

## Sizing

Default per side:

```text
targetPotentialProfitUsdt = 1.00
shares = targetPotentialProfitUsdt / (1 - price)
cost = shares * price
```

The cost is bounded by `minimumOrderUsdt` and `maximumOrderUsdt`. This is a clone
hypothesis derived from the target-wallet order clusters and remains an
experiment, not a proven private rule.

## Lifecycle persistence

The clone databases persist:

- market pair state and UP/DOWN placement skew;
- planned Bid, Ask, price, target-profit tier, cost and shares;
- signed quote ID/timestamps/RTT;
- Binance order ID and vendor order ID;
- maker amounts, filled amounts, shares and fill percentage from order status;
- cancel/reject/ambiguous states;
- recent lifecycle events.

Dashboard V2 exposes both ETH and BNB on `/wallet-clone`. Each asset shows UP and
DOWN simultaneously so one-sided fills are visible immediately.

## Cancellation

The clone uses Binance Prediction batch-cancel for recorded order IDs. The
endpoint has a bracket-key signing quirk, so the implementation signs and sends
the raw form body while preserving keys such as:

```text
cancelInfoList[0].orderId
```

Resting clone orders are cancelled on explicit Pause, market rollover, normal
live interlock, process shutdown, or incomplete pair submission when their order
IDs are known.

## V1 limitations

- `MINT` versus normal same-token fill is not inferred from Binance active-order
  state. The dashboard intentionally reports `UNKNOWN_UNTIL_MATCH_RECONCILIATION`.
- `autoRequote` defaults OFF. V1 may cancel a stale pair when enabled, but then
  self-disables auto-requote rather than creating repeated generations in the
  same market. Multi-generation quote management belongs in the next version.
- Filled shares are not auto-sold. Settlement/redeem and reward accounting remain
  separate from order-lifecycle observation.
- Do not run the normal ETH/BNB live strategy and the clone for the same asset at
  the same time.
