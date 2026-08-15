# Polymarket lead / Binance lag Paper strategies

This feature is an isolated forward-only research family backed by `data/cross_oracle.db`. It never calls Binance quote, place-order, redeem, live-control, or live-sell endpoints and is not part of the real-money strategy whitelist.

The Dashboard exposes it as its own lower strategy tab, `Poly 跨市場`, instead of placing the strategy cards under the top market card. The top market area continues to show the live Polymarket / Chainlink reference data used by the experiments.

## Shared signal definition

- Polymarket direction uses its live UP best bid/ask midpoint.
- UP is confident at `UP mid >= 0.55`.
- DOWN is confident at `UP mid <= 0.45`.
- Values inside `(0.45, 0.55)` are a deadband and do not count as a flip.
- A flip means the last confident direction changed to the opposite confident direction; passing through the deadband does not erase the previous confident side.
- The Binance comparison uses the current Binance Prediction UP best bid/ask midpoint from `/api/realtime`.
- The two active 5-minute windows must have countdowns aligned within 10 seconds.
- New Paper entries use the observed Binance ask for the selected outcome.
- Paper early exits use the observed Binance bid for the held outcome.
- Stale Polymarket / Binance observations or excessive Binance book age / UP-DOWN book skew fail closed for new Paper execution.

## Independent lead / gap experiments

### R_POLY_LEAD_ENTRY

A signal exists only when Polymarket changes from one confident direction to the opposite confident direction and Binance Prediction has not yet crossed to the same direction. Buy the Binance outcome matching Polymarket at the current ask and hold until the Binance market has an official settlement.

One trade maximum per Binance market.

### R_POLY_LEAD_EXIT

Uses exactly the same entry event as `R_POLY_LEAD_ENTRY`. If Polymarket later makes a confident flip against the held direction before official settlement, exit immediately at the current Binance bid. If no such flip happens, hold to official settlement.

One trade maximum per Binance market so its results can be compared directly with the hold-to-settlement entry variant.

### R_POLY_GAP_SCALP

This is the repeated gap/scalp experiment:

1. Determine the current confident Polymarket direction.
2. Convert the Polymarket UP midpoint to the probability of that selected side.
3. Read the Binance executable ask for the same side.
4. Enter when `Poly selected-side mid - Binance ask >= 0.03`.
5. Exit the open position at Binance bid on the next confident Polymarket direction flip.
6. After exit, a new position may be opened in the same five-minute market if a new executable gap exists.
7. If no Polymarket flip occurs, do not sell early; hold to official settlement.

The executable-gap threshold is configurable through `PREDICT_POLY_GAP_SCALP_MIN_EDGE`.

The original three experiments still report gross PnL. They are separate from the source-mirror confidence shadows below.

## Five source-mirror Polymarket confidence exit shadows

The confidence family does **not** create its own entry. It mirrors an actual new Paper trade from one of these five source strategies:

- `R_CALIBRATED_VALUE`
- `R_MICROPRICE`
- `R_MICROPRICE_CONFIRM`
- `R_FUTURES_LEAD`
- `R_OFI`

Each source trade is mirrored one-to-one by `source_trade_id` after the confidence sidecar is enabled. Old historical source trades are not backfilled.

### Causal exit rule

A confidence shadow is armed only after its source strategy has actually opened the trade. A Polymarket state that already opposed the source at entry does not count as a flip.

The shadow exits only when all of the following are true:

1. The source trade opened first.
2. A **new** confident Polymarket direction flip happened after that source entry.
3. The new Polymarket direction is opposite the held source side.
4. A fresh Binance best bid exists for the held side.

The exit is simulated at that Binance bid. If the exact flip poll has no executable bid, the shadow remains `EXIT_TRIGGERED` and retries the exit instead of silently losing the trigger.

If no new opposing Polymarket flip happens, the shadow does not exit early and simply inherits the source strategy's eventual realized PnL.

### Counterfactual accounting

After an early Poly exit, the original source trade continues unchanged. When the source trade eventually realizes its own PnL, the shadow records both paths:

- `shadow_exit_pnl_usdt`: what the Poly-triggered Binance Bid exit actually produced, including entry and simulated exit fees.
- `counterfactual_no_exit_pnl_usdt`: the source strategy's eventual realized PnL if the Poly exit had not been taken.
- `net_protection_usdt = shadow_effective_pnl - counterfactual_no_exit_pnl`.

Protection metrics follow the same vocabulary as the strong-opposing-trend guard:

- **避免虧損**: the portion of a source loss actually prevented by the early exit.
- **犧牲獲利**: the portion of a source profit lost because of the early exit.
- **淨保護**: the full PnL difference between the Poly-exit path and the original source path.

The Dashboard reports these metrics per source strategy and in aggregate, plus the individual exit timestamp, exit bid, early-exit PnL, no-exit PnL, and source final status.

### Forward-quality safeguards

- The first activation stores a source-trade ID floor in `cross_oracle.db`; older trades are not retroactively mirrored.
- A new source trade must be attached to the aligned current Polymarket/Binance five-minute window within the configured attach-lag limit (default 2500 ms).
- Late attachment or already-final source trades are marked `NOT_EVALUABLE` rather than being counted as protection wins or losses.
- Confidence shadows are Paper-only and never modify the original source trade.

## Runtime

`predict_bot.supervisor` starts two isolated cross-oracle sidecars:

- port 8767: Chainlink / Polymarket data collector
- port 8768: Polymarket lead / gap + confidence-exit Paper strategy engine

The Dashboard proxies port 8768 through `/api/oracle-cross-strategies`.

`stop-local.ps1` clears both sidecar ports along with the existing project services.
