# Professionalization V1 implementation and acceptance report

## Executive decision

Branch implementation is complete and reproducible. No live activation, merge,
Observer change, or account-wide risk controller was performed.

The seven-day replay is useful for identifying research directions, but it is
not sufficient for live promotion. The broader recorded market history available
at the frozen cutoff is only 14.73 days, validation and holdout have already been
inspected in earlier work, and Futures Lead has only 10 development trades.

Acceptance status:
`BRANCH_IMPLEMENTATION_COMPLETE_LIVE_PROMOTION_NOT_APPROVED`.

## Isolation and frozen scope

- Branch: `feature/professionalization-v1`
- Worktree: `predict-bnb-professionalization-v1`
- Baseline snapshot: `9793cfd`
- Original live branch retained: `feature/regime-guard`
- Historical `simulation.db` is opened with SQLite `mode=ro` and
  `PRAGMA query_only=ON`.
- Generated forecasts and audits are written only to an ignored artifact
  directory and a separate research ledger.
- The replay end is fixed at `2026-07-30T17:39:59.883347+00:00`.
- The active source database can continue receiving later rows without changing
  this experiment. Two consecutive runs produced the same experiment ID and
  report-core hash.
- No live API is imported or called by the research pipeline.
- Observer versions remain frozen as strategy-specific composite models:
  `R_FUTURES_LEAD+V2`, `R_CALIBRATED_VALUE+V6+DD20`, and
  `R_MICROPRICE+V6+DD20+2L1`.
- No global daily/weekly/account-wide portfolio loss controller was added.

## Reproducibility identity

- Experiment ID: `c3ef65026f9e00c356de4b8f`
- Deterministic report-core SHA-256:
  `8d7272bd6c19142a928c3342fe40e082f497fd8f0e4ef97c44b7e09a5d19af6f`
- Replay SHA-256:
  `bd66b78b893b6d1a43d69f6b8b8ebcca51bd29c2900db7aa9c80683ad26718f5`
- Dataset identity includes the fixed replay, fixed-end market-data aggregates,
  every consumed horizon markout, and both implementation file hashes.

Reproduction command:

```powershell
python research\professionalization_plan.py `
  --replay-report <seven-day-observer-market-replay.json> `
  --simulation-db <simulation.db> `
  --output-dir artifacts\professionalization-v1
```

## Part 1 - professional evidence foundation

### Implementation

The branch adds:

- a separate SQLite experiment ledger with dataset/config hashes, frozen
  composites, reuse status, probability forecasts, execution audits, executable
  markouts, marginal blocked-signal counterfactuals, and platform risks;
- Brier score, log loss, ECE, maximum calibration error, fixed deciles, Wilson
  intervals, cohort counts, and explicit unavailable states;
- fixed-end market coverage and data-quality stress statistics;
- 50/100/200 bps slippage, half/quarter depth, quote-age, partial-fill, fee,
  and executable-bid stress models;
- recorded 3/10/30/60/120 second markouts with full-depth and snapshot-gap
  requirements;
- strategy-local flat and rolling 1-to-4 sizing comparisons that cannot be
  forwarded to live execution;
- a five-item API/protocol/oracle/wallet/clock risk registry.

### Data and selected-composite result

The fixed seven-day replay contains 2,003 candidate markets. The broader source
coverage at the fixed cutoff contains 1,124,219 observations, 4,148 markets, and
4,037 official settlements over 14.73 days. Therefore long-horizon status is
`INSUFFICIENT_LONG_HORIZON_DATA` against the 30-day minimum and 90-day preferred
target.

The frozen composites produced the following simulated settlement results. These
are reused historical audit figures, not fresh forward proof.

| Composite | Trades | Wins | Losses | Simulated PnL | ROI on stake |
| --- | ---: | ---: | ---: | ---: | ---: |
| `R_FUTURES_LEAD+V2` | 49 | 24 | 25 | 95.3715 | 0.4866 |
| `R_CALIBRATED_VALUE+V6+DD20` | 107 | 58 | 49 | 54.2201 | 0.2534 |
| `R_MICROPRICE+V6+DD20+2L1` | 107 | 49 | 58 | 64.2433 | 0.3002 |

Microprice had 125 `V6+DD20` candidates before its causal 2L1 rule. Eighteen were
skipped, leaving 107.

### Calibration and execution evidence

- Calibrated Value supplied 107 recoverable probabilities: Brier 0.2386, log
  loss 0.6690, ECE 0.1223, and maximum bucket error 0.2845.
- Futures Lead and Microprice emit directional scores rather than calibrated
  selected-side probabilities. Their probability metrics correctly return
  `UNAVAILABLE` instead of treating scores as probabilities.
- Under the most severe quarter-depth/stale-quote scenario, execution rates by
  development/validation/holdout were 0.80/0.82/0.55 for Futures Lead,
  0.60/0.72/0.48 for Calibrated Value, and 0.70/0.69/0.80 for Microprice. This
  exposes material execution sensitivity that settlement-only PnL hides.

### Capital-control research

The rolling 1-to-4 rule is not adopted:

- Calibrated Value ROI fell from 0.2534 to 0.1611 and drawdown increased.
- Futures Lead ROI fell from 0.4866 to 0.2268; development became negative.
- Microprice improved in development, but validation fell to approximately zero
  PnL and holdout ROI was slightly worse. This is split instability, not proof.
- Flat 1-unit sizing reduces absolute loss and drawdown mechanically, while
  leaving per-stake ROI unchanged under the linear-fill assumption.

## Part 2 - strategy defect remediation research

### Calibrated Value

The conservative lower-Wilson-bound gate allowed zero trades. With only 50
development forecasts spread across fixed deciles, no bucket reached the
predeclared 20-sample minimum. Status is
`INSUFFICIENT_CALIBRATION_EVIDENCE`; no threshold is promoted.

Recorded quote-edge stress remained above the 0.01 minimum for 100% of trades at
0 and 50 extra bps, 97.20% at 100 bps, 71.03% at 200 bps, and 0% at 500 bps.
This is a shadow diagnostic, not a new live quote gate.

### Futures Lead

Only 10 development trades are available, below the 30 executable-trade minimum.
Every tested development markout horizon underperformed settlement on its covered
cohort. Status is `INSUFFICIENT_DEVELOPMENT_EXIT_EVIDENCE`; no early-exit horizon
is selected.

### Microprice and local cooldown

The causal two-loss/skip-one rule blocked 18 trades: 12 losses and 6 wins with
counterfactual PnL of -11.3287. Thus the rule improved this replay by 11.3287.
The blocked PnL was negative in development (-8.6065), validation (-1.3378), and
holdout (-1.3844), which is directionally consistent but still a small reused
sample.

No fixed early exit was selected for Microprice or Calibrated Value. Their best
eligible development horizons still underperformed covered settlement by 3.7443
and 19.8596 respectively, so the implementation now rejects the “least bad”
horizon rather than mislabeling it as beneficial.

## Part 3 - amplify advantages without changing Observer versions

### Advantage fingerprints

- Futures Lead has only 10 development trades, so its feature fingerprint is
  explicitly insufficient.
- Microprice signal-strength quartiles were monotonic in development and broadly
  improving in validation. The top holdout bucket contained only three trades
  and all lost, so this remains a useful collection target rather than a gate.
- Older-book Calibrated Value entries were weaker in development and validation,
  but each quartile has only about 12 development trades and later splits are
  inconsistent. Book age should be logged and stress-tested, not blocked yet.
- Entry price and visible depth showed substantial split instability for both
  strategies. They are not promoted as filters.

### Marginal controls and leakage

- Calibrated Value Observer blocking saved PnL in development and validation but
  rejected positive PnL in holdout. Its drawdown controller also had mixed
  marginal behavior.
- Futures Lead V2 blocking was strongly split-dependent. This reinforces the
  decision to freeze rather than globally re-rank Observer versions.
- Microprice Observer blocking saved PnL in development and validation but leaked
  positive PnL in holdout. Its DD20 and 2L1 controls were directionally helpful
  across this replay.
- Saved replay data supports sequential attribution for Observer, drawdown, and
  cooldown. It does not contain all rejected price/execution candidates or every
  overlapping blocker, so those fields are marked unavailable until a future
  all-candidate forward ledger exists.

The output includes a composite-specific edge waterfall from signal/model edge
through raw ask, simulated entry, fees, execution stress, markouts, and
settlement. No global Observer score is calculated.

## Verification

- New focused suite: 14 passed.
- Full Python suite after implementation: 591 passed, 31 failed.
- Baseline before implementation: 577 passed, 31 failed.
- Regression decision: pass; the 31 stale legacy failures are unchanged. They
  concern an old 50-strategy count and legacy A/C/D/J/L/M enabled-by-default
  assumptions, not the professionalization files.
- Dashboard build and rendered HTML test: 1 passed, 0 failed.
- Determinism: two consecutive fixed-window executions returned the same
  experiment ID and report-core hash while the source collector continued.

## Live isolation audit

After all branch work, `GET /api/live-rules` in the original workspace reported:

- status `LIVE`, configured enabled, runtime enabled, armed, and real-money true;
- strategies `R_FUTURES_LEAD`, `R_CALIBRATED_VALUE`, `R_MICROPRICE`;
- stakes 4/4/4 USDT;
- Observer versions V2/V6/V6, all enabled;
- drawdown controls off/on/on;
- loss cooldown controls off/off/on;
- order-sync watchdog at zero consecutive transport errors;
- no last live error.

These values match the pre-branch state. No live rule, process, credential,
database, or activation state was changed by this implementation.

## User acceptance choices

The branch is ready for code and report review. Recommended acceptance boundary:

1. Accept the research infrastructure and reports on this branch.
2. Do not approve a new calibration gate, early-exit rule, or rolling 1-to-4
   sizing mechanism from this seven-day reused cohort.
3. Continue collecting an untouched forward cohort and at least 30 days of
   fixed-schema market data.
4. Treat merge approval and live activation as two future, separate decisions.
