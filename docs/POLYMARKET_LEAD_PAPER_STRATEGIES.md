# Polymarket lead / Binance lag Paper strategies

This feature adds three forward-only research strategies backed by the separate `data/cross_oracle.db` ledger. They never call Binance quote, place-order, redeem, live-control, or live-sell endpoints and are not members of the real-money strategy whitelist.

## Shared signal definition

- Polymarket direction uses its live UP best bid/ask midpoint.
- UP is confident at `UP mid >= 0.55`.
- DOWN is confident at `UP mid <= 0.45`.
- Values inside `(0.45, 0.55)` are a deadband and do not count as a flip.
- The Binance comparison uses the current Binance Prediction UP best bid/ask midpoint from `/api/realtime`.
- The two active 5-minute windows must have countdowns aligned within 10 seconds.
- Paper entries use the observed Binance ask for the selected outcome.
- Paper early exits use the observed Binance bid for the held outcome.
- Default Paper stake is 1 USDT per entry.
- The ledger currently reports gross PnL; Prediction trading fees are explicitly excluded until fee metadata is wired into this isolated sidecar.

## R_POLY_LEAD_ENTRY

A signal exists only when Polymarket changes from one confident direction to the opposite confident direction and Binance Prediction has not yet crossed to the same direction. Buy the Binance outcome matching Polymarket at the current ask. Hold until the Binance market has an official `UP`/`DOWN` settlement in `simulation.db`.

One trade maximum per Binance market.

## R_POLY_LEAD_EXIT

Uses exactly the same entry event as `R_POLY_LEAD_ENTRY`. If Polymarket later makes a confident flip against the held direction before official settlement, exit immediately at the current Binance bid. If no such flip happens, hold to official settlement.

One trade maximum per Binance market so its results can be compared directly with the hold-to-settlement entry variant.

## R_POLY_GAP_SCALP

This is the repeated gap/scalp experiment. At every strategy evaluation:

1. Determine the current confident Polymarket direction.
2. Convert the Polymarket UP midpoint to the probability of that selected side.
3. Read the Binance executable ask for the same side.
4. Enter when `Poly selected-side mid - Binance ask >= 0.03`.
5. Exit the open position at Binance bid on the next confident Polymarket direction flip.
6. After exit, a new position may be opened in the same five-minute market if a new executable gap exists.
7. If no Polymarket flip occurs, do not sell early; hold to official settlement.

The 0.03 executable-gap threshold is intentionally configurable through `PREDICT_POLY_GAP_SCALP_MIN_EDGE` so later forward data can compare stricter and looser versions without changing the other two strategies.

## Runtime

`predict_bot.supervisor` starts two isolated cross-oracle sidecars:

- port 8767: Chainlink / Polymarket data collector
- port 8768: Polymarket lead / gap Paper strategy engine

The Dashboard proxies port 8768 through `/api/oracle-cross-strategies` and renders the three independent strategy summaries underneath the Polymarket market panel in `正式市場 · 模擬研究`.

`stop-local.ps1` clears both sidecar ports along with the existing project services.
