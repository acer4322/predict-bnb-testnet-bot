# Decision Strategy Native V2

## Scope

`R_DECISION_RANK1` and `R_DECISION_RANK2` are derived forward Paper controllers.
They consume only these independent source families:

- `R_FUTURES_LEAD`
- `R_CALIBRATED_VALUE`
- `R_CONSENSUS`

M01, raw Microprice, and OFI are excluded from history, voting, and selection.

## Isolation contract

1. The controllers run only after one of the included source strategies opens a new Paper trade.
2. The original M-series/research candidate list is returned unchanged if controller evaluation fails.
3. Controller decisions and source contexts use dedicated SQLite tables.
4. Controller Paper entries use the existing `Store.open_trade` ledger and normal settlement path.
5. `/api/state` keeps its existing handler, history query, summaries, and polling cadence.
6. The dashboard extension only reads the dedicated small tables and appends `researchForward.decisionStrategyExperiment`.
7. Dashboard summary failure must never fail or delay the base payload.
8. Rank 2 does not scan or backfill historical observations during startup, event processing, or dashboard reads.
9. Any historical context backfill must be an explicit offline operation.
10. Live selection and forwarding are a separate phase and remain disabled until Paper/backend regression tests pass.

## Frozen rules

### Rank 1

- last 60 official settled source results
- half-life 20
- Beta(2,2) shrinkage
- minimum 10 source-family history samples
- score: `max(0, utility / 5) * max(0, 2p - 1)`
- at least two positive-weight same-side supporters
- at least 67% direction weight
- positive mean supporter utility

### Rank 2

- context: phase, start-move alignment, entry-price bucket, and path-ER bucket
- last 30 official settled same-context source results
- half-life 10
- Beta(2,2) shrinkage
- minimum 8 same-context samples
- at least 3 percentage points above effective break-even
- maximum 50% positive weight per family
- two same-side eligible families, or one family with posterior probability at least 72% and at least a 5 percentage-point edge lead

### Shared execution and trend rules

- native event-driven entry only
- one controller trade per market
- current direct UP/DOWN book only
- maximum book age 2 seconds
- maximum book skew 500 milliseconds
- maximum spread 0.03
- 50 bps Paper slippage
- 5 USDT full top-level depth
- strong-opposing-trend block at elapsed >=30 seconds, absolute start move >=2.5 bps, and path ER >=0.40
- missing/stale trend inputs fail closed
