# BTC 5M Wallet Shadow Lab · 6DA6 V0

## Goal

Reverse-engineer the BTC 5-minute execution structure of:

`0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03`

The observer cannot see this wallet's open or cancelled orders. It therefore keeps two strictly separate streams:

1. **Target** — only real executed Predict.fun match data.
2. **Shadow** — our own causal inference of what the unseen strategy may have been trying to do.

Target fills are scoring data only. They never trigger or alter Shadow decisions.

## Runtime

- `8771` — existing read-only Predict.fun current-market/order-book observer.
- `8768` — existing strategy sidecar; V0 uses `runtime.binanceDirection` as the primary core direction.
- `8776` — new `predict_wallet_shadow_observer_v2` service.
- Dashboard V2 route — `/wallet-shadow`.
- Database — `data/predict_wallet_shadow.db`.

The normal `multi_asset_live_supervisor` owns 8776. After first pulling this feature onto a machine already running the previous supervisor, stop and start Dashboard V2 once so the new child process is created.

## Predict match decoding

The target match parser follows the same convention already used by `profile_predict_wallet_fair_value_execution_v2_impl.py`:

- `participant.amount / 1e18` => shares.
- `participant.price / 1e18` => probability price.
- dedupe individual fill legs.
- aggregate fill legs into parent orders by order hash + role.

Maker and Taker are fetched separately using the current Predict market ID, target signer address, and `isSignerMaker` role filter.

## V0 Shadow assumptions

### Maker

Observed historical fingerprint:

- fixed unit: 18 shares.
- cent-grid prices.
- passive BID behavior on both UP and DOWN.

V0 inference:

- quote 18 shares on each side at the current Predict best bid floored to the 0.01 grid.
- record `MAKER_QUOTE` when the inferred quote changes.
- record `MAKER_FILL_PROXY` when the live book moves through that prior quote or the ask touches it.

`MAKER_FILL_PROXY` is explicitly an inference, not an observed execution.

### Taker

V0 core direction source:

1. `8768.runtime.binanceDirection` when available.
2. fallback to Predict UP mid above/below 0.50.

If the inferred Maker inventory residual points opposite the core direction, create `TAKER_INTENT` on the core side in 18-share blocks. This encodes the current `WALLET_MAKER_TAKER_DIVERGENCE` hypothesis.

`TAKER_INTENT` is an inferred intent, not a claim that the target wallet must trade there.

## Similarity metrics

The page reports event-structure similarity, not strategy profitability:

- target BUY/BID share.
- target Maker exact-18 share.
- target Maker price within one tick of our active inferred quote.
- target Maker timing within 3 seconds of our fill proxy.
- target Taker side match within 5 seconds.
- target Taker timing within 3 seconds.
- matched event price within one tick.

A late service start can backfill target fills that occurred before Shadow existed. These remain visible in the Target table but are excluded from similarity scoring. Only target parents whose first execution happened after the first causal Shadow event are eligible.

## Safety boundary

- Paper only.
- No target wallet authentication.
- No order creation endpoint.
- No live Binance or Predict writes.
- No target open-order visibility is claimed.
- No target fill may feed back into the Shadow decision path.

## What V0 is intended to answer

The first question is not "does this make money?" It is:

> When the target later reveals an executed Maker/Taker order, had our causal Shadow already inferred the same side, approximate price, size pattern, and timing?

If that event overlap persists over many forward BTC 5m markets, then the unseen-order hypothesis has explanatory value and can be refined into V1/V2 rules. If it does not, the failed components can be replaced independently without rewriting the observation pipeline.
