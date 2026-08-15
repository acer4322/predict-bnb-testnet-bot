# XPAIR Canary Dashboard

This patch adds an isolated dashboard route and a loopback-only local API for the one-shot BTC/ETH execution canary.

## What is added

- Dashboard page: `http://localhost:3000/xpair-canary`
- Local canary API: `http://127.0.0.1:8767`
- Local ledger: `data/xpair_btc_eth_canary.db`
- Main dashboard navigation link: `XPAIR Canary`

The canary does not run inside the normal `server.py` process. This separation is intentional: a canary API failure cannot stop the main collector/dashboard API, and the one-shot execution worker cannot accidentally enter the normal live strategy queue.

## Minimum funding

Default settings:

```text
pair budget        2.00 USDT
balance buffer     0.10 USDT
required available 2.10 USDT
```

Keep **3–5 USDT available** for the first test. The pair budget is dynamically split between BTC and ETH to target equal shares.

## Start

Set the same Binance credentials used by the main project:

```powershell
$env:BINANCE_API_KEY="..."
$env:BINANCE_API_SECRET="..."
```

Start the canary API in a separate terminal:

```powershell
python -m predict_bot.xpair_canary_dashboard_server
```

Start the normal dashboard:

```powershell
cd dashboard
npm run dev
```

Open:

```text
http://localhost:3000/xpair-canary
```

The API binds to `127.0.0.1` by default. It is not intended to be exposed to the internet or LAN.

## Required operating sequence

1. Stop the existing real-money executor.
2. Confirm Binance Prediction has no active order and no active position.
3. Run **Dry-run**.
4. Run **Quote-only** with a 2.00 USDT pair budget.
5. Only after quote-only succeeds, enter the full live confirmation phrase and run one live canary.
6. Inspect Binance immediately if the result is not `FILLED_BOTH`.

The server preflight rejects quote/live mode when it detects an active Prediction order or active position.

## Modes

### Dry-run

- Reads aligned BTC/ETH five-minute markets.
- Reads all four outcome books.
- Builds one equal-share plan.
- Does not request a signed quote.
- Does not place an order.

### Quote-only

- Runs wallet/quota/balance preflight.
- Requests both signed LIMIT quotes concurrently.
- Requotes both legs to a common gross-share target.
- Rejects share mismatch above 0.25%.
- Rejects total signed cost above the configured pair budget.
- Rejects final cost/share above the configured ceiling.
- Does not place an order.

### Live

Live mode additionally requires:

```text
I_ACCEPT_NON_ATOMIC_TWO_LEG_RISK
```

The browser also sends a separate confirmation header after a final native confirmation dialog.

- At most one run can be active per process.
- An aligned market pair cannot receive a second recorded live attempt.
- Both placements are sent concurrently but are not atomic.
- Rejected or ambiguous placement is never retried.
- The canary does not cancel, chase, market-sell, or automatically unwind.

## Important statuses

| Status | Meaning |
|---|---|
| `DRY_RUN_READY` | Plan passed; no quote or order was sent. |
| `QUOTE_ONLY_READY` | Both signed quotes passed; no order was sent. |
| `FILLED_BOTH` | Both orders were confirmed filled during reconciliation. |
| `SUBMITTED_NOT_BOTH_FILLED` | Both were submitted but both fills were not confirmed. Inspect active orders. |
| `ONE_SIDED_FILL_ALERT` | Only one fill was confirmed. Manual risk handling is required. |
| `PLACEMENT_INCOMPLETE_MANUAL_RECONCILE` | One or both placement results were rejected/ambiguous. Do not rerun before checking Binance. |

## API endpoints

```text
GET  /health
GET  /api/xpair-canary
POST /api/xpair-canary/run
```

The dashboard polls state once per second. The POST request launches one background worker and returns immediately.
