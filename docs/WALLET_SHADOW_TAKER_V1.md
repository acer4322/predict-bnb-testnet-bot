# Wallet Shadow Taker V1 paper control

Taker V1 is a separate forward-test control group for the BTC 5M Wallet Shadow Lab. It does not replace or modify Taker V0.

## Why V1 exists

The first recent-10 forward sample showed 300 target Taker parents versus 617 V0 Shadow Taker intents. V0 also frequently expanded individual Taker intents into 90-180 share orders, while the target commonly used much smaller and more bidirectional Taker flow. The same sample showed 168/300 target Taker parents had a same-side target Maker event in the previous five seconds, typically at a short lag and a small price step.

V1 therefore tests execution structure rather than trying to tune the V0 residual threshold.

## V1 triggers

### MAKER_FOLLOW

When the common Shadow Maker engine emits a `MAKER_FILL_PROXY`, V1 may buy the same side if the current ask remains within `PREDICT_WALLET_SHADOW_TAKER_V1_FOLLOW_MAX_PRICE_DELTA` of the inferred Maker fill price. Default maximum delta is 0.025.

This is a causal paper hypothesis for passive-to-aggressive execution switching. Target fills do not trigger it.

### CORE_FLIP

When the independent core direction changes from UP to DOWN or DOWN to UP, V1 may send one small paper Taker probe on the new side. The first core observation after startup does not trigger a trade.

This exists so V1 can be genuinely bidirectional instead of allowing inventory residual to lock the strategy to one side.

## Sizing and risk

Defaults:

- Maker-follow ticket: 12 shares
- Core-flip ticket: 10 shares
- Maximum single event: 18 shares
- Maximum absolute Taker net exposure: 90 shares
- Maximum total Taker volume per market: 600 shares
- Maximum accepted ask: 0.90
- Per-trigger/per-side cooldown: 1.2 seconds

Inventory is a risk limiter only. It never selects trade direction.

## Causal A/B boundary

Each market is registered in `wallet_shadow_taker_v1_markets` when V1 actually starts observing it. V1 performance only includes common Maker fill proxies at or after that market-specific start time plus V1 Taker events. Historical V0 Maker fills are not backfilled into V1 performance.

## Storage

V1 uses separate tables:

- `wallet_shadow_taker_v1_markets`
- `wallet_shadow_taker_v1_events`
- `wallet_shadow_taker_v1_market_results`

They follow the same bounded retention policy as the Wallet Shadow database.

## Dashboard

`/wallet-shadow` shows a Taker A/B section with:

- V0 vs V1 gross PnL
- win rate and ROI
- Taker cost and Taker PnL
- current V1 UP/DOWN inventory
- Target/V1 event-count ratio
- side match within five seconds
- same-side timing within three seconds
- recent V1 trigger events
- recent V1 settled markets

Both groups remain paper-only and no live order-write endpoint is added.
