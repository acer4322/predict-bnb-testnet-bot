# Chainlink / Polymarket Cross-Oracle Collector

This collector is observation-only. It does not participate in live signal generation, order preflight, order placement, or settlement.

## Sources

- Chainlink BTC/USD via Polymarket RTDS: `wss://ws-live-data.polymarket.com`
  - topic: `crypto_prices_chainlink`
  - symbol: `btc/usd`
- Polymarket public CLOB market stream: `wss://ws-subscriptions-clob.polymarket.com/ws/market`
- Current BTC 5m market metadata: Gamma market-by-slug using `btc-updown-5m-{five_minute_epoch_start}`
- CLOB REST `/book` is used only to prime the current UP/DOWN best book immediately after rollover.

No Polymarket trading credentials are required.

## Runtime

`predict_bot.supervisor` starts `python -m predict_bot.cross_oracle` beside the normal prediction API by default.

Environment variables:

- `PREDICT_CROSS_ORACLE_ENABLED=0` disables the sidecar.
- `PREDICT_CROSS_ORACLE_HOST=127.0.0.1`
- `PREDICT_CROSS_ORACLE_PORT=8767`
- `PREDICT_CROSS_ORACLE_DB=<repo>/data/cross_oracle.db`
- `PREDICT_CROSS_ORACLE_MARKET_REFRESH_SECONDS=3`
- `PREDICT_CROSS_ORACLE_RESTART_DELAY_SECONDS=5`
- Dashboard proxy override: `PREDICT_CROSS_ORACLE_BASE_URL=http://127.0.0.1:8767`

State endpoint: `GET http://127.0.0.1:8767/state`

## Persistence

SQLite file: `data/cross_oracle.db`

Tables:

### `chainlink_ticks`

Every accepted BTC/USD RTDS update stores:

- Chainlink source timestamp
- local receipt timestamp (`received_wall_ns`)
- price
- topic
- original raw JSON payload
- schema version

### `polymarket_markets`

Stores each discovered BTC Up or Down 5m market:

- slug / market ID / condition ID
- five-minute window start and end
- UP and DOWN CLOB token IDs
- original Gamma JSON

### `polymarket_events`

Every accepted public CLOB event stores:

- market / condition / token / outcome
- event type
- best bid / best ask / last trade when present
- source timestamp and local receipt timestamp
- up to 20 parsed bid/ask levels when present
- original raw WebSocket/REST JSON

There is intentionally no short retention policy yet. The purpose of this DB is later cross-oracle lead/lag and market-disagreement validation.

## Reconstructed opening price

The dashboard's Polymarket `起始價` is not silently claimed to be an official Polymarket UI value. It is reconstructed from the saved Chainlink BTC/USD tick nearest the five-minute epoch boundary, only when that tick is within 10 seconds of the boundary. The exact offset is displayed.

If the collector starts halfway through a market and has no valid boundary tick in its DB, the current market shows no start price. The next full five-minute market will establish one from live collected data.

## Dashboard

The main market panel shows a second `BTC Up or Down 5m` block below the existing Binance Prediction block:

- reconstructed Chainlink BTC/USD opening price
- current Chainlink BTC/USD price
- Polymarket UP best ask / bid / last trade
- Polymarket DOWN best ask / bid / last trade
- countdown
- source age and source timestamp for Chainlink and Polymarket
- market slug / condition / token IDs
- stored row counts and DB size

The block is explicitly marked `READ ONLY` and does not feed any strategy yet.
