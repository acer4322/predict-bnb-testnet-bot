# Binance production read-only test report

## Completed

- Confirmed the official Binance Prediction Trading REST paths and response schemas.
- Probed the production market-list endpoint without credentials.
- Binance returned `-2014 API-key format invalid`, confirming that Prediction market data is signed read-only SAPI data rather than anonymous public data.
- Implemented HMAC SHA-256 signing with redacted error reporting.
- Implemented production topic discovery, detail parsing, independent UP/DOWN token order books, paper decisions, and CSV logging.
- No trading, quote, transfer, wallet, or withdrawal endpoint exists in this program.

## Pending

The next research stage is settlement/PnL accounting and a much longer sample. Credentials must not be pasted into chat or committed to files.

## 2026-07-16 credential validation attempt

- Credential shape was valid: API key and secret were both 64 characters, distinct, and had no outer whitespace.
- Binance production returned `-2008 Invalid Api-Key ID` before signature validation.
- This indicates that the supplied key ID is not recognized by `api.binance.com` (for example, wrong environment/key type, revoked key, or non-Binance.com key).
- User-scoped temporary credential variables were deleted immediately after the failed validation.

## 2026-07-16 successful production validation

- A replacement read-only HMAC key passed Binance production authentication.
- Added automatic `/api/v3/time` synchronization after Binance reported the local clock 1000 ms ahead.
- Unit tests: `9 passed`.
- Successfully discovered topic `4404448`, market `6677674`, title `BTC Up or Down 5m`, Chainlink `BTCUSDT`, and both outcome token IDs.
- Collected 13 validated observations across market IDs `6677674` and `6677680`.
- Confirmed automatic rollover to the next five-minute topic (`4404448` -> `4404453`).
- Recorded two paper entries, one per market; blocked four duplicate entries with `ALREADY_TRADED`.
- Validated observations are stored in `data/binance_production_validated.csv`.
- User-scoped temporary credential variables were deleted after testing.

## 2026-07-16 local dual-strategy dashboard

- Added a local responsive dashboard at `http://localhost:4310` backed by a read-only Python API at `http://127.0.0.1:8766`.
- Added independent Strategy A and Strategy B paper ledgers with editable thresholds.
- Strategy A enters the cheaper outcome during the first 120 seconds when the ask gap is at least 0.20 and the cheaper ask is no more than 0.20, then waits for a simulated 0.40 bid fill.
- Strategy B enters an outcome priced at least 0.90 during the final 20 seconds and holds through settlement.
- Observations, configuration, trades, fees, and realized PnL persist locally in `data/simulation.db`.
- Python tests: `11 passed`.
- Dashboard render test: `1 passed`.
- Production dashboard build: passed.
- Local health, state, configuration, and CORS endpoints: passed.
- Credentials are requested securely at launcher startup and are not written to files or browser storage.

## 2026-07-16 Strategy K and twelve-strategy dashboard

- Added `K_v1_basis_adjusted_terminal_prob`, whose direction and probability use only basis-adjusted Binance spot distance, time remaining, realized volatility, latency, and basis uncertainty.
- Prediction-market prices are limited to execution checks: spread, freshness, visible depth, slippage, fee, and net edge. They cannot reverse K's direction.
- Historical probability queries exclude observations after the signal timestamp.
- Added independent K forecast checkpoints at 180, 120, 60, and 30 seconds; official settlement later labels these forecasts even when no K trade was opened.
- Python regression suite: `128 passed`.
- Dashboard production build and render test: `1 passed`.
- Restarted the live local service; API status `LIVE`, collection interval `1.0` second, K enabled, localhost and LAN dashboard responses both HTTP 200.

## 2026-07-16 millisecond microstructure observer

- Added read-only Spot, USDⓈ-M Futures, and signed Prediction WebSocket collection using four independent socket threads and a bounded single-writer queue.
- Added `data/microstructure.db` in WAL mode with raw event, 250 ms feature snapshot, and Prediction liquidity-removal tables; default retention is 6, 72, and 720 hours respectively.
- Added Spot／perpetual microprice, 10-level queue imbalance, 250 ms／1 s taker imbalance, perp-spot basis, Prediction midpoint/removal, event-rate, corrected-latency, queue/drop, and sequence diagnostics.
- Live verification found that the documented Prediction `TOPIC` envelope currently arrives as `DATA` on the dynamic topic. The parser now accepts both while ignoring `COMMAND`; the silent aggregate topic was replaced with the active market's dynamic topic.
- Added automatic signed Prediction reconnect on every five-minute market rollover. A live rollover from market `6686831` to `6686841` retained a non-zero Prediction event rate and immediately switched stored events to the new ID. Prediction removal events deliberately do not claim to distinguish cancellations from fills.
- Added reconnect/rollover invalidation, first-full-snapshot gating, stale-feature expiry, Spot/Futures out-of-order filtering, and Prediction canonical-book verification against the REST UP book. Existing cross-market false removal rows were removed while raw events were preserved.
- Added complete wire-frame persistence, writer heartbeat/error/lag, per-source drop counters, queue depth, persistent timestamped gap rows, and chunked retention cleanup without per-minute full-table counts.
- Python regression suite: `144 passed`; dashboard production build and rendered test: `1 passed`; Python compile check passed.
- Live API verification: Spot, USDⓈ-M Futures, and Prediction all `LIVE`; Prediction mapping `DIRECT_UP_VERIFIED`; all three produced non-zero event rates, writer `RUNNING` at sub-millisecond observed lag, queue depth and dropped events were zero, SQLite rows increased, and the dashboard remained read-only.

## 2026-07-16 Strategy B2 time-scaled stake and stop loss

- Kept Strategy B unchanged as the fixed-stake, hold-to-settlement control group.
- Added independent `B2_v1_time_scaled_stop_loss`: entries are limited to 20–3 seconds and a 0.90–0.95 leading ask. Requested stake follows a configurable squared time-progress curve from 5 to 20 USDT and is capped by visible top-ask depth.
- Added an exclusive 0.60 stop: an open B2 trade exits at the observed held-side best bid only when that bid is below 0.60 and visible bid depth can fill every share. Entry and exit taker fees are both included; missing/insufficient depth is persisted in diagnostics rather than inventing a full fill.
- Added safeguards against opening when the executable bid is already below the stop, overwriting a stopped trade during official settlement, or losing exit fees/PnL during startup accounting repair.
- Added the B2 dashboard card, independent summary, settings, trade badge and red `STOP_LOSS_EXIT` state. The interface explicitly describes the time-risk rule as an unverified hypothesis and the stop as a one-second observation, not millisecond execution.
- Python regression suite: `151 passed`; B2 targeted suite: `7 passed`; dashboard production build and rendered test: `1 passed`.

## 2026-07-16 per-strategy measurement reset

- Added a confirmation-protected reset button to every strategy card. A reset creates a new strategy-specific measurement cohort and never deletes or modifies historical trades.
- Added append-only `strategy_measurement_resets` rows with an atomic global trade-ID cutoff. Only trades committed after the latest cutoff contribute to that strategy's displayed trades, wins, losses, open count, and realized PnL.
- Pre-reset open positions remain in the ledger and continue settling, but are excluded from the new cohort and exposed separately as `carriedOpen`; `totalOpen` prevents hidden exposure.
- Added persisted `resetAt` and `cutoffTradeId` metadata, independent reset isolation for all 13 strategies, HTTP 400 validation for unknown strategies, and restart persistence.
- The reset does not affect configuration, observations, H state, K forecasts, trade history, or per-market duplicate-entry prevention.
- Python regression suite: `156 passed`; dashboard production build and rendered test: `1 passed`.

## 2026-07-16 Strategy B fixed-stake stop loss

- Added independent `strategy_b_stop_loss_price` with default `0.60`; the stored live value is `0.60`.
- Kept B's fixed stake and its existing configurable entry window/range. New entries are tagged `B_v2_stop_loss` and freeze their stop threshold in diagnostics, while historical B rows remain unchanged.
- B and B2 now share one stop processor: only a finite held-side best bid strictly below the trade's frozen threshold can trigger. The entire position must fit in known visible best-bid depth; missing, invalid, or shallow depth leaves the trade `OPEN` and records `stop_loss_blocked`.
- A completed stop exits at the observed bid, includes both entry and exit taker fees, and cannot later be overwritten by settlement or startup accounting repair. Pre-reset carried positions still receive stop processing without entering the new measurement cohort.
- Updated the B card with an editable stop field and live configuration-aware explanation. No strategy measurement reset was performed by this change.
- Targeted B/B2/reset suite: `21 passed`; full Python regression suite: `165 passed`; Python compile check passed; dashboard production build and rendered test: `1 passed`.
- Live restart verification: collector `LIVE` at 1-second intervals; Spot, Futures, and Prediction microstructure streams `LIVE`; writer `RUNNING`; localhost and `http://192.168.68.53:4310` both HTTP 200. Existing live I/J maximum-entry settings remained `0.02` and `0.10`.

## 2026-07-16 Strategy L two-leg early rebound

- Added Strategy L with two independent rows per market, one for UP and one for DOWN. Each side can enter once during elapsed seconds `0–60` when its ask is at most `0.50`; the two legs may enter on different observations.
- Added `strategy_l_total_stake=10`: each leg receives half of that value as its budget ceiling. Entry uses visible top-ask quantity and records requested versus filled stake/shares, so shallow liquidity produces a smaller real paper fill instead of invented size.
- Added conservative target exits: a leg closes only after its observed best bid reaches the frozen target (`0.70` by default) and known top-bid depth can fill every share. The ledger credits the frozen target, stores the observed bid, and includes entry plus exit taker fees using the trade's entry-time fee rate.
- Invalid, crossed, stale, or skewed books cannot open L; missing/invalid/shallow target depth leaves the leg `OPEN` and stores `target_exit_blocked`. Legs not closed at the target continue through the existing official settlement flow.
- Added L to persisted configuration, summaries, recent trades, individual measurement resets, the fourteen-strategy dashboard, mobile layout, documentation, and render coverage. Summary trade/win/loss counts are leg counts; reset is best performed between markets so one round is not split across cohorts.
- Strategy L tests: `24 passed`; L plus measurement-reset targeted suite: `29 passed`; full Python regression suite: `189 passed`; Python compile check passed; dashboard production build and rendered test: `1 passed`.
- Live restart verification: collector and all three microstructure streams `LIVE` at a 1-second interval, Prediction mapping `DIRECT_UP_VERIFIED`, writer `RUNNING` with zero drops, and localhost/LAN dashboards both HTTP 200. Strategy L loaded enabled with `60 / 0.50 / 0.70 / 10 USDT` defaults and opened its first real paper leg in market `6689755`: UP at `0.18`, elapsed `54.891` seconds, `3.9` visible shares, `0.702 USDT` actual partial-fill stake, status `OPEN` at verification time.

## 2026-07-17 Strategy M opening-direction blind hold

- Added `M_v1_opening_spot_direction_hold`. During elapsed seconds `0–10` of each strict five-minute market, the first snapshot whose signal and execution data are both valid buys UP when Binance BTCUSDT spot is above the official Prediction `startPrice`, or DOWN when it is below. Equal/invalid values wait for the next current snapshot; no deadband or stored pending signal is used.
- Prediction token prices cannot choose or reverse the direction. The selected token's current ask and visible ask size are used only for paper execution, with no minimum or maximum entry-price gate; top-of-book depth can reduce the actual fill below the default `10 USDT` budget.
- Entry rejects missing, non-finite, stale, skewed, illegal, or crossed full top-of-book data. Diagnostics preserve the cross-source signal label, actual elapsed seconds, official start, Binance spot, signed distance/bps, selected quote, requested/filled stake and shares, fill ratio, book quality, and frozen configuration.
- M has no intraday exit. Its single position per market remains open until the existing proxy/official settlement flow and pays only the entry-time taker fee. It is included in persisted configuration, summaries, individual measurement resets, recent trades, the fifteen-strategy dashboard, mobile layout, documentation, and render coverage.
- Strategy M tests: `54 passed`; M plus measurement-reset targeted suite: `59 passed`; full Python regression suite: `243 passed`; Python compile check passed; dashboard production build and rendered test: `1 passed`.
- Live rollover verification: collector and all three microstructure streams remained `LIVE` at 1-second intervals, Prediction mapping was `DIRECT_UP_VERIFIED`, writer was `RUNNING` with zero drops, and localhost/LAN dashboards returned HTTP 200. On the first new market after restart (`6690510`), M entered at elapsed `5.411` seconds: official start `64090.575`, Binance spot `64089.96`, signed distance `-0.615` (`-0.0960 bps`), selected DOWN at ask `0.53`. Visible depth filled `14` of `18.8679` requested shares, producing a real paper stake of `7.42 USDT`; the trade was `OPEN` at verification time.
