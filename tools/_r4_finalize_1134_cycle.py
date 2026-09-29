from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
r4reg=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
novreg=ROOT/'data/research/hourly_novel_test_registry_v1.json'
handoff=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
progress=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v12.md'
entry={
  'experimentId':'R4_LATE_FLOOR_DRAWDOWN_SEVERITY_QUOTE_ROUTING_V1_20260827_1134',
  'testedAt':'2026-08-27T11:38:00+08:00',
  'type':'FINAL60_CONTINUOUS_FLOOR_DRAWDOWN_SEVERITY_QUOTE_ROUTING',
  'status':'TESTED_REJECTED',
  'semanticKeys':['final 0-60s positive floor','future 10s floor drawdown magnitude','weak-side quote economics','continuous late tail severity','information not action authority'],
  'candidate':'In final 0-60s positive-floor states, test whether strict-past weak-side quote economics adds incremental information beyond portfolio/payoff geometry + Predict/strike for continuous next-10s floor drawdown magnitude.',
  'artifact':'data/research/r4_v0/hourly/r4_late_floor_drawdown_severity_quote_routing_v1.json',
  'preregisteredArtifact':'data/research/r4_v0/hourly/r4_late_floor_drawdown_severity_quote_routing_v1_preregistered.json',
  'candidateTool':'tools/test_r4_late_floor_drawdown_severity_quote_routing_v1.py',
  'primaryResult':{'markets':38,'rows':680,'eligibleFolds':3,'totalTestRows':363,'meanDeltaSpearman':0.018259877865489844,'worstFoldDeltaSpearman':-0.08326514926532158,'meanMaeImprovement':0.8776879401796247,'meanP90AbsErrorImprovement':0.01277169646359629,'seedNoRegression5of5':True,'trainerAcqSpearman':0.7176032897021389,'trainerPresSpearman':0.6621952899984763},
  'conclusion':'TESTED_REJECTED. Quote economics slightly improves mean MAE/P90 absolute error for continuous 10s floor-drawdown severity, but chronological rank stability fails: two folds worsen Spearman and worst-fold delta is -0.0833. This does not overturn the prior positive-floor any-break KEEP_SIGNAL; it shows that quote context is useful for imminent break classification but is not a stable estimator of break magnitude. Do not model/threshold-sweep rescue or grant action authority.',
  'promotion':'NONE_NOT_ACTION_AUTHORITY','retestAllowed':False,
  'nextDistinct':'Do not create another equivalent late quote severity/horizon target. Return to a semantically different execution/lifecycle gap such as pre-positioned resting-order occupancy / queue-position lifecycle on receipt-clock realistic HFT, after duplicate audit; alternatively wait for new independent chronology for the frozen late queue-opportunity replication.'
}
d=json.loads(r4reg.read_text(encoding='utf-8'))
arr=d.setdefault('experiments',[])
if not any(x.get('experimentId')==entry['experimentId'] for x in arr): arr.append(entry)
r4reg.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
nov={
 'testId':entry['experimentId'],'testedAt':entry['testedAt'],'axis':'R4_FINAL60_CONTINUOUS_FLOOR_DRAWDOWN_SEVERITY_QUOTE_INFORMATION_ROUTING','semanticKeys':entry['semanticKeys'],
 'hypothesis':'Strict-past weak-side quote economics may add chronology-stable information beyond geometry+Predict/strike about continuous next-10s floor drawdown magnitude in final-60s positive-floor states.',
 'cohort':{'source':'ordinary Target BTC + strict-past public_research_archive_v1','markets':38,'rows':680,'phase':'0-60s current positive floor','sealed20260816':True,'publicQuoteMaxLagMs':1500},
 'primaryResult':entry['primaryResult'],'status':'TESTED_REJECTED','artifact':entry['artifact'],'preregisteredArtifact':entry['preregisteredArtifact'],'tool':entry['candidateTool'],'conclusion':entry['conclusion'],'retestAllowed':False
}
d2=json.loads(novreg.read_text(encoding='utf-8'));tests=d2.setdefault('tests',[])
if not any(x.get('testId')==nov['testId'] for x in tests): tests.append(nov)
novreg.write_text(json.dumps(d2,ensure_ascii=False,indent=2),encoding='utf-8')
text='''# R4 Information Layer Progress 2026-08-27 V12\n\n## New result: final-60s continuous floor-drawdown severity quote routing — REJECTED\n\nThis cycle tested a semantically new late protection target: not whether an already-positive floor breaks, whether a negative floor crosses, or whether a break persists, but the **continuous magnitude** of the next-10s floor drawdown.\n\nLayer assignment remains strict: weak-side public quote economics = INFORMATION; Predict/strike = BELIEF context; portfolio/payoff geometry = LOGIC state; output = LATE CROSSING_PROTECTION severity context only. NOT_ACTION_AUTHORITY.\n\nCohort: 38 ordinary Target markets / 680 final-60s positive-floor rows, strict-past quote median lag 164ms / max 263ms, 2026-08-16 SEALED. Three chronological folds / 363 test rows satisfied support.\n\nResult: mean delta Spearman +0.0183, but fold deltas were -0.0607 / -0.0833 / +0.1988; fixed all-fold-nonnegative stability rule fails. Mean MAE improved by 0.878 and mean P90 absolute error by 0.013, but those average calibration gains cannot rescue the unstable ranking. Classification: TESTED_REJECTED.\n\nInterpretation: preserve the prior KEEP_SIGNAL that quote economics helps classify imminent positive-floor break risk; do not extend it into a stable estimator of break magnitude. No threshold/model sweep and no action authority.\n\nTrainer refresh: 500 ordinary Target markets, acquisition Spearman 0.7176, preservation Spearman 0.6622, frozen Echtgeld exactly 8. Seed/no-regression 5/5 PASS. Champion/live R3-S/R3.1/8781 unchanged.\n\nNext: stop generating equivalent late quote severity/horizon variants. After duplicate audit, prefer a semantically different receipt-clock execution/lifecycle question such as pre-positioned resting-order occupancy / queue-position lifecycle, or wait for new independent chronology for the frozen queue-opportunity replication.\n'''
progress.write_text(text,encoding='utf-8')
append='''\n\n## 2026-08-27 11:38 — LATE FLOOR-DRAWDOWN SEVERITY QUOTE ROUTING V1 — REJECTED\n- Semantic novelty: unlike prior any-break classification, negative-floor cross readiness, or persistent-loss targets, this predicts continuous next-10s floor drawdown magnitude from a currently positive floor.\n- Layer: weak-side quote economics = INFORMATION; Predict/strike = BELIEF context; portfolio/payoff geometry = LOGIC state; output = late protection severity context only; NOT_ACTION_AUTHORITY.\n- Cohort: 38 ordinary Target BTC markets / 680 final-60s positive-floor rows; strict-past quote median lag 164ms, max 263ms; 2026-08-16 SEALED. Three eligible chronological folds / 363 test rows.\n- Result: mean delta Spearman +0.0183 but fold deltas -0.0607 / -0.0833 / +0.1988; mean MAE improvement +0.878 and mean P90 absolute-error improvement +0.013. Fixed KEEP gate required every fold deltaSpearman>=0 and mean>=0.03, so FAIL.\n- Decision: TESTED_REJECTED. The prior late positive-floor any-break quote KEEP_SIGNAL remains valid, but quote economics is not a stable estimator of drawdown magnitude. No model/threshold sweep, no action authority.\n- Trainer refresh: 500 ordinary Target markets; acquisition Spearman 0.7176; preservation 0.6622; frozen Echtgeld exactly 8. Seed/no-regression 5/5 PASS. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `data/research/r4_v0/hourly/r4_late_floor_drawdown_severity_quote_routing_v1.json`; preregistration: `data/research/r4_v0/hourly/r4_late_floor_drawdown_severity_quote_routing_v1_preregistered.json`; tool: `tools/test_r4_late_floor_drawdown_severity_quote_routing_v1.py`. Both registries and information progress V12 updated.\n- Next distinct gap: stop equivalent late quote severity/horizon variants. Prefer a semantically different receipt-clock execution/lifecycle study such as pre-positioned resting-order occupancy / queue-position lifecycle after duplicate audit, or wait for new independent chronology for the frozen late queue-opportunity replication.\n<!-- R4_LATE_FLOOR_DRAWDOWN_SEVERITY_QUOTE_ROUTING_V1_20260827_1134 -->\n'''
h=handoff.read_text(encoding='utf-8')
if 'R4_LATE_FLOOR_DRAWDOWN_SEVERITY_QUOTE_ROUTING_V1_20260827_1134' not in h: handoff.write_text(h+append,encoding='utf-8')
print(json.dumps({'ok':True,'r4Experiments':len(arr),'novelTests':len(tests),'progress':str(progress.relative_to(ROOT)).replace('\\\\','/')}))
