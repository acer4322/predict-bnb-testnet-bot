# Professionalization Plan V1

## Isolation and activation boundary

- Development branch: `feature/professionalization-v1`.
- Development worktree: `predict-bnb-professionalization-v1`.
- Historical market databases are opened read-only.
- Research results use a separate professionalization ledger and artifact directory.
- No live settings, live databases, credentials, process state, or enabled strategies may be changed.
- Merging code and activating a live rule are two separate approvals. All future live-facing features remain disabled until the second explicit approval.

## Frozen strategy and Observer composites

Observer versions are not candidates in this plan. Each pair is treated as one composite model:

| Composite | Source strategy | Observer | Existing drawdown | Existing cooldown |
| --- | --- | --- | --- | --- |
| `R_FUTURES_LEAD+V2` | `R_FUTURES_LEAD` | `V2` | off | off |
| `R_CALIBRATED_VALUE+V6+DD20` | `R_CALIBRATED_VALUE` | `V6` | on | off |
| `R_MICROPRICE+V6+DD20+2L1` | `R_MICROPRICE` | `V6` | on | two-loss/skip-one candidate or existing live choice |

No Observer ranking, version consolidation, or automatic version migration is in scope.

## Explicit exclusion

The user excluded a new account-wide portfolio risk engine. This plan does not add daily/weekly account loss limits, cross-strategy capital budgets, or a new global exposure controller.

## Part 1 - professional evidence foundation

1. A research-only SQLite ledger for immutable experiment metadata, probabilistic forecasts, execution audits, blocked-signal counterfactuals, horizon markouts, and platform-risk observations.
2. Calibration reports with Brier score, log loss, calibration buckets, Wilson intervals, ECE, and explicit unavailable fields for strategies that do not emit probabilities.
3. Experiment governance with dataset/config hashes, frozen composite definitions, development-only selection policy, and a flag that prevents reused validation/holdout data from being described as fresh proof.
4. Execution stress analysis for slippage, visible-depth haircut, partial fills, quote expiry proxies, book age/skew, and mark-to-market exit liquidity.
5. Data coverage and stress-regime audit. Fewer than 30 observed days must report `INSUFFICIENT_LONG_HORIZON_DATA` rather than pass.
6. Signal-horizon and settlement-vs-exit research using recorded post-entry bids at fixed horizons.
7. Platform/API/wallet/oracle/settlement/redeem risk registry. This is an evidence register, not a live risk engine.
8. Capital sizing research only. No sizing result is forwarded to live execution.

## Part 2 - strategy defect remediation research

1. `R_CALIBRATED_VALUE+V6+DD20`: infer the recorded selected-side model probability, measure calibration, and test a conservative lower-confidence-bound edge gate as a shadow candidate.
2. `R_FUTURES_LEAD+V2`: measure 3/10/30/60/120 second executable bid markouts and select any exit horizon using development data only before auditing later splits.
3. Quote edge preservation: recompute conservative edge after stressed quote price and reject only in the research counterfactual when edge is gone.
4. `R_MICROPRICE+V6+DD20+2L1`: preserve its Observer and cooldown semantics; analyze signal/depth/book-quality monotonicity without deploying the failed learned 1/2/4 sizing model.
5. Strategy-specific exit candidates remain paper counterfactuals.

## Part 3 - amplify proven advantages

1. Composite-specific advantage fingerprints using only entry-time-known features and development-frozen buckets.
2. Edge realization waterfall from signal/model edge through raw ask, simulated execution, fees, stress, markout, and settlement.
3. Marginal blocked-signal counterfactuals for Observer, drawdown, cooldown, price/execution, and overlapping blockers where the saved data supports attribution.
4. Composite-specific opportunity and leakage report. No global Observer score is allowed.
5. Data flywheel outputs that can be appended by future forward-shadow runs without changing the experiment definition.

## Acceptance gates

- Reproducible command and deterministic report core.
- Every metric declares its cohort, split, and unavailable inputs.
- Development may choose a candidate; validation/holdout may only audit it.
- Any cohort already inspected during prior work is labeled reused and cannot be called fresh validation.
- Fixed composite definitions are asserted by tests.
- No code path writes to `simulation.db`, `microstructure.db`, or `live_m0w.db`.
- No live API call is made by the research pipeline.
- Targeted new tests pass.
- Existing baseline failures do not increase.
- Dashboard build remains green if dashboard files are touched.
- Current live runtime, armed state, selected strategies, stakes, Observer versions, drawdown controls, and cooldown controls are rechecked after all work.
