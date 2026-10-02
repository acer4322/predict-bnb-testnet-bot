# XPAIR BTC/ETH Live Canary

`XPAIR_BTC_ETH_LIVE_CANARY` is an isolated, one-shot execution test for the
paper experiment in `xpair_btc_eth_paper.py`. It is meant to answer one narrow
question: can Binance accept, place, and fill both BTC/ETH legs with the same
share target under current live conditions?

It is not a production strategy and it does not prove profitability.

## Safety model

- Dry-run is the default.
- Quote-only obtains two signed LIMIT quotes but never places them.
- Live mode requires both a command-line unlock and an environment unlock.
- At most one aligned BTC/ETH round is attempted per process.
- At most one variant is selected per round.
- Both legs are quoted concurrently and then requoted to a common gross-share
  target before either order is placed.
- Final signed cost per share must remain at or below `--max-total-cost`.
- The two placements are concurrent but **not atomic**.
- Placement is never retried after an ambiguous or rejected response.
- The canary never cancels, chases, market-sells, or automatically unwinds an
  incomplete pair.
- Existing active Prediction orders or positions fail preflight. Stop the other
  live executor and reconcile the wallet before using quote-only or live mode.
- All records go to `data/xpair_btc_eth_canary.db`.

## Minimum funding

The default pair budget is **2.00 USDT total**, dynamically divided between the
BTC and ETH legs to target equal shares. The preflight requires an additional
**0.10 USDT balance buffer**, so the enabled Prediction balance must be at least:

```text
2.10 USDT available
```

For a first test, keep **3–5 USDT available** in the Prediction payment account.
This gives room for fee/rounding effects and temporary balance reservation.

The repository accepts configurable LIMIT stakes down to `0.01 USDT`, but that
is only a local software floor. Binance does not expose a stable public
Prediction-specific minimum in the documentation used by this project. The
quote-only step is therefore the authoritative minimum-size check for the
current wallet and market.

If 2.00 USDT quote-only fails because one leg is too small, do not unlock live
mode. Retry a later round in quote-only with:

```powershell
python -m predict_bot.xpair_btc_eth_canary `
  --mode quote-only `
  --pair-budget-usdt 3.00
```

The canary has a hard pair-budget ceiling of **3.00 USDT**.

## Selection rule

The default is:

```text
BTC_DOWN_ETH_UP
```

This is deliberate: in the first eligible paper sample it was the only arm with
positive observed ROI. The result is not assumed to be permanent. For execution
research only, the alternatives are:

```text
BTC_UP_ETH_DOWN
CHEAPEST_ELIGIBLE
```

## Recommended sequence

### 1. Stop the existing live executor

Do not run this canary while another branch or process can trade the same
Prediction wallet. The preflight also rejects existing active orders and active
positions.

### 2. Set credentials

The API key needs the same Prediction trading permissions as the existing live
engine.

```powershell
$env:BINANCE_API_KEY="your key"
$env:BINANCE_API_SECRET="your secret"
```

### 3. Dry-run

This reads market data, waits for the entry window, selects the variant, and
prints the modeled equal-share plan. It does not request a signed quote.

```powershell
python -m predict_bot.xpair_btc_eth_canary --mode dry-run
```

Expected terminal message:

```text
DRY_RUN_READY
```

### 4. Quote-only

This verifies wallet, quota, balance, clean-wallet state, both signed quotes,
common-share requoting, quote freshness, and final signed total cost. It does
not place an order.

```powershell
python -m predict_bot.xpair_btc_eth_canary `
  --mode quote-only `
  --pair-budget-usdt 2.00
```

Expected terminal message:

```text
QUOTE_ONLY_READY
```

Do not proceed when the result is `QUOTE_REJECTED`.

### 5. One live canary

Only after quote-only succeeds on the same configuration:

```powershell
$env:XPAIR_LIVE_CONFIRM="I_ACCEPT_NON_ATOMIC_TWO_LEG_RISK"

python -m predict_bot.xpair_btc_eth_canary `
  --mode live `
  --execute-live `
  --pair-budget-usdt 2.00
```

Live mode uses LIMIT GTC orders. After the command exits, inspect Binance active
orders, order history, and positions until both legs are terminal.

Possible terminal states:

- `FILLED_BOTH`: both orders were observed filled during the short reconciliation
  window.
- `SUBMITTED_NOT_BOTH_FILLED`: both were submitted, but neither/both were not yet
  confirmed filled. Manual monitoring is required.
- `ONE_SIDED_FILL_ALERT`: only one leg was observed filled. This is an unhedged
  live position and needs immediate operator attention.
- `PLACEMENT_INCOMPLETE_MANUAL_RECONCILE`: one placement was rejected or
  ambiguous. Do not rerun; inspect both market IDs manually.

## Useful options

```text
--max-total-cost 0.98
--max-leg-reprice 0.01
--entry-seconds-left 180
--entry-window-seconds 10
--selection BTC_DOWN_ETH_UP
--reconcile-seconds 15
```

Show recent canary records:

```powershell
python -m predict_bot.xpair_btc_eth_canary --summary-only
```

## What this canary does not do

- It does not atomically place both legs.
- It does not automatically cancel stale GTC orders.
- It does not automatically repair a one-sided fill.
- It does not redeem winning positions.
- It does not share the existing live ledger or runtime controls.
- It does not run continuously after one attempted round.

Those omissions are intentional. The first live test should expose execution
behavior with the smallest practical loss boundary, not silently hide failures
behind recovery logic.
