# BTC 5M Lab — Codex Research / Development Handoff


## AUTHORITATIVE DURATION RULE (2026-09-05)

There is NO project-level 30-minute work/session/run limit. Any older >=30-minute approval, notice, proposal, or wrap-up wording is superseded and non-operative. Do not self-impose a 30-minute conversation/work cutoff. Long research may continue across tool calls while process/heartbeat/artifacts show measurable progress; stop only for actual stall/crash/safety/resource conditions or explicit user direction.

## PROJECT-WIDE RESEARCH EXECUTION PIPELINE — REQUIRED
Before launching heavy/batch research jobs, read and follow `data/research/RESEARCH_EXECUTION_PIPELINE_CONTRACT_V1.md`. This is the canonical orchestration rule for all BTC 5M Lab research conversations. Use continuous `wave` queues when the next jobs are pre-definable; maintain bounded parallel lanes, collect completed jobs immediately, refill freed lanes immediately, attach existing jobs rather than duplicate-launch, and require expected artifacts before treating process success as research completion. On 502/timeout/apparent stall, inspect job/artifact first. This contract supersedes older one-wave-at-a-time orchestration instructions only; scientific/safety boundaries remain unchanged.

Updated: 2026-08-22 23:48 Asia/Taipei
Repo branch: `feature/dashboard-v2-polyhermes`
Observed HEAD: `e8454d5`
Purpose: short, current entry point for Codex. The long canonical history remains in `BTC5M_PROJECT_HANDOFF.md`.

## Read order

1. `AGENTS.md` — workspace safety / operating conventions.
2. `BTC5M_CODEX_RESEARCH_HANDOFF.md` — this short current-state handoff.
3. `BTC5M_PROJECT_HANDOFF.md` — canonical long-form history and guards; use it to verify older decisions/semantics, not as the first file to digest linearly.
4. `data/research/execution_aware_fill_lifecycle_v0/hft_native_pivot_checkpoint_20260822_v2.json` — latest structural research pivot.
5. `data/research/execution_aware_fill_lifecycle_v0/hft_native_value_surface_v2_full_side_report.json` — latest experiment at this handoff.
6. `data/research/simulation_execution_realism_contract_v1.json` — execution realism hard guard.
7. `data/research/hourly_supervisor_hft_forward_20260822_2257.json` — latest verified official HFT Forward PAPER checkpoint currently written into the canonical handoff.

When continuing a specific subproblem, inspect the artifact/tool that produced it before modifying anything.

## Project state in one paragraph

The strategy/theory side reached a strong R2 result under the old optimistic PAPER semantics, but that result is not deployable evidence because realistic venue execution materially changes fills, own-state trajectories, repair/Taker decisions, and sometimes even the sign of PnL. HftBacktest + Predict Execution Tape V1 is therefore now mandatory for performance-grade simulation and for official forward PAPER evidence. The current research bottleneck is no longer discovering the broad R2 alpha/controller theory; it is learning an execution policy that can operate under queue position, latency, partial fills, order lifecycle and sparse fill opportunities without destroying the strategy economics.

## Hard execution evidence rule

Do NOT use dream fills, fill-on-touch, QUEUECLEAR_PASS, DEPLETE_PASS, or instant observed-ask Taker fills as primary performance evidence.

Performance-grade simulation must use HftBacktest + Predict Execution Tape V1 with realistic queue/latency/partial-fill lifecycle and actual-fill own-state feedback. Post-cutover official forward PAPER is HFT-only.

Legacy optimistic paper results may be used only as theory/reference/diagnostic evidence and should be labeled `OPTIMISTIC_EXECUTION_UNCALIBRATED` where applicable.

## Current frozen/reference strategies

- R2 theory strategy: frozen reference; do not silently change its alpha/theory semantics while working on execution.
- CAP100: frozen comparison/safety lane; do not treat old optimistic PAPER as deployable evidence.
- 8781/8782 Echtgeld chain: do not restart/reconfigure/use for research orders.
- 8784/8786 post-HFT-cutover optimistic runtime may still exist for snapshots/intents/diagnostics, but its fills/inventory/PnL are not official new PAPER evidence.
- 8788 is the official HFT Forward PAPER collector.

## Latest official HFT Forward PAPER status

At the latest canonical checkpoint written at 2026-08-22 22:57, after fixing 8778 database-writer contention:

- official matched settled HFT cohort: 20 markets
- R2 realized PnL: `-$93.5810`, 10/20 positive
- CAP100 realized PnL: `+$22.6692`, 10/20 positive
- CAP100 - R2: `+$116.2502`, 12 better / 8 worse, median delta `+$7.9452`
- CAP100 max HFT capital in these 20: `$99.468`

N=20 is too small for tuning/promotion. Both remain frozen. Do not tune on this forward cohort.

## Important 8778 / 8788 incident already resolved

A ~3 hour 8778 durable public-book writer stall was isolated to 8788 repeatedly calling `record_quality()` into `wallet_maker_book_inference.db`, causing sustained SQLite writer contention. The opportunistic quality-index refresh was removed; 8788 archive discovery is read-only with respect to the 8778 BOOK_DB, and 8778 required restart to recover its stale long-lived SQLite connection. Markets spanning the storage gap are not backfilled as fresh evidence.

Do not reintroduce a writer from 8788 into the 8778 book DB.

## What has already been tried and rejected

### 1. Treating execution as a small patch on top of frozen R2

Many attempts to make R2 simply "wait better" or reproduce the old intent path under HFT were not enough. Fixed-decision and closed-loop HFT replays showed that realistic execution changes controller state, not just fill count.

### 2. Candidate V1 execution graduation

Frozen Candidate V1 completed a formal untouched 10-market HftBacktest graduation exam:

- realized PnL `+$12.8232`
- 3 wins / 7 losses = 30%
- required: PnL > 0 AND win rate >= 50%
- result: FAILED

Do not rescue/tune Candidate V1 from those 10 revealed markets.

### 3. Candidate V2 monolithic/hierarchical arbitration timing

V0/V1/V2 `ARBITRATION_NEEDED` classifiers were rejected for chronological instability. The teacher audit found the existing sequential arbitration teacher to be future-counterfactual: WAIT/RETURN vs KEEP/REPLACE depends on future dominance/5-10-20s counterfactual outcomes. It is not a deployable strict-past current-state label.

Reframing it as a future-clarity hazard fixed the semantics but timing ranking still did not become stable enough to freeze. Do not train another current-state gate against the same future-defined teacher just by adding more scalar features.

### 4. Separate R2 execution controller attempts

`r2_executor_checkpoint_20260822_v1.json` records two explicit rejects on a fresh10 cohort:

- HFT R2 baseline: `+$23.8032`, 5W/5L
- Executor V0 queue-value greedy: `-$31.8204`, 4W/6L — rejected greedy churn
- Executor V1 option-lease using existing staged controller: `-$27.9954`, 3W/7L — rejected as current executor

Prior toxicity-only/depth-shading also reduced paired coverage and increased residual imbalance; rejected.

Conclusion: execution separation is structurally valid, but naive queue-value greed, toxicity-only shading and existing staged controllers destroy too much of R2 intent/economics.

## Latest structural pivot — current mainline

The newest checkpoint is:

`data/research/execution_aware_fill_lifecycle_v0/hft_native_pivot_checkpoint_20260822_v2.json`

Current decision:

**Stop adapting Frozen R2 intents as the main research object. Preserve the HftBacktest counterfactual sweep infrastructure. Learn execution-regime-conditioned action value from simulator-generated counterfactual outcomes, not from observed Target actions and not from exact R2 intent imitation.**

The key question becomes:

> Given the strict-past execution/portfolio state and the current venue regime, what is the value of WAIT vs a specific passive offset / pair action / selective Taker action after realistic queue/latency/fill mechanics?

A policy is not interesting merely because it loses less than fixed quoting. It must produce **positive unseen holdout execution value**.

## Evidence behind the pivot

### Single-leg HFT-native timegrid counterfactual

Across 60 markets / 120 valid checkpoints:

- fixed offset0 1s MTM reward: `-9.2567`
- fixed offset1: `-7.7000`
- fixed offset2: `-9.0282`
- oracle WAIT + best action reward: `+12.8651`
- oracle act rate: `25.83%`
- oracle best actions: offset0=12, offset1=8, offset2=11, WAIT=89

Interpretation: most states should WAIT; profitable passive actions exist but are sparse and state-dependent.

### First contextual policy

- train30 reward `+3.78`
- validation10 `-0.27`
- forward20 `+0.81`
- fresh20 combined `0.0`

Rejected for promotion. It mainly avoids losses and does not capture enough positive oracle value.

### Latency probe

FreshA first4 changes materially with latency (`0ms +1.8`, `100ms +1.71`, `250ms +1.62`, `300/500ms +0.63`, `1092ms +0.45`), but FreshB first4 showed the same `+1.88` at 250ms and 1092ms.

Conclusion: latency matters, but there is no universal 250ms magic threshold. The useful hypothesis is **latency × execution-regime interaction**.

### Pair actions

Several small HFT-native pair sweeps show positive oracle counterfactual value with sparse ACT states, but the current strict-past learned gate failed on unseen newest5 and chose no actions despite oracle reward `+2.61`. Keep the pair counterfactual generator; do not promote the learned gate.

### Taker actions

Sparse positive oracle Taker value exists in small probes, but using public directionScore sign as a Taker direction rule failed badly. Do not scale a simple direction-sign policy.

## Latest experiment — HFT native value surface V2 full side

Artifact:

`data/research/execution_aware_fill_lifecycle_v0/hft_native_value_surface_v2_full_side_report.json`

Training:

- 383 counterfactual action rows
- 99 filled rows
- action-value score = `P(fill5s) * (E[1s MTM | fill] + deterministic delta worst-case-floor for 18-share fill)`
- no new weights/threshold/PnL rescue
- WAIT=0
- compares R2-side restricted vs free-side actions using the same model

Combined unseen holdouts FreshA/FreshB/FreshC:

- 50 checkpoints
- oracle reward: `+1.1860`
- R2-side restricted learned reward: `0.0`
- free-side learned reward: `0.0`
- free-side policy acted 21 times and all 21 were opposite the R2 side, but achieved zero reward

Interpretation: the counterfactual action surface contains some positive states, but the current learned value model still does not identify them OOS. Freeing side choice alone does not solve it.

## Current research question for Codex

Work on the **HFT-native execution value problem**, not another cosmetic R2 intent adapter.

Good next work should do one of these:

1. improve the simulator-generated action-value dataset/state representation so sparse positive execution states become identifiable across chronology/regimes;
2. model fill probability / markout / portfolio-floor change in a way that is explicitly conditioned on execution regime and latency rather than pooled across incompatible state distributions;
3. test a clean contextual-bandit / offline-RL / semi-MDP formulation using simulator counterfactual labels, with strict chronology and an explicit WAIT action;
4. demonstrate, with untouched holdout data, positive realized counterfactual execution value — not just higher classification AUC and not merely "less negative than baseline";
5. if larger clean counterfactual data still cannot predict sparse positive states OOS, produce evidence for an infrastructure/venue-edge ceiling or recommend switching economic strategy family rather than endlessly tuning small knobs.

## Stop rules / avoid wasting time

Do not revive these without genuinely new evidence:

- exact-intent tracking as the core target
- queue-cancel micro-tuning loops
- toxicity-only filters/shading
- continuous inventory skew as a standalone fix
- placement imitation / Target action imitation
- another current-state arbitration gate trained on the future-defined teacher
- threshold sweeps against revealed PnL
- repeated tuning on the same FreshA/B/C holdouts

If a direction shows repeated zero/negative OOS value, abandon it and pivot rather than repairing tiny details indefinitely.

## Data / experiment integrity guards

- strict-past / blind-before-reveal.
- no Target future action, winner, settlement or PnL as runtime/model input.
- Target/winner/PnL may be used only where explicitly allowed as teacher/evaluation/post-hoc labels.
- HftBacktest + Predict Execution Tape V1 for performance-grade execution.
- no dream fill.
- no Echtgeld research orders.
- do not change frozen R2 theory semantics as an execution experiment shortcut.
- do not tune on official HFT Forward PAPER cohort.
- 2026-08-16 special markets remain SEALED for their declared later stress purpose.
- final75-99 Supervisor holdout remains sealed.
- No fixed 30-minute run boundary. Elapsed duration alone never requires approval or stopping; continue while measurable progress exists.

## Repository hygiene

The current working tree is heavily dirty/untracked from ongoing project development. Do NOT mass-reset, checkout, clean, delete, or rewrite unrelated files. Treat pre-existing modifications as user/project work. Make narrow changes, inspect diffs for only the files you intentionally touched, and avoid destructive cleanup.

Prefer research-only tools/artifacts under `tools/` and `data/research/execution_aware_fill_lifecycle_v0/` until a candidate is genuinely validated.

## What Codex should report after each useful iteration

Keep it compact and decision-oriented:

- hypothesis tested
- exact files changed/created
- cohorts used and whether they were opened vs untouched
- execution semantics used
- main metrics, including WAIT/ACT rate, oracle value ceiling, realized learned-policy value, and OOS chronology
- whether the direction is KEEP / REJECT / NEED_MORE_DATA
- the single highest-value next experiment
- run duration alone does not require proposal/approval; long runs are allowed when progress is observable through heartbeat/status/artifacts

The project goal is not to make charts look better. The goal is to find an execution policy that preserves the already-discovered theory edge under real queue/latency/fill mechanics, or to establish with evidence that this venue/infrastructure cannot support that edge and pivot economically.

## 2026-08-23 — queue-reactive search and inventory-repair pivot

### Mature-research / data preflight

The useful external references were the Queue-Reactive model, multilevel/order-size Queue-Reactive extensions, DeepLOB, and offline-RL support-control methods such as CQL/IQL. The immediate implication was to test strict-past queue/order-flow state before attempting a neural LOB encoder or offline RL. The current full-action simulator sweep is more identifiable than logged-action offline RL; CQL/IQL should wait for a true multi-step lifecycle dataset.

Predict.fun data acquisition does not need a parallel implementation. The repo already archives `predictOrderbook/{marketId}` L2 snapshots/deltas plus `orderCount`, `lastOrderSettled`, `settlementsPending`, and raw `/v1/orders/matches` payloads in Predict Execution Tape V1. Extend that existing collector/archive if more markets are needed; synthetic/reconstructed order flow may support representation learning but is not performance evidence.

### Queue-reactive chronological V2

Artifacts:

- `hft_native_unused_chronology_v2_preregistered.json`
- `hft_native_unused120_collector_v2.json`
- `hft_native_unused120_bothsides_v2.json`
- `hft_native_queue_reactive_chronology_v2_report.json`
- `hft_native_queue_reactive_chronology_v2_audit.json`

This was locked before action outcomes on 120 previously unused COMPLETE_FORWARD_V1 ordinary markets, split chronologically 80/20/20. It used HftBacktest, Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, partial fills, WAIT plus both sides offsets 0/1/2. No official Forward, sealed 2026-08-16, or Supervisor final75-99 markets were used.

Primary queue-reactive result:

- validation: oracle `+28.71`, learned `+10.71`, ACT 14/40
- unseen holdout: oracle `+6.58`, learned `+6.22`, ACT 13/40, 7 positive / 0 negative / 6 zero-fill
- holdout reward decomposition: 1s MTM `+0.63`, portfolio-floor delta `+5.59`

However, the same-cohort static queue/portfolio/public ablation produced validation `+25.29` and holdout `+5.68`. Raw 250ms/1s/3s add/remove/depletion features therefore do not have robust incremental evidence. **KEEP the positive action family; REJECT the current queue-reactive feature augmentation as necessary.**

All learned ACTs in this experiment reduced actual inventory imbalance; holdout ACTs were 13/13 opposite the contemporaneous R2 placement side. All positive oracle and learned actions occurred at the second placement checkpoint. This required a placement-independent test before interpreting the result.

### Placement-independent time-grid inventory V3

Artifacts:

- `hft_native_timegrid_inventory_chronology_v3_preregistered.json`
- `hft_native_timegrid_inventory_unused80_v3.json`
- `hft_native_timegrid_inventory_value_v3_report.json`
- `hft_native_timegrid_inventory_v3_audit.json`

This second experiment used 80 additional markets absent from every prior HFT-native artifact, split chronologically 50/10/20. Each market used deterministic 10%/50%/90% usable-public-snapshot checkpoints, independent of R2/Target placement. Own portfolio state was reconstructed only from base-replay actual fills with `eventMs <= checkpointMs`. The model, score, WAIT rule, and execution semantics were locked before generating outcomes.

Results:

- train: oracle `+353.62`, learned `+290.98`, ACT 131/150
- validation: oracle `+94.41`, learned `+80.20`, ACT 27/30
- unseen holdout: oracle `+121.32`, learned `+53.73`, ACT 54/60, 14 positive / 0 negative / 40 zero-fill
- holdout decomposition: 1s MTM `-0.88`, portfolio-floor delta `+54.61`

The effect was not tied to checkpoint position, but it was not a sparse learned execution gate either: learned ACT rate was 90%, and all 54 holdout ACTs reduced actual inventory imbalance. Fixed inventory-reducing passive baselines were also strongly positive on the same holdout: offset0 `+51.30`, offset1 `+49.37`, offset2 `+91.71`; offset2 decomposition was MTM `-2.61` plus floor delta `+94.32`.

Decision:

- **KEEP** passive actual-inventory repair as a research-only economic action family under realistic execution.
- **REJECT** the current learned gate as necessary/general sparse execution-alpha evidence; fixed offsets already explain the effect.
- Do not call this markout alpha or promote it as an R2 execution solution. The reward is overwhelmingly portfolio-floor utility, and a broad 90% ACT policy could repeatedly resubmit, over-hedge, or erase intended R2 exposure in a real closed loop.

Highest-value next experiment: a multi-step HftBacktest semi-MDP/closed-loop episode with WAIT as an explicit action, one live repair child at a time, actual-fill own-state feedback, cancel/response lifecycle, and an exposure-preservation constraint based only on strict-past portfolio/intended state. Compare fixed offsets before learned policies. Evaluate on new Predict Execution Tape V1 markets and report both MTM and floor utility; do not use threshold sweeps or the official Forward cohort.

## 2026-08-23 — constrained markout ranking with Target-public regime pretraining V4

This experiment deliberately did not repeat R2/Target imitation, queue-cancel tuning, toxicity shading, or the additive portfolio-floor objective. Portfolio floor was used only as an action feasibility constraint; the learned reward was actual-fill 1-second MTM, with WAIT as a formal action and WAIT winning exact zero-reward ties.

Artifacts:

- `tools/select_hft_native_constrained_rank_v4.py`
- `tools/hft_native_constrained_rank_v4.py`
- `hft_native_constrained_rank_v4_preregistered.json`
- `hft_native_constrained_rank_unused90_v4.json`
- `hft_native_constrained_rank_v4_report.json`
- `hft_native_constrained_rank_v4.joblib`

The Target data contribution was representation-only: 16,028 earlier public-book control rows from 60 Target markets were clustered from 13 book/depth/spread/age features. Target action, `pMaker`, portfolio, winner, settlement, and PnL were not used. These Target markets have no overlap with the currently archived Predict Execution Tape V1 markets, and the local wallet-inference database does not contain their historical raw L2 tape. Therefore no performance-grade Target-history HftBacktest claim is possible from the current data; `/orders/matches` alone is insufficient to reconstruct cancels and queue position.

The performance cohort was 90 newly unused COMPLETE_FORWARD_V1 markets, chronological 60/15/15, all later than the Target representation data and before official HFT Forward. It excluded all previous HFT-native artifacts, official Forward, sealed/graduation markets, and Supervisor final75-99. Each market used three deterministic placement-independent checkpoints. Execution was HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, 5-second partial-fill horizon, and 1-second post-fill MTM.

Primary pairwise ranker with Target regime one-hots:

- train: constrained oracle `+37.20` MTM, learned `+10.98`, ACT 13/180, 12 positive / 0 negative / 1 zero-fill
- validation: constrained oracle `+2.61`, learned `0.00`, ACT 4/45, 0 fills
- unseen holdout: constrained oracle `+6.02`, unconstrained oracle `+14.78`, learned `+1.35`, ACT 6/45, 1 positive fill / 0 negative / 5 zero-fill
- holdout realized floor audit was `+8.82`, but floor was not added to reward

The identically trained ablation without Target regime one-hots produced validation `0.00` and unseen holdout `-0.45`, ACT 5/45, with one negative fill. Target regime conditioning therefore changed the sign on this holdout. The primary positive value came from one 18-share `UP_2` fill in Target-public regime cluster 6; validation had no fills. The artifact's preregistered mechanical rule reports `KEEP` because holdout MTM and the holdout oracle were positive, but this is not enough fill-level support for promotion.

Decision: **NEED_MORE_DATA for policy validity; KEEP only as a candidate representation/ranking direction.** Do not tune on these 15 holdout markets or retrospectively add a cluster-6 threshold. The next experiment must follow a small-first gate: freeze the V4 model and action rules, run a small chronological pilot on new Tape V1 markets, and proceed to a larger replication only if the pilot has a positive feasible oracle, nonzero actual fills, and no negative realized MTM. Long runs are allowed without special approval based solely on duration; do not stop or wrap up because 30 minutes elapsed.

## 2026-08-23 — Target-trained strict-public side prior under HFT execution V1

This iteration tested a new use of Target data without returning to R2/Target action imitation. Target behavior supplied only an offline side prior; HftBacktest outcomes remained the economic judge, and WAIT remained a formal runtime action. Target own inventory/fills, `pMaker`/`pTaker`, future Target actions, winner, settlement, and PnL were excluded from the model.

First, a deterministic Target-next-2s timing/value alignment test used HftBacktest counterfactuals at nine public time-grid checkpoints per market. The smoke3 passed, but the unchanged-rule seven-market expansion rejected Target placement timing as an ACT gate:

- expansion Target-labelled checkpoints: 30; free oracle `+9.58325` MTM, or `+0.319442` per checkpoint
- no-Target controls: 33; free oracle `+11.16`, or `+0.338182` per checkpoint
- Target-side oracle `+4.59` vs opposite-side oracle `+3.82325`
- fixed Target-side offset0 `+1.89`, but offset1 `-1.26` and offset2 `-1.08`
- decision: **REJECT Target placement timing as the execution-value gate**; no threshold/horizon rescue

The retained partial hypothesis was that Target side choice, not Target timing, could be useful. A six-feature strict-public model used only `secondsLeft`, `directionScore`, and UP/DOWN bid/ask. It was frozen before the pilot HFT dataset was generated. Training used the earliest 48 of 58 Target markets (4,887 action rows); chronological Target validation used the latest 10 markets (557 rows): balanced accuracy `0.6874`, ROC AUC `0.7338`, confidence>=0.60 coverage `70.56%`.

The HFT pilot used five later **opened-development** markets (`1555937,1556296,1556493,1557291,1557533`), which the user explicitly allowed. None overlapped Target training or the timing smoke/expansion, and none belonged to official HFT Forward, sealed 2026-08-16, or Supervisor final75-99. There were 45 deterministic checkpoints and 270 counterfactual passive actions. Execution semantics were HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, 5-second partial-fill horizon, 1-second post-fill MTM, quantity 18, offsets 0/1/2, plus WAIT.

Frozen confidence-gated policy (`confidence>=0.60 -> predicted-side offset0`, else WAIT):

- WAIT 15 / ACT 30 (`66.67%` ACT); 14 chosen actions actually filled, total 231.1 shares
- learned-policy realized 1s MTM: `+1.3723`
- same ACT checkpoints with public `directionScore` sign: `-7.65`
- free WAIT+all-action oracle ceiling: `+11.7223`
- predicted-side oracle: `+6.2323`; opposite-side oracle: `+5.8755`
- always-ACT predicted-side offset0: `-1.9577`; always-ACT directionScore offset0: `-8.19`
- per-market learned rewards: `-0.18,+0.64,+0.36,+0.28,+0.27`

All preregistered conditions passed, so the result is **KEEP_COMPONENT**, not policy graduation. The negative always-ACT result is important: the evidence supports the combination of a Target-trained side prior and explicit WAIT, not continuous Target side imitation. One of five markets was negative and the predicted-side oracle advantage was only `+0.3568`, so no large run or live promotion is justified yet.

Artifacts:

- `tools/analyze_target_hft_actionpoint_value_pilot_v1.py`
- `target_hft_actionpoint_value_pilot_v1_preregistered.json`
- `target_hft_actionpoint_value_smoke3_v1.json`
- `target_hft_actionpoint_value_pilot_v1_report.json`
- `target_hft_actionpoint_value_pilot_v1_expand7_preregistered.json`
- `target_hft_actionpoint_value_expand7_v1.json`
- `target_hft_actionpoint_value_pilot_v1_expand7_report.json`
- `tools/train_evaluate_target_public_side_prior_hft_pilot_v1.py`
- `target_public_side_prior_hft_pilot_v1_preregistered.json`
- `target_public_side_prior_hft_pilot_v1_frozen_manifest.json`
- `target_public_side_prior_hft_pilot_v1_target_frozen_report.json`
- `target_public_side_prior_hft_pilot_v1.joblib`
- `target_public_side_prior_hft_pilot_v1_hft5.json`
- `target_public_side_prior_hft_pilot_v1_report.json`

Highest-value next experiment: put the frozen Target side probability/confidence into the simulator-generated HFT action-value dataset as an **auxiliary state feature**, then train a WAIT-inclusive contextual value ranker on HFT outcomes. Model fill probability, fill-conditioned MTM, and portfolio-floor change separately; include interactions between side-prior confidence and spread/queue/latency/fill-history regime. Run one separately preregistered five-market chronological pilot against an otherwise identical no-Target-prior ablation. Expand only if realized MTM is positive with multiple actual fills and beats the ablation; do not tune on this five-market result; replication duration alone is not a stop/notice boundary.

## 2026-08-23 — Target-prior HFT value ranker and frozen-policy replication

This iteration executed the previous highest-value experiment without changing the frozen Target side model. It used the existing V4 HftBacktest action dataset, so no parallel simulator or dream-fill path was created. Target side probability/confidence entered only as strict-public auxiliary features; HftBacktest actual-fill outcomes were the economic labels.

### Hybrid HFT value ranker

Artifacts:

- `tools/hft_target_side_prior_value_rank_v1.py`
- `hft_target_side_prior_value_rank_v1_preregistered.json`
- `hft_target_side_prior_value_rank_v1_frame.joblib`
- `hft_target_side_prior_value_rank_v1_model.joblib`
- `hft_target_side_prior_value_rank_v1_train_report.json`
- `hft_target_side_prior_value_rank_v1_frozen_manifest.json`
- `hft_target_side_prior_value_rank_v1_pilot5_report.json`

The HFT learner reused the fixed three-head architecture: `P(fill5s)`, `E(filled shares|fill)`, and `E(1s MTM/share|fill)`. The primary added ten locked Target-prior/action interaction features; the ablation was otherwise identical. Score was `P(fill) * E(shares|fill) * E(MTM/share|fill)`. Portfolio floor was not added to reward; actions with negative deterministic full-18-share floor delta were excluded, and actual-fill floor change was audit-only. WAIT had value zero and won whenever the best feasible score was non-positive.

Training used the existing first 60 chronological V4 markets, validation the next 15, and the pilot the first five V4 holdout markets (`1530879,1531154,1531272,1531343,1531361`). These were opened-development markets but later than train/validation and not used by the earlier side-prior HFT5. Execution remained HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, partial fills, 5-second fill horizon and 1-second post-fill MTM. No official Forward or sealed market was used.

Results:

- train primary: oracle `+37.1998`, learned `+33.9366`, ACT 120/180
- validation primary: oracle `+2.6062`, learned `-2.9534`, ACT 31/45, 10 fills
- validation ablation: learned `-3.0434`, ACT 30/45, 9 fills
- pilot primary: feasible oracle `+0.7146`, learned `0.0`, WAIT 11 / ACT 4, **0 actual fills**
- pilot ablation: learned `0.0`, WAIT 10 / ACT 5, **0 actual fills**
- primary pilot ACT rate `26.67%`; all four actions were zero-fill

The preregistered decision is **NEED_MORE_DATA** because the pilot had positive oracle support but no chosen fills. Do not expand this model: validation already supplied ten primary fills and negative realized MTM, while Target features improved it over the ablation by only `0.09`. OOS fill prediction was effectively uninformative (`fill AUC 0.4705` validation and `0.5130` pilot). The direct global hurdle learner is not capturing sparse fill support.

### Frozen Target side/WAIT replication

To separate HFT-learner failure from Target-prior instability, the original frozen policy was replayed unchanged on the immediately following five chronological V4 holdout markets (`1531383,1531431,1531479,1531482,1531569`): confidence>=0.60 chose predicted-side offset0; otherwise WAIT. It was preregistered after the hybrid pilot but before calculating these policy outcomes.

Artifacts:

- `tools/analyze_frozen_target_side_prior_replication_v1.py`
- `target_public_side_prior_replication5_v1_preregistered.json`
- `target_public_side_prior_replication5_v1_report.json`

Replication results:

- free oracle `+7.11`
- WAIT 3 / ACT 12 (`80%` ACT), 6 actual fills / 108 shares
- frozen Target-side policy MTM `+0.81`
- directionScore sign on the same ACT checkpoints `+0.90`
- predicted-side oracle `+4.05` vs opposite-side oracle `+3.42`
- always-ACT predicted-side offset0 `-0.18`
- per-market policy MTM: `+1.08,-1.35,0,+0.09,+0.99`

Coverage was sufficient and the policy was positive, but it failed the locked incremental-value condition against directionScore. Decision: **REJECT this frozen Target side/WAIT policy as a replicated incremental execution component.** Retain only the weaker representation clue that its predicted-side oracle exceeded the opposite-side oracle; do not tune confidence or offset on either five-market cohort.

Combined conclusion: Target public behavior contains some side information, but neither adding it to the current global HFT hurdle model nor replaying the frozen confidence gate has produced robust incremental OOS execution value. The bottleneck is now actual-fill support generalization, not Target action imitation.

Highest-value next experiment: replace the global fill classifier with a checkpoint-grouped sparse-positive fill-support ranker. Train pairwise within each checkpoint to rank actual-filled actions above unfilled actions, keep WAIT as an explicit competitor, and model fill-conditioned markout only after support selection. Compare against the current global HGB fill head using the remaining chronological development markets; use one small five-market pilot first. Do not reuse Target confidence as an ACT threshold. Replication duration alone is not a stop/notice boundary.

## 2026-08-23 — sparse support / sequence rejects and terminal-objective pivot

### Grouped fill support and raw order-flow sequence representation

The preregistered checkpoint-grouped fill-support architecture separated `P(any feasible fill)`, within-checkpoint filled-vs-unfilled ranking, filled-size, and q25 fill-conditioned markout. It reused the V4 HftBacktest frame and kept WAIT formal. Validation failed the locked sign-flip gate:

- train: oracle `+37.1998`, policy `+26.23715`, ACT 101/180
- validation: oracle `+2.6062`, policy `-2.0534`, ACT 26/45, 8 actual fills
- validation checkpoint-any-fill AUC `0.564`
- decision: **REJECT_BEFORE_PILOT**; the final five V4 pilot markets were not evaluated

Artifacts: `tools/hft_grouped_fill_support_rank_v1.py`, `hft_grouped_fill_support_rank_v1_preregistered.json`, `hft_grouped_fill_support_rank_v1_model.joblib`, and `hft_grouped_fill_support_rank_v1_train_report.json`.

A larger representation pivot then used 5 seconds of strict-past action-oriented L2/order flow at 100ms resolution. A preflight across V4 found dense real event paths (median 489 depth events and 22 trades per 5 seconds; zero depth-event checkpoints 0%). A fixed three-layer Conv1d encoder was self-supervised on 3,449 sequences from 100 strictly earlier clean Tape V1 markets, then frozen before HFT value fitting. The representation learned its pretext task (validation normalized MSE `0.8568`) but did not transfer to execution value:

- HFT train fill AUC `0.9915`, policy `+28.7601`
- chronological validation fill AUC `0.4903`, oracle `+2.6062`, policy `-3.31065`
- validation WAIT 21 / ACT 24, 9 filled acts
- grouped static baseline on the same validation was `-2.0534`
- decision: **REJECT_BEFORE_PILOT**; no CNN/threshold rescue and no final-five reveal

Artifacts: `tools/preflight_hft_l2_event_sequence_v1.py`, `tools/build_hft_orderflow_sequence_dataset_v1.py`, `tools/train_hft_orderflow_sequence_value_v1.py`, `hft_l2_event_sequence_v1_preflight.json`, `hft_orderflow_sequence_value_v1_preregistered.json`, `hft_orderflow_sequence_value_v1_pretrain100_amendment.json`, dataset/model/encoder artifacts, and `hft_orderflow_sequence_value_v1_train_report.json`.

Interpretation: raw event representation is feasible and information-rich, but isolated one-step passive fill/value labels remain non-stationary across market regimes. More encoder or threshold tuning is not justified.

### Terminal economic objective tests

To test whether 1-second MTM was the wrong economic label, a new three-head model learned `P(fill)`, `P(action side wins | actual fill)`, and conditional fill size, with actual HftBacktest-filled settlement PnL as the offline reward and winner excluded from runtime features. Training used 140 earlier markets; the next 10 chronological V3 markets were validation. The learned policy failed despite a large terminal oracle:

- train oracle `+1421.6433`, learned `+98.6964`
- validation oracle `+103.6019`, learned `-8.548`
- validation WAIT 8 / ACT 22, 8 filled acts
- a training-selected directionScore-side offset2 diagnostic was `+45.092` on validation
- decision: **REJECT_BEFORE_HOLDOUT**; the final 20 V3 winner labels remain unopened under this contract

Artifacts: `tools/hft_terminal_value_policy_v1.py`, preregistration + invalid-action amendment, frozen model, and `hft_terminal_value_policy_v1_validation_report.json`.

The positive diagnostic was frozen without threshold changes and tested on a later B12 five-market cohort. It failed immediately: WAIT 0 / ACT 10, one actual fill, terminal reward `-10.62`, oracle `+4.8792`. Decision: **REJECT_BEFORE_EXPANSION**; B11-B9 were not opened for this replication.

Terminal joint-pair economics were also audited on the existing opened 40-market pair-training cohort. Fixed pair offsets 0/1/2 returned `-72.0348 / -34.92 / -42.0618`. The WAIT+best oracle was `+120.1356` at 25% ACT, but 14/20 oracle ACTs were winner-selected one-sided fills and only six were both-filled pairs. Combined with the earlier learned pair-gate OOS failure, this is **REJECT** for the current joint-pair action family; do not generate a new pair cohort from this oracle.

Artifacts: `tools/analyze_hft_direction_terminal_value_replication_v1.py`, its preregistration/pilot report, and `tools/analyze_hft_pair_terminal_ceiling_v1.py` with `hft_pair_terminal_ceiling_v1_train40_report.json`.

### Taker terminal tests and official-cohort quarantine

An exploratory Taker terminal aggregation initially reported `+38.4336`, but a directory-level audit found that nine of its ten markets already belonged to the official HFT Forward cohort. That aggregation is **INVALID_FOR_RESEARCH_SELECTION** and is explicitly quarantined in `hft_taker_terminal_ceiling_v1_official_contamination_audit.json`; none of its numbers may select or tune a candidate.

Two clean, pre-official five-market pilots then used newly generated HftBacktest Taker actions at placement-independent 10%/90% checkpoints:

1. Fixed public `directionScore` sign, no threshold: WAIT 0 / ACT 10, 10 fills, terminal `-1.2996`, 1s MTM `-0.6696`, oracle terminal `+36.3384`. **REJECT**.
2. R2-like direct outcome model: logistic `P(UP wins)` trained on 140 earlier markets, with runtime score `18 * (P(win side)-ask) - fee` and WAIT=0. Chronological outcome diagnostic had AUC `0.8519`, but the frozen HFT pilot was WAIT 3 / ACT 7, 7 fills, terminal `-8.6616`, direction baseline `-9.738`, oracle `+58.1256`. **REJECT**; high outcome AUC and less-negative value are not sufficient.

Artifacts: `tools/hft_native_taker_timegrid_v0.py` (reused generator), `tools/analyze_hft_direction_taker_terminal_pilot_v1.py`, `tools/hft_taker_terminal_outcome_value_v1.py`, their preregistrations, action datasets, frozen model and reports.

## 2026-08-23 — integrated HFT control-system pivot

The correct research unit is now the whole Target/R2-style economic/execution cycle, rebuilt under HFT transitions: passive two-sided making, passive actual-inventory repair, active intervention, WAIT/KEEP, cancel/ACK and unresolved remainder ownership must share one actual-fill state trajectory. Isolated Maker, repair and Taker heads cannot be interpreted as a complete strategy, and the experiments above show that individually plausible heads do not compose automatically.

New contract and preflight:

- `hft_integrated_control_semimdp_v1_contract.json`
- `tools/preflight_hft_integrated_control_semimdp_v1.py`
- `hft_integrated_control_semimdp_v1_preflight.json`

The contract formally includes `WAIT_KEEP`, `PASSIVE_MAKE_PAIR`, `PASSIVE_ALPHA`, `PASSIVE_REPAIR`, `CANCEL_RETURN`, and `TAKER_INTERVENE`. Only confirmed HftBacktest fills mutate inventory/cost. Target history may pretrain a public-state/outcome representation, but Target future action and Frozen R2 intents are not teachers or runtime inputs. Winner is offline terminal reward only.

The existing V10 objective-token adapter is reusable only as a venue/lifecycle layer: it already covers `ACKED_OPEN`, partial fills, `CANCEL_REQUESTED`, terminal-zero-fill, Taker completion, unresolved remainder ownership, fees and actual-fill inventory. Its desired R2/Target ledger and future-counterfactual arbitration teacher must not be reused as the new policy.

Preflight decision: **READY_FOR_ONE_MARKET_SYSTEM_EPISODE_SMOKE**. Pilot markets `1572594,1572805,1573000` all have `COMPLETE_FORWARD_V1` tapes, are pre-official and non-sealed. Next experiment: measure one-market runtime, then compare a fixed integrated program and system-program oracle against WAIT plus isolated passive-pair/passive-alpha/repair/Taker ablations on these three markets. Expand to a chronological semi-MDP trajectory dataset only if the integrated loop has positive terminal value, multiple actual fills, materially beats every isolated ablation, and does not leave worse unresolved terminal exposure.

### R2 cycle-preservation amendment before outcome generation

The initial integrated-control wording removed too much R2 state. It was corrected before any system-episode outcome was generated. Frozen R2 remains the high-level economic logic and portfolio controller: it owns making/alpha mode, desired portfolio action, side/exposure bounds, repair responsibility, active-intervention authorization, and option transitions. The HFT layer is a constrained option executor beneath it, not a replacement Strategy Brain.

This preserves the established separation between Desired Portfolio Action and Execution Choice. The executor may choose `WAIT_KEEP_OBJECTIVE`, passive price/offset/size within the authorized budget, cancel/ACK handling, passive repair, and protected Taker completion, but it may not freely change the R2 economic objective. Confirmed HftBacktest fills and lifecycle events update actual state; after a material fill, completion, failure, expiry, or unresolved-remainder handoff, the same Frozen R2 controller is evaluated again to generate the next high-level objective. Individual optimistic PAPER orders/fills/timestamps are not imitation targets.

The amended contract adds hard cycle invariants for single objective ownership, partial-fill/cancel persistence, terminal cancel ACK before replacement, explicit Taker remainder ownership, exposure-preserving repair, formal WAIT, and mandatory return-to-logic. Evaluation now includes desired-portfolio tracking and invariant violations in addition to terminal PnL/floor/exposure. The next smoke must verify a complete `R2 option -> HFT lifecycle -> actual-fill feedback -> R2 re-evaluation` cycle; any apparent value requiring violation of R2 option authority is infeasible, not oracle value.

## 2026-08-23 — R2 cycle-preserving HFT smoke and static-program rejection

Hypothesis: preserve Frozen R2 as the logic/portfolio controller and replace only the option execution transition. A fixed executor keeps the current R2 objective; R2 continues to generate `PASSIVE_MAINTAIN`, `PASSIVE_REPAIR`, and authorized Taker decisions from actual HftBacktest fills. No Target/future arbitration teacher is loaded.

Files/artifacts:

- `tools/hft_r2_cycle_preserving_execution_smoke_v1.py`
- `tools/hft_r2_cycle_preserving_static_program_smoke_v1.py`
- `tools/hft_r2_cycle_option_scope_ablation_v1.py`
- `hft_r2_cycle_preserving_execution_smoke_v1.json`
- `hft_r2_cycle_preserving_execution_smoke_v1_offset0.json`
- `hft_r2_cycle_preserving_static_program_smoke_v1_preregistered.json`
- `hft_r2_cycle_preserving_static_program_smoke_v1_report.json`
- `hft_r2_cycle_option_scope_ablation_v1_preregistered.json`
- `hft_r2_cycle_option_scope_ablation_v1_report.json`

Cohort/execution semantics: development market `1572594` only, `COMPLETE_FORWARD_V1`, pre-official and non-sealed. HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response, partial fills, protected Taker lifecycle, actual-fill-only inventory. This is an architecture/action-family smoke, not chronological OOS or graduation evidence.

The cycle semantics passed. In the fixed offset1 run, Frozen R2 made 203 high-level decisions: `PASSIVE_MAINTAIN=11`, `PASSIVE_REPAIR=192`, with 187 WAIT / 16 MAKER execution choices and three mode transitions. Nine Maker fills plus one authorized R2 Taker fill were observed; every fill was visible to the controller before its next step. Actual inventory exactly matched the Hft fill ledger, no free executor Taker action occurred, and cycle-invariant violations were zero. Twenty optimistic paper inventory events were suppressed.

Economically, the fixed program failed: offset1 realized `-41.2128`, final actual |net| 108 shares, final desired-target tracking error 252. The locked one-market static-program ceiling was:

- WAIT `0`
- offset0 `-30.0244`
- offset1 `-41.2128`
- offset2 `-30.2400`
- constrained oracle = WAIT; oracle ACT rate `0%`

Decision: **REJECT_STATIC_OFFSET_FAMILY** and do not expand to the preregistered three-market pilot.

A second locked diagnostic tested whether the failure was isolated to one R2 option. `EXECUTE_MAINTAIN_ONLY` realized `-17.46` with 72 Maker shares and only 18-share final tracking error. `EXECUTE_REPAIR_ONLY` never became reachable because without a preceding actual Maker fill R2 correctly stayed in `PASSIVE_MAINTAIN`; it matched WAIT at `0`. `EXECUTE_ALL` was `-30.0244`. Oracle remained WAIT, so **REJECT_STATIC_OPTION_SCOPE**. This confirms the user's systems point: repair is endogenous to prior making fills, so separately trained/static heads cannot reconstruct the cycle.

The offset0 audit also reproduced the already-known V8/V9/V10 integration boundary on a new market: 24 R2 Maker intents created 23 target increments / 410.92 desired shares, while only two same-objective reissues were coalesced and 14 responsibility tokens were created. This does not justify reopening committed-inventory, side-credit, order-token, queue-cancel, or threshold tuning; those paths were already rejected in canonical history.

Next highest-value experiment: a simulator-generated **multi-step option value-to-go dataset**, not another point classifier or static program. Each sample must start at a Frozen R2 option/mode transition, include actual inventory plus live-child/ownership/partial/cancel/Taker-completion history, keep WAIT formal, and score the full trajectory until option completion/return-to-logic. Target data may pretrain public-state representation, but option action values must come from HftBacktest counterfactual outcomes. Start on one new pre-official market, then only expand chronologically if a constrained ACT trajectory has positive value and zero cycle violations.

## 2026-08-23 — option-program dataset smoke, fixed-program rejection, and feasibility-conditioned ceiling

Hypothesis: the execution learner needs a trajectory-level R2 option program rather than an isolated placement label. At each Frozen R2 option transition, state includes strict-past public execution regime, actual inventory/cost, desired tracking, live Maker/Taker children, cumulative/leaves quantity, cancel/replace state, remainder ownership, objective tokens, and recent actual fills. WAIT is action 0. Counterfactual actions remain constrained to R2-authorized passive maintain/repair programs; winner and Target future action are excluded.

Files/artifacts:

- `tools/hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py`
- `tools/hft_r2_cycle_preserving_execution_smoke_v1.py`
- `tools/hft_r2_cycle_option_program_dataset_v1.py`
- `tools/export_hft_r2_cycle_option_program_minari_v1.py`
- `tools/evaluate_hft_r2_cycle_option_program_replication3_v1.py`
- `tools/analyze_hft_r2_cycle_option_program_ceiling2_v1.py`
- `tools/preflight_predict_fun_api_ws_v1.py`
- `hft_r2_cycle_option_program_dataset_v1_preregistered.json` and report
- `hft_r2_cycle_option_program_replication3_v1_preregistered.json` and report
- `hft_r2_cycle_option_program_ceiling2_v1_preregistered.json` and per-market/combined reports
- `hft_r2_cycle_option_program_minari_v1_validation.json`
- `predict_fun_official_api_ws_preflight_v1.json`

Execution semantics stayed performance-grade: HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response, partial fills, actual-fill-only inventory, protected Taker lifecycle, mandatory return to Frozen R2 logic, and zero tolerance for lifecycle-invariant violations. All research markets were COMPLETE_FORWARD_V1, pre-official, non-sealed opened-development markets not previously used by this option-program contract.

The one-market dataset smoke on `1573252` evaluated 13 locked programs: WAIT plus passive-maintain offset 0/1/2 crossed with passive-repair WAIT/offset 0/1/2. `MAKE_OFFSET0__REPAIR_OFFSET0` achieved terminal PnL and worst-case floor `+52.642`, 305.86 Maker shares, zero Taker shares, final paired coverage `0.94069`, 18.14-share absolute net, 5/5 ACT option transitions, and zero cycle violations. The resulting offline dataset contains 13 episodes, 54 transitions, 79 finite strict-past features, and four encoded option actions. Minari round-trip validation is exact under Python 3.12 (`btc5m/hft-r2-cycle-option-program-1573252-v2`). This is dataset/tooling validation and an in-sample oracle, not learned-policy or OOS evidence.

The promising fixed program was frozen without tuning and replicated chronologically on `1573447,1573662,1573848`. `FULL_CYCLE_OFFSET0` realized `-14.4, -29.448, -30.1104`, total `-73.9584`, with 100% ACT and zero positive markets; `MAKE_ONLY_OFFSET0` totaled `-36.648`. WAIT totaled `0` and was the restricted oracle on every market. Decision: **REJECT_FIXED_PROGRAM**; do not tune offsets on these revealed markets.

A new unchanged two-market action-family ceiling on `1574038,1574352` then tested whether contextual selection could still have economic room. Using terminal worst-case floor relative to WAIT as the primary value, `1574038` had a valid oracle program `MAKE_OFFSET0__REPAIR_OFFSET2` at `+48.96` floor (`+84.96` PnL audit), 576 Maker shares, zero Taker shares, paired coverage `0.9375`, 36-share absolute net, and zero violations. On `1574352`, every valid ACT program was negative and the oracle was WAIT at `0`; two additional ACT programs were marked infeasible because a terminal fill had no subsequent R2 re-evaluation before tape end. Combined valid oracle floor ceiling was `+48.96` across two markets, with one ACT-oracle market and one WAIT-oracle market. Learned-policy realized value remains **not measured**; training now would conflate value with lifecycle feasibility.

Decision: **NEED_MORE_DATA_FEASIBILITY_MASK_BEFORE_CONTEXTUAL_TRAINING**. The fixed full-cycle program is rejected, while sparse regime-conditioned full-cycle value remains possible. The next experiment should first learn or hard-mask whether an option program can complete and return control within the remaining lifecycle horizon, then rank WAIT versus only feasible programs using fill probability, fill-conditioned trajectory value, and portfolio-floor improvement. Do not install/train the heavier d3rlpy/Torch stack until this mask is represented and a larger chronological dataset exists.

Predict.fun official data access is ready for that next dataset. A read-only preflight used the existing `PREDICT_FUN_API_KEY`: REST `/v1/search` returned 200, and the official WebSocket accepted `predictOrderbook/1610061` and delivered a 26-bid/24-ask snapshot with version/order-count/timestamp metadata. No orders, databases, services, or Echtgeld processes were modified. Decision: **READY_USE_EXISTING_OFFICIAL_COLLECTOR** rather than building a parallel feed.

## 2026-08-23 — own-state event re-entry amendment and duplicate-method audit

The two terminally infeasible option-program rows were traced to a simulator scheduling defect, not to a missing economic feasibility classifier. HftBacktest own-order state had been checked only when a public snapshot arrived, so a late confirmed R2-authorized Taker fill could reach tape end without another controller step. Research tooling now optionally polls own state every 250ms and re-enters Frozen R2 only after a newly confirmed fill or explicit lifecycle return, carrying forward the last strictly observed public snapshot as-of that event. Defaults remain unchanged.

On revealed architecture market `1574352`, the correction changed the trajectory and removed the lifecycle violation: own-state re-entries `0 -> 18`, fill-triggered re-entries `0 -> 18`, actual-fill-without-re-evaluation violations `1 -> 0`, and Taker filled shares `18 -> 0`. Actual inventory still exactly matched the HftBacktest fill ledger and unauthorized executor Taker attempts stayed zero. Economically it did not help: worst-case floor moved from `-14.04` to `-14.58`. Therefore this is **KEEP_SIMULATOR_CORRECTION_ONLY**, not execution alpha.

The preregistered unchanged 13-program continuation on chronological development markets `1574538,1574737` had zero lifecycle violations. Using the preregistered terminal worst-case floor rather than winner-dependent settled PnL, `1574538` had five positive-floor programs and oracle `MAKE_OFFSET0__REPAIR_OFFSET2 = +36.0`; `1574737` had no positive-floor ACT and oracle WAIT `0`. Combined floor oracle was `+36.0`, market-level oracle ACT rate `50%`, oracle transition ACT rate `77.78%`, and learned-policy realized value was not measured. Positive settled PnL with negative floor on `1574737` is explicitly not counted as value.

A project-wide de-duplication audit found that actual-fill feedback (V8-V10), integrated actual-fill route control, maintain/repair offsets, KEEP/WAIT/REPLACE/RETURN handoff, and same-R2-curriculum HFT retraining had already been implemented or tested and were insufficient or rejected. The polling change is novel only as an event-clock simulator correction. Decision: **REJECT_AS_NEW_POLICY_DIRECTION**; do not train another contextual offset gate, CQL or IQL policy on this unchanged economic action space. Artifact: `hft_r2_cycle_ownstate_event_reentry_v1_duplicate_audit.json`.

The next genuinely distinct candidate found by project search is the analytic-only `POST_INTERVENTION_CONVERSION_MODE` in `target_vs_r2_alignment_conversion_20260823_v1.json`. Target data show a sustained post-first-intervention Maker+Taker inventory conversion, while Frozen R2 HFT continues adverse-side Maker pressure. No equivalent full-loop HftBacktest implementation was found. This must be tested, if at all, as a research-only sustained mode using strict-past public direction plus confirmed actual inventory/lifecycle state, with repair responsibility and R2-authorized Taker insurance preserved. It is not the already-rejected one-checkpoint `POST_FIRST_TAKER` Target action classifier, a repair threshold, or side imitation. Pre-register and run only a minimal full-loop ablation after checking all post-Taker/re-entry artifacts for semantic overlap.

## 2026-08-23 — conversion-mode de-dup correction and observation-only occupancy reject

Deeper project search corrected the preceding novelty statement before any conversion-mode code was written. `v32` already blocked unfavorable dominant-side Maker growth, `v33` gated unfavorable acquisition, and `v34` routed unfavorable Maker acquisition to the opposite side inside an existing Maker + passive-repair + Taker controller. The two V34 HftBacktest smokes were `-18.5112` with `0/2` positive markets and `-58.14` with `0/3`, or `-76.6512` and `0/5` combined. Target post-Taker SAME/OPP sequential re-entry hazards were also already trained and chronologically evaluated; when bridged to OUR counterfactual states, their economic ranking was weak or inverted (for example OPP-vs-switch AUC `0.4293` on 148 OUR OPEN states). Therefore a hand-written `POST_INTERVENTION_CONVERSION_MODE`, favorable-side gate, or opposite-side router is **REJECT_AS_DUPLICATE_DIRECTION**.

The remaining materially different hypothesis came from mature cross-dynamics imitation-from-observation research: use Target state transitions rather than Target actions. Project search found no existing GAIL/GAIfO/SMODICE/IOSTOM or transition-occupancy implementation. A locked small preflight used the SMODICE-style density-ratio idea only as a prerequisite, not as a policy. Target historical post-Taker state transitions were expert observations; the chronological Target forward set was representation validation. HFT density-ratio training used existing full-cycle program markets `1573252,1574038,1574352`; economic ranking holdout used later `1574538,1574737`. Target SAME/OPP labels, future actions, winner and settlement PnL were excluded. Shared transition features were public-direction-oriented and scale-normalized; the model was fixed class-balanced logistic regression with no threshold sweep.

Files/artifacts:

- `tools/hft_target_transition_occupancy_preflight_v1.py`
- `hft_target_transition_occupancy_preflight_v1_preregistered.json`
- `hft_target_transition_occupancy_preflight_v1_report.json`

The representation separated chronological Target-forward from HFT holdout transitions strongly: AUC `0.880756`, Target mean log density ratio `+2.10574`, HFT `-0.94463`. This was not success because the locked economic test failed decisively. Across 24 valid ACT programs, Target-likeness versus terminal worst-case floor had pooled Spearman `-0.429565`; top-score quartile mean floor was `-59.28` versus bottom quartile `-22.08`. Per market, correlation was only `+0.153846` on `1574538` and `-0.237762` on `1574737`. On `1574538`, the highest-score program happened to equal the `+36` floor oracle; on `1574737`, highest-score `MAKE_OFFSET0__REPAIR_OFFSET0` had floor `-54.72`, while formal WAIT remained `0` and correctly dominated every ACT program.

Execution semantics were inherited from the existing full-cycle artifacts: HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, partial fills, confirmed-fill-only inventory and zero-tolerance lifecycle gates. No new simulator run, official HFT Forward tuning, sealed market, or Echtgeld change occurred. Candidate set contained formal WAIT plus 12 ACT programs per market, but no learned policy was executed; learned-policy realized value remains unmeasured. Combined holdout floor oracle including WAIT was `+36`, with market oracle ACT rate `50%`.

Decision: **REJECT_TARGET_TRANSITION_OCCUPANCY_AS_ECONOMIC_SURROGATE**. Being more Target-like under a dynamics-mismatched representation is not portable economic value. Do not install/train full SMODICE, GAIL, diffusion imitation, CQL or IQL from this reward, and do not rescue it with discriminator features or thresholds.

Highest-value next experiment: require an action-conditioned HFT reachability/value object rather than another imitation reward. Target trajectories may propose latent progress coordinates only after a transferability audit, but every action must be learned from HftBacktest `P(s' | s,a)` and terminal floor/value. Before generating more data, audit whether the existing option-program transitions have enough same-state multi-action support to identify an action-conditioned dynamics model; reject immediately if checkpoint overlap is too sparse. This is a data-identifiability gate, not another policy fit or offset sweep.

## 2026-08-23 — external Queue-Reactive world-model adapter feasibility

External-tool de-duplication separated a genuinely new use of the mature Queue-Reactive LOB model from directions already rejected here. The earlier queue-reactive scalar feature augmentation and raw-order-flow CNN tried to predict isolated HFT value directly and failed OOS. The new object is instead a **training-only stochastic multi-step queue world model** intended to generate action-conditioned option trajectories; it may never count as performance evidence, which remains unseen Predict Tape V1 replay under HftBacktest.

The official external implementation at Queue-Reactive commit `3080096cc0c79f43f1b82112cbde713710c014f6` requires exchange-sequenced Add/Cancel/Trade events with pre-event LOB state. Predict Tape V1 contains 200ms-class L2 net deltas plus second-granular raw true matches, so direct MBP-10 ingestion is rejected. A locked adapter preflight used existing opened-development, pre-official, non-sealed option-program markets: train `1573252,1574038,1574352`, then chronological validation `1574538,1574737`. No policy, HftBacktest action run, winner, settlement PnL, official cohort, or sealed cohort was used.

All original feasibility gates passed: median market update gap `200ms`; validation state rows `2,957`; train-only coarse queue-regime coverage `0.88705`; validation true-match quantity aligned to same-side/same-price negative-depth capacity `0.96429`; trade-count alignment `0.98216`. A post-run integrity amendment was added before null outcomes because 94%-99% of delta updates batch multiple price changes and only about 4%-7% of total removal capacity is trade-attributed. Fixed nulls then showed time-shift `0.28236`, side-flip `0.54818`, and price-shift `0.27022`; observed alignment beat the strongest null by `0.41610`. Both validation markets independently retained about 96% quantity alignment.

Decision: **KEEP_NET_QR_WORLD_MODEL_PILOT**, not policy success. Direct per-order Add/Cancel reconstruction remains unidentifiable, but a trade-aware net-depth transition model has sufficient cadence, chronological state support, and nontrivial match timing/side/price specificity to justify one small generator test. Artifacts: `tools/preflight_hft_queue_reactive_world_model_adapter_v1.py`, `hft_queue_reactive_world_model_adapter_v1_preregistered.json`, and `hft_queue_reactive_world_model_adapter_v1_report.json`.

Next highest-value experiment: fit the smallest expiry-regime-conditioned net-event generator on the three train markets and compare generated versus real chronological validation stylized facts only: spread/imbalance occupancy, queue depletion/replenishment, inter-update and trade clustering, cross-side dependence, and transition-horizon coverage. Do not train an execution policy until this independent generator test passes. If it passes, use it only to expand action-conditioned full-cycle option trajectories, then return every candidate to unseen real-tape HftBacktest with formal WAIT and terminal worst-case floor.

## 2026-08-23 — Target terminal-goal representation and HFT safe-floor reachability pivot

Hypothesis: learn the economic terminal goal or cycle-level payoff shape from Target, but learn all WAIT/passive/pair/selective-Taker action value from HFT execution dynamics. This is not Target action imitation and does not require HFT to reproduce Frozen R2 intent. External UVFA/successor-feature research supports separating goal/reward representation from environment dynamics rather than transferring the demonstrator's action policy across mismatched dynamics.

Project-wide de-duplication found local Target inverse choice, action hazards, transition occupancy, public-book regime KMeans, first-Taker conversion, lifecycle controllers, and R2 cycle repairs, but no full-market winner-free terminal payoff-shape discovery followed by strict-past chronological goal inference. A locked 80-market preflight used the existing general one-second Target state grid rather than action-point-conditioned samples. Markets resolved from 2026-08-19 09:45 UTC through 2026-08-20 00:30 UTC; 2026-08-16, official HFT Forward, Supervisor final75-99, winner, PnL, and Target future actions were excluded. Split was chronological 48/16/16. Train-only StandardScaler + fixed KMeans(k=3) clustered `[paired coverage, floor/gross, best/gross]`; fixed 240s was primary, with 180s/120s diagnostics.

Files/artifacts:

- `tools/preflight_target_latent_terminal_goal_v1.py`
- `target_latent_terminal_goal_v1_preregistered.json`
- `target_latent_terminal_goal_v1_report.json`
- `tools/audit_hft_terminal_goal_reachability_v1.py`
- `hft_terminal_goal_reachability_v1_report.json`

The representation gate passed: train cluster counts were `13/14/21`, all-market standardized silhouette `0.29456`. Economically the clusters were not three rational goals. Cluster 0 was high-coverage positive-floor inventory (median coverage `0.95966`, floor/gross `+0.01046`); cluster 1 was directional optionality (coverage `0.82339`, floor/gross `-0.09814`, best/gross `+0.07214`); cluster 2 was high-coverage but overpaid/dominated inventory (floor/gross `-0.06401`, best/gross `-0.00401`). Cluster 2 is an execution failure state, not a goal to imitate.

At the primary 240s checkpoint, the fixed public logistic selector beat only the weak majority baseline in macro-F1: validation `0.21645` versus `0.13333`, unseen holdout `0.30682` versus `0.20290`. It did not beat current-shape persistence on validation (`0.46667`), and public-model log-loss was worse than the prior on both validation (`1.4830` versus `1.1539`) and holdout (`1.1349` versus `1.0686`). Public-plus-portfolio also failed the preregistered persistence gate. Decision: **KEEP_REPRESENTATION_NEED_MORE_DATA_SELECTOR**. The target-only preflight had no HftBacktest policy, WAIT/ACT rate, oracle value, or learned realized value.

A zero-rerun audit then mapped existing HftBacktest full-cycle artifacts into the economic terminal states. The partition was much stronger than any local execution knob:

- mature inventory-manifold 50: positive-floor `18/18` profitable, `+365.4927`; directional optionality `1/32` profitable, `-502.3830`. Revealed safe-floor oracle ACT rate `36%`, oracle value `+365.4927` versus always-run `-136.8903`.
- paired-working-order 30: positive-floor `13/13`, `+235.2993`; directional optionality `0/17`, `-282.8005`. Oracle ACT rate `43.33%`, value `+235.2993` versus always-run `-47.5012`.
- queue-progress 30: positive-floor `13/13`, `+229.4541`; directional optionality `0/17`, `-288.8222`. Oracle ACT rate `43.33%`, value `+229.4541` versus always-run `-59.3681`.
- withhold 30: positive-floor `8/8`, `+77.5152`; directional optionality `1/20`, `-132.2286`; dominated failure `0/2`, `-9.4392`.
- integrated semi-MDP 3: all three ended in directional optionality and totaled `-59.1804`.

These families overlap in markets and are not independent OOS evidence; the audit used revealed terminal state and cannot be deployed. Nevertheless, every family with positive-floor outcomes had positive aggregate value, while all five directional-optionality families had negative aggregate value. Winner and PnL were audit-only after reveal; execution semantics were inherited from the existing real HftBacktest artifacts with no dream fill or new simulator run.

Decision: **KEEP_SAFE_FLOOR_REACHABILITY_PIVOT**. The principal HFT failure is failure to reach a protected positive-floor cycle, not a small placement/cancel defect. The next policy object must be full-cycle safe-floor reachability/value under HftBacktest, with formal WAIT. Directional optionality becomes a separate economic strategy family and remains disabled unless it independently earns positive unseen execution value. Do not patch every R2 cycle after entry and do not use the Target terminal selector directly as a trading policy.

Next highest-value experiment: after a final semantic-overlap audit against prior pair-completion/insurance programs, generate a small HftBacktest counterfactual set whose actions are complete option programs from strict-past state: `WAIT`, protected positive-floor pair acquisition, and explicit abort/completion transitions. Score terminal floor and settled PnL separately. Expand only if at least one non-revealed contextual rule or learned ranker preserves positive realized value on a chronological unseen real-tape holdout.

## 2026-08-23 — flat-start contingent safe-floor option and static entry-gate rejection

The semantic-overlap audit found a genuinely distinct action program. Prior pair-completion/survival/insurance work began after Frozen R2 had already created first-leg inventory or tracking error; its one low-survival insurance ACT worsened PnL by `-3.5568`. Prior simultaneous-pair sweeps stopped at 5s MTM, and mature paired-working-order runs remained driven by R2 desired inventory. The new research-only option begins flat, reads no R2/Target/PAPER intent, submits equal UP/DOWN passive legs, stops surplus accumulation after confirmed fills, preserves the deficit passive child, and allows one cancel-to-ACK Taker completion only when the Taker limit price plus fee still locks terminal floor above zero. Formal WAIT is zero.

Files/artifacts:

- `tools/hft_safe_floor_contingent_pair_smoke_v1.py`
- `hft_safe_floor_contingent_pair_smoke_v1_preregistered.json` and report
- `tools/evaluate_hft_safe_floor_contingent_pair_replication3_v1.py`
- `hft_safe_floor_contingent_pair_replication3_v1_preregistered.json` and report
- `tools/evaluate_hft_safe_floor_contingent_pair_holdout5_v1.py`
- `hft_safe_floor_contingent_pair_holdout5_v1_preregistered.json` and report
- `tools/evaluate_hft_safe_floor_contingent_pair_expansion20_v1.py`
- `hft_safe_floor_contingent_pair_expansion20_v1_preregistered.json` and report
- `tools/collect_hft_safe_floor_entry_value_dataset60_v1.py`
- `tools/train_hft_safe_floor_entry_value_gate_v1.py`
- `hft_safe_floor_entry_value_dataset60_v1_preregistered.json`, dataset and gate report

Execution semantics were HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, 250ms actual-own-state polling, partial fills, cancel/ACK before Taker, confirmed-fill inventory, and Taker fee accounting. Winner/settlement/PnL were audit-only after terminal. No official HFT Forward market (cutover starts at `1606593`), 2026-08-16 market, Supervisor final75-99, Echtgeld service or live config was used.

The one-market architecture smoke on `1573252` completed safely at both locked offsets. Offset0 Maker-UP 18 plus Taker-DOWN 18 produced floor/PnL `+1.6848`; offset1 produced `+1.8648`; both had zero lifecycle violations. Unchanged replication on `1574038,1574352,1574538` was positive at both fixed offsets: offset0 `+1.4472` total and offset1 `+2.1672`, 3/3 positive for each. A frozen offset1 family-unseen holdout on `1574737,1574932,1575211,1575399,1575633` returned `+1.7144`, with 4/5 positive, one no-fill WAIT-equivalent, zero negative tails and zero violations.

The next unchanged 20-market chronological expansion correctly falsified always-ACT. Fifteen protected cycles produced only `+8.8848` in aggregate, while five one-sided tails lost `-8.28,-4.32,-9.72,-7.74,-1.98`; total floor and settled PnL were `-23.1552`, max chronological drawdown `27.054`, and zero lifecycle violations. Decision: **REJECT_FIXED_CONTINGENT_PAIR_OFFSET1_EXPANSION**. Do not tune checkpoint, offset, size, safety margin or timeout on those revealed markets. The new economic issue is pre-entry full-cycle reachability, not post-fill repair.

A locked 60-market dataset then evaluated the same action on the next chronological pre-official markets and split 30/15/15. Strict-past features at the fixed 240s checkpoint included public Predict/spot/futures state plus Hft tape best/quote depth, spread, pair edge and true-trade 1s/3s/10s imbalance. Train-only logistic tail probability was combined with train mean tail value `-6.03` and mean nonnegative value `+0.67433`; the unswept economic ACT boundary was `P(tail)<0.10058`.

Always-ACT outcomes shifted chronologically: train30 `+6.8213` with 2 tails; validation15 `+5.6669` with 1 tail; unseen15 `-28.6291` with 4 tails. The preregistered public-plus-queue gate looked useful on validation: ACT 10/15, no negative ACT markets, realized `+6.6942`. It failed unseen: tail AUC `0.52273`, average precision `0.35833`, predicted mean tail probability only `0.0580` against observed `0.2667`; it ACTed 12/15, retained three tails and realized `-23.4993`. Public-only was worse at `-29.7993`. Unseen oracle WAIT+ACT ceiling remained `+7.0926`, with oracle ACT rate `66.67%`. One unseen market (`1592981`) submitted a safe-completion Taker but received zero Taker fill before terminal, leaving a `-6.30` one-sided tail and the dataset's sole lifecycle violation.

Decision: **REJECT_ENTRY_VALUE_GATE_V1**. Static 240s public+queue state does not generalize across the later reachability regime; do not sweep probability/PnL thresholds or add another local classifier on this cohort. The action family still has sparse positive oracle room, but realized unseen value remains negative.

Highest-value next experiment: continue the already-approved **net Queue-Reactive world-model pilot**, now with a precise economic target. Fit the smallest expiry-regime-conditioned multi-step generator on the preregistered three training markets and validate stylized facts on chronological real tapes. If and only if it passes, use generated paths to estimate the distribution of `first-leg fill -> opposite queue depletion/replenishment -> passive or bounded-Taker completion` before entry. Any resulting WAIT/ACT ranker must return to a later unseen real-tape HftBacktest block and achieve realized value above zero. Do not use the world model itself as performance evidence and do not reopen post-fill R2 repair/insurance.

## 2026-08-23 — net Queue-Reactive multi-step generator gate

The preregistered training-only generator test passed on both chronological validation tapes. This is the first clean evidence in the current pivot that explicit state-conditioned multi-step queue dynamics materially outperform a regime-poor transition baseline; it is not a policy or PnL result.

Files/artifacts:

- `tools/evaluate_hft_net_qr_world_model_pilot_v1.py`
- `hft_net_qr_world_model_pilot_v1_preregistered.json`
- `hft_net_qr_world_model_pilot_v1_report.json`

The model was a conditional empirical transition bootstrap with Markov state feedback. It trained only on markets `1573252,1574038,1574352`; state was 50-second expiry bin, capped spread, imbalance bin and train-only total-best-depth quartile. Each sampled transition evolved bid/ask best depth, spread, inter-update gap and the raw observed trade batch. Backoff was locked before validation. The stronger null used the same transition/evolution machinery but conditioned only on expiry bin. Validation used later markets `1574538,1574737`; only each market's first real snapshot initialized the path, and all subsequent states were generated. There were 256 paths per model per validation market with fixed seed.

Both markets passed their individual preregistered gates. On `1574538`, the conditional normalized stylized-fact error was `0.47441` versus expiry-only `0.95733`, a `50.44%` improvement; exact-state support was `0.9000`, maximum unsupported run `1.395s`, 8/10 facts passed and all critical facts passed. On `1574737`, error was `0.39634` versus `0.88183`, a `55.05%` improvement; support was `0.87407`, maximum unsupported run `2.399s`, 9/10 facts passed and all critical facts passed. Combined error was `0.43538` versus `0.91958`, improving `52.75%`.

The improvement was structural rather than universal. Conditional state materially repaired imbalance occupancy, total-depth occupancy and 1s/5s/15s transition-horizon occupancy. Expiry-only was already competitive or slightly better for spread, update cadence, queue-direction marginals and cross-side depth correlation. Trade clustering remains incomplete: `1574538` failed the locked 5-second Fano and trade-active lag-1 tolerances; `1574737` failed lag-1. Do not claim exact Add/Cancel/Trade reconstruction or use synthetic paths as performance evidence.

Execution semantics: generator-only, no HftBacktest action run, no winner/settlement/PnL input, no official HFT Forward, sealed cohort, Supervisor final75-99 or Echtgeld change. WAIT/ACT rate, oracle ceiling and learned realized value are all unmeasured. Decision: **KEEP_NET_QR_WORLD_MODEL** for one small action-conditioned training-data pilot only.

Next highest-value experiment: feed generated net-depth paths through the existing HftBacktest execution semantics rather than an approximate fill formula, and estimate the distribution of `first-leg fill -> opposite passive completion or bounded Taker completion` for formal WAIT versus the frozen protected-cycle program. Start with a very small train-only path/action set. Synthetic results may rank what to test but may not graduate anything; the chosen rule must still earn positive value on a later untouched real Predict Tape V1 HftBacktest block.

## 2026-08-23 — real HftBacktest protected-cycle blind tests and general net-QR gate rejection

The generated-path work was advanced into actual execution testing rather than evaluated with an approximate fill formula. A relative-L2 adapter sampled state-conditioned best-price moves, 12 levels per side, inter-update gaps and side/price-relative true-trade batches from the three net-QR train tapes, then fed each generated path through the unchanged HftBacktest contingent-pair program. HftBacktest retained risk queue, 1092ms entry / 273ms response latency, partial fills, 250ms confirmed-own-state polling, cancel ACK before Taker, and the existing fee-inclusive bounded-Taker safety test. A regression check reproduced the original `1573252` offset1 result exactly before any new blind run.

Files/artifacts:

- `tools/hft_net_qr_safe_floor_blind5_v1.py`
- `hft_net_qr_safe_floor_blind5_v1_preregistered.json`, locked decisions and report
- `hft_net_qr_safe_floor_blind20_v1_preregistered.json`, locked decisions and report
- `tools/evaluate_hft_safe_floor_late_regime_blind25_v1.py`
- `hft_safe_floor_late_regime_blind25_v1_preregistered.json` and report
- `tools/evaluate_hft_net_qr_final25_postreveal_diagnostic_v1.py`
- `hft_net_qr_safe_floor_final25_postreveal_diagnostic_v1_contract.json`, decisions and report

Blind protocol was two-phase. For each market the decision phase used the fixed 240-second public checkpoint plus only Tape updates/matches at or before that checkpoint. Thirty-two generated HftBacktest paths were run with fixed seed; `CONTINGENT_PAIR_OFFSET1` was locked iff mean synthetic terminal worst-case floor was strictly positive, otherwise formal WAIT. The decision JSON and preregistration hash were written before any full real-tape action run. No threshold, margin, offset, size, timeout or generator parameter was swept.

The first blind5 (`1595018..1595913`) locked ACT 2 / WAIT 3. Both ACT markets were positive on real Tape: `+0.72,+0.54`, for policy value `+1.26` with zero lifecycle violations. However every one of the five real action counterfactuals was positive; always-ACT and revealed oracle were both `+3.77916`. This passed the preregistered smoke value gate but provided no evidence that the world-model selector discriminated tails.

The unchanged chronological blind20 (`1596106..1601017`) locked ACT 8 / WAIT 12. Policy real terminal floor and settled audit value were `+6.2676`, with zero real ACT lifecycle violations. Again, the block contained no negative action tail: 19 positive and one zero, so always-ACT/oracle were `+13.176`. The stricter gate-discrimination contract therefore returned **NEED_MORE_DATA_NET_QR_GATE**: the action family remained positive, but the selector rejected only zero/small-positive opportunities and added no economic value.

The apparent late regime was then tested without the QR gate on the final untouched pre-official block, all 25 archived markets `1601208..1606400`, strictly below official HFT Forward cutover `1606593`. The same offset1 full-cycle action was fixed always-ACT. It returned 20 positive markets and five one-sided tails, but the tails `-10.26,-4.68,-9.54,-1.62,-10.26` overwhelmed the small protected wins. Aggregate terminal floor and settled audit PnL were `-18.9504`, max drawdown `24.2352`, and one market had `TAKER_SUBMITTED_WITHOUT_FILL`. Revealed WAIT/positive-floor oracle was `+17.4096` at 80% ACT. Decision: **REJECT_LATE_REGIME_ALWAYS_ACT**. The preceding 25-market positive streak was a temporary execution regime and cannot justify a chronological cutoff or always-ACT promotion.

Lifecycle inspection changed the modeling target. Every final25 tail had exactly one 18-share Maker leg and no completion. Four tails never submitted Taker because, after confirmed first fill and cancel ACK, the opposite ask made fee-inclusive projected floor nonpositive. The fifth submitted a safe bounded Taker but received no fill. First Maker fill was generally early (`1.34s,2.45s,2.32s,4.06s`; the remaining tail `15.94s`), so the failure is not primarily late entry or queue waiting. It is the joint distribution of **which leg fills first and whether the opposite side remains economically and mechanically completable after that fill**.

For completeness, the blind5/blind20 QR generator and zero threshold were then applied unchanged to final25 as an explicitly post-reveal diagnostic; this is not blind/OOS performance evidence. The frozen rule ACTed 13/25, caught only 2/5 negative tails, missed 3/5, and had positive-action precision `76.92%`. Diagnostic realized floor was still `-8.5752`. It improved over always-ACT by `+10.3752` but remained economically negative, so “less loss” does not pass. Decision: **REJECT_CURRENT_NET_QR_GATE_MECHANISM**. Do not rescue it with a threshold or safety-margin sweep on final25.

The net-QR infrastructure remains useful as a public-book path component, but a general state-conditioned L2 generator is not the missing action-value object. The next model must be phase/action conditioned and semi-Markov: separately estimate `(1)` first-leg fill side and time, `(2)` opposite ask/queue distribution at confirmed fill, `(3)` passive deficit completion, `(4)` cancel-to-ACK state, `(5)` bounded-Taker fill probability before timeout, and `(6)` terminal floor. Train from HftBacktest actual lifecycle trajectories with formal WAIT, then require a newly authorized chronological real-tape block for performance. Do not use official HFT Forward, final25, winner, settlement or PnL as runtime inputs, and do not reopen static entry thresholds or post-fill R2 repair.

## 2026-08-23 — protected-cycle semi-Markov phase dataset and two action-family rejects

The protected-pair runner was instrumented without changing its default execution behavior. It now records strict-causal phase observations from public Tape events at or before the 250ms actual-own-state poll frontier: first confirmed Maker fill, first fee-inclusive positive completion opportunity, Maker cancel ACKs, and confirmed Taker fill. The `1573252` offset1 regression remained exactly `+1.8648`. No R2/Target intent, winner, settlement, future fill or PnL became a runtime input.

Files/artifacts:

- `tools/hft_safe_floor_contingent_pair_smoke_v1.py`
- `tools/build_hft_safe_floor_semimarkov_phase20_v1.py`
- `hft_safe_floor_semimarkov_phase20_v1_preregistered.json` and report
- `tools/build_hft_safe_floor_semimarkov_transition20_v2.py`
- `hft_safe_floor_semimarkov_transition20_v2_preregistered.json` and report
- `tools/build_hft_safe_floor_semimarkov_phase130_v1.py`
- `tools/audit_hft_safe_floor_semimarkov_phase130_v1_gate.py`
- `hft_safe_floor_semimarkov_phase130_v1_preregistered.json`, report and gate audit
- `tools/evaluate_hft_first_imbalance_taker_expansion20_v1.py`
- `hft_first_imbalance_taker_expansion20_v1_preregistered.json` and report
- `tools/evaluate_hft_safe_floor_entry_timegrid5_v1.py`
- `hft_safe_floor_entry_timegrid5_v1_preregistered.json` and report

The first 20-market support pilot falsified the proposed `first fill -> completion value` decision point: all 20 first-fill observations had nonpositive fee-inclusive Taker completion floor, including all five markets that later completed with Taker. A preregistered V2 replay recorded the missing transition, the first time completion became positive. It occurred in 15/20 markets; downstream paths were 10 passive completions and five Taker completions. It never occurred in the five tails. Time from first confirmed fill to opportunity ranged `3.0s..153.75s`, median `17.0s`. Execution values exactly matched the earlier report and no lifecycle result changed. Decision: **KEEP_SEMIMARKOV_OPTION_ARRIVAL_SCHEMA**. Fixed 5s/10s horizons and first-fill-only labels are structurally insufficient.

The frozen phase schema was then replayed across 130 already revealed pre-official markets (`1575819..1606400`) solely to measure support and chronology, not as an OOS policy test. All 130 terminal floors matched their prior reports exactly. Aggregate fixed-action value remained `-41.291384`; revealed WAIT-or-positive-action oracle was `+79.390316`, oracle ACT rate `85.38%`. There were 111 positive, two zero and 17 tail markets; completion opportunity arrived in 95 and did not arrive in 35. Downstream support was 72 passive pair completions, 41 Taker submissions, 39 actual Taker fills and two Taker no-fills. Three tails occurred despite an earlier positive opportunity, proving at least two tail hazards: no option arrival and post-arrival execution failure.

Chronology changed the transition mix. Opportunity-arrival rates by block ranged `60%..78.33%`; the middle blind25 support block had no tails, while development80 had 12 and late25 had five. Larger data also exposed direct passive completion before a positive-Taker opportunity was ever observed. The phase graph must therefore keep `direct passive completion` parallel to `positive option -> ACK -> Taker`, not make positive-Taker feasibility a universal success prerequisite.

The initial phase130 evaluator mistakenly implemented the preregistered `zero new violation` gate as `zero historical violations`. The separate zero-rerun audit compared per-market prior reports and found no violation mismatch: `1592981` and `1602825` already had `TAKER_SUBMITTED_WITHOUT_FILL`. All true preregistered gates pass. Corrected decision: **KEEP_PHASE_SCHEMA_NEED_MORE_TAKER_FAILURE_DATA**. Two no-fill cases are insufficient for an independently learned Taker-failure head; keep that branch conservative until more data arrive. Do not reinterpret the opened blocks as unseen policy performance.

Mature external work supports the representation choice: limit-order survival distinguishes first fill from completion; time-varying competing-risk/multi-state models treat intermediate states explicitly; latency-aware market-making is an MDP/switching problem. Dynamic-DeepHit-style deep longitudinal models are not justified by only 130 markets. The practical next representation is a low-capacity time-varying cause-specific hazard or landmark model, with market-level chronological splits and phase transitions rather than one static tail classifier.

Two complete-action pilots were then run and rejected without local tuning:

1. `FIRST_IMBALANCE_TAKER_OFFSET1` canceled both Maker children after the first confirmed imbalance, waited for terminal ACKs, then submitted one marketable-limit Taker even at negative floor. On expansion20 it reduced every one of the five old tail magnitudes, but damaged nearly every successful cycle: fixed value `-38.3148` versus safe-only `-23.1552`, 19/20 negative floors, two new Taker no-fills, and incremental WAIT/safe/new-action oracle ceiling exactly `0`. Decision: **REJECT_FIRST_IMBALANCE_TAKER_ACTION**. Do not tune loss cap, delay or price ticks.
2. A preregistered five-market full-lifecycle entry-time ceiling checked offset1 at target seconds-left `270,240,210,180,150,120,90,60`, always with formal WAIT and real HftBacktest lifecycle. Every market had some positive start time and four oracle choices were non-240s, but the WAIT-or-best-time ceiling increased only `+2.8908` (`1.9260 -> 4.8168`), below the locked `+5` gate, and one timing created a Taker no-fill. Decision: **REJECT_COARSE_FULL_LIFECYCLE_ENTRY_TIMEGRID**. This was an opened-cohort ceiling diagnostic only; it does not reopen checkpoint tuning. Clock time is not a portable execution regime.

Current next step: do not train another static 240s tail gate and do not reuse Target placement time as an ACT label. If research continues on this action family, collect a strict-past time-varying at-risk trajectory after first imbalance and model competing transitions `direct passive completion`, `positive completion-option arrival`, `ACK survival`, `Taker fill/failure`, and censoring. A new action should only be tested if that model identifies a decision distinct from the rejected immediate-Taker and fixed-time interventions. Any policy-value claim still requires a newly authorized chronological real-Tape HftBacktest block; current official HFT Forward remains excluded from tuning.

## 2026-08-23 — literal Target-action HFT teacher-forcing and own-memory smoke

The user's literal proposal was tested without rebuilding the already reconstructed Target logic/passive/active architecture. Every reconstructable historical Target parent action was forced into the existing HftBacktest executor; our inventory and memory advanced only from our own confirmed submissions, fills, cancel requests and ACKs. This differs from the earlier actionpoint interrogation, student-state supervisor and DAgger-like Maker corrective tests, none of which executed every Target action to create a teacher-forced HFT roll-in trajectory.

Files/artifacts:

- `tools/hft_target_forced_action_memory_smoke3_v1.py`
- `hft_target_forced_action_memory_smoke3_v1_preregistered.json`, state CSV and report
- `tools/audit_hft_target_forced_action_memory_smoke3_settlement_v1.py`
- `hft_target_forced_action_memory_smoke3_v1_settlement_audit.json`

The opened development cohort was `1569361,1571387` train and later `1572594` chronological one-market validation. It excluded official HFT Forward (`>=1606593`), 2026-08-16, Supervisor final75-99 and Echtgeld. Teacher actions were 226 reconstructed Maker parents plus 12 official Taker parents. Execution used HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, 250ms actual-own-state polling, partial fills, cancellation ACK and Taker fees. Target future action was deliberately used only in this historical roll-in and is not deployable runtime input.

Across 896 one-second state rows, forced ACT rate was `19.53125%` and formal WAIT rate `80.46875%`. The 238 teacher actions requested 4,443.063 shares; actual fill realization was `60.8351%`. All three HFT trajectories had negative winner-free terminal floor: `-97.9915,-43.0044,-52.2744`, aggregate `-193.2703`; market-level WAIT-inclusive oracle was therefore `0`. The preregistered decision is **REJECT_DIRECT_TARGET_FORCED_IMITATION_UNDER_HFT**. Do not tune reconstruction confidence, horizon, price cap, action size or classification thresholds on these three markets.

A fixed class-balanced one-step logistic comparison trained on the first two markets and scored the third. Current-state macro average precision was `0.13151`; adding only our own action/fill/cancel memory gave `0.12771`, delta `-0.00381`. Autonomous closed-loop learned-policy value was not run because the forced economics and memory gates failed.

The post-hoc settlement audit used already recorded official winners strictly after replay/model scoring and never as an input. Target's original observed BID portfolio was positive in 2/3 markets but `-11.8862` aggregate; the HFT forced trajectory was positive in 0/3 and `-193.2703`, a `-181.3841` execution gap. Thus literal per-action imitation does not preserve the Target cycle under our latency/queue mechanics: about 39% non-realization is composition-dependent and removes winning-side/completion inventory rather than simply scaling the Target portfolio down.

The actionable part to keep is the **teacher-forced state-distribution generator**, not the direct imitation policy or this weak one-step memory head. The next high-value experiment should represent each Target action as a *cycle obligation/goal* and let an execution-aware constrained policy choose WAIT, passive placement, repair or bounded Taker from our actual state to restore the intended paired/inventory manifold. Rewards must compare resulting HftBacktest settlement/floor value with the forced-action trajectory, with Target events limited to offline teacher construction. Start on a tiny opened cohort and require an action-value/oracle ceiling before training; do not force identical child orders again.

A separate Taker-seed-to-passive-completion smoke was also rejected before this test: on five opened markets all 20 seed options had nonpositive fee-inclusive entry pair floor, so the locked policy and oracle both WAITed with zero incremental value. Do not tune that action family.

## 2026-08-23 — Target replay fidelity gate supersedes literal-imitation economic interpretation

The literal Target-action smoke above contained a material clock/semantics mismatch and must not be used as evidence that the Target architecture itself is uneconomic. `maker_book_inference_v21_parent_lifecycles.placement_first_ms` is an inferred public placement **source clock**, while Predict Execution Tape V1 deliberately replays L2 on `received_at_ms`. The smoke treated the source-clock placement as our local submission time and then added another 1092ms entry latency. It therefore measured a delayed follower, not an exchange-arrival-aligned offline teacher-forced trajectory. Some inferred cancellation deadlines could also precede the simulated order's creation. Its preregistered `REJECT_DIRECT_TARGET_FORCED_IMITATION_UNDER_HFT` remains valid only for that delayed child-order follower implementation.

Files/artifacts:

- `tools/evaluate_target_replay_fidelity_gate_v1.py`
- `target_replay_fidelity_gate_v1_preregistered.json`
- `target_replay_fidelity_gate_v1_report.json` and parent-level CSV

The calibration smoke used opened markets `1569361,1571387` and chronological validation market `1572594`, with no official HFT Forward, sealed or Echtgeld input. To avoid inventing cancellation truth, it retained only high-confidence, fully observed Target Maker parents with exactly one allocated public placement update and placement `received_at_ms` no later than the end of the first possible fill second. Replay support was 24, 20 and 39 parents respectively, 83 parents / 1,494 shares total. This is filled-parent recall only; false-positive fills on Target's private canceled/unfilled orders remain unmeasurable.

Four variants were frozen before outcome generation. `FOLLOWER_RAW_RISK` kept the old delayed submission. `ARRIVAL_RAW_RISK` submitted 1092ms earlier so exchange arrival aligned with the inferred placement update. Two leave-one-Target-out variants additionally subtracted inferred outstanding Target placement quantity from L2, under risk and log queue models. All retained Tape V1 true matches, 273ms response latency, 250ms polling and partial fills; no dream fill or strategy/PnL evaluation was used.

Clock alignment materially improved replay. On the first two markets, follower target-window share reproduction was `70.45%`; arrival-aligned raw risk was `79.55%` and was selected by the locked calibration rule. On validation `1572594`, follower was `49.54%`; selected arrival-aligned raw risk reached `72.03%`, an improvement of `+22.48pp`. However full-parent recall was only `64.10%`, any-fill recall `74.36%`, and only `43.59%` of parents had first simulated fill in the Target's observed fill second. Twelve of 39 simulated parents filled earlier, ten later and six never filled. The gate therefore returned **NEED_MORE_DATA_REPLAY_FIDELITY**, not KEEP.

Leave-one-Target-out depth subtraction did not solve the gap. On validation, stripped risk reached `73.74%` share reproduction and stripped log `74.56%`; log full-parent recall was `74.36%`, but timing remained poor. More importantly, inferred Target outstanding quantity exceeded public level quantity often enough to clip subtraction equal to `15%` of placement allocation, failing the locked 10% integrity boundary. Anonymous L2 ownership is not reliable enough to construct a clean Target-stripped tape from these inferred parents.

The official Predict API was probed read-only with the configured API key. Public `/v1/orders/matches` with Target signer filtering returned 200, but `/v1/orders` and `/v1/orders/{hash}` both returned 401 because those endpoints expose only one's own orders and require account JWT. Public API data therefore cannot recover Target's unfilled/canceled order lifecycle or private queue position. Future WS improves anonymous order-flow resolution but does not supply Target order identity.

Current interpretation: HftBacktest/Tape is not generally disproven—the Taker control reproduced all 12 Target Taker quantities, and an older own-live calibration reproduced 8/8 Maker fills—but the **Target fill-only replacement replay is not fidelity-qualified**. Do not use its negative PnL to reject the Target system, and do not declare Target economics reproduced by a positive variant either. Performance work should pause until queue/latency is calibrated on a broader set of our own historical real order lifecycles, where submit/ACK/cancel/fill ground truth is actually observable. Target data should remain the behavior/economic-goal teacher, not the source of private execution truth.

## 2026-08-23 — mutable ABIDES Target-in-book fidelity smoke rejected

Hypothesis: the prior HftBacktest replacement replay might fail because it inserts a deterministic Market-By-Price follower into an immutable tape. A genuinely different measurement instrument was tested: the official JPMorgan ABIDES-Markets price-time FIFO order book at commit `f9cbe51342b7dedd9587e4e069040d68a5c6477f`, with each inferred Target Maker order inserted as an actual visible participant. Real Predict Tape V1 L2 and public matches drove anonymous background orders; 128 latent paths integrated over placement time within the preceding L2 receipt interval, whole-second match timing, and front-versus-back anonymous cancellation location. This was not another net Queue-Reactive generator, HftBacktest risk/log queue sweep, or policy/PnL experiment.

Files/artifacts:

- `tools/evaluate_predict_latent_exchange_smoke_v1.py`
- `predict_latent_exchange_smoke_v1_preregistered.json`
- `predict_latent_exchange_smoke_v1_report.json`
- `predict_latent_exchange_smoke_v1_validation_parents.csv`

The single already-opened development market `1569361` used its first nine qualified fully-filled single-placement Target Maker parents for posterior weighting and the following 15 parents as a chronological validation block; the validation placement span was about 61 seconds. No official HFT Forward, 2026-08-16, Supervisor final75-99, winner, settlement, PnL or Echtgeld input was used. Target validation fill times were score-only after path generation. ABIDES toy regression first verified FIFO background priority, Target partial fill and public-depth synchronization.

The frozen `ARRIVAL_RAW_RISK` baseline on the same 15 parents reproduced `73.3333%` of Target-window shares, full-parent recall `73.3333%`, and first-fill observed-second hit rate `40.0%`. The mutable posterior produced expected share reproduction `74.7118%` (`+1.3784pp`) and expected full-parent recall `73.5030%`, but first-fill same-second probability fell to `38.1163%` (`-1.8837pp`). Timing 10%-90% interval coverage was `53.3333%`; weighted maximum Target/public-depth overhang was `11.0434%` of placed validation shares. Effective sample size was `122.76/128`, and weighted cancellation-from-front probability stayed `0.5023` versus prior mean `0.5076`: the nine calibration fills supplied almost no information about the hidden queue/cancel regime.

Decision: **REJECT_CURRENT_LATENT_EXCHANGE_ADAPTER**. A professional mutable FIFO engine does not recover information absent from aggregated L2 plus second-granular public matches. Do not tune its priors, posterior temperature, visibility, trade timestamp, cancellation distribution or validation gate on this market, and do not expand it to three markets. The failure is data identifiability, not lack of simulator sophistication.

Official Predict documentation independently confirms the public `predictOrderbook/{marketId}` stream is aggregated price-level depth. Exact `orderAccepted/orderCancelled/orderTransaction*` lifecycle events are available only on the user-scoped `predictWalletEvents/{jwt}` topic. Existing `lastOrderSettled/settlementsPending` alignment work already found high ambiguity and cannot be promoted to Target order truth. A read-only audit of the current own Echtgeld database still finds only market `1513668`: ten Maker orders, eight filled, one canceled and one rejected. There is no newly accrued multi-market own-order calibration set to fit a richer execution environment today.

Highest-value next step: stop searching for another public-data simulator wrapper. The blocking asset is labeled venue execution data—our own accepted/resting/cancelled/partial/full order lifecycles joined to the exact Tape interval. Passively preserve any such naturally occurring records and run a frozen multi-market simulator-calibration gate once support exists. Creating active mainnet probe orders would be the cleanest system-identification experiment, but it is a separate Echtgeld authorization and must not be started under the current research mandate. Until then, Target remains a behavior/economic-goal teacher and HftBacktest remains the conservative counterfactual validator; neither Target-private queue truth nor exact historical Target environment is identifiable from the public interface.

## 2026-08-23 — Predict orderflow + inferred Target-depth identifiability pilot

Hypothesis: the Target analysis database may add useful execution-state information without another simulator wrapper. At inferred Target Maker placement time, augment the existing raw-L2 placement state first with strict-past Predict `orderCount`, global L2 flow, raw-match quantity/maker fragmentation, and clock state, then with confidence-bounded concurrent inferred Target outstanding depth. Test whether either layer improves conditional first-fill timing on a chronological market. This is not an eventual-fill classifier, action imitation, policy fit, HftBacktest PnL run, or ABIDES replay.

Files/artifacts:

- `tools/evaluate_target_orderflow_multiview_identifiability_v1.py`
- `target_orderflow_multiview_identifiability_v1_preregistered.json`
- `target_orderflow_multiview_identifiability_v1_report.json`
- `target_orderflow_multiview_identifiability_v1_parents.csv`

Cohort: already-opened development markets `1569361,1571387` trained the fixed model; later market `1572594` was the sole chronological validation. There was no official HFT Forward, `2026-08-16`, Supervisor final75-99, winner, settlement, PnL, policy threshold, or Echtgeld input. The sample contained 44 train and 39 validation high-confidence, single-placement, known-to-fill Target Maker parents.

Label integrity was unusually strong. All 83/83 inferred parent `order_hash` values joined to maker hashes in Predict Execution Tape V1 raw matches; all 83 agreed with the Target-analyzer filled shares within 0.05 share and all 83 agreed on the first whole-second timestamp. This proves the combined sources are useful for exact historical filled-parent attribution. It does not reveal Target-private unfilled/cancelled orders or pre-fill queue position, and exact hash matches arrive with the fill rather than serving as runtime predictors.

The fixed Random-Forest interval model predicted the first-fill duration interval from placement-time strict-past state. On chronological validation:

- raw L2 median interval error `159.20ms`, exact interval coverage `35.90%`, +/-1s coverage `82.05%`, duration Spearman `-0.0724`
- adding Predict orderflow median interval error `267.53ms` (worse by `108.34ms`), exact coverage `30.77%`, +/-1s unchanged, Spearman `+0.1736`; 64-permutation one-sided p=`0.8769`
- adding inferred Target outstanding depth median interval error `304.52ms` (another `36.99ms` worse), exact coverage `28.21%`, +/-1s unchanged, Spearman `+0.1754`; Target-state incremental p=`0.9385`
- combined versus raw worsened median interval error by `145.32ms` (`-91.29%` relative improvement under the locked sign convention)

Training importance looked tempting—strict-past 1s raw-match quantity had about 15%-16% importance, and inferred Target side quantity about 4.5%—but neither transferred to the next market. This is another execution-regime nonstationarity result, not a hidden positive signal to rescue with a different forest, duration transform, or threshold.

Execution semantics: Predict Execution Tape V1 public updates through the exact placement receipt/source tuple; raw matches were features only after their entire second-granular bucket had ended; inferred Target state used only placement/fill-decrease allocation receipts no later than the subject placement and excluded the subject parent. No simulator, fill invention, WAIT/ACT decision, oracle action value, learned-policy realized value, or PnL was produced; all are `N/A` for this identifiability-only iteration.

Decision: **REJECT_CURRENT_MULTIVIEW_IDENTIFIABILITY**. Keep the exact maker-hash join as a provenance/labeling improvement. Reject placement-time `orderCount + raw-match flow + inferred Target outstanding` as a portable first-fill timing state under this frozen small pilot, and do not tune it on `1572594` or expand the same feature/model family.

Highest-value next experiment remains a different information source, not another public feature composition: passively capture our own user-scoped Predict `orderAccepted/orderCancelled/orderTransaction*` lifecycle events and join them to exact Tape intervals. Once multiple independent markets contain accepted, partial/full, cancel and no-fill lifecycles, freeze a leave-market-out queue/latency calibration gate. Without that labeled venue support, public Target data can improve historical attribution but cannot fidelity-qualify the execution environment.

## 2026-08-23 — own Predict wallet lifecycle forward collection activated

Hypothesis: the missing execution-calibration asset is our own labeled venue lifecycle, not another public-L2 simulator variant. Exact `orderAccepted/orderNotAccepted/orderExpired/orderCancelled/orderTransactionSubmitted/orderTransactionSuccess/orderTransactionFailed` events, local receipt clocks and reconnect provenance should make submit/ACK, partial-fill, cancel-race and terminal no-fill states identifiable once joined to local request clocks and Predict Execution Tape V1.

Files:

- `src/predict_bot/predict_own_wallet_lifecycle_collector_v1.py`
- `tests/test_predict_own_wallet_lifecycle_collector_v1.py`
- `tools/audit_predict_own_wallet_lifecycle_v1.py`
- `start-predict-own-wallet-lifecycle-v1.ps1` and `stop-predict-own-wallet-lifecycle-v1.ps1`
- `data/research/execution_aware_fill_lifecycle_v0/predict_own_wallet_lifecycle_collection_v1_contract.json`
- live database `data/predict_own_wallet_lifecycle_v1.db` and collection report `predict_own_wallet_lifecycle_v1_collection_report.json`

Collection is research-only and passive. The client has a hard endpoint allowlist containing only auth challenge, auth token and authenticated `GET /v1/orders`; it has no order placement/cancel/approval method. Secrets stay in process memory and are not placed in SQLite, logs, argv or the PID file. On every connection, authenticated REST OPEN orders are preserved as reconciliation observations, followed by the official user-scoped `predictWalletEvents/{jwt}` topic. Raw wallet events are compressed losslessly and normalized into SQLite WAL with server timestamp, local receive timestamp, order/hash/market, cumulative filled quantity, transaction/fill/maker/fee fields and connection session. REST disappearance is never labeled as fill/cancel without a wallet event or exact raw match.

The 20-second read-only probe completed one successful OPEN bootstrap and wallet subscription. The long-running collector is now `LIVE` on local state port 8795; first official heartbeat was echoed, reconnect count was zero and last error was null. Initial support is correctly zero events/orders/markets because no active probe order was created and the account had no OPEN order at bootstrap. Existing Echtgeld 8781/8782 services and their state were not changed.

Cohort is forward own-wallet lifecycle from collector activation onward, with no Target-wallet, official HFT Forward, sealed cohort, winner, settlement outcome or PnL input. This is source-data acquisition, not a policy experiment: HftBacktest action semantics, WAIT/ACT rate, oracle value ceiling and learned-policy realized value are all `N/A`. Four focused tests pass, including normalized partial-fill storage, canonical deduplication, durable empty OPEN reconciliation and refusal of mutation endpoints.

Decision: **KEEP_COLLECTING**. Calibration remains blocked until at least 10 independent markets, 100 accepted orders, 30 filled, 30 cancelled/expired/rejected and 10 partial-fill orders exist, followed by at least 90% local submit/cancel-clock and COMPLETE_FORWARD Tape join coverage and 95% terminal lifecycle coverage. These thresholds are a data-readiness gate, not permission to tune a policy on revealed outcomes.

Highest-value next step: let naturally occurring own activity accumulate without active Echtgeld probes. Once the first lifecycle events arrive, add the exact local submit-request/cancel-request ledger joins and COMPLETE_FORWARD Tape maker-hash/interval audit; do not fit queue or latency parameters before support spans multiple independent markets. The eventual simulator calibration must be frozen and leave-market-out before any HFT value comparison.

## 2026-08-23 — observable Target Maker-cycle reachability: clock dominates, patience helps but is not sufficient

The research objective was deliberately changed for this diagnostic: HFT positive PnL was not a gate. HftBacktest remained the venue-physics engine, but the tested object was control cadence—delayed follower versus exchange-arrival alignment versus patient GTC—not whether the Target strategy must itself be high-frequency. This follows the user's hypothesis that the Target system may be structurally mismatched to an HFT-style short child lifecycle.

Files/artifacts:

- `tools/evaluate_target_observable_maker_cycle_reachability_v1.py`
- `target_observable_maker_cycle_reachability_v1_preregistered.json`
- `target_observable_maker_cycle_reachability_v1_report.json`
- `target_observable_maker_cycle_reachability_v1_patient_parents.csv`

The small preflight reused the already-opened replay-fidelity cohort: markets `1569361,1571387` were description/calibration only and later `1572594` was the locked chronological primary market. There was no official HFT Forward, sealed market, winner, settlement outcome, PnL or Echtgeld input. Only the existing 83 high-confidence, single-placement, fully-filled Target Maker parents were scored; private unfilled/cancelled Target orders remain unobservable. The three control ceilings used HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, 250ms polling and actual partial fills. The patient variant left orders GTC through all executable Tape events; validation confirmed zero cancel requests before data end. No fill was forced or injected.

On primary market `1572594`, the delayed follower observable-portfolio normalized L1 gap was `50.4573%`. Exchange-arrival alignment reduced the Target-window gap to `27.9729%`, a `22.4843pp` improvement. Allowing the ordinary arrival-aligned replay to settle through its cancellation response reduced terminal gap to `19.5014%`; patient GTC reduced it further to `12.8205%`, an incremental `6.6809pp`. Patient GTC reproduced all 306 observable UP Maker shares, but only 306/396 DOWN shares (`77.2727%`), leaving five 18-share DOWN parents unfilled and a 90-share signed-net gap. Paired-share reachability nevertheless reached `100%`. On the two earlier markets patient GTC reproduced 432/432 and 360/360 observable Maker shares exactly; therefore persistence was powerful in-sample but did not fully transfer to the next market.

The locked classifier returned **CLOCK_ALIGNMENT_DOMINANT**: clock correction was the largest portable jump, while patient persistence did not meet the preregistered >=15pp incremental-recovery condition. Interpretation is **KEEP patient/event-driven lifecycle as a control-architecture component; REJECT patience alone as the explanation of the Target cycle**. The remaining primary failure is concentrated entirely on the DOWN/native-ASK side at prices `0.45,0.14,0.14,0.14,0.12`, including orders that Target filled in the same whole-second as their inferred public placement receipt. That pattern is consistent with unresolved arrival-within-interval/queue/hidden-order state, but cannot distinguish those mechanisms from Target-private pending orders using public data.

This was not a policy/value experiment: formal WAIT/ACT rate, oracle value ceiling, learned-policy realized value and OOS PnL are all `N/A`. It also does not yet reproduce the full Target Maker-repair-Taker cycle. The informational result is that forcing HFT-style rapid child closure is not necessary and can lose reachability, but removing it does not eliminate cross-market execution residuals.

Highest-value next experiment: perform cycle-phase reachability attribution around the five primary DOWN failures and the subsequent Target repair/Taker transitions. Keep Target events as offline goal/score data; let only legal HftBacktest WAIT, persistent passive, cancel/ACK/replace and bounded Taker actions attempt to restore the paired/inventory manifold. Classify each phase as `(a) no legal execution route`, `(b) legal oracle route but strict-past state cannot identify it`, or `(c) reachable state but wrong economic goal`. Do not tune a longer TTL or another offset on these markets. Own-wallet lifecycle calibration remains required before attributing the residual specifically to Predict queue priority.

## 2026-08-23 — residual responsibility trace: venue route exists, semantic dependency is missing

Hypothesis: trace the five DOWN Target parents that arrival-aligned patient GTC never filled on primary market `1572594`, distinguish paired repair from directional residual construction, and test whether their absence is mechanically unavoidable or whether a legal bounded active route exists. HFT positive PnL was not a gate; this was a responsibility/reachability diagnostic.

Files/artifacts:

- `tools/evaluate_target_residual_failure_route_trace_v1.py`
- `target_residual_failure_route_trace_v1_preregistered.json`
- `target_residual_failure_route_trace_v1_report.json`
- `target_residual_failure_route_trace_v1_routes.csv`

The cohort was only already-opened chronological primary market `1572594`; no official HFT Forward, sealed data, winner, settlement outcome, PnL or Echtgeld input was used. Each missing Target parent was classified from official Target fills immediately before its fill. Legal route probes used independent HftBacktest + Predict Execution Tape V1 risk-queue replays with 1092ms entry / 273ms response latency, 250ms polling, actual partial fills and no fill injection. `REACTIVE_TAKER` submitted at inferred placement receipt; `PREPOSITIONED_TAKER_CEILING` submitted 1092ms earlier. Both used an 18-share DOWN marketable-limit at the strict-past outcome ask plus two ticks, capped at 0.99.

The five missing parents were not one homogeneous repair bucket. The first occurred with Target signed inventory `UP +25.5565` and its 18 DOWN shares reduced imbalance to `+7.5565`: **one `REPAIR_TOWARD_PAIR`**. The other four occurred while Target was already DOWN-dominant (`-264.77,-202.65,-256.65,-256.65`) and increased absolute imbalance: **four `DIRECTIONAL_ADD`**. No Target Taker followed any of them. Opposite UP Maker fills followed the four directional adds after `2s,8s,1s,1s`, showing a passive add-then-repair dependency; the first repair was followed by the next UP fill only after 20s while intervening DOWN fills changed the dominant side.

All five reactive bounded Takers filled the full 18 shares within five seconds, with first exchange fill at exactly 1092ms after local submission; prepositioning was unnecessary. The locked classification was therefore **5/5 `LEGAL_REACTIVE_ACTIVE_ROUTE`** and zero `PREPOSITIONING_REQUIRED` / `NO_BOUNDED_ACTIVE_ROUTE`. Mechanically, this venue/Tape did not make the missing 90 shares unreachable.

Economically the active route was not equivalent to Target passive execution. Reactive executed prices were `0.58,0.89,0.90,0.93,0.91` versus Target passive `0.55,0.86,0.86,0.86,0.88`: a `3–7¢` premium, mean `4¢/share`, about `$3.60` across 90 shares, plus `$0.2844` Taker-fee audit. This cost was not used for route classification and no PnL was computed.

Decision: **KEEP_ROLE_AWARE_DUAL_LAYER_CYCLE; REJECT_VENUE_UNREACHABLE_AS_THE_PRIMARY_EXPLANATION; REJECT_AUTO_TAKER_FOR_ALL_MISSED_PASSIVE_FILLS**. The protected paired foundation, safety repair and directional residual are different obligations. A missed upstream directional ADD must not be followed blindly by the Target's downstream UP repair, because under our actual-fill state that action changes semantic role; this is exactly how forced Target actions produced HFT 306/306 while Target retained 306/396. Bounded Taker may be appropriate for a repair only when it improves the portfolio floor after fees; it must not automatically chase an optional directional add.

Formal WAIT/ACT rate, action-value oracle, learned-policy value and OOS PnL remain `N/A`; all five probes ACTed only because they were offline route ceilings. This single opened market is not generalization evidence.

Highest-value next experiment: implement an actual-fill, role-preserving obligation controller on this same small opened market. At every Target offline goal transition, label only the economic role `FOUNDATION / REPAIR / DIRECTIONAL_OPTION`, then let our state choose: persistent passive for foundation, WAIT/passive or floor-improving bounded Taker for repair, and passive-or-WAIT with no forced completion for directional optionality. Suppress downstream repair when its upstream directional obligation never filled. Compare cycle-manifold reachability and invariant violations against literal teacher forcing; do not optimize PnL or offsets on this market. Only if semantic preservation produces a large structural improvement should it be expanded chronologically.

## 2026-08-23 — actual-fill role-sign correction is a no-op

Hypothesis: some literal Target Maker actions invert `FOUNDATION / REPAIR / DIRECTIONAL_OPTION` role after the HFT follower misses upstream fills, so recomputing the role from confirmed HftBacktest fills and choosing formal WAIT on a mismatch should preserve the observable cycle manifold or winner-free floor.

Files/artifacts:

- `tools/evaluate_target_actual_fill_role_correction_v1.py`
- `target_actual_fill_role_correction_v1_preregistered.json`
- `target_actual_fill_role_correction_v1_report.json`
- `target_actual_fill_role_correction_v1_decisions.csv`

The smoke used only already-opened market `1572594` and the same 39 high-confidence observable Target Maker goals. Both variants used HftBacktest + Predict Execution Tape V1, risk queue, 1092ms entry / 273ms response latency, 250ms polling, GTC exact Target passive placement, actual partial fills and no dream fill. Target role labels used only qualified parents whose final whole-second fill interval ended strictly before the current placement receipt; follower state used confirmed simulator fills only. No Target Taker, winner, settlement, official Forward, sealed cohort or Echtgeld input was used.

`LITERAL_PATIENT` and `ACTUAL_FILL_ROLE_WAIT` were exactly identical: both ACTed `39/39`, WAITed `0/39`, produced UP `306` / DOWN `306`, paired reachability `100%`, normalized L1 gap `0.128205`, and winner-free floor `+39.24`. There were zero role-sign mismatch goals before submission. The preregistered gate returned **NEED_MORE_DATA**, but the operational disposition is **REJECT this exact role-sign WAIT correction as a no-op**; it has no intervention support to justify expanding or tuning.

The missing semantic state is more granular than side dominance. The five residual DOWN fills change obligation magnitude, while the subsequent actions still have the same coarse directional/repair sign under both Target and follower state. Therefore do not spend another iteration adjusting a role threshold. The next diagnostic must attribute the unpaired quantity/completion-cost effect inside the whole cycle.

## 2026-08-23 — full-cycle residual Taker substitution attribution

Hypothesis: the five passive residual failures causally explain the entire observable Maker manifold gap. Substitute each failure individually and all five jointly with bounded HftBacktest-executable Taker routes inside the full 39-goal replay, then measure both manifold recovery and winner-free economic cost.

Files/artifacts:

- `tools/evaluate_target_residual_taker_substitution_attribution_v1.py`
- `target_residual_taker_substitution_attribution_v1_preregistered.json`
- `target_residual_taker_substitution_attribution_v1_report.json`
- `target_residual_taker_substitution_attribution_v1_orders.csv`

The cohort remained the single opened diagnostic market `1572594`. Seven small full-cycle variants reused HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, 250ms polling, actual partial fills and no dream fill: literal passive, five one-parent substitutions, and the joint five-parent substitution. A substitute used the same arrival-aligned local decision time and a GTC marketable limit at strict-past outcome ask +2 ticks capped at 0.99. Configured Predict Taker fees were applied only to actual substitute fills. The five parents were selected from an already revealed opened replay, so this is offline component attribution, not a deployable runtime trigger. WAIT/ACT was `0/39` and `39/39` in base and joint variants; there was no learned policy or chronological/unseen OOS claim. Oracle action-value and learned-policy realized value are `N/A`.

All five substitutes filled 18 shares. Each independently improved normalized L1 gap by exactly `0.025641` and added zero paired shares; its winner-free floor delta was negative (`-$10.5912`, `-$15.8832`, `-$16.2360`, `-$16.7652`, `-$16.4124`). Joint substitution changed HFT from UP `306` / DOWN `306` to UP `306` / DOWN `396`, reducing normalized L1 gap from `0.128205` to numerical zero and reproducing the observable Target Maker surface exactly. Paired shares remained `306`. Taker fees were `$0.288`; winner-free floor fell from `+$39.24` to `-$36.648`, a `-$75.888` delta, while best-case audit rose to `+$53.352`.

Decision: **KEEP_MANIFOLD_ATTRIBUTION_REJECT_UNCONDITIONAL_COMPLETION**. The five residual fills fully cause the visible trajectory gap, but filling them is an unpaired directional exposure decision, not a repair of the paired core. Four are Target directional adds and one is a repair in the full Target inventory; on this isolated observable subsystem none adds paired shares. This proves that closer Target trajectory and safer execution economics are different objectives. Do not auto-Taker missed Target fills and do not score a controller only by imitation/manifold distance.

Highest-value next experiment: represent live passive commitments and unpaired obligation quantity explicitly, then learn or rank the **incremental completion value** of `WAIT / leave passive / bounded Taker` from strict-past venue state. The target should decompose `(paired-core/floor delta, directional residual value, completion premium and fee)` rather than use a coarse role sign or Target action label. Start with a small opened-market dataset audit to verify support and label dispersion; expand chronologically only if the component target shows a large, nontrivial separation.

## 2026-08-23 — Target versus Frozen R2 perfect-fill event alignment

Hypothesis/question: remove HFT execution entirely and measure whether Frozen R2 optimistic-PAPER fill events actually coincide with official Target executed BID-parent events in the same market. This de-duplicates the existing `strategy_target_cross_compare_v1`, which compared older Maker/Taker EBM cohorts rather than Frozen R2, and the prior Target-vs-R2 conversion report, which compared portfolio trajectories rather than one-to-one events.

Files/artifacts:

- `tools/analyze_target_vs_r2_perfect_fill_event_alignment_v1.py`
- `target_vs_r2_perfect_fill_event_alignment_v1_preregistered.json`
- `target_vs_r2_perfect_fill_event_alignment_v1_report.json`
- `target_vs_r2_perfect_fill_event_alignment_v1_primary_matches.csv`

The opened diagnostic cohort was `1569361,1571387,1572594`, with `1572594` the primary same-market question. No HftBacktest, queue, latency, winner, settlement, PnL, official HFT Forward or sealed cohort was used. R2 events were optimistic-PAPER Maker/Taker `filled_at_ms`; Target events were official executed BTC BID parents at `first_event_ms`. The primary Target unit followed the existing cross-compare convention: identical role+side parents coalesced into bursts with <=1s idle gap and <=3s total span. Matching was one-to-one, identical role+side, fixed 5s primary window; 1s/3s/10s were preregistered sensitivity diagnostics. Price and size were not required for semantic overlap and were audited separately.

On primary market `1572594`, R2 had 36 events, all Maker (`15 UP / 21 DOWN`); Target had 90 raw parents or 49 bursts (`23 Maker UP / 24 Maker DOWN / 2 Taker`). At 5s, 22 R2 events matched a Target burst with the same role and side: R2 precision `61.1111%`, Target recall `44.8980%`, F1 `51.7647%`. Window sensitivity was substantial: semantic F1 `18.8235%` at 1s, `37.6471%` at 3s and `56.4706%` at 10s. Only 4/36 R2 events (`11.1111%`) matched role+side in the exact same second. Among the 22 burst semantic matches, only 1/36 R2 events (`2.7778%`) also had price within one tick and shares within 0.05. Against uncoalesced Target parents, 5s semantic precision was `72.2222%`, recall `28.8889%`, F1 `41.2698%`; 7/36 (`19.4444%`) also met the price/size condition.

Across all three opened markets, the 5s burst comparison had exactly 132 R2 and 132 Target events, with 61 matches: precision/recall/F1 all `46.2121%`. The preregistered primary classifier returned **HIGH_EVENT_ALIGNMENT** because primary burst F1 exceeded 0.50, but the correct interpretation is **macro cadence/side overlap around one half, not exact event imitation**. R2 learned a meaningful portion of the Target-like Maker ecology under perfect fills, while exact-second and full price/quantity coincidence remained low; primary R2 also emitted no Taker despite two Target Taker bursts.

WAIT/ACT, oracle action-value ceiling, learned-policy realized value and chronological unseen OOS value are all `N/A` for this descriptive event-alignment audit. Do not use the 51.8% number as a strategy-performance claim or as evidence that HFT should imitate individual Target actions.

Highest-value implication: separate the approximately shared macro event mode from the non-overlapping half. If research uses Target again, the useful object is likely a latent cycle/mode transition or goal coordinate—not exact child action labels. A next diagnostic should test whether the non-overlapping Target bursts explain terminal portfolio conversion after controlling for the shared R2 Maker events, before any new policy training.

## 2026-08-23 — injected maintain-blackout recovery program smoke

Hypothesis: use Target only for the broad cycle scaffold and derive missing self-repair through simulator trial and error. If the existing Frozen R2 integrated control loop already contains a useful recovery skeleton, it should preserve positive winner-free portfolio floor after one or two deliberately dropped passive-maintain decisions. This is a new fault-injection diagnostic, not Target action imitation, normal-trajectory offset tuning, a pointwise lifecycle classifier, or queue/cancel threshold repair.

Files/artifacts:

- `tools/hft_r2_fault_recovery_program_smoke_v1.py`
- `hft_r2_fault_recovery_program_smoke_v1_preregistered.json`
- `hft_r2_fault_recovery_program_smoke_v1_report.json`

The small preregistered smoke used only opened architecture-development market `1573252`, whose no-fault integrated `PASSIVE_MAINTAIN=offset0 / PASSIVE_REPAIR=offset0` program already had a positive baseline. For the first N distinct quote-active `PASSIVE_MAINTAIN` decisions, with N=`1` or `2`, both passive quote sides were forced to formal WAIT; after the blackout the program resumed. This represents a dropped-objective/no-execution fault, not a literal claim that a submitted resting child received zero queue fills. Four coarse recovery programs were tested at each fault depth: `WAIT_ALL`, both roles offset0, maintain-only offset0 and repair-only offset0. Nine total replays used HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, actual partial fills, actual-fill-only inventory feedback, Frozen R2 logic and the existing fixed-KEEP lifecycle. No Target action/fill, winner, settlement or PnL was a runtime input.

The no-fault winner-free worst-case floor was `+52.642`. With one maintain decision dropped, fault-matched WAIT was `0`; the constrained recovery oracle was the same both-role offset0 program at `+33.022`, `269.86` actual Maker shares, zero Taker shares and passive decision ACT rate `97.3684%`, retaining `62.7294%` of the no-fault floor. With two consecutive maintain decisions dropped, the same program produced `+20.602`, `287.86` Maker shares, zero Taker shares and ACT rate `94.7368%`, retaining `39.1361%`. All nine semantic audits passed and all programs had zero cycle-invariant violations.

The important negative control is role decomposition. Maintain-only offset0 produced floor `-2.16` after one fault and `-18.2482` after two; repair-only offset0 produced no fills and floor `0` at both depths. Therefore the retained value was not a small isolated repair rule: on this market it required the maintain and repair roles to remain coupled inside the integrated loop. Recovery was economically positive but incomplete: combined paired coverage fell from `94.0692%` with no fault to `79.9377%` and `74.9392%`, while final absolute R2-target tracking error rose to `270.14` and `432.14` shares. The preregistered decision is **KEEP_RECOVERY_ARCHITECTURE**, but this is a restricted in-market floor oracle ceiling, not evidence that the original target trajectory was restored, a learned policy or a chronological/unseen OOS result. Learned-policy realized value is `N/A`; no scale-out or deployability is claimed.

Highest-value next experiment: add realistic partial-fill and cancel/ACK fault states plus explicit `continue / retire obligation / rebase from actual portfolio / WAIT` recovery actions. Score multi-step recovery by decomposed floor/paired-core, directional residual and completion-cost value. Only if that second small smoke has a nontrivial constrained oracle should chronological episodes be generated for recurrent offline RL or contextual ranking; do not tune offset0 on `1573252`.

## 2026-08-23 — matched fault-state recovery matrix: action ceiling kept, point-state learner rejected

Hypothesis: derive the missing self-repair layer through matched simulator trial and error rather than Target action labels. At the first observable asymmetric lifecycle checkpoint, replay the identical strict-past HftBacktest state under formal WAIT, cancel-to-ACK bounded Taker completion, and cancel-to-ACK retirement/rebase. Maintain and repair remain coupled in the common upstream Frozen R2 trajectory; only the response to the realized fault changes.

Files/artifacts:

- `tools/hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py`
- `tools/hft_r2_cycle_preserving_execution_smoke_v1.py`
- `tools/hft_r2_fault_conditioned_recovery_matrix_v1.py`
- `tools/train_hft_r2_fault_conditioned_recovery_policy_v1.py`
- `hft_r2_fault_conditioned_recovery_matrix_v1_preregistered.json`
- `hft_r2_fault_conditioned_recovery_contract3_v1_report.json`
- `hft_r2_fault_conditioned_recovery_contract3_v1_amendment.json`
- `hft_r2_fault_conditioned_recovery_train12_validation6_v1_report.json`
- `hft_r2_fault_conditioned_recovery_policy_v1.joblib` and report

The lifecycle adapter gained a research-only `RETIRE_OBLIGATION` transition. A live child must first receive terminal cancel evidence; unresolved quantity is returned with explicit owner state, unchanged target revision is blocked from immediate re-escalation, and actual portfolio state returns to Frozen R2. No desired/dream fill is credited and Frozen R2 alpha/theory is unchanged. The smoke runner gained backward-compatible price/lifecycle hooks and a narrow allowlist for the already-existing protected `PAIR_COMPLETION_REPLACE` Taker route. Defaults remain unchanged.

Execution semantics were HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, 250ms confirmed-own-state polling, real partial fills, cancel ACK before route release, actual-fill-only inventory and configured Taker fees. The two fault profiles were natural lifecycle and one real first passive child submitted at venue-minimum outcome price `0.06`; the latter was not guaranteed a no-fill outcome. No Target action/fill, winner, settlement or revealed PnL was a runtime input.

The contract smoke used opened markets `1575819,1576119,1576324`. All 6 market/fault contexts had identical first-action timestamp/side/state hash across continuations, all 24 replays had zero violations, and 4/6 contexts had a positive non-WAIT oracle. Aggregate recovery advantage versus matched WAIT was `+93.42`; oracle actions were WAIT 2, bounded Taker 2 and retire/rebase 2. `CONTINUE_PASSIVE` equaled WAIT terminal value in 6/6 contexts because both preserved the same live child without a new venue mutation. A locked pre-training amendment removed that transition-equivalent action, saving 25% compute without changing any threshold or price.

The chronological large matrix used globally opened but contract-new markets: train12 `1575819..1578546`, then validation6 `1578732..1579874`. The six later preregistered holdout markets `1580153..1581413` remained unused. Across 36 matched contexts and 108 replays, all semantic/cycle audits passed. The restricted oracle selected WAIT 14, bounded Taker 15 and retire/rebase 7; 22/36 contexts had positive non-WAIT incremental value and aggregate oracle improvement over WAIT was `+965.2515`. On validation alone, WAIT floor totaled `-175.32`, while the restricted oracle totaled `+97.0656`, an improvement of `+272.3856`; oracle action counts were WAIT 6, Taker 5 and retire 1. The recovery action family therefore has a large, nontrivial chronological value ceiling.

The fixed train-only learner was exactly the preregistered low-capacity per-action Random-Forest advantage regressor: 300 trees/action, min leaf 3, max features 0.7, seed `20260823`; duplicate state hashes were collapsed. It chose the highest median predicted advantage only when the tree-level 20th percentile was strictly positive, otherwise WAIT. No validation threshold or hyperparameter sweep occurred. On 23 unique train states it ACTed 7 (`30.43%`), realized floor `+98.997` versus WAIT `-341.40`, and gained `+440.397`. On chronological validation it ACTed only 1/12 (`8.33%`), and that Taker action was a false positive: learned-policy floor `-210.24`, matched WAIT `-175.32`, realized advantage `-34.92`, while oracle remained `+97.0656`. Always-Taker improved validation versus WAIT by `+126.9683` but still ended at `-48.3517`, so it also fails the absolute positive-value requirement. Retire fixed baseline was worse at `-221.839`.

Decision: **KEEP_MATCHED_RECOVERY_ACTION_VALUE_FAMILY; REJECT_POINT_STATE_POLICY_V1_BEFORE_HOLDOUT**. The policy WAIT/ACT rate was `91.67% / 8.33%`; learned-policy chronological value was negative and the holdout was not revealed. Do not tune q20, forest parameters, thresholds or action prices on these markets, and do not reinterpret less-negative fixed Taker value as success.

## 2026-08-24 — research objective boundary: Frozen R2 autonomous repair, not a replacement strategy

The user clarified and the research contract is now explicit: the objective is to teach the existing Frozen R2 control system to repair execution divergence autonomously, not to train a new directional or economic strategy. Frozen R2 remains the sole owner of market direction, desired inventory, passive making, passive repair, the economic authorization for active intervention and the cycle objective. A recovery learner may act only after an R2-owned obligation encounters actual execution divergence, and may select only execution responses such as formal WAIT, bounded Taker completion or cancel-ACK retire/rebase. Confirmed fills, partial fills, cancel acknowledgements and actual portfolio state must feed back into R2 before its next decision. Target actions and prefix oracles are offline diagnostic/curriculum labels only; they are never candidate runtime strategies.

This boundary is material. A policy that earns value by inventing independent entries, changing R2 side/direction, moving the inventory target or bypassing R2's passive-make/passive-repair/active-intervention balance is out of scope even if its HftBacktest PnL is positive. Conversely, a recovery controller is not required to reproduce Target child actions; it must preserve or restore the R2 cycle under actual queue, latency, partial-fill and order-lifecycle outcomes.

## 2026-08-24 — fixed-action semi-MDP FQI fails when deployed closed loop

Hypothesis: the matched first-state semi-MDP action-value learner may become useful when it is allowed to re-evaluate after every actual recovery option outcome and R2 re-entry, rather than being scored only by a first-action fixed proxy.

Files/artifacts:

- `tools/hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py`
- `tools/evaluate_hft_r2_recovery_semimdp_fqi_closed_loop_v1.py`
- `hft_r2_recovery_semimdp_fqi_closed_loop_v1_preregistered.json`
- `hft_r2_recovery_semimdp_fqi_closed_loop_contract1_v1_report.json`
- `hft_r2_recovery_semimdp_fqi_closed_loop_validation6_v1_report.json`

The adapter exposes the existing 79-field strict-past execution observation to a research-only lifecycle callback without changing default behavior. Each selected action is committed through its current option boundary; the next decision occurs only after real HftBacktest fills/cancel ACK and Frozen R2 re-entry. Execution used Predict Execution Tape V1 risk queue, configured latency, actual partial fills, actual-fill-only portfolio feedback and no dream fill. The contract market `1575819` reproduced both frozen first actions and baseline trajectories with zero violations.

On the locked chronological validation6, all 12 first state hashes and first actions matched the frozen FQI report. Across 48 option decisions the closed-loop policy chose WAIT 37, bounded Taker 10 and retire/rebase 1, for WAIT/ACT `77.08% / 22.92%`. Terminal winner-free floor was `-257.94` versus fixed WAIT `-175.32`, an actual closed-loop disadvantage of `-82.62`; it was also `-114.30` below the earlier first-state fixed-action proxy `-143.64`. The matched fixed-action oracle ceiling remained `+97.0656`.

Decision: **REJECT_CLOSED_LOOP_V1_BEFORE_HOLDOUT**. Subsequent actions learned only from fixed-action trajectories actively destroyed value. The six later holdout markets remained untouched. Do not tune Bellman iterations, RF parameters or thresholds on validation6.

## 2026-08-24 — matched two-step recovery branching proves a real autonomous-repair curriculum

Hypothesis: R2 self-repair may depend on an action-outcome-action sequence. Enumerate the first two recovery options under identical first state, using formal `WAIT_PRESERVE / CANCEL_ACK_BOUNDED_TAKER / CANCEL_ACK_RETIRE_REBASE`; commit each option through its real lifecycle boundary and force all later options to WAIT. This is a recovery curriculum beneath Frozen R2, not a new policy or alpha strategy.

Files/artifacts:

- `tools/evaluate_hft_r2_recovery_action_prefix_ceiling_v1.py`
- `hft_r2_recovery_action_prefix_ceiling_v1_preregistered.json`
- `hft_r2_recovery_action_prefix_ceiling_pilot1_v1_report.json`
- `hft_r2_recovery_action_prefix_expansion3_v1_report.json`
- `hft_r2_autonomous_recovery_branch_train23_v1_preregistered.json`
- `hft_r2_autonomous_recovery_branch_train23_v1_report.json`
- `hft_r2_autonomous_recovery_branch_train23_v1_audit.json`
- `tools/build_hft_r2_autonomous_recovery_branch_dataset_v1.py`
- `hft_r2_autonomous_recovery_branch_dataset_v1.json`

The one-market pilot (`1575819`, two natural/fault contexts, 18 HftBacktest runs) passed its small-scale gate: fixed-action oracle floor was `-15.12`, while the two-option prefix oracle was `-3.96`, an incremental `+11.16`. The chronological three-market check (`1575819,1576119,1576324`, six contexts, 54 runs) increased fixed-action oracle `+1.26` to prefix oracle `+35.64`, an incremental `+34.38`; 3/6 contexts required an applied second-action switch. All states matched and there were zero violations.

The authorized larger curriculum then used 23 train-only markets, two fault profiles and nine two-action prefixes: 414 HftBacktest runs and 46 initial recovery contexts. All 414 first states matched, all 46 WAIT baselines reproduced and semantic/cycle violations were zero. Fixed-action oracle floor was `+422.5111`; prefix oracle floor was `+648.7660`, incremental `+226.2549`, with an applied switch in 22/46 contexts. Across prefix-oracle trajectories the action counts were WAIT 112, Taker 26 and retire 20, for WAIT/ACT `70.89% / 29.11%`.

The audit stored 735 finite 79-field observations and found 107/107 complete matched second-state action groups, zero partial groups and correct previous-action memory. The resulting dataset contains 153 decision contexts across 23 markets and 84 causal features (79 current strict-past fields, option index and previous-action one-hot). First-state oracle improvement over WAIT was `+996.3502`; second-state oracle improvement was `+1597.8826`.

Decision: **KEEP_MATCHED_R2_AUTONOMOUS_RECOVERY_CURRICULUM**. The large incremental sequence ceiling is new evidence that Frozen R2 can, in principle, recover through execution-aware multi-step responses. It is not learned OOS value and prefix oracles must never be deployed.

## 2026-08-24 — current-state and transition-memory supervised repair both fail chronological generalization

Hypothesis V1: current strict-past state plus option index and previous recovery action may identify the matched two-step action value. Hypothesis V2: if that fails, explicitly encode the pre-action to post-action transition so the learner sees how R2 reacted to the actual outcome. These were two preregistered structural tests, not a threshold/model sweep.

Files/artifacts:

- `tools/train_hft_r2_autonomous_recovery_policy_v1.py`
- `hft_r2_autonomous_recovery_policy_v1_preregistered.json`
- `hft_r2_autonomous_recovery_policy_v1_report.json`
- `tools/build_hft_r2_autonomous_recovery_transition_memory_v2.py`
- `tools/train_hft_r2_autonomous_recovery_transition_memory_v2.py`
- `hft_r2_autonomous_recovery_transition_memory_v2_preregistered.json`
- `hft_r2_autonomous_recovery_transition_memory_v2.json`
- `hft_r2_autonomous_recovery_transition_memory_policy_v2_report.json`

Both learners used only train23 with chronological inner train17 / inner validation6; the external chronological validation6 and final holdout remained blind. The locked learner was per-non-WAIT matched-advantage Random Forest with a positive tree-q20 gate and no sweep. V1 used 84 current-state/action-memory features. It ACTed 51/115 on inner train and achieved matched advantage `+1512.6226`, but on inner validation it WAITed 36/38 and its two ACTs produced total selected advantage `-104.40` against an oracle `+899.04`. Decision: **REJECT_AUTONOMOUS_RECOVERY_POLICY_V1_BEFORE_EXTERNAL_VALIDATION**.

V2 forced the first recovery option to WAIT and learned only the second response from 162 fields: current 79, delta from previous 79, elapsed time and previous-action one-hot. Across all 26 inner-validation second states it chose WAIT 20, Taker 2 and retire 4, WAIT/ACT `76.92% / 23.08%`, with matched advantage `+90.36` versus oracle `+522.66`. But the deployable branch actually reached after a first WAIT contained four validation contexts; V2 ACTed zero, realized advantage `0`, and missed oracle advantage `+115.86`. The preregistered support/value gate failed.

Decision: **REJECT_TRANSITION_MEMORY_V2_AND_SUPERVISED_RECOVERY_FAMILY**. Do not tune feature subsets, forests, q20 or thresholds on this inner block, and do not run either rejected model on external validation or holdout. The sequence oracle is strong, but the currently observable public/current execution state does not identify the portable switch.

Overall disposition: **KEEP** the Frozen-R2 matched branch curriculum and actual-fill feedback architecture; **REJECT** fixed-action FQI and the current supervised cross-market recovery learners; **NEED_MORE_DATA** for user-scoped accepted/cancelled/partial/full own-order lifecycle and exact submit/cancel clocks. Existing public queue/depletion/order-age proxies were already tested in staged execution V1–V3 and must not be repeated. The current own-wallet collector still has zero accepted/fill/cancel support, so no venue-calibrated learner is justified yet.

Highest-value next experiment: treat recovery as a constrained R2 error-feedback problem, not an action classifier or new trading policy. Freeze the R2-owned desired cycle/manifold and test whether a small invariant controller can choose only among the already-proven legal recovery transitions based on the signed actual-versus-R2 obligation error and confirmed lifecycle phase, while abstaining when own queue state is unidentified. First run a small train-only matched-branch diagnostic; expand only if it separates the existing chronological false actions without using market direction, winner, PnL or Target future behavior. In parallel, keep collecting passive own-wallet lifecycle support; do not synthesize queue position from public Target data again.

## 2026-08-24 — later-landmark WAIT curriculum is a clean negative

Hypothesis: the first recovery checkpoint may be too impoverished. Let Frozen R2 and actual execution feedback proceed through formal WAIT, then branch the same two-action `WAIT / bounded Taker / retire-rebase` grid from later recovery landmarks. If autonomous repair can learn by observing longer before acting, later landmarks should retain non-WAIT oracle value and applied action switches.

Files/artifacts:

- `tools/evaluate_hft_r2_recovery_multilandmark_branch_pilot_v1.py`
- `hft_r2_recovery_multilandmark_branch_pilot_v1_preregistered.json`
- `hft_r2_recovery_multilandmark_branch_pilot_v1_report.json`

The one-market train-only pilot used already-opened market `1569361`, natural lifecycle, branch ordinals 1/2/3 and all nine two-action prefixes at each landmark: 27 HftBacktest runs. Every run WAITed through all prior R2 recovery options; branch actions were committed through their actual lifecycle boundary and every later action was WAIT. Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response, actual partial fills, cancel ACK and actual-fill-only R2 feedback were unchanged. No Target future action, winner, settlement or PnL was an input.

All 27 runs reached their locked branch, all three landmark state hashes matched across nine continuations, all-WAIT terminal floor was stable and lifecycle/cycle violations were zero. The result was nevertheless exact: at all three later landmarks both the fixed-action oracle and two-action prefix oracle were WAIT. Aggregate WAIT, fixed-action oracle and prefix-oracle floors were all `+52.92` over the three repeated contexts; oracle advantage and incremental two-step value were both `0`, with zero applied switches. WAIT/ACT was therefore `100% / 0%` for the oracle.

Decision: **REJECT_MULTI_LANDMARK_PILOT**. Do not expand ordinary later-WAIT checkpoints. Waiting longer does not create the missing autonomous-repair signal on this trajectory; intervention value can be perishable at the first execution fault.

## 2026-08-24 — explicit action-failure curriculum has regime shift but fails the train scale gate

Before building a new tool, de-duplication found an existing post-fault HftBacktest curriculum: `tools/hft_r2_fault_recovery_second_action_curriculum_v1.py`. It forces the first R2-authorized pair-completion Taker, injects either immediate `SUBMIT_REJECT` or acknowledged-open `NO_FILL_STALL` without crediting a fill, then branches the matched post-fault state to WAIT, exactly one bounded retry or retire/rebase. Five later opened markets had already produced eight usable contexts with WAIT oracle 3 / retry oracle 5, aggregate retry ceiling `+171.291` versus WAIT and zero violations. Those revealed contexts were retained only as a chronological regime diagnostic, not as training or policy-selection data.

Files/artifacts for the new staged expansion:

- `tools/hft_r2_fault_recovery_second_action_curriculum_v1.py`
- `hft_r2_fault_recovery_second_action_train23_v1_preregistered.json`
- `hft_r2_fault_recovery_second_action_train23_v1_runtime_amendment.json`
- `hft_r2_fault_recovery_second_action_train23_v1_report.json`
- `hft_r2_fault_recovery_second_action_stage1_v1_audit.json`

The preregistered train23 expansion was checkpointed and stopped when measured runtime showed the original 15–20 minute estimate would be about 90–110 minutes. Before resuming, a runtime-only amendment locked a six-market stage1 (`1569361,1571387,1572594,1572805,1573000,1573252`), 36 runs and a minimum aggregate oracle advantage of `+25`; no action, fault, price or value rule changed. The first five markets produced both fault branches; the sixth did not reach the identical injected fault in all continuations and was correctly unusable.

Stage1 produced 12 contexts, 10 usable matched contexts, zero semantic violations, WAIT oracle 8, one-retry oracle 2 and retire oracle 0. WAIT/ACT was `80% / 20%`. WAIT aggregate floor was `-12.492`; oracle floor was `+5.148`, advantage `+17.64`, below the locked `+25` scale gate. `SUBMIT_REJECT` contributed only `+0.54` across five contexts; `NO_FILL_STALL` contributed `+17.10`. Retire/rebase exactly equaled WAIT in all 10 usable contexts.

The already-revealed later five-market diagnostic was materially different: eight usable contexts, WAIT/ACT `37.5% / 62.5%`, WAIT floor `-39.531`, oracle `+131.76`, advantage `+171.291`. This is strong chronological regime drift in post-fault retry value, not evidence that a classifier can identify it.

Decision: **REJECT_FAULT_CURRICULUM_SCALE_BEFORE_FULL_TRAIN_NO_MODEL**. The remaining 17 train markets were not run, no model was trained and the six final holdout markets remain untouched. Confirmed lifecycle failure is a valid R2 autonomous-repair state and fixed retry is disproven, but early support is WAIT-dominant and weak while later opened support is ACT-heavy. Mixing them into another supervised learner would repeat the known nonstationarity failure. For this specific post-fault branch, collapse retire into WAIT because it is transition-equivalent in every usable context.

Overall current conclusion: **KEEP** the matched first-fault R2 recovery architecture and explicit own-lifecycle fault state; **REJECT** ordinary delayed-WAIT curriculum, unconditional retry and immediate full fault-dataset/model scale; **NEED_MORE_DATA** for real own-wallet accepted/reject/partial/no-fill/cancel lifecycles or a genuinely new strict-past venue-regime variable that explains the early-to-late retry-value shift. The next useful work is venue-state identification/calibration, not a new economic strategy and not another RF/threshold pass.

## 2026-08-24 — current success-standard amendment: prove autonomous repair before requiring positive PnL

The user corrected the current research gate. Absolute PnL `> 0` and unseen-holdout positive economic value are **not required for the present architecture-training stage**. The immediate objective is to prove that Frozen R2 can autonomously recover after one or several deliberately failed execution attempts, including both passive and active repair, and that the closed-loop recovery materially improves the faulted trajectory. PnL remains an audit field and a later optimization/promotion standard only.

The authoritative current contract is `hft_r2_autonomous_repair_behavior_standard_v1.json`. Frozen R2 still owns direction, desired inventory, passive making, passive repair and the economic authorization for active intervention. The recovery layer may only detect confirmed execution faults, choose formal WAIT or a bounded passive/active response inside an existing R2 obligation, and feed confirmed fills/cancel state/portfolio back to R2. A profitable independent policy would still be out of scope.

Current required behavior evidence:

- deliberately fail one passive order and several consecutive passive orders;
- include partial-fill remainder stall, cancel/ACK race, Taker submit reject and Taker no-fill/partial outcomes;
- demonstrate at least one autonomous passive repair path and one autonomous bounded active repair path;
- demonstrate at least one multi-step recovery after two or more consecutive failures;
- compare against a fault-matched no-repair/WAIT baseline using tracking-error area, terminal residual obligation, paired/core recovery and time/cycles to recovery;
- require zero duplicate-exposure, ownership-loss, dream-fill and lifecycle/cycle-invariant violations.

For the next small preregistered architecture pilot, "material improvement" should be locked before reveal; the initial suggested gate is at least `30%` reduction in tracking-error area or terminal residual obligation versus fault-matched no repair in at least two distinct fault families, with both passive and active repair represented. This is intentionally an attainable architecture-evidence gate, not a final profitability or deployment gate. Once the behavior is proven, chronological/unseen value and PnL tuning resume as the later stage.

This amendment supersedes earlier wording only for the **current** proof stage; it does not retroactively alter old experiment results or authorize changes to Frozen R2 alpha/theory semantics.

## 2026-08-24 — amended-standard reassessment pulls the coupled passive→active repair architecture back into the main line

The existing candidates were rescored under `hft_r2_autonomous_repair_behavior_standard_v1.json`, using their original HftBacktest + Predict Execution Tape V1 outcomes rather than rerunning or changing execution semantics. This is explicitly retrospective candidate triage: the validation outcomes were already present before the repair-quality metric was adopted, so it is not a new unseen confirmation. The reproducible audit is `hft_r2_autonomous_repair_candidate_reassessment_v1.json`.

The strongest recovered candidate is the existing coupled R2 passive-maintain/passive-repair trajectory followed by `CANCEL_ACK_BOUNDED_TAKER` only at an R2-authorized lifecycle fault. On chronological validation6 (12 natural/deep-first-passive contexts), matched WAIT had tracking-error area `941233.1082` and terminal residual `6318`. The bounded-active continuation reduced these to `453157.6126` and `2450.58`: improvements of `51.85%` and `61.21%`. Mean paired coverage rose from `0.5730` to `0.8765`; 9/12 contexts cleared a 30% recovery threshold; cycle/semantic violations were zero. By fault family, natural lifecycle improved terminal residual `35.26%`, while deep-first-passive improved tracking area `67.49%` and residual `75.75%`. Thus both locked fault-family gates pass. The PnL/floor audit also improved from `-175.32` to `-48.3517`, but absolute positivity is not required at this stage. WAIT/ACT at the lifecycle checkpoint was `0% / 100%` for this deterministic active-repair baseline. Decision: **KEEP_PRIMARY_ARCHITECTURE_FOR_MULTI_FAULT_PILOT**. This does not yet prove recovery after the Taker itself rejects/stalls/partially fills.

The passive half also remains supported by `hft_r2_fault_recovery_program_smoke_v1_report.json`: after one and two dropped passive maintain decisions, the coupled `BOTH_OFFSET0` program autonomously resumed passive maintain+repair with passive ACT `97.37%` / `94.74%`, recovered paired coverage `79.94%` / `74.94%`, and had zero invariants. Maintain-only and repair-only did not reproduce it. It is single-market and incomplete, so the decision is **KEEP_PASSIVE_REPAIR_COMPONENT**, not full graduation.

A new small locked test asked whether merely changing the learning target from terminal floor to recovery quality would produce a portable selector. Files: `tools/train_hft_r2_repair_quality_policy_v1.py`, `hft_r2_repair_quality_policy_v1_preregistered.json`, `hft_r2_repair_quality_policy_v1_report.json`, and its research-only joblib. Runtime state, actions, RF parameters and q20 rule were unchanged; the target was locked to `70%` normalized tracking-area reduction plus `30%` normalized terminal-residual reduction. Train12 used 23 unique states; validation6 remained chronological and holdout was untouched.

The learned selector WAITed 4 and ACTed 8 of 12 validation contexts. It reduced aggregate tracking-error area `37.36%`, terminal residual `48.11%`, increased mean paired coverage to `0.7986`, produced zero violations, and its PnL/floor audit was `+38.3856` versus WAIT `-175.32`. Nevertheless it failed the preregistered two-fault-family gate: deep-first-passive improved `47.51% / 62.41%`, but natural lifecycle only `22.32% / 22.56%`. Decision: **REJECT_REPAIR_QUALITY_SELECTOR_V1**. Do not rescue it with a validation threshold or RF sweep. The signal supports the recovery-quality objective, not this selector.

The recurrent joint behavior evidence is also promoted as a control primitive. Across nine matched second-decision contexts, an actual-fill/lifecycle change required a new decision epoch; two contexts let bounded active repair reduce terminal tracking error by `50%` and `93.33%` versus second-step WAIT, with zero invariants. Decision: **KEEP_RECURRENT_CONTROL_PRIMITIVE**, but do not mistake its matched-branch oracle for an autonomous learned policy.

Overall: **KEEP** the coupled passive→one bounded active→actual-state feedback architecture; **REJECT** the repair-quality RF V1; **NEED_MORE_DATA** only for full multi-fault graduation. The next small experiment should cross one/two passive failures with one bounded active attempt, then inject Taker submit reject or no-fill/partial plus cancel-ACK and require explicit residual ownership to return to Frozen R2 passive repair. This tests the missing self-repair loop without unconditional Taker retry, another point classifier, a new economic strategy, or any sealed cohort.

## 2026-08-24 — manual fault-state refresh entrance exam passes, but it is not yet an autonomous-repair result

Before testing another recovery candidate, the frozen structural examiner `tools/hft_r2_fault_state_refresh_exam_v1.py` was de-duplicated against its existing runs on `1578732,1579313,1579674`, then run on previously unexamined opened market `1579874`. The six cases were single/double/triple submit reject, single/double no-fill stall and mixed reject→no-fill. All requested faults were actually injected, including all four multi-fault cases. Every case reopened post-fault decision state with confirmed actual inventory, current desired portfolio and failure memory visible; reject/no-fill counters accumulated to the requested depth and lifecycle/cycle violations were zero. Structural grade: **PASS_STATE_RECONSTRUCTION_PREREQUISITE (6/6)**.

Audit-only terminal floors were `-8.10` after a single reject/no-fill, `-14.76` after double reject/double no-fill/mixed, and `-14.22` after triple reject. These are not recovery evidence. The v1 `RefreshProbePolicy` intentionally forces one first ACTIVE, emits WAIT after a fault and, after two or more faults, proposes desired UP/DOWN equal to actual holdings. It is a state-visibility probe, not a valid Frozen-R2 recovery policy; its post-fault behavior-level WAIT/ACT is `100% / 0%`. There is no matched no-repair baseline, no action oracle and no persisted terminal tracking-area/residual comparison. In the first eight stored post-fault states of all six cases, tracking error remained `-414`.

Decision: **KEEP_EXAMINER_AS_STRUCTURAL_GATE; AUTONOMOUS_REPAIR_NOT_EVALUATED**. Do not report this 6/6 structural pass as self-repair success. The audit is `hft_r2_fault_state_refresh_exam_market1579874_v1_audit.json`. The next candidate exam must preserve Frozen R2 desired inventory, use the coupled passive-maintain/passive-repair plus one bounded active route, return confirmed residual ownership to R2 after reject/no-fill, and compare tracking-error area, terminal residual, paired coverage and recovery cycles against a fault-matched WAIT continuation.

The user then selected the canonical V2 grader. To avoid rerunning the same six V1 paths, `tools/hft_r2_fault_state_refresh_exam_v2.py` was run on non-duplicate opened market `1579116`; report/audit: `hft_r2_fault_state_refresh_exam_market1579116_v2_report.json` and `hft_r2_fault_state_refresh_exam_market1579116_v2_audit.json`. V2 officially graded all six applicable cases PASS with refreshed actual/desired/failure state and zero invariants. However the planned double/triple/mixed cases each injected only their first fault: after that fault the trajectory produced no second eligible active child. Actual multi-fault coverage was therefore `0/4`, and all six audit floors were identically `+11.34`.

Interpret V2 precisely: **PASS_SINGLE_FAULT_STATE_RECONSTRUCTION; MULTI_FAULT_NOT_REACHED; AUTONOMOUS_REPAIR_NOT_EVALUATED**. V2 is the preferred structural preflight because it distinguishes non-applicable paths, but its PASS rule intentionally does not require every planned later fault to occur. Future multi-fault graduation must add a separate minimum actual fault-depth requirement and must run the real coupled R2 recovery candidate rather than `RefreshProbePolicy`.

## 2026-08-24 — V2 completed with matched candidate grading; simple return-to-R2 is exactly WAIT-equivalent

`tools/hft_r2_fault_state_refresh_exam_v2.py` now preserves its original structural mode and adds a preregistered `--exam-mode candidate`. Candidate and post-fault WAIT baseline must match the first injected fault state, both must reach the full requested fault depth, the behavior layer may not mutate Frozen R2 desired inventory, and safety/invariants must remain clean. It now reports tracking-error area, terminal residual, paired coverage, time/states to halve initial post-fault error, candidate WAIT/ACT, numeric two-action oracle ceiling and audit-only PnL/floor. Unknown partial/cancel/ambiguous cases are rejected rather than fabricated because the current adapter cannot deterministically create them without synthetic fill credit.

The small pilot used previously revealed opened market `1578921`, five reject/no-fill/mixed cases and ten matched HftBacktest replays. Prereg/report/audit: `hft_r2_fault_state_refresh_exam_v2_candidate_pilot_preregistered.json`, `hft_r2_fault_state_refresh_exam_market1578921_v2_candidate_pilot_report.json`, `hft_r2_fault_state_refresh_exam_v2_completion_audit.json`. The candidate issued one R2-authorized ACTIVE proposal, exactly one WAIT for each terminal failure event, never changed desired inventory, then returned authority to Frozen R2/default coupled passive handling. The baseline continued post-fault WAIT.

All five first-fault states matched and safety passed with zero desired mutations and zero invariant violations. Single reject and single no-fill reached full depth but both failed material recovery: candidate and baseline were exactly identical. Aggregate tracking-error area was `225311.595` for both, terminal residual `2610` for both, mean paired coverage `0.583333` for both, and audit floor `-104.40` for both. The two-action oracle recovery ceiling was also exactly zero. Candidate WAIT/ACT proposals were `5/5` (`50%` ACT). Both paths temporarily halved first post-fault error after `39.972s / 41` states, then ended with the same residual. Double reject, double no-fill and mixed reached only the first planned fault in both continuations, so they were correctly graded `NOT_REACHED_FAULT_DEPTH`.

Decision: **KEEP_V2_COMPLETED_FOR_SUPPORTED_FAULTS; REJECT_COUPLED_RETURN_R2_CANDIDATE_V1; NEED_MORE_APPLICABLE_MULTI_FAULT_PATHS**. Do not tune WAIT count or thresholds. Refreshed state is present, but merely WAITing once and returning to the existing default KEEP/passive path makes no venue mutation and is economically/trajectory-equivalent to WAIT. The next candidate must use refreshed confirmed fault/ownership state to choose a genuinely distinct legal transition—bounded state-dependent retry or cancel-ACK retire/rebase—while leaving R2 desired inventory authoritative.

### Superseded next-step note from the preceding 2026-08-23 iteration

The failure localizes the next problem. Same-state counterfactual action value is large and regime dependent, but the first-checkpoint feature vector does not carry enough history to identify the switch. Highest-value next experiment: replace the point state with a strict-past recovery belief/memory state summarizing the trajectory from child submit to decision—queue/depth depletion and replenishment path, bid/ask/spread path, cumulative/leaves evolution, fill sequence, target-revision/owner history, prior option duration and time-to-event censoring. Use a low-capacity landmark/cause-specific-hazard or recurrent ranker only after verifying this sequence representation separates the validation false positive from the five missed positive Taker contexts. Preserve the same matched action outcomes and validation split; do not fit a deeper model to the unchanged point features.

## 2026-08-24 — V2 native-strategy basic fault exam rejects the existing post-fault reaction

The completed V2 examiner added a deliberately small `--exam-mode strategy` test for the actual Frozen R2 plus the existing offset0 passive-maintain/passive-repair loop. It differs from the earlier probes and fault curricula because the candidate has no behavior override before or after the fault. Its matched baseline is also fully native until the same first fault, then issues formal WAIT with `freezeNewEconomicIntents=true`. A no-fault native reference is descriptive only. The pilot used opened/revealed market `1579874` and only `SINGLE_REJECT` plus `SINGLE_NO_FILL`; no sealed or unseen cohort was touched.

Examiner calibration found a real research-adapter wiring bug: `controller_step()` assigned `behavior_freeze_new_intents` without declaring it `nonlocal`. Freeze proposals appeared in prior reports but did not reach the maker/Taker intent gates. The one-line fix was applied in `hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py` and verified by actual HFT behavior: after the fix, each WAIT baseline made zero Taker attempts strictly after its faulted attempt, while each native candidate made three. Older post-fault containment artifacts that relied on the recorded flag should not be treated as proof that intent containment executed unless rerun.

Both first-fault states matched, both fault plans reached full depth, desired inventory mutations and cycle invariants were zero. Native R2 did visibly react after each fault: post-fault high-level WAIT/ACT was `94/24` per case (`20.34%` ACT), and it made three later Taker attempts. This was not effective autonomous repair. Aggregate candidate tracking-error area was `228841.812` versus frozen-WAIT `193316.58`, i.e. `18.38%` worse; terminal absolute tracking residual was `1224` versus `828`, i.e. `47.83%` worse. Neither path ever halved the first post-fault error. The audit-only floor was less negative for native (`-8.10` per case versus `-14.76`), but PnL/floor is not the present gate and cannot override failure of both recovery metrics.

Decision: **REJECT_NATIVE_R2_AUTONOMOUS_REPAIR_ON_THIS_BASIC_PILOT**. The current native trajectory has post-fault reactions, but they do not constitute repair. Do not expand this candidate or tune thresholds. The next small test, if pursued, should exercise one genuinely distinct legal residual-ownership return transition conditioned on confirmed fault state; it should not be another unconditional native retry or a new economic strategy. Prereg/report/audit: `hft_r2_fault_state_refresh_exam_v2_native_strategy_pilot_preregistered.json`, `hft_r2_fault_state_refresh_exam_market1579874_v2_native_strategy_basic_exam_report.json`, and `hft_r2_fault_state_refresh_exam_v2_native_strategy_basic_exam_audit.json`.

## 2026-08-24 — highest-prior bounded-active→passive version also fails the basic exam

The next quick V2 exam selected the existing R2-preserving structure with the strongest prior recovery evidence: native behavior until the first confirmed Taker fault, exactly one lifecycle-controlled `PAIR_COMPLETION_REPLACE`, then direct Frozen-R2 Takers disabled while passive maintain/repair and actual-fill feedback continue. Its matched no-repair baseline retains Frozen R2 target formation but blocks new maker execution children and direct Frozen-R2 Takers after the same first fault. This is not a new economic strategy and does not mutate desired inventory.

Two adapter gaps had to be fixed before interpreting value. `freezeNewEconomicIntents` state now reaches its outer gate; and the previously unused `fault_containment_freeze_before_redecision` now reopens a lifecycle decision epoch on a confirmed fault rather than leaving the pre-fault `KEEP_EXECUTING` latch in authority. Verification was execution-level: both reject and no-fill candidates recorded one actual post-fault `PAIR_COMPLETION_REPLACE` Taker; both baselines recorded zero post-fault Takers. First-fault states matched, requested fault depth was reached and cycle violations were zero.

The intended bounded repair still failed. On `SINGLE_REJECT`, tracking-error area improved only `0.95%`, terminal residual improved `0%`, and first post-fault error never halved. On `SINGLE_NO_FILL`, area worsened `5.17%` and residual worsened `8.57%`; it also never halved. Aggregate area was `239469.28509` versus no-repair `234524.088` (`-2.11%` reduction), and residual was `1314` versus `1260` (`-4.29%`). Paired coverage did improve by `+0.206` and `+0.409`, proving the venue action changed the portfolio, but not in a way that repaired the R2 obligation under the locked primary metrics. The two-action per-context oracle ceiling was only `0.47%` area improvement and `0%` residual improvement, so there is no threshold/model rescue inside this action pair.

Decision: **REJECT_BOUNDED_ACTIVE_THEN_PASSIVE_ON_THIS_BASIC_PILOT**. Do not expand or tune this retry. The highest-value distinct next basic test is passive residual-ownership return itself: require a failed active child to return an explicit obligation token, prove that token causes a new maker repair child and confirmed passive fill, and grade that path before any further active retry is permitted. Prereg/report/audit: `hft_r2_fault_state_refresh_exam_v2_bounded_active_passive_basic_preregistered.json`, `hft_r2_fault_state_refresh_exam_market1579874_v2_bounded_active_passive_basic_report.json`, and `hft_r2_fault_state_refresh_exam_v2_bounded_active_passive_basic_audit.json`.

## 2026-08-24 — direct-fault passive-only isolation is an exact no-op and localizes the missing controller state

Before inventing a new ownership token, V2 isolated the passive leg after a naturally generated Frozen-R2 Taker fault. Candidate and baseline preserved Frozen R2 target formation and prohibited later direct Takers. Candidate alone allowed maker execution and emitted `KEEP_PASSIVE`; baseline blocked new maker execution children. This direct-fault context is distinct from the old dropped-passive-decision program and from pair-completion remainder ownership.

The opened/revealed `1579874` pilot used only single submit-reject and single no-fill. Both first-fault states matched, both faults reached full depth, no desired mutation or invariant occurred, and candidate post-fault behavior was `KEEP_PASSIVE` 234 times with zero active proposals. Nevertheless both candidate and target-preserving no-repair were exactly identical: aggregate tracking-error area `234524.088`, terminal residual `1260`, and two-action oracle advantage `0`. The mechanism audit explains why: candidate produced zero confirmed post-fault maker fills and zero ownership-return/release events. Merely permitting passive execution does not create a repair obligation.

The state also exposes an important arbitration boundary. The faulted direct Taker was `DOWN`, while first post-fault tracking error was `-414`, so the signed portfolio recovery side was `UP`. Therefore a new token must not copy the failed order side. The controller first has to decide whether the failed intent was an ADD obligation, a REPAIR obligation or unrelated alpha intervention, then map a genuine repair obligation to the current signed deficit.

Decision: **REJECT_PASSIVE_ONLY_AFTER_DIRECT_FAULT_ON_THIS_BASIC_PILOT**. Do not tune passive offsets. The next distinct small experiment should instrument the frozen current Taker decision with its own `predEffect` and signed tracking alignment, retire ADD/anti-aligned failures, and create a passive-repair obligation only for REPAIR/aligned failures. This is the missing high-level obligation mapper predicted by the hierarchical-controller hypothesis. Prereg/report/audit: `hft_r2_fault_state_refresh_exam_v2_passive_only_basic_preregistered.json`, `hft_r2_fault_state_refresh_exam_market1579874_v2_passive_only_basic_report.json`, and `hft_r2_fault_state_refresh_exam_v2_passive_only_basic_audit.json`.

## 2026-08-24 — true repair-child fault reveals strong native passive self-repair; explicit handoff is redundant

Intent instrumentation corrected the target of the recent manual exams. The first faulted direct Frozen-R2 Taker on `1579874` was confirmed `predEffect=ADD_EFFECT`, side `DOWN`, with actual net `-36`, target net `+378`, tracking error `-414` and signed recovery side `UP`; it was anti-aligned with portfolio repair. A strict repair-aligned direct-Taker selector then found no applicable natural direct Taker on opened markets `1576991,1579313,1579674`. Therefore those direct-fault rejections remain valid only for treating an alpha/add failure as a repair trigger. A true repair-fault exam must target the lifecycle-authorized `PAIR_COMPLETION_REPLACE` child.

The new one-market basic pilot reused the existing second-action curriculum on already-opened train-only market `1569361`, injected one `SUBMIT_REJECT` into the first `PAIR_COMPLETION_REPLACE`, and matched the post-fault second-state hash. Predict Execution Tape V1, HftBacktest risk queue, 1092ms entry / 273ms response latency, 250ms actual-own-state polling, partial fills and actual-fill-only inventory remained unchanged. Direct Frozen-R2 Takers were isolated after the observed fault in both continuations; desired inventory was never mutated and sealed/HFT-Forward/Echtgeld data were not used.

First, a new research-only lifecycle action explicitly returned the residual ownership to the passive repair loop. The action and `OWNERSHIP_RETURNED_TO_PASSIVE_REPAIR` event both executed, but `RETURN_TO_PASSIVE_REPAIR` and formal WAIT produced exactly the same trajectory: each had 26 confirmed post-second Maker fills, tracking-error area `39339.025`, terminal residual `252`, paired coverage `0.76923` and zero violations. The two-action recovery oracle ceiling was exactly zero. Decision: **REJECT_EXPLICIT_PASSIVE_RETURN_REDUNDANT_ON_THIS_BASIC_PILOT**. Do not tune this handoff action.

The equality exposed the real mechanism: lifecycle WAIT does not stop Frozen R2's existing passive loop. A preregistered matched ablation therefore kept the same first action, fault and second WAIT but blocked every new Maker execution child after the fault while preserving the desired target. With the native passive loop active, the initial 18-share post-fault error reached zero and crossed its half-error threshold in `5081ms`; the no-repair ablation never improved below 18. Native passive repair produced 26 confirmed post-second Maker fills versus zero. Across the full market, tracking-error area fell from `252018.558` to `39339.025` (`84.39%` improvement), terminal residual fell from `1710` to `252` (`85.26%` improvement), and paired coverage rose from `0` to `0.76923`, with matched state, zero desired mutation and zero lifecycle/cycle violations. Audit-only worst-case floor improved from `-9.36` to `-3.24`, while revealed realized PnL worsened from `+8.64` to `-3.24`; this divergence is why the result is repair-behavior evidence only, not economic success.

Decision: **KEEP_NATIVE_PASSIVE_REPAIR_EVIDENCE**. This is the first clean result in the current basic-exam sequence that passes the locked 30% autonomous-repair threshold after a deliberately failed true repair child. It supports the hierarchical interpretation: the logic/lifecycle layer identifies and owns the repair obligation, while the existing smaller R2 passive loop performs recovery; a new monolithic handoff action was unnecessary. It is one opened train market and one submit-reject family, so it is not active-repair, multi-fault, chronological/unseen-OOS or economic graduation evidence.

Files/artifacts:

- `tools/hft_r2_repair_fault_passive_return_basic_exam_v1.py`
- `tools/hft_r2_repair_fault_native_passive_loop_ablation_v1.py`
- `hft_r2_repair_fault_passive_return_basic_preregistered.json`
- `hft_r2_repair_fault_passive_return_basic_exam_v1_report.json`
- `hft_r2_repair_fault_native_passive_loop_ablation_preregistered.json`
- `hft_r2_repair_fault_native_passive_loop_ablation_v1_report.json`
- `hft_r2_repair_fault_native_passive_loop_ablation_v1_audit.json`

Highest-value next experiment: repeat the locked matched native-passive versus no-repair ablation for `PAIR_COMPLETION_REPLACE NO_FILL_STALL` on one known-applicable opened train market. Expand across markets only if that second fault family also produces confirmed passive recovery and material improvement. Do not tune passive offsets, lifecycle timing, classifiers or PnL thresholds.

## 2026-08-24 — NO_FILL_STALL shows strong trajectory containment but fails strict residual attribution

The locked second fault-family pilot reused opened train-only market `1569361` and the same matched native-passive versus no-new-maker-child ablation. The first `PAIR_COMPLETION_REPLACE` was acknowledged open but credited zero execution through its existing confirmation deadline; both continuations reached the same second-state hash with tracking error `+18`, recovery side `DOWN` and target revisions UP/DOWN `2/2`. Both formally chose lifecycle WAIT. Direct Frozen-R2 Takers were isolated after the observed fault, desired inventory was unchanged, and HftBacktest + Predict Execution Tape V1 queue/latency/partial-fill/actual-own-state semantics remained fixed.

The native passive loop materially contained the complete moving R2 trajectory. It produced 26 confirmed post-second Maker fills / 450 shares versus one 18-share fill from an already-live UP child in the no-repair ablation; 498 attempted new Maker executions were blocked in the ablation. Tracking-error area fell from `258992.838` to `64193.407` (`75.21%` improvement), terminal residual fell from `1764` to `162` (`90.82%`), and paired coverage rose from `0` to `0.92308`. Worst-case floor improved from `-18.72` to `+22.32`; revealed PnL improved from `+17.28` to `+22.32`. State matching, desired-mutation and lifecycle/cycle safety gates all passed.

The preregistered local residual gate did not pass: total absolute tracking error never fell below the initial 18 in either branch, so neither reached the locked half-error threshold of 9. The trace shows why attribution is ambiguous rather than simply absent: an already-live opposite-side UP Maker filled first, while subsequent Frozen R2 target revisions continued moving the portfolio objective. Aggregate tracking error therefore mixes discharge of the original failed DOWN repair obligation with later obligations.

Decision: **NEED_MORE_DATA_OBLIGATION_ATTRIBUTION**. Do not claim the second fault family has graduated and do not tune offsets or timing. Keep the strong global containment evidence, but add an obligation-scoped residual ledger keyed to the failed pair-completion target revision/recovery side so confirmed Maker fills that discharge that fixed residual are separated from later R2 target changes. Re-score the same small path before expanding markets. Prereg/report/audit: `hft_r2_repair_fault_native_passive_loop_nofill_preregistered.json`, `hft_r2_repair_fault_native_passive_loop_nofill_v1_report.json`, and `hft_r2_repair_fault_native_passive_loop_nofill_v1_audit.json`.

## 2026-08-24 — parallel execution-incident notification contract passes without changing R2 trajectory

The user selected a parallel incident-notification architecture: it classifies execution facts and delivers structured messages to the existing R2 information receiver, but it may not select an execution action or mutate Frozen R2 desired state. De-duplication found that Echtgeld V24/V25 already exercise executor fail-closed safety and the V1/V2 fault tools already exercise state refresh/recovery actions. The distinct missing test was notification classification plus receiver delivery, especially the reversal from a live no-fill stall to a later confirmed fill, with exact non-interference against an otherwise identical HFT run.

`tools/hft_r2_execution_incident_notification_exam_v1.py` added a research-only notifier/receiver probe and an 11-case lifecycle contract: pre-write reject, acknowledged long no-fill then cancel, long no-fill then late fill, partial-fill stall then completion, cancel/fill race, cancel-ACK timeout, unknown submission recovered filled, unresolved unknown, Taker reject, Taker terminal no-fill, and duplicate/out-of-order input. All `11/11` passed. Uncertain states preserve ownership and emit freeze/reconcile; terminal confirmed zero-fill returns unfilled responsibility; late/partial fills carry only confirmed fill deltas and require recomputation from actual inventory. The notifier always reports `noExecutionActionSelected=true`.

The HFT pilot used only already-opened train market `1569361`; no sealed, official HFT Forward, unseen holdout or Echtgeld input was touched. Three paired with-notifier/no-notifier cases used HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, 250ms polling, actual partial fills and actual-fill-only inventory: natural execution, one Maker submit reject, and one Maker acknowledged-open zero-fill held for 15 seconds before terminal failure. A backward-compatible research trace exposes existing submit/fill clocks, and the fault adapter accepts an optional no-fill duration; defaults and normal execution semantics are unchanged.

All three HFT pairs passed exact non-interference. Enabling the notifier left fills, tracking-error area, terminal residual, paired coverage, audit-only floor/PnL, controller decision count and cycle violations exactly equal to the matched no-notifier run; notifier-selected actions, desired mutations, schema errors and cycle violations were all zero. In the natural path it classified 16 delay notices, eight live no-fill stalls, three partial fills, 30 full fills and 15 late fills after a prior delay. Those 15 actual HftBacktest fills arrived after `3.308s` to `104.212s`, proving that the notifier can reverse a stall assessment when a genuinely late fill appears rather than treating the earlier warning as terminal. Reject and 15-second no-fill each emitted their required confirmed terminal notification.

The three descriptive terminal paths were: natural area/residual/coverage `28677.37841 / 252 / 0.86667`; Maker reject `14469.203 / 108 / 0.54545`; Maker long no-fill `55247.299 / 396 / 0.80`. These are not comparisons between policies, so PnL and oracle value remain audit-only/N/A. WAIT/ACT is also N/A because the notification layer formally selects no action.

Decision: **KEEP_PARALLEL_INCIDENT_NOTIFICATION_CONTRACT_FOR_R2_RECEIVER_EXAM**. This proves the parallel channel is well-formed, actual-fill-safe and behaviorally non-interfering; it does not yet prove R2 uses the message to improve recovery. The next small experiment should connect the same structured inbox to R2's existing information receiver and compare notified versus raw-own-state-only response for one reject, one confirmed long no-fill and one stall-then-late-fill path. Do not train a model until that rule-based compatibility test shows whether Frozen R2 already reacts correctly.

Files/artifact:

- `tools/hft_r2_execution_incident_notification_exam_v1.py`
- `tools/hft_r2_cycle_preserving_execution_smoke_v1.py`
- `tools/hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py`
- `hft_r2_execution_incident_notification_exam_market1569361_v1_report.json`

## 2026-08-24 — R2.1 information-only incident inbox is connected to the actual R2 snapshot path

The previously validated notifier was only a parallel receiver probe. The next distinct test connected its strict-past lifecycle facts to the real Frozen-R2 controller input used by the HFT runner. The research name for this additive interface is **R2.1**. Each controller snapshot may carry one new namespaced field, `r21ExecutionIncidentInbox`; no existing R2 public, portfolio, book, desired-target or own-state field can be overwritten through the provider interface. The inbox exposes only observed order/fill facts. Action-like `receiverDirective` and `ownershipDirective` fields are deliberately removed, and the contract declares `actionAuthority=false`, `orderMutationAuthority=false` and `desiredPortfolioMutationAuthority=false`. Frozen R2, its model features and all Maker/Taker execution functions were unchanged.

The small paired A/B reused already-opened train market `1569361` and the same three cases as the notification exam: natural lifecycle, first Maker submit reject, and first Maker acknowledged-open no-fill stall held for 15 seconds. HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, 250ms own-state polling, partial fills and actual-fill-only inventory were unchanged. The bridge observes lifecycle state after a controller decision, so its incident can enter only a later controller snapshot; every delivered event also passes `event.atMs <= snapshot.sampledAtMs`.

All `3/3` cases passed. The required incident types were present in the snapshots actually passed to `controller._step`: natural execution included live no-fill stall and late-fill reversal, reject included `SUBMIT_REJECT_CONFIRMED`, and the 15-second stall included live stall plus `TERMINAL_ZERO_FILL_CONFIRMED`. R2.1 had non-empty inboxes on `281 / 271 / 273` controller calls respectively. More importantly, all 253 high-level Frozen-R2 decisions per case were byte-canonical identical to the matched R2 baseline; the complete Maker/Taker lifecycle traces and descriptive terminal execution summaries were also exact. Thus the connection is visible to R2 but behaviorally inert until a separately trained/defined R2.1 response layer exists.

WAIT/ACT, oracle ceiling and learned-policy realized value are **N/A** for this iteration because the inbox is not a policy and formally cannot choose an action. This is a single opened train market compatibility/safety test, not chronological or unseen-OOS recovery/value evidence. Descriptive baseline and R2.1 execution stayed exactly equal: natural area/residual `28677.37841 / 252`, reject `14469.203 / 108`, and long no-fill `55247.299 / 396`.

Decision: **KEEP_R21_INFORMATION_ONLY_INCIDENT_INBOX**. R2.1 is now the name of the research-only information contract, not a new strategy and not a production/live mode. The next experiment may train or hand-code a response consumer only outside the executor: it can map inbox history plus actual state into information-receiver features, but it still cannot call order functions or mutate desired inventory. Graduation remains fault-matched passive and bounded-active autonomous recovery with zero dream fill/ownership/cycle violations; positive PnL is not required at this architecture stage.

Files/artifact:

- `tools/hft_r21_information_only_incident_inbox_exam_v1.py`
- `tools/hft_r2_cycle_preserving_execution_smoke_v1.py`
- `hft_r21_information_only_incident_inbox_exam_market1569361_v1_report.json`

## 2026-08-24 — R2.1 cooperation training keeps an obligation-scoped lifecycle belief, not an action gate

The first R2.1 cooperation-training pilot deliberately did **not** repeat the rejected point-state/one-step-delta recovery selectors. It trained a low-capacity, no-sweep logistic information head to estimate whether the unchanged native passive loop would obtain a confirmed recovery-side Maker fill within 15 seconds. Its output was added inside `r21ExecutionIncidentInbox` with `actionRecommendation=null`; it had no order, desired-portfolio or executor authority.

V1 trained on the natural trajectory of opened train market `1569361` and validated on separate Maker submit-reject and 15-second no-fill trajectories from the same market. Train was strong, but unseen-fault calibration failed: validation AP lift was `1.4417x` and top-quintile lift `1.6393x`, while Brier was `0.5254` versus a training-prevalence constant `0.2601`. Reject was worse than no-fill, and the model was severely overconfident. Decision: **REJECT_R21_LIFECYCLE_BELIEF_V1**. No probability threshold, regularization or feature-depth tuning was attempted.

The failure matched the preceding obligation-attribution diagnosis: moving R2 target/tracking state mixed old failed obligations with later objectives. V2 therefore changed the research unit rather than the model knob. Each confirmed failed Maker child creates a fixed ledger containing fault side, unresolved quantity and target revision. Confirmed Maker inventory progress discharges same-side ledgers FIFO; later R2 target changes cannot relabel the old residual. Training injected the first three Maker submit rejects on `1569361` and validation injected the first three Maker no-fill stalls on later opened train market `1571387`. All planned faults and six paired obligations were reached.

The first V2 diagnostic had AP `0.9815`, AUC `0.8788` and Brier `0.1316` versus constant `0.1886`, but its preregistered AP/top-quintile relative-lift gates were mathematically impossible at validation prevalence `0.8684`: their theoretical maximum was `1.1515x`, below locked `1.25x / 1.5x`. It was therefore not promoted or rescored as a pass. The model and features were frozen without retraining or threshold changes, and a new replication metric was preregistered before opening the R2.1 trajectory on train market `1572594`: prevalence-normalized AP improvement `>=0.25`, AUC `>=0.65`, relative Brier improvement `>=5%`, at least two actual faults and exact non-interference.

The frozen replication passed. All three Maker no-fill faults occurred and produced three obligation ledgers / 30 uncensored belief states, with 25 positive and five negative future-progress labels. AP was `0.96419`, prevalence-normalized AP improvement `0.78517`, AUC `0.8000`, and Brier `0.16666` versus training-prevalence constant `0.19534` (`14.68%` relative improvement). The paired baseline and belief runs had all 203 Frozen-R2 high-level decisions byte-canonical identical, complete Maker/Taker lifecycle traces identical, terminal execution identical, strict-past/schema gates clean and zero cycle violations. Descriptive execution remained area/residual `70254.43646 / 396`, Maker/Taker fills `324 / 0`, paired coverage `0.3333` and audit floor/PnL `-29.88` in both branches.

WAIT/ACT, oracle value ceiling and learned-policy realized value are **N/A**: this model estimates obligation progress and cannot select an action. The replication is a later opened train trajectory, not official HFT Forward, sealed or unseen promotion OOS. Decision: **KEEP_R21_OBLIGATION_RESIDUAL_BELIEF_COMPONENT**. This is the first retained R2.1 situation-understanding component, not autonomous-repair graduation.

Highest-value next experiment: freeze this belief and generate a small chronological multi-market cooperation curriculum where only the R2 logic layer may consume it. At matched confirmed fault states compare native passive continuation, formal WAIT and at most one already-R2-authorized bounded active option. Do not let R2.1 call the executor or create an independent action, and do not train the response head until the matched HftBacktest action-value rows have nonzero recovery-quality dispersion.

Files/artifacts:

- `tools/train_hft_r21_lifecycle_belief_v1.py`
- `tools/train_hft_r21_obligation_residual_belief_v2.py`
- `tools/evaluate_hft_r21_obligation_residual_belief_v2_replication.py`
- `hft_r21_lifecycle_belief_pilot_v1_preregistered.json`
- `hft_r21_lifecycle_belief_pilot_market1569361_v1_report.json`
- `hft_r21_obligation_residual_belief_v2_preregistered.json`
- `hft_r21_obligation_residual_belief_v2_report.json`
- `hft_r21_obligation_residual_belief_v2_replication_preregistered.json`
- `hft_r21_obligation_residual_belief_v2_replication_report.json`

## 2026-08-24 — R2.1 cooperation response head preserves WAIT and captures one sparse active-repair regime on two chronological pilots

The already-generated but previously unhandoffed cooperation curriculum was audited before any new run. It directly used the frozen R2.1 obligation-residual belief inside the Maker-fault path and compared matched `NATIVE_PASSIVE`, `FORMAL_WAIT` and exactly one R2-authorized `REPLACE_ROUTE`; it was therefore reused instead of regenerating the older Taker-fault second-action curriculum or fabricating a cross-role belief adapter. Revealed development markets `1573662,1573848` supplied four usable contexts with zero strict-past/cycle violations and nonzero recovery-label dispersion under the locked 30% architecture threshold: WAIT 3, ACTIVE 1.

Two unrestricted development-only trees were rejected before any holdout branch was opened because they bypassed R2.1 and split only on point-state working remainder or bounded capacity. The final response representation was structurally constrained to one strict-past interaction: `R2.1 P(passive progress within 15s) × outstanding working-recovery fraction × one-shot bounded-active coverage`. A depth-1 balanced tree, with no sweep, learned the frozen cutoff `>0.45 => ACTIVE_ONCE`; otherwise it preserves native passive recovery. R2.1 still has `actionAuthority=false`, order-mutation authority false and desired-portfolio authority false. The response belongs to the R2 logic layer.

On chronological opened-train holdout `1574038`, predictions were persisted before alternative branch outcomes: SUBMIT_REJECT WAIT and NO_FILL_STALL ACTIVE. The selected no-fill action reduced terminal residual `702→216` (`69.23%`) and tracking-error area `24.34%`; the submit WAIT missed another positive active oracle without causing harm. Aggregate residual improved `1422→936` (`34.18%`) and area improved `10.28%`. WAIT/ACT was `1/1`; both contexts matched and all strict-past/semantic gates passed.

The exact model artifact (SHA-256 `B063E1BB83B1126F462F81A89D406383C0CF29EE7AE843EE1F0F2DF2ACF1E80C`) was then loaded without fitting or threshold changes on later chronological opened-train market `1574352`. It locked WAIT for both faults. That was the correct regime: ACTIVE would have worsened no-fill residual `33.33%` and submit-reject residual `457.14%`. The selected policy therefore matched native passive with zero harm, WAIT/ACT `2/0`, while the active oracle ceiling was zero.

Across the four holdout contexts, learned WAIT/ACT was `3/1` (`75%/25%`). Native residual `1926` fell to `1440` (`25.23%`), versus oracle `900`; native tracking-error area `292342.825` fell to `270616.229` (`7.43%`), versus oracle `212947.756`. The head captured `47.37%` of the residual oracle advantage and `27.37%` of the area oracle advantage, selected no harmful ACTIVE, and correctly WAITed on the entire no-positive-active replication market. Audit-only worst-case floor sum improved from `-72.18` to `-37.98`; realized PnL was not persisted by this curriculum and is not the present gate.

Decision: **KEEP_R21_COOPERATION_RESPONSE_HEAD_V1_FOR_EXPANDED_FAULT_CURRICULUM**. This is the first small blind evidence that the R2.1→R2 cooperation structure can both preserve the native passive loop and selectively authorize one useful active repair. It is only four holdout contexts and two single-Maker-fault families, so it is not profitability, multi-fault, unseen-promotion or deployment graduation.

Highest-value next experiment: keep this head frozen and add small matched HFT curricula for (1) live no-fill warning followed by a late actual fill, where active duplication should be suppressed; (2) partial-fill stalled remainder, where the obligation quantity must shrink to confirmed residual; and (3) reject/no-fill of the one bounded active child, where ownership must return to native passive repair. Expand market count only if these lifecycle transitions preserve strict-past state, zero duplicate exposure and material recovery.

Files/artifacts:

- `tools/hft_r21_cooperation_response_belief_curriculum_v1.py`
- `tools/train_evaluate_hft_r21_cooperation_response_head_v1.py`
- `hft_r21_cooperation_response_head_v1_preregistered.json`
- `hft_r21_cooperation_response_head_v1_train_report.json`
- `hft_r21_cooperation_response_head_v1_holdout1574038_locked_predictions.json`
- `hft_r21_cooperation_response_head_v1_holdout1574038_report.json`
- `hft_r21_cooperation_response_head_v1_replication_preregistered.json`
- `hft_r21_cooperation_response_head_v1_holdout1574352_locked_predictions.json`
- `hft_r21_cooperation_response_head_v1_holdout1574352_report.json`
- `hft_r21_cooperation_response_head_v1_replication_summary.json`

## 2026-08-24 — active-child failure keeps WAIT; forced passive ownership return over-repairs no-fill

The next locked small exam kept the V1 response model and SHA-256 unchanged. It reused opened train market `1574038`, where the frozen head already selects ACTIVE after an initial Maker `NO_FILL_STALL`, then injected a real HFT lifecycle failure into that one `PAIR_COMPLETION_REPLACE`: submit reject or acknowledged-open terminal no-fill. At the matched post-active-failure state, the only counterfactual was the existing V1 behavior `WAIT_FOR_CLARITY` versus the R2 lifecycle action `RETURN_TO_PASSIVE_REPAIR`. This was distinct from the old deterministic ownership tests because all outcomes used HftBacktest + Predict Execution Tape V1 queue/latency/actual-fill semantics.

Both fault contexts were usable: the primary and post-active-failure state hashes matched, V1 selected ACTIVE `2/2`, both active-child faults occurred, both fallback branches executed and strict-past/cycle violations were zero. Explicit passive return also emitted its ownership event and obtained confirmed recovery-side Maker fills in both faults. It nevertheless failed recovery value. After active submit reject, residual improved only `576→558` (`3.13%`) and tracking-error area only `1.06%`. After active no-fill, residual worsened `162→216` (`33.33%`) and area worsened `19010.309→62230.847` (`227.35%`). Aggregate PASSIVE_RETURN worsened residual `738→774` (`4.88%`) and area `116350.647→158540.811` (`36.26%`).

The mechanism is important: formal WAIT did not stop the native passive loop. WAIT still received `162 / 126` recovery-side Maker shares after submit-reject/no-fill. Forcing ownership return increased those to `234 / 144`, but the extra path over-repaired the no-fill trajectory. The matched two-action oracle ceiling was only `2.44%` residual and `0.89%` area improvement over WAIT, so there is no material selector rescue inside this action pair. Audit-only floor favored passive return (`31.32` versus WAIT `21.78`) but cannot override failure of the locked recovery metrics.

Decision: **REJECT_ACTIVE_FAILURE_PASSIVE_RETURN_V1; KEEP_EXISTING_POST_ACTIVE_FAILURE_WAIT**. Do not tune the V1 cutoff or add an unconditional passive-return rule. The current V1 behavior—one selected active attempt, then formal WAIT while confirmed actual-state feedback and the existing passive loop continue—is the safer coordination structure on this pilot.

A separate matched natural-lifecycle guard then reconnected the frozen head on opened train market `1569361`. Actual HFT execution produced three partial incidents and 15 late fills, but no terminal Maker fault/obligation. The response head correctly activated zero times. Actual execution, every Frozen R2 decision, Maker lifecycle and Taker lifecycle were exact against the R2.1 information-only baseline; strict-past and cycle gates were clean. Decision: **KEEP_LIVE_PARTIAL_LATE_FILL_TERMINALITY_GUARD**. A live stall/partial warning remains owned by the still-live child and cannot become a repair action until terminal confirmation.

The notifier and obligation ledger already understand `TERMINAL_PARTIAL_FILL_CONFIRMED`, but this natural pilot did not contain one. Existing canonical V2 deliberately refuses deterministic partial injection because synthetic fill credit would violate performance semantics, and old Taker `PARTIAL_CHILD_REMAINDER` contexts have already been used by rejected recovery learners. Decision: **NEED_REAL_TERMINAL_PARTIAL_SUPPORT_DO_NOT_FABRICATE**. Wait for a genuine terminal partial Maker lifecycle from HFT/own-wallet data or add a real simulator-supported injector before training that fault family.

Files/artifacts:

- `tools/hft_r21_active_failure_passive_return_exam_v1.py`
- `hft_r21_active_failure_passive_return_exam_v1_preregistered.json`
- `hft_r21_active_failure_passive_return_exam_v1_report.json`
- `tools/hft_r21_live_partial_late_fill_guard_exam_v1.py`
- `hft_r21_live_partial_late_fill_guard_exam_v1_preregistered.json`
- `hft_r21_live_partial_late_fill_guard_exam_v1_report.json`
- `hft_r21_lifecycle_transition_expansion_v1_summary.json`

## 2026-08-24 — R2.1 cancel/ACK cooperation is clean; no natural fill-during-cancel support in two small pilots

Hypothesis: after the frozen R2.1 response head authorizes one bounded ACTIVE, a Maker fill that arrives while the existing recovery child is cancel-pending must be reported to R2, remain owned by that child until terminal venue evidence, and be included in the executor's actual-state remainder recomputation before the PairCompletion Taker is sent. This does not give R2.1 action authority. Frozen R2 owns the ACTIVE decision; the executor owns cancel/remainder mechanics.

De-duplication excluded the existing deterministic `FILL_DURING_CANCEL` notifier contract and V7/V8/V10 ownership arithmetic. The distinct exam followed the frozen response V1 through the actual HftBacktest cancel route. It added only research trace fields at cancel ACK (`actualNetAtCancelAck`, `targetNetAtCancelAck`, `trackingErrorAtCancelAck`, `targetRevisionAtCancelAck`) and did not alter order behavior, Frozen R2, the response model or its SHA-256.

The preregistered small cohort ran opened development markets `1574038` with three initial Maker `NO_FILL_STALL` faults and `1573848` with three initial Maker `SUBMIT_REJECT` faults. It stopped at two because neither path contained a natural Maker fill during the cancel window. HftBacktest + Predict Execution Tape V1 risk queue, 1092ms entry / 273ms response latency, 250ms own-state polling and confirmed-actual-fill inventory were fixed. No sealed, official HFT Forward, unseen promotion or Echtgeld path was touched.

Frozen response WAIT/ACT was `0/2`: it selected `ACTIVE_ONCE` in both markets. Both routes observed venue-terminal cancel ACK, submitted no PairCompletion Taker before ACK, and recomputed exactly from the actual tracking error at ACK. Market `1574038` canceled at `1787350239663`, observed terminal `CANCELED` at `1787350241237`, had tracking error `18`, recomputed `18`, and submitted the Taker at the ACK timestamp. Market `1573848` did the same over `1787349910112→1787349911707`. Strict-past, receiver schema and cycle violations were all zero; every semantic subgate passed.

Neither canceled child filled: cumulative execution was `0→0` in both cancel windows. R2.1 correctly emitted zero `FILL_DURING_CANCEL` notifications rather than inventing a race. Oracle recovery ceiling and learned-policy realized value are **N/A** because this is a lifecycle cooperation/safety exam with no matched alternative policy. Descriptive terminal residual/area were `216 / 67536.6291` and `54 / 14801.482`; PnL/floor are audit-only.

Decision: **NEED_MORE_DATA_NATURAL_CANCEL_FILL_RACE**, while **KEEP_CANCEL_ACK_WAIT_AND_ACTUAL_STATE_RECOMPUTE** for the two applicable no-race paths. This is not a failure of R2.1 cooperation: the supported transition passed, but the specific adverse event was absent, so notification-under-race remains unproven.

Highest-value next experiment: search the already-collected Predict own-wallet lifecycle and future newly arriving lifecycle records for a genuine fill between cancel request and terminal ACK. If one exists, replay that exact sequence as a tape-derived safety fixture; otherwise keep the gate pending. Do not synthesize a favorable fill or sweep more R2 markets merely to manufacture the event.

Files/artifacts:

- `tools/hft_r21_cancel_fill_race_cooperation_exam_v1.py`
- `tools/hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py`
- `hft_r21_cancel_fill_race_cooperation_exam_v1_preregistered.json`
- `hft_r21_cancel_fill_race_cooperation_exam_v1_report.json`

## 2026-08-24 — current semantic R2+R2.1 cooperation is connected to the Echtgeld engine, still safely unarmed

The live integration boundary was re-audited before deployment. The promoted component is `PASS_CURRENT_R21_R2_SEMANTIC_COOPERATION_RETEST`: R2.1 is an information-only lifecycle/state layer and Frozen R2 remains the sole decision authority. The later safe-response V2 and rich-context V3 heads remain `MORE_CURRICULUM_BEFORE_PROMOTION` and were deliberately not loaded into Echtgeld.

The previous 8789 draft exposed only the last global CAP100 event and left actual inventory, target revision and remaining obligation null. The V2 integration now aggregates per-child ownership, projects actual-confirmed inventory and per-side execution revisions/residual obligations, filters engine events by `R2_R21_8789`, and injects the namespaced `r21ExecutionIncidentInbox` into the exact snapshot recorded and evaluated by R2. R2.1 has no action, order-mutation, desired-portfolio-mutation or executor authority. Retained lifecycle cooperation is enforced: active completion waits for terminal cancel ACK, and a confirmed active-child zero-fill/reject enters formal WAIT until confirmed actual state changes. Live partial/late fills remain owned by the live child.

8781 now preserves dynamic source/strategy attribution in its durable order ledger and live replay, projects source identity on events, and rejects a sub-$1 Maker request before quote/write without silently upsizing. This closes the 10-share price-below-0.10 failure path. The production launcher verifies engine source support, exact 8789 version, 10-share quantity, information-only R2.1 authority and the accepted semantic artifact before reporting startup.

Small offline validation passed `22/22` targeted tests across the new R2.1 bridge and existing Echtgeld V23/V26/V27/V28 safety paths. Five unrelated historical launcher-wiring tests still fail against already-diverged 8776/V423/legacy-string contracts; no out-of-scope files were changed to hide them. Runtime was then replaced under the existing safe state: old 8781 was paused, source-unselected, had zero active orders and no unknown write; the new 8781 retained those exact conditions. Public-only 8783 and R2+R2.1 8789 are healthy, old 8787 was stopped, and 8781 reports allowed sources `CAP100_8787,R2_R21_8789`. Final state remained `armed=false`, `selectedSourceId=null`, `activeOrders=0`, `entryWriteFrozen=false`; no Predict order was sent.

Decision: **KEEP_R2_R21_ECHTGELD_SEMANTIC_INTEGRATION_V2; NOT_YET_ARMED**. The next action is a separately authorized controlled live test: select only `R2_R21_8789`, resume 8781, wait for the engine-enforced next complete market, and monitor source attribution, cancel/ACK ownership, confirmed-fill inventory and R2.1 residual state. Do not enable a learned ACTIVE response head.

## 2026-08-24 — Echtgeld runtime replaced with R2+R2.1 V3.3 information/state-sequence transport, still fail-closed and unarmed

The user selected the newer R2+R2.1 V3.3 integration. De-duplication identified the exact artifact family rather than the older rich-context V3 response experiment: `R21_R2_CONTEXT_SEQUENCE_V3_3_CONTRACT`, the corrected six-case fault ladder, L6–L11 lifecycle/restart/degraded-information exams, 500×80 randomized chaos, three actual-HftBacktest information-transport chaos overlays and `R21_V33_LIVE_DEPLOYMENT_CONTRACT_V1`. V3.3 remains information/state transport only: `r21ActionAuthority=false`, event mutation and executor callback are forbidden, Frozen R2 owns decisions, and the learned ACTIVE response head remains `NOT_PROMOTED`.

The checked-in/untracked V3.3 source patch was already present before this migration. Focused validation compiled the bridge/controller/engine, passed all deployment-contract checks and passed `16/16` Echtgeld/R2.1 tests. The deployment contract deliberately changes the capital contract from CAP100 to exactly `10` shares per order with no strategy-level notional cap; the venue-side minimum remains `1 USDT` and 8781 remains the only credential/order owner. This is a material operating difference, not merely a version label.

8781 was replaced only after proving `PAUSED`, source-unselected, zero active orders, no unknown write and no entry freeze. After replacement it preserved `armed=false`, `runtimeStatus=PAUSED`, `selectedSourceId=null`, `activeOrders=0`, `entryWriteFrozen=false`, stop-loss `40 USDT`, auto-redeem `READY`, and allowed sources `CAP100_8787,R2_R21_8789`. 8789 reports `UNIFIED_R2_R21_V33_INFORMATION_ONLY_ECHTGELD_10SHARE_V3`, bridge contract `R21_ECHTGELD_STATE_BRIDGE_V33_CONTEXT_SEQUENCE`, `configuredShares=10`, `notionalCapEnabled=false`, sequence state present, zero pending cancels/orphans/Takers and the expected fail-closed block `8781_SOURCE_NOT_SELECTED`. No source was selected, no service was armed and no Predict order was sent.

Decision: **KEEP_R2_R21_V33_ECHTGELD_INTEGRATION_LOADED; NOT_ARMED**. Before any controlled live activation, capital provisioning and stop-loss must be reviewed against the new uncapped-notional contract; do not reuse the previous statement that a 100 USDT per-market hard cap still exists.

## 2026-08-25 — Echtgeld one-complete-market control and collection/cancel audit, still unarmed

The controlled-live operator boundary was tightened without changing Frozen R2 or R2.1 decision semantics. Ordinary 8781 `RESUME` still enters the existing engine-side `ARMED_WAIT_NEXT_MARKET` gate and then runs continuously from the next complete market. A separate opt-in `run-next-market-once` mode now uses the same anchor, admits only that next market, blocks any second-market entry engine-side and automatically invokes the existing PAUSE plus Maker cancel-all path at market rollover. Both the standalone `/echtgeld.html` page and the React Echtgeld page expose distinct continuous and one-market buttons. With no selected source both remain disabled.

The live-data audit separated two collectors. The 8781 durable order ledger/live replay is populated by prior real venue activity and contains actual FILLED, partial (`0.98/0.99`), REJECTED and CANCELED states with vendor IDs, cumulative shares/cost, cancel-request timestamps, source attribution and official settlement; replay currently spans 12 markets. The independent own-wallet WebSocket collector is subscribed and heartbeat-fresh, read-only and unable to place/cancel orders. It still contains zero events because no own order occurred after activation, so it is collecting correctly but is not calibration-ready. A stale prior JWT-rejection message was cleared on successful resubscription and explicit `subscriptionReady/heartbeatFresh/dataCaptureReady` fields were added.

R2+R2.1 V3.3 cancellation was exercised without venue writes through the real controller-to-engine method boundary: the controller sends `/cap100/cancel`, 8781 reaches `batch_cancel_orders_raw`, the child remains `CANCEL_PENDING` until a terminal venue update, and R2.1 retains ownership. The previously unused `cap100_lock` now serializes Maker/Taker submission, operator/heartbeat/stop-loss pause and cancel mutations; a repeated cancel for an already `CANCEL_PENDING` child is idempotent, preventing controller-rollover and engine-auto-pause from issuing duplicate venue cancels or creating false uncertainty. Immediate live telemetry was also corrected to label V3.3 as `10` shares with no strategy notional cap rather than the stale CAP100/KEEP18 label.

Focused validation passed `31/31` execution/data/cancel tests plus the V3.3 deployment contract, Python compilation, Dashboard TypeScript/Vite production build, standalone inline-JS syntax and a browser-visible page check. Final runtime remained `8781 armed=false`, `PAUSED`, source-unselected, zero active orders and no entry freeze; 8789 remained blocked by `8781_SOURCE_NOT_SELECTED`; the wallet collector remained `LIVE`, subscription-ready and heartbeat-fresh. No source was selected and no Predict order was sent.

Decision: **KEEP_ENGINE_OWNED_ONE_MARKET_CONTROL_AND_SERIALIZED_CANCEL_PATH; NOT_ARMED**. The first actual V3.3 live lifecycle remains unobserved. A future operator-selected one-market run must be treated as the first evidence for R2_R21_8789 venue attribution and wallet-event capture, not as already-proven live performance.

## 2026-08-25 — first R2+R2.1 V3.3 Echtgeld run attribution and rollover-fence repair

The first controlled Binance run was reconstructed before remediation. Market `1658035` contains 14 durable engine rows and every row is attributed to source `R2_R21_8789`, strategy `R2_R21_V33_INFORMATION_ONLY_10SHARE_NO_NOTIONAL_CAP`, with exactly 10 requested shares per order. The run had 13 Maker attempts, one Taker attempt, 12 FILLED and two REJECTED rows; venue-confirmed inventory was 40 UP / 79.8 DOWN shares, confirmed cost 52.3 USDT and settled PnL +27.5 USDT. Port 8787 was not listening and no CAP100 controller process was present. Therefore CAP100 did not place these orders. The misleading CAP100 appearance came from shared-adapter inheritance and hard-coded Dashboard/engine labels.

The audit nevertheless found two material implementation defects. First, 8789 directly inherited the CAP100 shadow controller and only disabled its cap at runtime, leaving an ambiguous semantic/code boundary despite the uncapped 10-share behavior. Second, one-market rollover could block the request while PAUSE/cancel completed; the controller timed out and retained a local next-market order as an orphan even though 8781 had rejected it before venue submission. Treating every HTTP 400 as transport uncertainty amplified the false orphan. Binance history and active-order reads proved that this rollover intent never reached the venue.

The repaired 8789 now inherits the canonical Frozen R2 controller directly. Frozen R2 received default-false extension hooks, so R2.1 remains information-only and the ordinary Frozen R2 action/RNG path is unchanged. The engine now enters a synchronous `COMPLETING` fence before launching slow PAUSE/cancel work, rejects every second-market entry immediately and deterministically, and the controller rolls back local state on proven pre-venue HTTP 4xx rejections instead of fabricating an unknown submission. Durable projections and both Echtgeld Dashboard surfaces now derive names/events from `entry_source`; R2+R2.1 rows report `cap100=false` and remain explicitly marked as using the shared execution adapter.

Focused validation passed `29/29` tests, including a deliberately slow PAUSE test proving that the second-market request returns before PAUSE completes and creates no venue row. The V3.3 deployment contract passed, Python compilation passed and the Dashboard production build passed. Runtime replacement preserved the safe boundary: 8781 is `PAUSED`, `armed=false`, source `R2_R21_8789`, venue `binance`, zero adapter active orders and no entry freeze; 8789 reports Frozen R2, 10 shares, no cap, no R2.1 action authority, zero active Makers/pending cancels/orphans and block reason `8781_PAUSED`. Binance's signed official active-order endpoint also returned zero. Port 8787 remains closed.

Decision: **KEEP_R2_R21_SOURCE_ATTRIBUTION_AND_FAIL_FAST_ROLLOVER_FENCE; PAUSED_NOT_ARMED**. The historical local orphan row remains immutable audit evidence, while the restarted runtime is clean. A later controlled run must still be explicitly resumed by the operator; this repair did not send, cancel or resume any order.

## 2026-08-25 — market 1658035 postmortem: live R2+R2.1 mapping passes; fixed-action HFT is approximate, native closed loop diverges

Hypothesis: the first controlled 10-share R2+R2.1 Echtgeld run should map exactly from Frozen R2/R2.1 controller records to 8781 writes, its failure notifications should be visible in R2's recorded strict-past input, and the same observed actions should retain similar execution when replayed through HftBacktest on the same market. De-duplication excluded the existing native 18-share closed-loop artifact, the Oracle replay that injects actual fills, and the old CAP100 market-1513668 calibration. The distinct test fixed the observed market-1658035 intent sequence and allowed HftBacktest + Predict Execution Tape V1 to determine fills without injecting Echtgeld outcomes.

The live policy mapping passed. All 14 engine attempts came from `R2_R21_8789`, used strategy `R2_R21_V33_INFORMATION_ONLY_10SHARE_NO_NOTIONAL_CAP`, requested exactly 10 shares and mapped without side/price/size mismatch to the R2+R2.1 controller ledger or the Frozen-R2 active-intervention decision. There were 13 Maker attempts—9 DOWN and 4 UP—in sequence `D,D,D,D,U,D,D,U,U,D,D,U,D`, plus one UP Taker. Across 248 recorded decisions, WAIT/ACT was `237/11`, an ACT rate of `4.44%`; the durable write ledger is authoritative because a rolled-back Maker reject and the active-intervention hook can create an attempted write while the final decision row records WAIT. Live realized execution was 12 filled orders, UP 40 / DOWN 79.8 shares, cost 52.3 USDT and audit-only settled PnL +27.5.

R2.1 correctly detected the Maker `DOWN 10 @ 0.34` pre-venue post-only reject. Event seq 73 was present in the next R2 input after 299ms as `SUBMIT_REJECT_CONFIRMED`, `TERMINAL_REMAINDER`, actual DOWN 29.82 versus target 39.82 and remaining DOWN obligation 10. The passive loop then attempted DOWN repairs after 7.000s and 8.275s; the first formal `PASSIVE_REPAIR` state appeared after 17.863s and the first Maker write from a formal repair decision after 19.927s. This proves detection and delivery, not causal action lift, because no matched inbox-removed counterfactual was run.

The active UP Taker exposed a second lifecycle boundary. R2.1 delivered `LIVE_NO_FILL_DELAY` after 5.175s and `LIVE_NO_FILL_STALL` after 15.229s. R2 remained `ACTIVE_INTERVENTION_REQUIRED` but execution stayed WAIT while ownership was `CURRENT_CHILD`, so no duplicate Taker was sent. The venue's terminal `FAILED` arrived only 6.459s after the last market decision and never appeared in an R2 decision snapshot. Decision: **KEEP_R21_MAKER_REJECT_AND_NONTERMINAL_STALL_TRANSPORT; NEED_FIX_POST_MARKET_TERMINAL_HANDOFF**.

The primary fixed-action HFT replay included the 13 writes that reached Binance. Semantics were Predict Execution Tape V1 receipt-aligned L2 plus normalized true matches, HftBacktest partial-fill exchange, risk-adverse queue, 1092ms entry / 273ms response latency, GTX Maker and 2200ms marketable-limit Taker confirmation with no late fill credit. Dream fill and actual-fill injection were forbidden. HFT matched fill/no-fill state on 12/13 orders (`92.31%`), reproduced the UP Taker no-fill and all 40 UP shares, but filled only 70 DOWN shares versus live 79.8. The missing order was the first `DOWN 10 @ 0.39`: Binance accepted and filled 9.82 shares, while the delayed Predict/HFT post-only order expired with zero fill. Median absolute first-fill timing error among orders filled in both environments was 2370ms. Audit-only PnL was +21.5 versus live +27.5, exactly a -6.0 difference driven by the missing winning DOWN fill. Actual decision-to-venue-response median was 1170ms (Maker 1139ms), so the standard 1092ms HFT entry delay is not grossly inconsistent; the remaining first-order mismatch is primarily a venue-book/execution-path difference, not evidence for a universal latency retune.

The already-produced native closed-loop HFT trajectory is not a behavioral reproduction. Live had 13 Maker attempts and one UP Taker; native HFT had 28 Maker placements (17 DOWN / 11 UP) and one DOWN Taker. Live Frozen-R2 rows were WAIT/ACT `237/11`; native HFT was `225/27`. Divergence began before the first fill: live first Maker was DOWN 0.39 at +11.820s, while native HFT first Maker was UP 0.54 at +17.201s. Native HFT later sent and filled a DOWN Taker at +184.828s, whereas live sent an UP Taker at +81.326s and it did not fill. The native artifact also uses the original 18-share scale, so it is diagnostic of state/action trajectory divergence rather than a size-matched economic comparison.

Decision: **KEEP_EXACT_R2_R21_LIVE_POLICY_MAPPING; REJECT_HFT_AS_EXACT_BINANCE_REPRODUCTION; KEEP_FIXED_ACTION_HFT_AS_APPROXIMATE_DIAGNOSTIC; REJECT_NATIVE_HFT_AS_BEHAVIORAL_REPRODUCTION_OF_THIS_RUN**. The critical separation is now evidenced: most fixed actions reproduce, but an early venue-state/action difference is enough for actual-fill feedback to move the autonomous closed loop onto a very different path. Because Echtgeld used Binance Prediction while performance-grade HFT uses Predict.fun Tape V1, this single market measures total venue-data plus simulator divergence, not pure simulator error. The highest-value next evidence is a Binance-specific book/lifecycle tape replay and a durable post-market terminal delivery fixture, not another Predict-HFT threshold sweep.

## 2026-08-25 — R2+R2.1 Echtgeld Taker timeout lifecycle gap repaired; one controlled live ACK still required

Market `1658035` exposed a concrete execution-contract omission rather than a new strategy failure. Existing HFT tools and the live controller both fixed Taker confirmation at `2200ms`, but the Echtgeld path returned `False` after that horizon without canceling the unfinished Binance Taker. Because 8789 also polled 8781 events only while consuming public strategy snapshots, the zero-fill child remained `CURRENT_CHILD` through the rest of the market and its terminal state arrived after the last R2 decision.

The repair is executor-owned and does not change Frozen R2 or R2.1 authority. 8781 now cancels only the unfinished remainder of an `R2_R21_8789` Taker after the existing 2200ms horizon, retains `CANCEL_PENDING/CANCEL_UNKNOWN` ownership until venue reconciliation, preserves any fill-during-cancel delta, and prevents a stale NEW/partial history row from rolling cancel state backward or causing duplicate cancellation. 8789 now polls the durable lifecycle feed from its heartbeat even when the public market snapshot stream has stopped, so terminal evidence can still release ownership and enter the R2.1 inbox.

Focused and inherited validation passed `54/54` tests plus the V3.3 deployment contract. The fixture covers zero-fill timeout, partial fill during cancel, one-and-only-one venue cancel, terminal-only ownership release and terminal delivery without a strategy snapshot. No venue order was sent for validation. Runtime replacement preserved `armed=false`, selected source `R2_R21_8789`, zero active orders, no entry freeze, 8789 block `8781_PAUSED`, and R2.1 ownership `NO_CHILD`.

Decision: **KEEP_LIFECYCLE_FIX_NEED_ONE_CONTROLLED_LIVE_ACK**. Oracle ceiling, learned value and chronological OOS value are N/A because this is a lifecycle correctness repair. The next already-authorized single-market run should verify an organically occurring unfinished Taker produces exactly one timeout cancel, remains owned through cancel pending, reaches a Binance terminal state, and then allows Frozen R2 to resume its repair cycle without duplicate exposure. Do not submit an artificial Echtgeld order solely to force this event.

Files/artifacts:

- `tools/audit_r21_echtgeld_market_hft_replay_v1.py`
- `data/research/execution_aware_fill_lifecycle_v0/r21_echtgeld_behavior_hft_replay_1658035_v1_preregistered.json`
- `data/research/execution_aware_fill_lifecycle_v0/r21_echtgeld_behavior_hft_replay_1658035_v1_report.json`

## 2026-09-03 — ETH lifecycle V65–V69 and V70 composite-carrier checkpoint

Current priority remains functional architecture before numeric tuning. Mandatory progression is 1–3 market smoke -> ownership/responsibility/overfill/role-drift audit -> fixed Stage-A 16 -> only then any larger cohort. No H100 at this checkpoint. Realistic-HFT, no dream fill, no 8781, no new exposure at <=180 seconds, and PnL remains diagnostic only.

### V65 global Expand ownership dedup — smoke PASS

V65 preserved the useful V64 same-objective ACTIVE_EXPAND execution fallback while blocking an older failed V44 objective when a newer Expand responsibility/carrier already existed.

Official 3-market smoke `1823769,1827223,1827903`:

- ACTIVE_EXPAND submits: 2
- ACTIVE_EXPAND fill quantity: 4.8368298368 shares
- global supersede blocks: 1
- Repair->Expand->Repair rounds: 3 -> 5, gain +2
- generated Expand exposures subsequently repaired: 2
- truthMismatch / overOwned / repairDrift / responsibilityOverfill: all zero

Market `1827223` is the critical ownership proof: the duplicate V64 fallback was superseded and the candidate returned exactly to baseline. V65 smoke passed, but no H100 authorization followed.

### V66–V68 Target experience tests — action authority rejected

V66 forced actions at same-market Target wall clocks and was mixed/nonportable: 32 tokens, 14 pre-180, 3 consumed, 2 teacher submits, one 2.0833-share fill repaired, but aggregate rounds stayed 5 -> 5 and `1823769` was harmed. Same-market Target time is diagnostic experience, not authority.

V67 phase-aligned OUR-clock counterfactual produced only five Stage-A samples (2 positive / 3 negative), below the preregistered minimum 12. The minimum was not lowered.

V68 bidirectional counterfactual added HOLD->OPEN and suppress-one-existing-OPEN->CONTINUE branches. Stage-A 16 produced 19 exact samples (9 positive / 10 negative), with zero safety-accounting violations. The fixed logistic model severely overfit: train AUC 0.9444, fixed-validation AUC 0.3333, leave-one-market-out AUC 0.1444, balanced accuracy 0.2611, Brier 0.5301. The saved model is research-only and has no runtime/action authority.

Artifacts:

- `data/research/r4_v0/p0_provenance_v1/target_eth_v68_stagea_bidirectional_counterfactual_router_20260903.joblib`
- `data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V68_STAGEA16_BIDIRECTIONAL_COUNTERFACTUAL_ROUTER_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V68_STAGEA16_ROUTER_TRAINING_DECISION_20260903.json`

### V69 Target-action condition-gap audit — functional PASS, architecture signal found

Runner: `tools/run_eth_repair_v69_target_action_condition_gap_smoke.py`.

The first smoke was invalidated because `EXISTING_SAME_LANE_CARRIER` was mistakenly treated as universally mandatory. The corrected V2 instrumentation passed 3-market smoke with 95/95 strict-past snapshots, median lag 122ms, all four roles represented and all safety metrics zero.

Fixed Stage-A 16 then completed on the second worker in four waves. Aggregate:

- markets: 16/16
- Target lifecycle components: 580
- strict-past OUR snapshots covered: 580/580
- median snapshot lag: 98ms; maximum: 5624ms
- truthMismatch / overOwned / repairDrift / responsibilityOverfill: all zero
- PASSIVE_REPAIR same-role ready 80/203; exact Target side/action ready 16
- ACTIVE_REPAIR same-role ready 0/99
- PASSIVE_EXPAND, time-allowed: 16/72 same-role ready; 7 exact
- ACTIVE_EXPAND, time-allowed: 6/44 same-role ready; 3 exact

The largest condition gaps were Repair capacity/venue-min legality for passive Repair, and active Repair shared budget/carrier/fresh-evidence/payment-scope availability. For Expand, the main gaps were recent Repair interaction/model authorization and failed-passive-source/shared-budget availability.

Official artifacts:

- `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V69_TARGET_ACTION_CONDITION_GAP_PREREGISTERED_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V69_TARGET_ACTION_CONDITION_GAP_INSTRUMENTATION_AMENDMENT_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V69_STAGEA16_TARGET_ACTION_CONDITION_GAP_DECISION_20260903.json`

### V69 parent-fill reinterpretation — next architecture is composite, not imitation

`tools/compare_eth_v53_our_target_same_market.py` reconstructs a Target weak-side parent fill as Repair first up to the pre-fill inventory gap, then assigns only the remainder to Expand. Therefore many apparent same-clock Repair+Expand actions are two ledger allocations of one physical parent fill, not two independent submissions.

On Stage-A 16, Target had 119 composite parent fills across 14/16 markets:

- passive composite: 78 fills across 14 markets; 29 before the <=180 exposure cutoff; median total 10 shares, median Repair 3.7866, median Expand overflow 4.6265
- active composite: 41 fills across 13 markets; 21 before cutoff; median total 5.6318, median Repair 3.2724, median Expand overflow 3.0825
- under current OUR rules, exact readiness for both component roles at the same parent fill was zero

This changes the learning unit. A component-level Target role classifier is semantically wrong for these events. Any future experience model must use the parent carrier plus its preauthorized Repair and Expand allocations.

### V70 current authorized next step — micro-world first

Hypothesis: one physical carrier may hold two preauthorized, non-overlapping ledger responsibilities: Repair pays oldest-first up to the bound obligation; only an already-authorized Expand budget may receive overflow. Expand overflow births the next Repair generation. This must use one shared total budget and global Expand occupancy, without adding a second Expand objective.

Required micro-world cases before realistic-HFT:

1. full fill crossing Repair residual into authorized Expand overflow;
2. partial fill wholly inside Repair;
3. later partial fill crossing into Expand;
4. multiple Repair obligations paid oldest-first;
5. overflow rejected without prior Expand authorization;
6. older Expand source rejected when newer global Expand occupancy exists;
7. passive/active lanes cannot double-spend the shared carrier budget;
8. at <=180 seconds only the Repair portion may execute; no Expand overflow;
9. active quantity derives from shared responsibility, not Maker 10/18-share sizing;
10. replayed/duplicate fill cannot double-pay or create duplicate generation debt.

Promotion gate: conservation of physical fill quantity, zero double-spend, zero overfill, zero overOwned, zero truthMismatch, zero unauthorized role drift, and generation debt exactly equal to authorized Expand overflow. A micro-world pass authorizes only a 1–3 market reachability/functional smoke, not Stage-A or H100.



## 2026-09-03 — V70B corrected rejection -> V70D parallel relay preregistered

Latest evidence supersedes the earlier V70 micro-world-only checkpoint. Corrected one-market V70B on market 1823769 removed the prior pre-birth payment leak and truth-role mismatch, but still failed the architecture gate: physical composite fill 5.5 = Repair 4.1666666667 + Expand overflow 1.3333333333; generation debt was exactly 1.3333333333, generationPaid remained 0, preBirthPaymentLeak=0, truthMismatch=0, while semantic Repair->Expand->Repair rounds fell from matched V65 baseline 2 to candidate 1. Decision: REJECT_V70B_SINGLE_PARENT_COMPOSITE_BIND_POINT; do not retry with threshold/qty/delay tuning.

V70C parallel shared-budget ledger remains the stronger architecture candidate. The current-code regression rerun passed all 10/10 micro-world cases with physical conservation, exact generation debt, zero Repair/Expand overbooking, zero unauthorized role drift, and duplicate-fill idempotence. Target relay evidence also supports generation semantics: among 50 pre-180 composite parents, 49 are followed by opposite-side Repair, 45 within 30s, 48 within 60s, median lag 8.5s.

New unique next step is preregistered as `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V70D_PARALLEL_RELAY_HFT_SMOKE_PREREGISTERED_20260903.json`. V70D preserves a valid passive Repair carrier and gives any additional Active/Expand path only an explicit disjoint reservation from one shared responsibility ledger. Confirmed Expand fill creates equal opposite-side generation debt; only strictly post-birth opposite Repair increments may pay it. No pre-birth fill credit, no duplicate Expand objective, newer global Expand occupancy remains authoritative, <=180s forbids new Expand, and active sizing derives from responsibility rather than 10/18-share conventions.

Current authorization: run only the preregistered one-market realistic-HFT smoke on 1823769. KEEP requires observed parallel reservation, all safety/accounting gates zero/true, exact debt-at-birth, and candidate semantic rounds >= matched V65 baseline. If no parallel reservation is reachable, reject this bind point and audit an earlier Repair-parent reservation clock; do not tune parameters. No Stage-A/H100 and no 8781 yet.


## 2026-09-03 — V70D post-fill bind rejected; V70E early Repair-submit clock found

The preregistered one-market V70D realistic-HFT smoke on `1823769` completed. Safety/accounting remained clean (`truthMismatch=0`, `overOwned=0`, `responsibilityOverfill=0`, `repairDrift=0`, `preBirthPaymentLeak=0`, `duplicateGenerationDebt=0`), but no parallel reservation was reachable. Candidate semantic Repair->Expand->Repair rounds fell from matched V65 baseline `2` to `1`; Expand fill/debt/payment were all zero. The two model-authorized post-Repair states had `pExpand=0.7474` and `0.9954`, but both saw `NO_PARALLEL_REPAIR_CARRIER`. Decision: **REJECT_V70D_POST_FILL_BIND_POINT**. Do not tune thresholds/qty/delay. Artifact: `TARGET_ETH_V70D_PARALLEL_RELAY_HFT_SMOKE_20260903.json`.

A behavior-inert V70E reachability audit then moved observation to the earlier Repair submission/reservation clock, as required by the V70D reject branch. On the same realistic-HFT market there were six Repair submit clocks; one was already eligible under the frozen V44 teacher and unchanged `0.5` authorization rule while the Repair carrier still had its full outstanding quota: `t=1788160855560`, key `UP_10`, Repair qty `1.7543859649 @ 0.57`, remaining `244.44s`, `pExpand=0.6976948`, coord debt `1.9930338`, Repair progress `0.69049`. Therefore the architecture is reachable earlier; the previous failure was a bind-clock problem, not absence of authorization. Artifact: `TARGET_ETH_V70E_EARLY_REPAIR_RESERVATION_CLOCK_AUDIT_20260903.json`.

The next unique step is preregistered as `ETH_REPAIR_V70F_EARLY_CLOCK_PARALLEL_RELAY_SMOKE_PREREGISTERED_20260903.json`: consume at most that already-authorized Repair-submit clock, preserve the Repair carrier unchanged, create only one globally-deduped disjoint Expand responsibility, and grade actual HFT fill -> exact generation debt -> strictly post-birth opposite Repair payment plus non-decreasing semantic rounds. No Stage-A/H100, no parameter sweep, no 8781.

Worker note: `btc5m-worker` SSH was reachable, but copying the full V70 runtime reset the SCP connection. The one-market smoke/audit was therefore executed on the host rather than left idle; future compute should retry the second worker first.


## 2026-09-03 — V70F recursive relay invalidated; V70G generation-scoped scheduler micro-world PASS

V70F early-clock realistic-HFT on market `1823769` materially proved relay reachability but failed its preregistered authority scope and is **INVALIDATED**, not promoted. The first implementation created a new disjoint Expand reservation on every candidate-induced Repair submit whose frozen V44 score exceeded 0.5, and it also suppressed the inherited V44 `_score_state` path. This produced 10 reservations / 10 Active Expand fills, Expand fill `21.5504814947`, exact generation debt `21.5504814947`, post-birth Repair payment `20.6687631002`, zero truthMismatch / overOwned / responsibilityOverfill / repairDrift / preBirthPaymentLeak / duplicateGenerationDebt / Repair-preservation violations, and semantic rounds `2 -> 11`. Those attractive rounds are recursive action-authority feedback and therefore cannot be used as PASS evidence. Diagnostic PnL moved `+0.300746 -> -0.509015` but remains non-authoritative. Artifact: `ETH_REPAIR_V70F_FIRST_IMPLEMENTATION_INVALIDATION_20260903.json`.

The retained structural evidence is important: early Repair-submit relay can physically fill in realistic HFT, exact fill->generation-debt conservation works, and strictly post-birth opposite Repair payment can repeatedly close most generated debt. The missing component is not another threshold; it is generation-scoped responsibility scheduling.

V70G therefore defines one Expand responsibility per Repair generation/debt episode. Repair child reprice/replacement cannot reauthorize Expand. Existing V44/V64 Expand occupancy absorbs the same generation responsibility rather than allowing a duplicate economic objective. Terminal zero-fill may transfer the same responsibility once to bounded active execution. Confirmed Expand fill births exact debt; the next generation cannot authorize Expand until prior debt is discharged. <=180s still blocks new Expand but not Repair.

The preregistered V70G scheduler micro-world completed **10/10 PASS**: one authorization edge/generation, reprice non-reauthorization, passive/active occupancy dedup, one-shot zero-fill transfer, exact partial-fill debt, strict post-birth payment, debt-discharge unlock of next generation, <=180 exposure fence, and duplicate-event idempotence. Artifacts: `ETH_REPAIR_V70G_GENERATION_SCOPED_RELAY_SCHEDULER_MICROWORLD_PREREGISTERED_20260903.json` and `TARGET_ETH_V70G_GENERATION_SCOPED_RELAY_SCHEDULER_MICROWORLD_20260903.json`.

Current unique authorization is `ETH_REPAIR_V70G_GENERATION_SCOPED_RELAY_HFT_SMOKE_PREREGISTERED_20260903.json`: integrate only the ownership scheduler while preserving the complete V65/V44/V64 action behavior, then run one-market realistic-HFT on `1823769`. KEEP requires <=1 Expand responsibility per generation, zero duplicate objective/double-spend/overfill/overOwned/truthMismatch/role drift/pre-birth payment, exact fill/debt accounting, and semantic rounds >= matched V65 baseline. If ownership dedup yields behavior identical to V65, KEEP that no-op ownership correction rather than manufacturing action authority. No Stage-A/H100, no numeric tuning, no 8781.

Worker note: `btc5m-worker` SSH was reachable this round, but the expected V70 runtime directory was not available through the remote path check after the prior SCP reset. The V70F one-market job was run on host rather than left idle; the shell transport 504 occurred while the process continued, was explicitly checked, and a duplicate retry process was terminated before reading the completed artifact.


## 2026-09-03 — V70G generation-scoped ownership passes 3-market functional smoke; Stage-A16 authorized

V70G was integrated as an ownership scheduler only. It preserves the inherited V44/V65/V64 action path unless generation ownership requires a duplicate-objective block. A Repair generation can own at most one Expand responsibility; existing inherited Expand carriers absorb that responsibility, Repair reprices do not reauthorize it, confirmed Expand fill creates equal generation debt, strictly post-birth opposite Repair increments pay debt, and the next generation remains closed until prior debt is discharged. No threshold/qty/price/delay/winner/PnL tuning was used.

The preregistered one-market realistic-HFT smoke on `1823769` returned **KEEP_NOOP_OWNERSHIP_CORRECTION**. It bound one inherited V44/V65 Expand carrier (`DOWN_7`) to generation 1, blocked four duplicate authorization opportunities, kept semantic rounds `2 -> 2`, and had zero truthMismatch / overOwned / responsibilityOverfill / repairDrift / preBirthPaymentLeak / duplicateGenerationDebt / Repair-preservation violations. This was intentionally retained as a no-op ownership correction rather than manufacturing new action authority.

The same frozen scheduler was then preregistered and extended to the complete three-market V65 smoke cohort `1823769,1827223,1827903`. Aggregate result:

- markets: `3/3`
- max Expand responsibilities per generation: `1`
- duplicate-objective opportunities blocked: `9`
- inherited Expand carrier binds: `5`
- explicit early parallel reservation: `1` (market `1827903`)
- physical Expand fill: `5.4411764706`
- generation debt: `5.4411764706`
- strictly post-birth Repair payment: `5.4411764706`
- matched V65 semantic rounds: `5 -> 5`
- truthMismatch / overOwned / responsibilityOverfill / unauthorized role drift / pre-birth payment / duplicate debt / Repair-preservation violations: all `0`

Per market, `1827223` produced `2.5` Expand fill = debt = payment with rounds `1 -> 1`; `1827903` produced `2.9411764706` fill = debt = payment with rounds `2 -> 2`; `1823769` remained behaviorally no-op at `2 -> 2`. Decision: **KEEP_V70G_GENERATION_SCOPED_OWNERSHIP_SCHEDULER_AUTHORIZE_FIXED_STAGEA16**. This is architecture/ownership/accounting evidence, not profitability graduation.

Artifacts:

- `tools/run_eth_repair_v70g_generation_scoped_relay_hft_smoke.py`
- `data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70G_GENERATION_SCOPED_RELAY_HFT_SMOKE_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V70G_THREE_MARKET_OWNERSHIP_REACHABILITY_PREREGISTERED_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70G_THREE_MARKET_OWNERSHIP_REACHABILITY_20260903.json`
- `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V70G_STAGEA16_GENERATION_SCOPED_OWNERSHIP_PREREGISTERED_20260903.json`

Current unique authorization: run the frozen V70G scheduler on the fixed existing ETH Stage-A 16 cohort, preferably on the second LAN worker. KEEP requires 16/16 completion, max one responsibility/generation, zero safety/accounting violations, exact physical Expand fill = generation debt, and aggregate semantic rounds >= matched V65 baseline; report every per-market round regression separately. No H100, no numeric tuning, no 8781. Worker status this round: SSH reachable, but `C:\BTC5M-worker\...\v70_worker_runtime\bundle.zip` is still missing, so the small smoke was run on host rather than left idle.
`n`n## 2026-09-03 12:44 V70G Stage-A16 execution resumed on second LAN worker`n`nThe fixed V70G Stage-A16 preregistered cohort was resumed without numeric tuning. The second LAN worker btc5m-worker is reachable. The V70G runner was copied to C:\BTC5M-worker\.lan_worker_v1\staging, and all required frozen inputs used by the earlier V69 Stage-A waves were verified present.`n`nA four-wave background launcher was attempted but did not leave persistent Python children or artifacts, so those starts are not counted as execution evidence. The verified direct invocation path was then used for first-wave markets 1823603,1823769,1823894,1823897. The worker process is actively running; after 50+ seconds it remained alive with roughly 2.7 GiB resident memory and no completed result.json yet. Running is not treated as PASS.`n`nProgress artifact: data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V70G_STAGEA16_PROGRESS_20260903_1244.json.`n`nDecision: CONTINUE_FIXED_V70G_STAGEA16_NO_TUNING. Unique next step is to collect the already-running first-wave result.json, grade ownership/safety/accounting and per-market semantic-round regression exactly as preregistered, then launch the remaining fixed waves through the verified direct worker path. No H100, no threshold/qty/price/delay tuning, no 8781.`n


## 2026-09-03 V70G Stage-A16 worker continuation — duplicate-launch infra diagnosis

Research coordinate remains **V70G frozen Stage-A16 realistic-HFT**. No strategy thresholds/qty/price/delay were changed and 8781 was not touched.

This run audited the prior `RUNNING_FIRST_WAVE_ON_SECOND_LAN_WORKER` claim. At the first audit, `v70g-a1..a4` had no `result.json` and no persistent V70G Python child, so the previous background launcher is not valid completion evidence.

A direct foreground worker invocation for fixed Stage-A16 market `1823603` returned a connector 502, but a remote process audit proved that the HFT had actually started. Retrying after the 502 created a duplicate execution of the same market/output. Process-tree evidence showed two launcher/heavy-child pairs: `(43168 -> 66792)` created 13:17:19 and `(48496 -> 68520)` created 13:19:21. The later duplicate pair was terminated; the older pair was preserved. Latest preserved heavy child status: PID `66792`, CPU time `442.12s`, working set `7294.4 MiB`, still actively computing, no `result.json` yet. Running is **not** graded as PASS/REJECT.

Infra fix added: `launch_v70g_single_idempotent_20260903.ps1`. It checks both existing result and any already-running V70G process with the same `--market-ids` before launching. Validation against the preserved `1823603` run returned `ALREADY_RUNNING` for PIDs 43168/66792 and did not spawn another copy. Supporting artifact: `ETH_REPAIR_V70G_STAGEA16_INFRA_DUPLICATE_LAUNCH_DIAG_20260903_1323.json`.

**Decision:** KEEP frozen V70G; treat 502 as ambiguous launch state, never as proof of non-launch; no retry without remote process/result check. 

**Next unique authorized step:** collect `v70g-diag-1823603.json` when the preserved worker pair reaches terminal, grade all preregistered safety/accounting/semantic-round gates, then continue the remaining fixed Stage-A16 cohort with the idempotent launch guard. No H100, no numeric tuning, no 8781.


## 2026-09-03 14:39 V70G Stage-A16 midpoint — first real chronology regression found

Research coordinate remains **V70G frozen Stage-A16 realistic-HFT**. Eight fixed Stage-A markets have terminal results: 1823603, 1823769, 1823894, 1823897, 1824037, 1824747, 1824852, 1825353. Across all completed markets, truthMismatch / overOwned / responsibilityOverfill / repairDrift / pre-birth payment leak / duplicate generation debt / Repair-preservation violations remain zero, and max Expand responsibilities per generation is <=1.

The first scientifically material failure is market `1824747`. Its initial Stage-A run regressed matched V65 semantic rounds `3 -> 0` despite clean safety/accounting. A focused same-code audit rerun remained a clear reject at `3 -> 1`, with `4.4642857143` physical Expand fill = generation debt and `4.2677448338` post-birth Repair payment. The mechanism is chronology displacement, not accounting corruption: V70G binds generation 1 early to `ACTIVE_EXPAND DOWN_5` at Repair-submit clock `UP_4`; subsequent dedup then blocks/reassigns later inherited Expand opportunities that V65 would have used. Thus the scheduler can be ownership-safe while still pre-empting useful inherited action chronology.

Interpretation: **generation ownership must be subordinate to inherited V44/V65/V64 chronology**. The scheduler may deduplicate the same economic objective, but an earlier eligible Repair-submit clock must not manufacture a competing earlier Expand when a later inherited carrier would otherwise materialize. This is a structural rule candidate, not a threshold/qty/delay tuning result.

Execution infra was also rechecked. The Start-Process idempotent launcher again returned STARTED but left no persistent Python child/result. The already-developed ScheduledTask path is the verified persistent execution route. The next four fixed markets `1825959,1826030,1826386,1827223` were started through that route and were all `Running` at launch.

Artifact: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V70G_STAGEA16_MIDPOINT_REGRESSION_20260903_1439.json`.

Decision: **CONTINUE_FIXED_STAGEA16_AND_PREREGISTER_CHRONOLOGY_PRESERVATION_AUDIT_NO_TUNING**. Finish the fixed cohort first. After grading, the next structural branch should preserve inherited V44/V65/V64 action chronology and restrict V70 ownership logic to dedup/accounting only; do not perform numeric tuning, do not H100, and do not touch 8781.


## 2026-09-03 15:xx V73B–V73F recoverability and source→active-child family handoff

Latest research supersedes the earlier V70G Stage-A midpoint as the active coordinate. V72 backward marginal-pair blocking was already rejected by V73 because future settlement can differ materially from past Repair+current active-ask pairing. The next retained axis is strict-past whole-portfolio recoverability plus exact execution-family accounting.

### V73B / V73C ex-ante recoverability shadow

V73B fixed 3-market realistic-HFT shadow `1823769,1827223,1827903` completed on the second LAN worker. Two active-fallback contexts were exercised; both were strict-past recoverable and both corresponded to V73 post-episode recovered branches. Decision: **KEEP_EXANTE_RECOVERABILITY_AS_NEXT_FUNCTIONAL_AXIS**. Safety/accounting and action behavior were unchanged.

V73C then froze the same formula and ran the fixed Stage-A16 cohort on the second worker. Result: 16/16 complete, 4 active-fallback contexts across markets `1823769,1824037,1825353,1827903`; recoverable 4/4, nonrecoverable 0/4; zero safety/accounting change; shadow-only. Decision: **KEEP_STAGEA_RECOVERABILITY_KERNEL**. This proves portability but not discrimination.

V73D negative-control micro-world therefore tested whether the kernel is tautologically permissive. All 8/8 cases passed, including impossible Repair room, no admissible price, venue-min > room, exact-boundary acceptance and economic-ceiling binding. Decision: **KEEP_V73_RECOVERABILITY_KERNEL_NOT_TAUTOLOGICAL**.

### V73E found a real Stage-A mismatch and isolated the execution-family bug

A preregistered post-episode audit used only existing V73C realistic-HFT fills for the two new Stage-A contexts `1824037,1825353`. `1824037` validated the ex-ante classification: active DOWN 2.5 @0.40 was followed by opposite Repair, matched Repair price 0.5577966, forward pair 0.9577966, and active-through-settlement floor delta +0.7357316.

`1825353` was different: active fallback `UP_8` filled 2.1739130435 @0.46 but no subsequent opposite Repair fill occurred; floor moved from -0.4848485 before active to -1.4848485. The failure was not merely a recoverability-prediction miss. V70G had bound generation responsibility to passive source `UP_7`, while V64 executed the same objective through active child `UP_8`; because V70D debt accounting watched only the bound source key, the child fill created no `v70d` generation event/debt at all.

### V73F source→active-child responsibility family

The bugfix contract treats passive source and its bounded active fallback child as execution members of one Expand economic responsibility. No new objective, threshold, qty, price, delay or chronology authority is introduced.

V73F family micro-world completed **8/8 PASS** on the second worker: source-zero/child-fill debt birth, duplicate-child idempotence, partial source+child shared cap, pre-birth Repair exclusion, post-birth payment, side-mismatch rejection, family overfill detection and payment cap all passed.

The authorized one-market realistic-HFT smoke on `1825353` then completed on the second worker. The same `UP_7 -> UP_8` family was recognized; child physical fill `2.1739130435` = tracked Expand fill `2.1739130435` = generation debt `2.1739130435`. Max responsibilities/generation remained 1, and truthMismatch / overOwned / responsibilityOverfill / repairDrift / preBirthPaymentLeak / duplicateGenerationDebt were all zero. Decision: **KEEP_V73F_SOURCE_ACTIVE_CHILD_FAMILY_ACCOUNTING_AUDIT_REPAIR_REACHABILITY**.

Crucially, generation payment remained `0.0`, leaving `2.1739130435` debt outstanding and semantic rounds `0 -> 0`. This cleanly separates the next missing architecture layer: after accounting is corrected, the post-birth opposite Repair execution pipeline is still unreachable in this market.

Artifacts:
- `TARGET_ETH_V73B_ACTIVE_FALLBACK_EXANTE_RECOVERABILITY_SHADOW_20260903.json`
- `TARGET_ETH_V73C_STAGEA16_EXANTE_RECOVERABILITY_SHADOW_20260903.json`
- `TARGET_ETH_V73D_RECOVERABILITY_KERNEL_NEGATIVE_CONTROL_MICROWORLD_20260903.json`
- `TARGET_ETH_V73E_STAGEA_NEW_CONTEXT_FORWARD_SETTLEMENT_20260903.json`
- `TARGET_ETH_V73F_SOURCE_ACTIVE_CHILD_RESPONSIBILITY_FAMILY_MICROWORLD_20260903.json`
- `TARGET_ETH_V73F_SOURCE_ACTIVE_CHILD_FAMILY_HFT_SMOKE_1825353_20260903.json`
- corresponding preregisters/runners under `data/research/r4_v0/p0_provenance_v1/` and `tools/`.

**Current unique authorization:** `ETH_REPAIR_V73G_POST_BIRTH_REPAIR_EXECUTION_REACHABILITY_PREREGISTERED_20260903.json`. Re-run only fixed market `1825353` with V73F behavior unchanged and instrument strictly post-generation-birth Repair reachability: debt remaining, Repair bid/ask, economic ceiling, legal venue-min qty, Repair room, existing carriers/occupancy, queue/frontier connectivity, parent lifecycle and exact block reason. Shadow only. Classify the dominant failure as authority, price, qty, carrier/handoff, queue-unreachable, or submitted-no-fill. Do not tune numerics, do not H100, and do not touch 8781.


## 2026-09-03 16:xx V73G/V73H automation update
- Continued from V73F on fixed realistic-HFT market 1825353; no 8781, no dream fill, no winner/PnL tuning.
- V73G shadow audit completed on second LAN worker. Generation debt = 2.1739130435, paid = 0, remaining = 2.1739130435, 1200 post-birth states, safety/accounting all zero. Classification: NO_REPAIR_SUBMIT_DESPITE_REACHABLE_STATE. Reason counts: 802 reachable-but-no-submit, 398 NO_LEGAL_QTY. Repair parent remained id=1 side=DOWN; no live Repair carrier after generation birth.
- V73H one-shot generation-debt passive rearm preregistered and executed on second LAN worker. It reused repairParent=1 / existing repair objective id=2 and submitted DOWN_9 at 0.44 qty 2.2727272727. truthMismatch/overOwned/responsibilityOverfill/repairDrift/preBirthLeak/duplicateDebt all 0, max Expand responsibility/generation=1. However actual Repair fill=0, generationPaid=0, remaining debt unchanged. Decision: REJECT_V73H_SUBMITTED_NO_FILL_AUDIT_QUEUE_REPRICE.
- DOWN_9 generated a Repair churn event at t=1788166561724 (~5.6s after submit), confirming terminal zero-fill rather than accounting loss. Economic ceiling stayed fixed at ~0.4499701. After churn, first best Repair bid <= ceiling occurred at t=1788166657092 (bid=0.43, ask=0.47, legalQty=2.32558 <= repairRoom=2.69958); first ask <= ceiling occurred at t=1788166665310 (bid=0.41, ask=0.42).
- Unique authorized next step: V73I POST_CHURN_PASSIVE_REENTRY one-market smoke on 1825353. Preserve V73F ownership and V73H initial submit. After terminal zero-fill, allow exactly one same-parent/same-objective passive re-entry only when current best Repair bid <= economic ceiling and venue-min qty fits Repair room. No Active Repair, no numeric sweep, no Stage-A/H100 until this small smoke resolves. Preregister: data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V73I_POST_CHURN_PASSIVE_REENTRY_PREREGISTERED_20260903.json


## 2026-09-03 V80 MODULAR MANAGEMENT AUTHORITY FIREWALL — KEEP
- Effective research architecture changed from subclass-stacked Management authority to replaceable functional modules.
- Authoritative profile: `ECONOMIC_MANAGEMENT_V1`.
- Modules: completion=`economic_responsibility_completion_v1`; ownership=`recoverable_pre_safe_ownership_v1`; handoff=`whole_portfolio_recoverability_handoff_v1`; generation=`single_responsibility_per_generation_v70g`.
- Legacy V6..V76 files remain frozen and usable as A/B controls / execution helpers; do not delete them.
- New Management research MUST replace one module at a time while freezing the others; do not add another subclass override as final Management authority.
- Authority firewall result: `TARGET_ETH_V80_MODULAR_MANAGEMENT_KERNEL_20260903.json`, fixed 1911708/1911716/1912012, all migration+safety gates PASS.
- Critical migration check: 1912012 V76 falsely counted share-neutral floor<0 as `repairParentCompletions=1`; V80 records `repairParentCompletions=0`, `shareRepairSettlements=1`, `economicDeficitActiveAtEnd=1`.
- Engineering contract: `data/research/r4_v0/p0_provenance_v1/MODULAR_CONTROLLER_RESEARCH_CONTRACT_V1_20260903.md`.
- V80 is architecture migration evidence, NOT profitability graduation. Continue formal tests on realistic HFT/Predict Tape, second worker priority, no 8781.


## 2026-09-03 V81 V36 REPAIR EXECUTION UNDER V80 — PREREGISTERED
- V80 authority firewall is KEEP on migration markets 1911708/1911716/1912012; all migration+safety gates PASS. Legacy share-gap completion is no longer management authority.
- Legacy contamination salvage audit ranks V36 shared passive/active Repair execution first for isolated retest because prior triggered shards improved floor with zero harmful Active Repair fills, but old parent completion semantics could suppress/prematurely terminate its opportunity set.
- New preregister: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V81_V36_REPAIR_EXECUTION_UNDER_V80_PREREGISTERED_20260903.json`.
- Fixed one-market smoke: 1912012. Positive-control reason: under V80, shares settle 5/5 while floor remains -0.35 and ECONOMIC_DEFICIT stays open, so V36 can be tested without reviving legacy structural completion.
- Frozen authority: V80 completion + recoverable pre-safe ownership + whole-portfolio recoverability handoff + V70G one-responsibility-per-generation. Swap only V36 Repair execution.
- Safety gates remain truthMismatch/overOwned/responsibilityOverfill/repairDrift/pre-birth leak/duplicate generation debt = 0; <=180s no new exposure; no threshold/qty/price/delay tuning; no 8781.
- Unique authorized next step: implement/run the V81 one-market realistic-HFT smoke on 1912012. If V36 context is absent, instrument the exact V80 authority/reachability blocker rather than tuning. If clean functional Repair fill occurs, KEEP V36 as execution module and preregister V44 next; if authority/accounting violation occurs, REJECT integration and isolate in micro-world.


## 2026-09-03 V82 quick legacy-module salvage screen
- V80 modular authority remains canonical; no return to subclass-stacked management authority.
- V36 Shared Active Repair: causally confirmed useful under V80 (V81B); salvage as RepairExecution module.
- V38 incremental Repair: accounting-safe but economically mixed; keep as sizing feature/research input, do not restore fixed venue-min sizing authority as-is.
- V44 parallel Expand: opportunity signal useful in some markets but immediate submit admission mixed; keep signal, replace admission with modular economic gate.
- V49/V50/V51/V52 generation Active-Repair stack: V82 representative screen non-worse in all 5 markets, aggregate floor -1.66559 -> -1.23702; V50 blocked 86 stranding paths, V51 retained one live passive queue, V52 had 4 epoch resets / zero stale-evidence leak. Salvage stack, but V52 repeat-active action remained unexercised in this quick screen.
- V64/V65/V70G Active-Expand/ownership stack under V76 recoverability handoff: non-worse in all 5 markets, aggregate floor -1.61491 -> -1.23702; active fill 7.21778, one recoverability block, useful rounds in 1823769 and 1827903. Salvage under gated handoff.
- V48 generation ledger remains accounting substrate: zero overpay and zero pre-birth leak in screen.
- V32/V34/V35 remain information/readiness features only; do not promote standalone action authority.
- V42 continuous recursive Expand authority remains rejected.
- Canonical artifact: data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V82_QUICK_LEGACY_MODULE_SALVAGE_SCREEN_20260903.json


## 2026-09-03 18:xx V83 fresh unseen4 launched on second LAN worker — terminal grading pending
- Latest authorization advanced beyond V82 salvage into the frozen V83 Clean Modular Candidate. Fresh preregister: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V83_FRESH_UNSEEN4_PREREGISTERED_20260903.json`.
- Fixed fresh cohort: `1912941,1912944,1912946,1912961`; selected after V83 prereg/code freeze and outside V73/V76/V80/V81/V82/V83 development cohorts. Winner remains post-hoc only.
- V83 keeps V80 economic management authority, V36 RepairExecution, V48 ledger, V49/V50/V51 generation Repair, V64/V65/V70G Expand/ownership, V76 whole-portfolio recoverability; V44 remains signal-only and new V83 Expand admission requires whole-portfolio recoverability. V38 fixed sizing, V44 immediate-submit authority, V70F early reservation and V52 repeat-active authority remain disabled.
- The fresh4 realistic-HFT run was actually launched on `btc5m-worker` using ScheduledTask `BTC5M_V83_FRESH4_20260903`. WMIC verified a live process tree `81024 -> 63044 -> 92088`; the heavy Python child had ~4.40 GB working set and accumulating CPU time. A localized `tasklist` filter had falsely reported no Python process, so no retry was issued.
- At this checkpoint the required terminal `v83_fresh_unseen4_result_20260903.json` is not yet present. Running is **NOT** graded as KEEP/REJECT and no metrics are invented.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V83_FRESH_UNSEEN4_PROGRESS_20260903_1812.json`.
- Boundary unchanged: no threshold/qty/price/delay tuning, realistic HFT/Predict Tape only, no dream fill, no 8781, no <=180s new exposure.
- **Unique authorized next step:** collect the already-running terminal fresh4 artifact without duplicate launch; grade zero truthMismatch/overOwned/responsibilityOverfill/Repair drift/pre-birth leak/duplicate generation debt, max one responsibility/generation, V70F authority disabled and per-market module reachability. If safety is clean, KEEP V83 architecture even if tiny fresh4 economics are mixed; use losses only to localize the next module seam, not to tune this cohort.


## 2026-09-03 19:15 V83 infra invalidation -> V84 composite Repair axis
- The prior V83 fresh unseen4 ScheduledTask is not terminal strategy evidence. Expected `v83_fresh_unseen4_result_20260903.json` is absent; the prior ~4.4GB heavy Python process is gone; ScheduledTask `BTC5M_V83_FRESH4_20260903` reports Last Result `1`; stdout/stderr files are empty. Decision: **INVALID_V83_FRESH4_INFRA_RUN_DO_NOT_GRADE_STRATEGY**. Do not count the earlier RUNNING checkpoint as completion or KEEP/REJECT.
- To avoid rerunning the whole fresh4 blindly, continued along the already-preregistered V84 seam on fixed market `1912961`: legacy Repair may be blocked because a venue-minimum weak-side order crosses the remaining share gap, even though the physical carrier can improve worst-case floor if decomposed into Repair residual + overflow responsibility.
- V84B responsibility/accounting micro-world was executed and saved as `TARGET_ETH_V84B_COMPOSITE_REPAIR_LEDGER_MICROWORLD_20260903.json`: **8/8 PASS** (crossing allocation, non-cross, partial FIFO, physical conservation, strict post-birth payment, duplicate-fill idempotence, <=180 overflow block, floor-improvement requirement).
- V84 shadow-only realistic-HFT on `1912961` was launched on `btc5m-worker` as ScheduledTask `BTC5M_V84_SHADOW_1912961_20260903`. At latest check no result exists yet, but heavy Python PID `86580` is genuinely computing: working set ~2.00GB and CPU counters increased. Running is not graded.
- Safety boundary unchanged: shadow only, frozen V83 actions unchanged, realistic HFT/Predict Tape, no dream fill, no numeric tuning, no 8781, no <=180s new exposure.
- Progress artifact: `ETH_REPAIR_V84_COMPOSITE_AXIS_PROGRESS_20260903_1915.json`.
- **Unique authorized next step:** collect `v84_shadow_1912961_result_20260903.json` without duplicate launch. KEEP V84 axis only if legacy Repair blocking, venue-min crossing, floor-improving composite context, exact V83 behavior parity and zero safety/accounting change all pass. Only then run the already-preregistered V84B one-market behavior smoke; otherwise reject/refine the composite hypothesis without threshold/qty/price/delay tuning.


## 2026-09-03 20:17 V86/V87 latest artifacts -> V86C fresh4 replication launched
- Latest artifacts supersede the earlier V84 shadow checkpoint. Target-side post-market audits now include `TARGET_ETH_V86_COMPOSITE_RELAY_EXECUTION_ROUTE_20260903.json` and `TARGET_ETH_V87_COMPOSITE_RELAY_LEGAL_SLICE_20260903.json`.
- V86 execution-route evidence: on Stage-A16 pre-180 composite parents, 45/50 had a later opposite Repair; 43/50 within 30s, median lag 8s. Next route was 24 TAKER / 21 MAKER; the next Repair covered the overflow in 80% of matched cases. On fresh market 1912961, 21/22 had later opposite Repair, 20/22 within 30s, median lag 4s.
- V87 legal-slice evidence: Stage-A16 pre-180 overflow covered at least one venue-legal opposite Repair slice in 66.67% of matched cases; fresh 1912961 was also 66.67%. This supports an execution-handoff seam rather than numeric threshold tuning; Target data remains post-market descriptive only and has no runtime authority.
- Current preregistered test is `ETH_REPAIR_V86C_FRESH4_OVERFLOW_EXECUTION_HANDOFF_REPLICATION_PREREGISTERED_20260903.json`, fixed fresh markets 1912941/1912944/1912946/1912961. It freezes V85E behavior and tests only the V86B overflow-born one-disconnect Active Repair handoff; ordinary Repair parents retain the V36 two-disconnect rule.
- Runner/dependencies were staged to `btc5m-worker` and job `eth-v86c-fresh4-20260903-v1` was accepted by the LAN worker. Agent state is `running`, PID 79040 (launcher 85644); at submit CPU was 15.8%, free RAM 20.82 GiB, disk free 134.5 GiB. No result exists yet, so RUNNING is not graded as PASS/REJECT.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V86C_FRESH4_PROGRESS_20260903_2017.json`.
- **Unique authorized next step:** collect this exact job when terminal without duplicate launch; grade 4/4 completion, truthMismatch/unexplained overOwned/responsibilityOverfill/repairDrift/pre-birth leak/duplicate debt/shared realized overfill = 0, nonnegative Active-fill floor deltas, aggregate and every per-market floor non-worse than V85E, and trigger -> actual Active fill. KEEP only if all preregistered gates pass. No threshold/qty/price/delay tuning, no dream fill, no <=180s new exposure, no 8781.


## 2026-09-03 21:xx V88E -> V88F responsibility-transition micro-world PASS
- Latest artifacts supersede the V86C running checkpoint. `TARGET_ETH_V88E_SMALL_OVERFLOW_RESPONSIBILITY_TRANSITION_20260903.json` completed on the fixed Stage-A16 small-overflow cohort: n=15, next parent composite=10, pure Repair=5. Strong descriptive axes were bestDelta, bestBefore and oldExpandQty. These are responsibility-state evidence only; their fitted thresholds are explicitly non-authoritative and are not runtime tuning.
- V88E supports the structural interpretation that overflow smaller than a standalone venue-min Repair slice is not necessarily complete/dropped; Target frequently carries responsibility forward into another physical composite parent.
- New preregister: `ETH_REPAIR_V88F_RESPONSIBILITY_TRANSITION_MICROWORLD_PREREGISTERED_20260903.json`. It formalizes persistent sub-venue-min generation debt, oldest-first payment by a later opposite carrier, shared passive/active budget, no silent Expand promotion, exact generation transition and <=180s Repair-only behavior.
- First runner attempt failed only at JSON serialization of a Python set; the test semantics were unchanged, the runner was fixed, and the same preregister rerun completed **10/10 PASS**. Safety/accounting: truthMismatch=0, overOwned=0, responsibilityOverfill=0, repairDrift=0, doubleSpend=0, preBirthLeak=0; generationDebtConservation=true. Result: `TARGET_ETH_V88F_RESPONSIBILITY_TRANSITION_MICROWORLD_20260903.json`.
- Decision: **KEEP_V88F_RESPONSIBILITY_TRANSITION_SEMANTICS_AUTHORIZE_SHADOW_ONLY**. This is micro-world/accounting evidence, not HFT performance evidence and not action authority.
- Next preregister: `ETH_REPAIR_V88G_SUBVENUE_OVERFLOW_PERSISTENCE_SHADOW_1912961_PREREGISTERED_20260903.json`.
- **Unique authorized next step:** run one-market realistic-HFT/Predict-Tape shadow on fixed `1912961` with V85E/V86 action behavior frozen. Instrument whether a real sub-venue-min overflow debt stays OPEN and is later paid oldest-first by an opposite physical carrier with exact conservation and all safety/accounting zero. If no real handoff clock exists, audit responsibility birth/closure semantics; do not tune threshold/qty/price/delay. No 8781, no dream fill, no <=180s new exposure.


## 2026-09-03 22:21 V89A -> V89B worker continuation
- Latest artifacts supersede the V88G-only handoff coordinate. V89A `TARGET_ETH_V89A_MANAGER_DEBT_EXECUTION_TRANCHE_MICROWORLD_20260903.json` completed **13/13 PASS**. It establishes the architecture rule that manager debt caps allocation, not physical order size: confirmed physical carrier fill is Repair-first up to current manager debt; only excess may become one new overflow responsibility under the same generation/accounting semantics. Duplicate fills are idempotent, strict post-birth payment holds, pre-birth Repair cannot pay future overflow, <=180s blocks new overflow only, and physical allocation conservation passes.
- The next preregistered realistic-HFT step is V89B on fixed market `1912961`: `ETH_REPAIR_V89B_OVERFLOW_PARENT_PRECAP_CARRIER_HFT_PREREGISTERED_20260903.json`. It freezes the initial V84 composite and tests only whether the first passive carrier of an overflow-born Repair parent may use OUR existing pre-share-gap physical execution tranche; Active Repair is disabled for isolation.
- Second worker `btc5m-worker` was checked first as required. V84/V83 dependencies and the frozen bundle were already staged; only the V89B runner was missing. The runner and a ScheduledTask launcher were staged without changing strategy parameters.
- ScheduledTask `BTC5M_V89B_1912961_20260903` is genuinely running. Result file is not present yet, so **RUNNING is not graded**. Heavy Python PID `81824` increased from ~0.84 GiB to ~1.67 GiB working set while kernel/user CPU counters continued increasing, confirming real computation rather than an empty launcher.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V89B_1912961_PROGRESS_20260903_2221.json`.
- Boundary unchanged: realistic HFT/Predict Tape only, no dream fill, no Target runtime authority, no numeric tuning, no <=180s new overflow, no 8781.
- **Unique authorized next step:** collect the existing `C:\BTC5M-worker\.lan_worker_v1\v89b_1912961_result_20260903.json` without duplicate launch and grade the preregistered Repair allocation <= manager debt, physical conservation, one recursive carrier/parent, truth/overOwned/responsibilityOverfill/repair drift/pre-birth/duplicate-debt safety, and late-window gates. If recursive submit occurs but no fill, classify execution-inconclusive and move to the already-preregistered V89C one Active composite handoff; if V89B fills cleanly, KEEP the recursive pre-cap passive carrier before any V89C action test.


## 2026-09-03 22:4x V89E -> Modular V2 RepairExecutionRouter migration
- V89E fresh4 replication completed on fixed markets 1912941/1912944/1912946/1912961. Decision: `KEEP_V89D_RELAY_FOR_BROADER_FRESH_HFT`. All four markets were floor-non-worse. Aggregate floor delta +0.0255703803; aggregate PnL delta +1.2316006617; active composite submits=2; overflow paid=7.7771531388. All safety/accounting gates were zero: truthMismatch, unexplained overOwned, responsibilityOverfill, repairDrift, preBirthLeak, duplicateDebt, sharedOverfill. New relay behavior triggered only on 1912961; other three markets were unchanged.
- This replication authorizes modular migration of the V89D/V89E primitive; it does **not** authorize a hard-coded global two-handoff rule.
- New authoritative engineering contract: `data/research/r4_v0/p0_provenance_v1/MODULAR_CONTROLLER_RESEARCH_CONTRACT_V2_20260903.md`. V2 supersedes V1 for new research authority. New behavior must replace one functional module rather than stack another Management subclass exception.
- New Repair execution module: `tools/repair_execution_router_v2.py`, policy `recursive_composite_single_disconnect_execution_v1`. Ordinary Repair parents retain frozen V36/V83 execution. Overflow-born Repair parents may hand off to one bounded Active child after clear post-arm disconnect/churn with no payment progress; physical qty is the legal execution slice, Manager debt caps Repair allocation only, and excess confirmed fill births a new responsibility. There is no global fixed handoff count; each genuinely new responsibility is evaluated independently.
- Pure policy micro-world `eth-modular-v2-policy-microworld-20260903-v1` completed 13/13 PASS. It covers ordinary-parent inheritance, armed/ownership/progress/late/floor/debt/churn/frontier/legal-slice blocks, valid single-disconnect handoff, and repeated-new-parent evaluation by the same policy.
- First Modular V2 HFT job `eth-modular-v2-recursive-exec-1912961-20260903-v1` is **infra-invalid, not a strategy result**. It failed because legacy V80 substrate loaded `eth_repair_modular_v1.zip` into Python module cache before the V2 package import. No stdout/result was produced.
- Integration fix: RepairExecutionRouter V2 now uses an independent runtime namespace (`repair_execution_router_v2.py`) and does not overwrite/reload the legacy V80 policy package. Both files compile cleanly. The same frozen one-market HFT has been resubmitted as `eth-modular-v2-recursive-exec-1912961-20260903-v2`; no strategy parameter/qty/price/threshold/delay changed.
- Automation prompt updates were attempted twice but the platform incorrectly elicited cloud/connector access. The user confirmed no cloud connection is needed. Those automation updates failed and are not relied upon. Research authority is now carried by the project-local Modular Contract V2 and this handoff instead. Do not request cloud/Drive access for BTC5M research.
- **Unique authorized next step:** collect only `eth-modular-v2-recursive-exec-1912961-20260903-v2` when terminal. KEEP the RepairExecutionRouter module only if the module is exercised, one Active child per responsibility holds, Repair allocation <= Manager debt, physical allocation conservation holds, and all truth/ownership/overfill/drift/pre-birth/duplicate/shared-overfill safety remains zero. If PASS, freeze this module and run a fresh fixed-cohort replication without reintroducing a global N-handoff cap.


## 2026-09-03 23:17+08 ??Modular V2 ??AllocationLedger V2
- Collected `eth-modular-v2-recursive-exec-1912961-20260903-v2`: terminal `REJECT_OR_DIAGNOSE_MODULAR_V2_REPAIR_EXECUTION`. Router was exercised (15 fills, 6 Active-composite submits, 11.7895290913 fill qty), but safety found `repairDrift=1`; truthMismatch/overOwned/responsibilityOverfill/pre-birth/duplicate debt/shared overfill were zero.
- Reject branch isolated allocation semantics: manager Repair debt must be shared at parent/responsibility level across sibling physical carriers; confirmed fill allocates Repair-first and only confirmed excess may birth the next opposite responsibility.
- First AllocationLedger V2 run `eth-modular-allocation-v2-1912961-20260903-v1` is INVALID as behavior evidence because the `_scan_v84` override omitted the frozen V89 overflow-birth relay seam, making recursion unreachable. Integration side effect was restored without changing policy thresholds/qty/price/delay.
- Collected corrected `eth-modular-allocation-v2-1912961-20260903-v2`: `KEEP_ALLOCATION_LEDGER_V2_FOR_FRESH_REPLICATION`. 11 fills, 4 Active-composite submits, 9.1038766039 Active fill qty, 11 Repair-parent births, 10 parent debts fully retired; overflow paid 19.0452726322, remaining 0.7516948548. All fixed accounting gates PASS: physical conservation, Repair<=initial manager debt, truthMismatch=0, unexplainedOverOwned=0, unexplainedRepairDrift=0, responsibilityOverfill=0, preBirthLeak=0, duplicateDebt=0, sharedOverfill=0. Terminal floor -1.0761685286 is diagnostic only.
- New artifacts: `ETH_REPAIR_MODULAR_V2_TO_ALLOCATION_V2_RESULT_20260903_2317.json`, `ETH_REPAIR_MODULAR_ALLOCATION_V2_FRESH4_PREREGISTERED_20260903.json`.
- **UNIQUE AUTHORIZED NEXT STEP:** run the preregistered AllocationLedger V2 fresh4 replication on fixed `1912941/1912944/1912946/1912961`, freezing RepairExecutionRouter/V80 ownership+completion/V83 admission/V89 passive carrier. Grade safety/accounting + recursive reachability only; no threshold/qty/price/delay fitting, no Target runtime input, no 8781, <=180s exposure fence unchanged.



## 2026-09-04 00:15 V90 fresh4 reject branch -> reachability/admission localization launched
- Latest artifact `TARGET_ETH_V90_MODULAR_FRESH4_SYNTHESIS_20260903.json` supersedes the stale AllocationLedger-V2 fresh4 handoff coordinate. V90 fresh4 completed 4/4 with all safety zero, but OUR traded only 2/4 markets: 1915613=0 fills/PnL 0, 1915659=1 fill/PnL +2.5, 1915662=0 fills/PnL 0, 1915670=2 fills/PnL -0.3. Aggregate OUR PnL +2.2 and trade coverage 50%; Target same-market post-market benchmark was 3/4 wins, +10.90517. Positive aggregate PnL is not promotion evidence because activity coverage is too low. Decision remains **DO_NOT_SCALE_TO_FRESH16_YET_LOCALIZE_ACTIVITY_COVERAGE_AND_REPAIR_CONTINUATION**.
- The pre-existing V91 fresh16 preregister/bundle is therefore not launched yet; it remains frozen future scope, not current authorization.
- A behavior-inert V90 reachability/admission shadow was staged and launched on second LAN worker for fixed zero-fill market `1915613`. Task `BTC5M_V90_SHADOW_1915613_20260904`; expected result `C:/BTC5M-worker/.lan_worker_v1/v90_shadow_1915613_result_20260904.json`. Heavy child PID 87628 is genuinely computing: working set increased ~0.81 GiB -> ~1.05 GiB and CPU counters increased. RUNNING is not graded.
- The shadow changes no action behavior and classifies the structural seam as ENTRY_SUBMITTED_NO_FILL, REPAIR_PARENT_BORN_NO_CARRIER, REPAIR_CARRIER_EXISTS_NO_FILL, REPAIR_CARRIER_MATERIALIZED, or OTHER. No threshold/qty/price/delay tuning, no Target runtime input, no dream fill, no 8781, <=180s exposure fence unchanged.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V90_REACHABILITY_SHADOW_1915613_PROGRESS_20260904_0015.json`.
- **Unique authorized next step:** collect this exact existing 1915613 shadow without duplicate launch. If ENTRY_SUBMITTED_NO_FILL, isolate EntryExecutionReachability/queue-frontier. If REPAIR_PARENT_BORN_NO_CARRIER, isolate RepairCarrierAdmission/responsibility-to-carrier handoff. Do not launch V91 fresh16 until the activity-coverage seam is localized.


## 2026-09-04 Post-settlement transition training + responsibility frontier

- V91 fresh16 modular AllocationLedger/Router result remains far from live-prep: aggregate PnL about -3.69 USDT, ~12/16 active, ~3/12 positive among traded, versus Target 12/16 positive and +14.24 USDT. One safety failure at market 1915944: authorized EXPAND / truth REPAIR mismatch on the same crossing-fill clock.
- `ExecutionStateFrontier V1` micro-world 8/8 PASS but HFT 1915944 REJECT: simply refreshing AllocationLedger immediately before V83 `_score_state()` did not see a new reconciliation event and did not clear the mismatch. This shows the seam is responsibility-role transition, not just an extra ledger refresh.
- EconomicDeficitContinuation shadow on 1915740/1915743/1915761: 7 share-settlement checks, 0 new-thesis births; all were blocked by `EXISTING_THESIS`. Therefore the missing seam is Existing-Thesis / Responsibility Transition after settlement, not thesis birth.
- Large Target ETH post-crossing dataset: 23,928 crossing Repair post-fill states across 3,473 markets; 93.47% have another parent within 30s; chronology-later test 94.24%. Among active test states, next economic role Repair ~61.39%, next parent composite ~43.18%. Main learning task is therefore next-role / transition geometry, not Continue-vs-Stop.
- Transition Model V1: generic 24-event GRU REJECT. Test AUC current-state MLP vs GRU: next role ~0.611 vs 0.594; next composite ~0.639 vs 0.599.
- Transition Model V2 structured: fixed HGB current-state baseline test AUC ~0.632 role / 0.655 composite. Extra responsibility-ledger features moved role to ~0.640 (+0.008 test; below prereg +0.015 gate) and composite to ~0.651 (-0.004), so no structured-feature promotion. Keep current-state HGB only as shadow pending rolling chronology replication.
- Canonical Target regularities handoff updated: `data/research/r4_v0/p0_provenance_v1/TARGET_SYSTEM_REGULARITIES_RESEARCH_HANDOFF_V1_20260903.md`, new section 22 records composite Repair-first/overflow-second, manager debt != physical carrier size, ~94% post-crossing continuation, recursive composite relay, and current modeling implications.
- New deterministic module: `tools/eth_repair_modular/responsibility_transition.py`; micro-world 8/8 PASS. Rule: if an existing thesis side currently owns live Repair debt after a crossing fill, preserve the thesis belief but suspend conflicting EXPAND ownership; same-side physical carrier must first pay Repair debt, and only confirmed excess can become Expand overflow.
- Running HFT job: `eth-resp-transition-v1-1915944-20260904`; fixed one-market A/B, AllocationLedger/Router/V83 model/qty/price frozen. Success gate requires baseline truthMismatch>=1 -> candidate 0 plus all accounting/safety zero.
- Next queued model audit: fixed current-state HGB rolling chronology replication (`TARGET_ETH_POST_SETTLEMENT_HGB_ROLLING_V1_PREREGISTERED_20260904.json`), no feature/parameter changes.


## 2026-09-04 01:15 ResponsibilityTransition V1 positive-control PASS; rolling HGB split result
- Collected terminal `eth-resp-transition-v1-1915944-20260904/result.json`. Deterministic ResponsibilityTransition V1 converted the fixed V91 mismatch market 1915944 from baseline truthMismatch=1 to candidate truthMismatch=0. Transition conflict was exercised (5 checks / 1 Repair-first block). Candidate safety/accounting remained zero for unexplainedOverOwned, unexplainedRepairDrift, responsibilityOverfill, preBirthLeak, duplicateDebt and sharedOverfill; allocation conservation PASS. Decision: **KEEP_RESPONSIBILITY_TRANSITION_V1_FOR_FRESH_REPLICATION**. Candidate PnL/floor was worse (-3.18506 vs -1.30508), explicitly diagnostic only and not a tuning signal.
- The preregistered fixed current-state HGB rolling chronology audit also completed. Next-composite generalized across all four folds: AUCs 0.65069/0.64219/0.66218/0.65465, min=0.64219, median=0.65267, both prereg gates PASS. Next-role Repair did not: AUCs 0.59851/0.58386/0.62565/0.63625, min=0.58386, median=0.61208; both gates FAIL. Decision: **DO_NOT_PROMOTE_HGB_SHADOW_YET**. This separates a reasonably portable composite-transition information head from a still-unstable Repair-vs-Expand role head; neither has action authority.
- New preregister: `ETH_REPAIR_RESPONSIBILITY_TRANSITION_V1_FRESH4_PREREGISTERED_20260904.json`, fixed markets 1915613/1915659/1915662/1915670. Freeze AllocationLedger V2, RepairExecutionRouter, V83 admission/model and all numeric execution behavior; grade architecture/safety/reachability only.
- **Unique authorized next step:** run ResponsibilityTransition V1 fresh4 replication, second LAN worker first. KEEP only if 4/4 complete, at least one real transition conflict is exercised, truthMismatch=0 across cohort, all accounting/safety gates remain zero and allocation conservation holds. Do not use the worse 1915944 economics to tune threshold/qty/price/delay. No 8781, no dream fill, <=180s exposure fence unchanged.


## 2026-09-04 02:22 ResponsibilityTransition -> Expand Admission Pair Economics shadow
- Latest artifacts supersede the 01:15 fresh4-only coordinate. `TARGET_ETH_EXPAND_CONTINUATION_ADMISSION_QUALITY_V1_20260904.json` completed offline: continuation is learnable (test AUC 0.6662 enriched; rolling AUCs 0.6819/0.6721/0.7123/0.6707), but non-damaging Expand quality is not (test AUC 0.5391; rolling median 0.5486/min 0.5340). Decision: no runtime admission model promotion; keep stable next-composite HGB shadow unchanged and non-authoritative.
- New unique authorization is `ETH_EXPAND_ADMISSION_PAIR_ECONOMICS_SHADOW_V1_PREREGISTERED_20260904.json`: behavior-inert shadow at frozen V83 Expand-admission clocks after ResponsibilityTransition, measuring strict-past realized current Repair-parent weighted price + frozen candidate Expand price against structural pair sum <=1.0. First smoke market is 1916830; fixed smoke4 is 1916830/1916845/1916847/1916869.
- Before launching, process audit found an existing second-worker 1916830 shadow already started at 02:18:46+08 with output `C:/BTC5M-worker/.lan_worker_v1/expand_pair_shadow_1916830_20260904.json`. Because this launch was not yet recorded in handoff, a later duplicate was briefly created. Infra correction preserved only the oldest run and terminated the later duplicate trees / duplicate ScheduledTask. No duplicate result may be graded.
- Preserved heavy PID 552 is genuinely computing; latest working set 4,357,885,952 bytes, kernel time 372,500,000 and user time 1,311,093,750. Terminal result is not present yet, therefore RUNNING is NOT graded as PASS/REJECT.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_EXPAND_ADMISSION_PAIR_ECONOMICS_SHADOW_V1_PROGRESS_20260904_0222.json`.
- Boundary unchanged: realistic HFT/Predict Tape only, shadow/no behavior mutation, ResponsibilityTransition frozen, no threshold/qty/price/delay tuning, no Target runtime authority, no dream fill, no 8781.
- **Unique authorized next step:** collect only the preserved oldest 1916830 terminal result without relaunch. If instrumentation is valid and pair-scored admissions are reached, grade the single-market smoke; only then extend to 1916845/1916847/1916869 under the fixed preregister.


## 2026-09-04 04:24 Allocation-aware direct recoverability positive-control PASS -> negative-control launched
- Latest artifacts supersede the stale 02:22 handoff tail. The V3 responsibility-ledger / fresh admission funnel work localized the current functional seam to legacy V75 recoverability incorrectly treating venue-min physical carrier size as if it were Manager Repair debt.
- Positive-control realistic-HFT market `1916869` completed: `ETH_V83_ALLOCATION_AWARE_DIRECT_RECOVERABILITY_BEHAVIOR_SMOKE_1916869_20260904.json` decision **FUNCTIONAL_PASS_KEEP_FOR_SMALL_FRESH_REPLICATION**. Baseline V83 blocked one admission; candidate reclassified the same `FUTURE_REPAIR_QTY_EXCEEDS_ROOM` context because Repair-first/overflow-second physical allocation gave projected floor after physical carrier `+0.3259651425`. Baseline fills 2 -> candidate 3, V83 allows 0 -> 1. Safety/accounting remained zero and allocation conservation passed. Candidate PnL/floor fell to `-0.8390312`; this is diagnostic only and is not parameter-tuning evidence.
- Before fresh expansion, preregistered a falsification/negative-control on fixed `1916847`, where the same shadow anatomy has carrier `2.59711985`, Repair room `1.31004676`, overflow `1.28707310`, but floor after the physical carrier remains `-0.49557709`. The candidate kernel must therefore **not** flip this state to recoverable.
- New preregister: `data/research/r4_v0/p0_provenance_v1/ETH_V83_ALLOCATION_AWARE_DIRECT_RECOVERABILITY_NEGATIVE_CONTROL_1916847_PREREGISTERED_20260904.json`. New runner compiled cleanly: `tools/run_eth_v83_allocation_aware_direct_recoverability_negative_control_1916847.py`.
- Second LAN worker was used first. ScheduledTask `BTC5M_V83_ALLOCAWARE_NEGCTRL_1916847_20260904` is genuinely running; process tree `99832 -> 70876 -> 99336`. Heavy PID `99336` working set increased to `982081536` bytes and CPU counters increased (`KernelModeTime=45000000`, `UserModeTime=147812500`). Result is not present yet, therefore RUNNING is **not graded**.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_ALLOCATION_AWARE_DIRECT_RECOVERABILITY_NEGCTRL_1916847_PROGRESS_20260904_0424.json`.
- Boundary unchanged: ResponsibilityTransition/AllocationLedger V2/RepairExecutionRouter frozen; pExpand 0.50 and qty/price/delay/model weights frozen; realistic-HFT/Predict Tape only; no dream fill; no 8781; <=180s no new exposure.
- **Unique authorized next step:** collect only the existing `1916847` negative-control terminal result without duplicate launch. PASS requires no negative-floor reclassification, no seam-caused admission/fill increase, physical conservation PASS, and truthMismatch/overOwned/responsibilityOverfill/repairDrift/preBirthLeak/duplicateDebt/sharedOverfill all zero. Only after PASS may the direct-recoverability kernel move to a small fresh replication; any unexpected permission rejects/refines the kernel before scaling.


## 2026-09-04 05:12 Allocation-aware recoverability negative-control PASS -> Expand-fill Repair-responsibility birth launched
- Collected fixed realistic-HFT negative control `1916847`: decision **NEGATIVE_CONTROL_PASS_RECURSIVE_NEGATIVE_FLOOR_STILL_BLOCKED**. Baseline/candidate fills remained `2 -> 2`, V83 allows `0 -> 0`, reclassification=false. The venue-min crossing carrier remained rejected because allocation-aware physical-carrier floor was `-0.4955770884`. Physical allocation conservation PASS; truthMismatch/unexplainedOverOwned/unexplainedRepairDrift/responsibilityOverfill/preBirthLeak/duplicateDebt/sharedOverfill all zero.
- This falsifies the concern that the new allocation-aware recoverability seam simply permits every `FUTURE_REPAIR_QTY_EXCEEDS_ROOM` context. It discriminates the earlier positive-control `1916869` (+0.325965 projected carrier floor, admission opened) from this negative-floor context without numeric tuning.
- Latest existing preregister after that discrimination is `ETH_V83_EXPAND_FILL_REPAIR_RESPONSIBILITY_BIRTH_SMOKE_1916869_PREREGISTERED_20260904.json`: confirmed V83 Expand fill may schedule exactly one opposite Repair responsibility only on a strictly later receipt; no same-receipt birth, no direct Repair submit injection, frozen ResponsibilityTransition/AllocationLedgerV2/RepairExecutionRouter and frozen pExpand/qty/price/delay.
- Runner `tools/run_eth_v83_expand_fill_repair_responsibility_birth_smoke_1916869.py` py_compile PASS. It was staged to the second LAN worker and ScheduledTask `BTC5M_V83_BIRTH_1916869_20260904` was created and started. Terminal result is pending and RUNNING is not graded.
- Progress artifact: `ETH_V83_NEGCTRL_TO_REPAIR_BIRTH_PROGRESS_20260904_0512.json`.
- **Unique authorized next step:** collect only `C:\BTC5M-worker\.lan_worker_v1\v83_birth_1916869_result_20260904.json` without duplicate launch. KEEP the birth seam only if a strictly post-fill/later-receipt Repair responsibility is born (or correctly classified already-owned/self-settled), the frozen RepairExecutionRouter becomes reachable with later Repair progress/semantic round, physical conservation holds, and all safety/accounting gates remain zero. If birth occurs but router remains inactive, diagnose router reachability; do not tune numerics. No 8781, no dream fill.


## 2026-09-04 06:18 Expand-fill Repair-responsibility birth infra recovery
- Collected the fixed `1916869` birth smoke task state. The prior ScheduledTask run was terminal infra-failed (`LastTaskResult=1`) and produced no result artifact, so it is **INVALID_INFRA_RUN_DO_NOT_GRADE**.
- Root cause was instrumentation/package skew on the second worker: stale staged runner `_router_snapshot()` attempted `int(v38PassiveRepairFillEvents)` although this runtime field is a list. This was not a strategy/safety failure.
- Re-staged the already-correct project runner, which uses `len(list)` for list-valued passive Repair fill events. No policy, threshold, qty, price, delay, admission, responsibility, router, or <=180s behavior changed.
- Re-ran the exact same preregistered one-market smoke using the existing ScheduledTask. Corrected run is genuinely computing on the second worker: heavy PID `68232`, observed working set `965468160` bytes and CPU `17.96875s`; ScheduledTask code `267009` while running. Terminal result is not present yet, so RUNNING is not graded.
- Progress/recovery artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_EXPAND_FILL_REPAIR_RESPONSIBILITY_BIRTH_INFRA_RECOVERY_20260904_0618.json`.
- **Unique authorized next step:** collect only the existing corrected `C:\BTC5M-worker\.lan_worker_v1\v83_birth_1916869_result_20260904.json` without duplicate launch. Grade responsibility birth strictly later than Expand fill, router progress/Repair fill or semantic-round progress, physical conservation, and zero truthMismatch/overOwned/repairDrift/responsibilityOverfill/preBirthLeak/duplicateDebt/sharedOverfill. If birth occurs but Router remains inactive, diagnose RepairExecutionRouter reachability; do not tune numerics.


## 2026-09-04 08:18 Expand-fill responsibility resolves ALREADY_OWNED -> Router execution-family handoff shadow launched
- Collected corrected fixed `1916869` birth smoke terminal result: decision **EXISTING_PARENT_OWNED_BUT_ROUTER_UNREACHABLE_DIAGNOSE_ROUTER**. Confirmed V83 Expand fill occurred at `1788450022673`; on the strictly later receipt `1788450022675` the opposite DOWN Repair responsibility resolved to existing `repairParent id=1`, born earlier at `1788450005128`, terminal `ALREADY_OWNED`. Zero same-receipt birth and strict-later resolution gates PASS.
- Candidate behavior remained identical to allocation-aware baseline: fills `3 -> 3`, floor/PnL diagnostic `-0.8390312285`, Repair-parent births `1 -> 1`, no new Repair child/submit/fill/semantic round. `routerProgressVsBaseline=false`, `actualRepairFillProgress=false`.
- Safety/accounting stayed clean: truthMismatch=0, unexplainedOverOwned=0, unexplainedRepairDrift=0, responsibilityOverfill=0, preBirthLeak=0, duplicateDebt=0, sharedOverfill=0; allocation conservation PASS. This localizes the missing function after responsibility ownership, inside the Repair execution handoff.
- Code inspection found a specific structural hypothesis: RepairExecutionRouter V2 only applies recursive composite handoff when the Repair parent is tagged as overflow-born (`bornAt in v89OverflowBirthClocks`). Here the correctly owning DOWN parent predates the Expand fill, so responsibility may transfer without transferring execution-family semantics. This is a structural marker/handoff hypothesis, not parameter tuning.
- New preregister: `ETH_V83_EXISTING_PARENT_ROUTER_RESPONSIBILITY_HANDOFF_SHADOW_1916869_PREREGISTERED_20260904.json`. New behavior-inert runner: `tools/run_eth_v83_existing_parent_router_handoff_shadow_1916869.py`, py_compile PASS. The shadow evaluates the frozen Router both factually and with only `overflow_born_parent=true` counterfactually; the counterfactual has zero action authority.
- Second LAN worker task `BTC5M_V83_ROUTER_SHADOW_1916869_20260904` was created and started through the verified ScheduledTask path. Task status is RUNNING with code `267009`; terminal result is not yet present, therefore **RUNNING_NOT_GRADED**. Progress artifact: `ETH_V83_EXISTING_PARENT_ROUTER_HANDOFF_SHADOW_PROGRESS_20260904_0818.json`.
- **Unique authorized next step:** collect only the existing `v83_router_handoff_shadow_1916869_result_20260904.json` without duplicate launch. If the existing ALREADY_OWNED parent stays on factual `ORDINARY_PARENT_INHERIT_LEGACY_EXECUTION` while the identical frozen context becomes Router-eligible when only the execution-family overflow flag is shadow-flipped, preregister a Responsibility->ExecutionFamily handoff micro-world before any behavior mutation. Otherwise follow the observed factual blocker. No threshold/qty/price/delay tuning, no dream fill, no 8781.

## 2026-09-04 09:13 — Existing-parent responsibility-generation epoch micro-world PASS

- Previous Router shadow on fixed realistic-HFT market `1916869` rejected the single execution-family-flag hypothesis. Factual existing DOWN Repair parent `id=1` remained ordinary; overflow-only counterfactual still returned `NOT_ARMED`. The localized missing seam is a fresh responsibility-generation execution epoch when Expand-fill Repair responsibility resolves `ALREADY_OWNED`.
- Implemented reusable research module `tools/eth_repair_modular/responsibility_generation_epoch.py` and runner `tools/run_eth_v83_existing_parent_responsibility_generation_epoch_microworld.py`; `py_compile` PASS.
- Ran on second LAN worker job `btc5m-v83-existing-parent-epoch-microworld-20260904-v1`; terminal `succeeded`, return code 0, elapsed 0.143s. Formal result: `4/4 PASS`.
- Passed semantics: same Repair parent preserved while epoch increments; arm fill/payment/churn baselines rebase at responsibility attachment; only post-epoch disconnects count; confirmed Repair payment suppresses hard-active escalation; old churn cannot be reused after a later epoch.
- Safety/accounting gates: duplicateRepairParentBirths=0, duplicateDebt=0, responsibilityOverfill=0, sharedOverfill=0, truthMismatch=0, allocationConservation=PASS. No threshold/qty/price/delay changes, no dream fill, no 8781.
- Artifacts: `ETH_V83_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_MICROWORLD_RESULT_20260904.json` and `ETH_V83_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_HFT_1916869_PREREGISTERED_20260904.json`.
- **ONLY AUTHORIZED NEXT STEP:** implement the preregistered single-market `1916869` realistic-HFT epoch mutation runner, py_compile, stage to the second LAN worker, and run exactly that one market. Preserve ResponsibilityTransition, AllocationLedger V2, RepairExecutionRouter V2 numeric semantics, V83 admission, parent identity, and <=180s fence. Grade terminal result before any cohort expansion.


## 2026-09-04 10:12 — Existing-parent responsibility-generation epoch realistic-HFT launched
- Continued exactly from the preregistered unique authorization after the 4/4 micro-world PASS. No replanning and no cohort expansion.
- Existing HFT mutation runner `tools/run_eth_v83_existing_parent_responsibility_generation_epoch_hft_1916869.py` was found already implemented against the preregister; `py_compile` PASS together with `tools/eth_repair_modular/responsibility_generation_epoch.py`.
- Staged the runner plus matched `responsibility_generation_epoch.py`, `run_eth_v83_expand_fill_repair_responsibility_birth_smoke_1916869.py`, and `repair_execution_router_v2.py` to the second LAN worker. No policy/threshold/qty/price/delay changes.
- Launched exactly one fixed realistic-HFT market `1916869` as LAN job `btc5m-v83-existing-parent-epoch-hft-1916869-20260904-v1`, max_threads=4. Worker accepted the job; latest status is `running`, PID `32048`, stdout/stderr both 0 bytes at the first status check. Running is **NOT** graded as PASS/REJECT. Auto-collect is enabled.
- Frozen boundaries remain ResponsibilityTransition, AllocationLedger V2, RepairExecutionRouter V2 numeric semantics, V83 admission, Repair parent identity and <=180s fence. The only mutation is that an `ALREADY_OWNED` Repair responsibility attachment starts a new parent-local execution epoch and rebases fill/payment/churn evidence without rebirthing the parent.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_HFT_1916869_PROGRESS_20260904_1012.json`.
- **ONLY AUTHORIZED NEXT STEP:** collect this exact existing job when terminal without duplicate launch. Grade resolutionAlreadyOwned, same parent ID, new epoch observed, no pre-epoch evidence reuse, Router progress or explicit frozen blocker, allocation conservation, and zero truthMismatch/overOwned/responsibilityOverfill/repairDrift/doubleSpend/preBirthLeak/duplicateDebt/sharedOverfill/duplicate Repair-parent birth. Do not expand cohort until terminal grading.


## 2026-09-04 11:22 — post-epoch legal-min composite shadow launched
- Latest child-materialization shadow localized 39 post-Expand authorized Repair attempts on fixed 1916869 to `remainingCapBlocks` before package/physical submit; the new Repair residual is below venue-legal minimum at the passive bid. Router/epoch/ownership/timing are no longer the immediate blocker.
- Implemented `tools/run_eth_v83_post_epoch_repair_legal_min_composite_shadow_1916869.py`; py_compile PASS. This is behavior-inert and only counterfactually measures `legalQty=1/bid`, Repair-first allocation, excess overflow and immediate pair/floor safety. It does not round or submit qty.
- Staged to second LAN worker and started ScheduledTask `BTC5M_V83_LEGALMIN_SHADOW_1916869_20260904`; task is RUNNING, therefore not graded. Expected result: `C:/BTC5M-worker/.lan_worker_v1/v83_legal_min_composite_shadow_1916869_result_20260904.json`.
- Frozen: ResponsibilityTransition, AllocationLedger V2, RepairExecutionRouter V2, generation epoch, V83 admission, threshold/qty/price/delay and <=180s fence. No dream fill, no 8781.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_LEGAL_MIN_COMPOSITE_SHADOW_PROGRESS_20260904_1122.json`.
- **ONLY AUTHORIZED NEXT STEP:** collect this exact existing shadow without duplicate launch. If legal-min excess is accounting-conserved and floor-non-damaging, preregister one same-parent one-market behavior mutation; otherwise reject legal-min composite and move to passive-price/repricing reachability.


## 2026-09-04 12:16 — legal-min composite REJECT -> passive-price reachability launched
- Collected existing 1916869 legal-min composite shadow terminal result; ScheduledTask LastResult=0. Decision: **REJECT_LEGAL_MIN_COMPOSITE_STUDY_PASSIVE_PRICE_REACHABILITY**. Frozen Repair residual=2.1649963710 DOWN; 39 blocked rows, observed bid about 0.30–0.46, legal-min qty about 2.174–3.333, physical conservation PASS, admissibleRows=0, all safety/accounting zero. Do not round qty upward.
- Existing preregister `ETH_V83_POST_EPOCH_REPAIR_PASSIVE_PRICE_LEGALITY_REACHABILITY_SHADOW_1916869_PREREGISTERED_20260904.json` is now active. Implemented `tools/run_eth_v83_post_epoch_repair_passive_price_legality_reachability_shadow_1916869.py`; py_compile PASS.
- Staged to second LAN worker and launched ScheduledTask `BTC5M_V83_PASSIVE_PRICE_REACH_1916869_20260904`; task is RUNNING (267009), therefore not graded. Behavior is shadow-only: unchanged Repair qty, no reprice action, no threshold/price/delay/TTL change, no dream fill, no 8781.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_PASSIVE_PRICE_REACHABILITY_PROGRESS_20260904_1216.json`.
- **ONLY AUTHORIZED NEXT STEP:** collect only existing `v83_passive_price_reachability_1916869_result_20260904.json` without relaunch. If unchanged qty never becomes venue-legal at a causal non-crossing passive level, reject passive-price legality and move to remainder aggregation or bounded Active handoff architecture. If a legal level exists, audit inherited economic ceiling and raw-tape queue/depletion before any behavior mutation.


## 2026-09-04 13:12 — passive-price legality localized -> bounded Active Repair handoff shadow launched
- Collected fixed `1916869` passive-price reachability terminal: decision **PASS_PRICE_LEGALITY_BUT_REJECT_FAST_PASSIVE_REACHABILITY**. Frozen Repair residual `2.1649963710 DOWN` is not permanently venue-illegal: first causal non-crossing legal passive frontier appears `6049ms` after the new epoch (implied DOWN bid `0.48`, legal without qty uplift). However raw physical evidence is slow: first observed DOWN-maker fill at any venue-legal price is ~`67327ms` after epoch; exact `0.47` maker fill is ~`118327ms` after epoch. This moves the bottleneck from legal-min incompatibility to legal-but-slow passive execution reachability. No action-density gain in the shadow.
- Existing next preregister is `ETH_V83_POST_EPOCH_REPAIR_BOUNDED_ACTIVE_HANDOFF_SHADOW_1916869_PREREGISTERED_20260904.json`. It freezes same Repair parent/debt, qty `2.1649963710`, inherited economic Repair ceiling `0.6124560578`, ResponsibilityTransition, AllocationLedger V2 and Router accounting; Active authority remains Repair-only and requires post-epoch non-fill/disconnect evidence.
- Implemented `tools/run_eth_v83_post_epoch_repair_bounded_active_handoff_shadow_1916869.py`; `py_compile` PASS. The runner is behavior-inert and checks strictly-past DOWN ask, venue-legal qty, qty<=remaining debt, inherited economic ceiling, projected floor non-damage, post-epoch churn evidence and whether eligibility occurs before observed passive maker materialization. No qty uplift or threshold/price/delay/TTL sweep.
- Staged and launched on second LAN worker as job `eth-v83-bounded-active-shadow-1916869-20260904-v1`, max_threads=4. Worker accepted it; status is `running`, PID `67984`, stdout/stderr 0 bytes at first check. RUNNING is **not graded**. Auto-collect enabled.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_BOUNDED_ACTIVE_HANDOFF_SHADOW_PROGRESS_20260904_1312.json`.
- **ONLY AUTHORIZED NEXT STEP:** collect this exact existing job without relaunch. PASS requires at least one bounded Active Repair candidate after legal passive frontier + disconnect evidence and before passive maker fill evidence, qty<=remaining debt, venue legal, ask<=inherited ceiling, projected floor non-damaging, allocation conservation PASS and truthMismatch/overOwned/repairDrift/responsibilityOverfill/preBirthLeak/duplicateDebt/sharedOverfill all zero. If PASS, preregister exactly one-market behavior mutation; if REJECT, move to same-parent remainder aggregation or execution-price ceiling architecture, not numeric tuning.


## 2026-09-04 14:13 — bounded Active handoff shadow infra-invalid -> corrected rerun
- Collected `eth-v83-bounded-active-shadow-1916869-20260904-v1`: worker terminal succeeded, but the scientific result is **INVALID_INFRA_INSTRUMENTATION_DO_NOT_GRADE_STRATEGY**. Every bounded-Active candidate evaluation raised `AttributeError: module 'allocaware_direct_for_expand_fill_birth' has no attribute 'v1'`, so `eligibleCount=0` is not admissible evidence against the handoff hypothesis.
- Root cause is behavior-inert quote instrumentation only: runner used `birth.base.v1.quotes(self.book)` even though the actual runtime quote namespace is `birth.base.front.g.v1`. No ResponsibilityTransition, AllocationLedger V2, RepairExecutionRouter, admission, qty, price, threshold, delay, TTL, parent/debt, or <=180s behavior was implicated.
- Fixed only the quote accessor to `birth.base.front.g.v1.quotes(self.book)`; `py_compile` PASS. Re-staged the same runner to `btc5m-worker` and launched the exact same preregister as `eth-v83-bounded-active-shadow-1916869-20260904-v2`, max_threads=4. Worker accepted it; current state is `running`, PID `95964`, auto-collect enabled. RUNNING is not graded.
- Progress artifact: `data/research/r4_v0/p0_provenance_v1/ETH_V83_BOUNDED_ACTIVE_HANDOFF_SHADOW_INFRA_RECOVERY_20260904_1413.json`.
- Boundary unchanged: frozen Repair qty `2.1649963710`, inherited economic ceiling `0.6124560578`, same Repair parent/debt, no qty uplift, no numeric tuning, realistic HFT only, no dream fill, no 8781, <=180s no new speculative exposure.
- **ONLY AUTHORIZED NEXT STEP:** collect only `eth-v83-bounded-active-shadow-1916869-20260904-v2` when terminal, without duplicate launch, and grade the original bounded-Active preregister. PASS -> preregister exactly one-market behavior mutation. REJECT -> same-parent remainder aggregation or execution-price-ceiling architecture; no numeric tuning.
