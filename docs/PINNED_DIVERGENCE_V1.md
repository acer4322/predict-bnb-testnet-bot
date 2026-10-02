# R_PINNED_BINANCE_POLY_DIVERGENCE · V1

## Purpose

Detect a cross-venue state where Binance Prediction remains near an approximately
50/50 UP/DOWN price for a sustained period while Polymarket has already developed
a strong directional probability.  The strategy is selectable independently for
BTC, ETH and BNB from Dashboard V2.

This is an entry gate, not a second execution engine.  Once admitted, the existing
live execution lineage remains authoritative: fresh Binance direct book, signed
BUY quote, post-quote edge, MARKET/FOK placement/reconciliation, V40 reversal
SELL, V42 TAKE_PROFIT same-market lock, V43 source freshness and V44 idempotency.

## Default entry conditions

All are Dashboard-editable per asset:

- entry strategy mode: `PINNED_DIVERGENCE`
- Binance pin center: `0.50`
- Binance UP and DOWN mid both within center ± `0.03`
- minimum sustained pin duration: `5s`
- maximum UP/DOWN range during the pin window: `0.04`
- minimum fraction of pin-window samples inside the pin band: `0.80`
- fresh Polymarket selected-side probability: `>= 0.85`
- Poly selected probability minus Binance selected-side mid: `>= 0.30`
- market time remaining: `>= 20s`
- fresh direct Binance selected-side Ask: `<= 0.60`
- signed-quote `edgeAfterQuote` must still be `>= pinnedMinimumGap`
- one non-rejected pinned entry per market by default

`POLY_GAP` remains the default mode after upgrade, so BTC behavior does not change
until the operator explicitly selects `PINNED_DIVERGENCE`.

## Shotgun

Pinned mode suppresses creation of new Shotgun GTC entries and forces the normal
MARKET/FOK hot path.  Existing resting Shotgun GTC orders created before switching
modes are deliberately not auto-cancelled, preserving the project's prior policy.
Operators must inspect/cancel old resting orders manually when strict pinned-only
exposure is required.

## ETH / BNB live capability

Dashboard V2 now treats ETH and BNB live capability as enabled by default unless
`PREDICT_ETH_POLY_GAP_LIVE_ENABLED=false` or
`PREDICT_BNB_POLY_GAP_LIVE_ENABLED=false` is explicitly set.

The first `POLY_GAP_MULTI_ASSET_LIVE_V3` startup performs a one-time safe migration:
`runtime_enabled` is forced to `0`.  An explicit Dashboard V2 Resume is therefore
required before any new ETH/BNB BUY can occur.

ETH and BNB retain separate ports/databases:

- ETH: `8772`, `data/poly_gap_live_eth.db`
- BNB: `8773`, `data/poly_gap_live_bnb.db`
- shared read-only multi-market observer: `8770`, version
  `MULTI_PREDICTION_OBSERVER_V2`

They do not use BTC-only Paper CHOP or BTC Leader regime as an entry/exit authority.

## Dashboard

`http://localhost:4320/pinned-divergence`

The page displays the current detector state (`PINNING`, `ARMED`, blocked/waiting
reason), pin duration/ratio/ranges, Poly selected probability, Binance mids and
divergence gap.  Settings are persisted by each live engine in SQLite through the
existing localhost-only Dashboard V2 control bridge.

Stake, maximum-loss, take-profit, max-entry price and Runtime Resume/Pause remain
on the `Live Markets` page.

## Attribution

`poly_gap_live_rounds` gains:

- `entry_strategy`
- `entry_context_json`

Pinned entries use `entry_strategy=R_PINNED_BINANCE_POLY_DIVERGENCE`.  The context
stores the detector snapshot at the signal so later research can compare this
strategy against ordinary `R_POLY_GAP_SCALP` without reconstructing the trigger
from UI logs.
