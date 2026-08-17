# Dashboard V2 Multi-Asset Live

## Scope

Dashboard V2 supports three independent Binance Prediction 5-minute Echtgeld engines driven by Polymarket probability signals:

| Asset | Symbol | Live API | SQLite | Signal source |
|---|---|---:|---|---|
| BTC | BTCUSDT | 8769 | `data/poly_gap_live.db` | existing 8767 collector |
| ETH | ETHUSDT | 8772 | `data/poly_gap_live_eth.db` | 8770 ETH Polymarket WS |
| BNB | BNBUSDT | 8773 | `data/poly_gap_live_bnb.db` | 8770 BNB Polymarket WS |

8770 remains the shared read-only BTC/ETH/BNB market observer and trajectory store.

## Live policy lineage

ETH and BNB inherit the existing V44 trading policy rather than maintaining a separate strategy copy:

- V28 runtime max-entry and held-side Bid take-profit
- V31 per-market completed reversal-exit breaker
- V32 FOK visible-depth preflight
- V34/V35 exact SELL telemetry and durable exit commitment
- V36 bounded definitive-NO_FILL BUY retry
- V37 optional Shotgun LIMIT/GTC ladder
- V39 absolute signal-age and post-quote edge guard
- V40 immediate reversal SELL and cautious 2s re-entry
- V41/V43 quote-source freshness checks
- V42 TAKE_PROFIT same-market BUY lock
- V44 runtime decision envelope and execution idempotency

## Asset isolation

Do not use BTC-only research state to authorize another asset.

ETH/BNB therefore:

- use their own Binance market identity and token IDs;
- use their own SQLite live DB;
- use their own rounds, TP locks, halted markets and execution action keys;
- do **not** consume BTC 8768 Leader Guard decisions;
- do **not** consume the BTC Paper CHOP persistent pause;
- retain their own Live completed-reversal breaker and maximum-loss guard.

`leaderGuardMode` is forced to `OFF` for ETH/BNB until asset-specific lead-validation exists.

## Master switches

Starting the services never enables Echtgeld automatically.

BTC retains:

```text
PREDICT_POLY_GAP_LIVE_ENABLED=true
```

ETH requires:

```text
PREDICT_ETH_POLY_GAP_LIVE_ENABLED=true
```

BNB requires:

```text
PREDICT_BNB_POLY_GAP_LIVE_ENABLED=true
```

Each engine also has its persisted `runtimeEnabled` setting. Both master and runtime must allow entry.

ETH/BNB execution child processes intentionally remove `BINANCE_API_KEY` / `BINANCE_API_SECRET` so Echtgeld cannot silently fall back to a general/read-only key. Use `BINANCE_LIVE_API_KEY` and `BINANCE_LIVE_API_SECRET`.

## Dashboard V2 controls

Open:

```text
http://localhost:4320/live-markets
```

Runtime-editable per asset:

- stake
- maximum-loss enable/amount
- max entry price
- held-side Bid take-profit price
- same-market completed reversal-exit threshold
- Shotgun enable/min/max/levels/per-level amount
- BTC Leader Guard mode
- maximum-loss reset
- runtime pause/resume

The UI does not store settings drafts in localStorage. The live engine persists settings in its SQLite database.

## Stop-loss / take-profit terminology

The inherited BTC policy has:

- take profit: held-side Binance Bid reaches `takeProfitPrice`, then existing signed SELL/FOK executes;
- reversal-risk exit: first fresh/confident opposite Poly direction triggers immediate SELL under V40;
- maximum-loss guard: cumulative realized Echtgeld loss blocks new exposure.

There is no separate fixed per-position `Bid <= stopLossPrice` setting in the current V44 lineage.

## Control-plane security

Dashboard V2 remains visible over its normal Vite listener, but Echtgeld write proxies are protected by both:

1. loopback source requirement; and
2. an in-memory random token generated per Vite process.

`/control/session` is only returned to a loopback client. `/control/*` requires the same session token in `X-BTC-Lab-Control`.

LAN clients can read market/diagnostic pages but cannot change Echtgeld settings.

## Startup

Use:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-dashboard-v2.ps1
```

This starts the existing core API if needed, the multi-asset live supervisor, and Dashboard V2. It does not automatically turn ETH or BNB runtime on.
