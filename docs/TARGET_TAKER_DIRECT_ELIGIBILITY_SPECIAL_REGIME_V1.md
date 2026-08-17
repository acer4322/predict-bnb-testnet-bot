# TARGET_TAKER_DIRECT_ELIGIBILITY_SPECIAL_REGIME_V1

Offline EBM research only. Nothing in this workflow sends orders or promotes a paper/live strategy.

## What this fixes

The existing Target Taker side model answers a conditional question: given that Target already decided to take, which side does it buy?

This experiment adds the missing first-stage question: given only public state at this market-second, will Target place any Taker order in the next 1 / 2 / 5 seconds?

The same special-regime window is also applied to Target Maker because a severe Maker drawdown is useful as a negative stress regime.

## Leakage boundary

`target_event_ms` in the Target Taker mirror is second-quantized. Therefore a decision snapshot and a Target event that share the same wall-clock second are never treated as a positive pair.

For a decision in bucket `t`, eligibility labels only accept Target Taker event buckets strictly after `t`:

- 1s: `(t, t+1s]`
- 2s: `(t, t+2s]`
- 5s: `(t, t+5s]`

The risk set uses the latest public `wallet_taker_signal_snapshots` row per market-second and includes covered markets where Target made no Taker trade.

## Feature sets

The Taker trainer compares:

1. `frozen16` — same compact public feature set used by the current Target Taker side EBM.
2. `special_regime_only` — pin/strike persistence, short volatility, Prediction latent bias, decoupling, futures-vs-spot lead/lag.
3. `frozen16_plus_special`.
4. `full_public_plus_special`.

The special family includes distance/persistence around strike, strike crosses, 3/10/30s spot/futures realized volatility, Prediction UP-mid EMA/slope/persistence, Prediction-vs-spot/futures direction agreement, futures-minus-spot return/basis, and Chainlink-minus-spot distance.

## Validation

The special window is market-disjoint from historical training.

The report includes:

- Normal reference: early history -> latest pre-special history.
- Experiment A: pre-special history -> special-regime holdout.
- Experiment B: history + early special markets -> later special markets.
- Feature drift ranked by standardized mean difference.

The main question is whether `frozen16` degrades in the special holdout while `frozen16_plus_special` or `special_regime_only` retains positive log-loss lift / useful AUC.

## Maker stress test

`tools/train_target_maker_special_regime_v1.py` reuses the existing direct Maker datasets and evaluates:

- placement hazard: 1s / 2s / 5s;
- side: diagnostic only;
- quote aggressiveness: at/improves best bid and within 2 ticks;
- public feature drift between history and the special window.

Maker placement identity is still inferred from anonymous public-book changes linked retrospectively to known Target Maker fills. This diagnoses behavior/model regime shift; it does not infer Maker PnL from placement labels.

## Run

Install research dependencies once:

```powershell
pip install -e ".[research]"
```

Run both Taker and Maker using 2026-08-16 Taiwan midnight as the broad special-window start:

```powershell
.\run-target-special-regime-ebm-v1.ps1
```

If the abnormal regime began later, narrow it explicitly, for example:

```powershell
.\run-target-special-regime-ebm-v1.ps1 `
  -SpecialStart "2026-08-16T12:00:00+08:00"
```

Optionally cap the window:

```powershell
.\run-target-special-regime-ebm-v1.ps1 `
  -SpecialStart "2026-08-16T12:00:00+08:00" `
  -SpecialEnd   "2026-08-17T02:00:00+08:00"
```

Reuse already-built CSVs with `-SkipBuild`. Run only Taker with `-SkipMaker`.

## Outputs

- `data/research/target_taker_direct_eligibility_special_regime_v1.csv`
- `data/research/target_taker_direct_eligibility_special_regime_v1.meta.json`
- `data/research/target_taker_direct_eligibility_special_regime_v1_report.json`
- `data/research/target_maker_special_regime_v1_report.json`

For the first pass inspect, in order:

1. `tasks.eligibility_5s.rankingBySpecialLogLossLift`
2. `tasks.eligibility_2s.rankingBySpecialLogLossLift`
3. `featureDrift`
4. Maker `hazardTasks.maker_hazard_5s`
5. Maker `levelTasks.label_near_best_2ticks`
6. Maker `hazardFeatureDrift` and `behaviorFeatureDrift`

Do not promote anything to paper/live from this report alone.
