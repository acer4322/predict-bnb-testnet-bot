import json
from pathlib import Path
from datetime import datetime
ROOT=Path('.')
TEST_ID='R4_MANAGEMENT_EARLY_EXECUTION_KIND_CONTEXT_V1_20260828_0438'
ART='data/research/r4_v0/hourly/r4_management_early_execution_kind_context_v1.json'
PRE='data/research/r4_v0/hourly/r4_management_early_execution_kind_context_v1_preregistered.json'
TOOL='tools/test_r4_management_early_execution_kind_context_v1.py'
entry={
 'testId':TEST_ID,'testedAt':'2026-08-28T04:43:44+08:00','status':'TESTED_REJECTED','domain':'R4',
 'semanticAxis':'MANAGEMENT / EARLY_EXECUTION_INTENT_PROVENANCE_CONTEXT',
 'semanticKeys':['early management 120-180s','MAIN versus OPTION execution intent kind','responsibility provenance','pair balance trajectory','floor risk trajectory','realistic-HFT lifecycle quality','not action authority'],
 'hypothesis':'Within EARLY 120-180s Management, current non-live HFT execution-intent provenance (MAIN vs OPTION) may add reproducible local lifecycle-quality information beyond portfolio/lifecycle state plus the already-kept pair/balance and floor/risk trajectory context.',
 'artifact':ART,'preregistration':PRE,'tool':TOOL,'decision':'TESTED_REJECTED','actionAuthority':False,
 'cohort':{'train':'REPLICATION4','trainRows':46,'trainMarkets':15,'holdouts':['REPLICATION6','REPLICATION7_96'],'holdoutRows':[41,153],'holdoutMarkets':[11,38],'eligibleComparisons':6,'special20260816Sealed':True,'echtgeldTraining':False},
 'primaryResult':{'meanDeltaAuc':0.0,'meanDeltaAp':0.0,'meanLogLossImprovement':0.0,'worstDeltaAuc':0.0,'nonNegativeAucFraction':1.0,'seedNoRegression5of5':True,'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT'},
 'summary':'MAIN/OPTION execution-intent provenance has exactly zero incremental AUC/AP/log-loss value across all six eligible floor/abs-net/joint quality comparisons after conditioning on the frozen EARLY portfolio/lifecycle + pair/balance + floor/risk trajectory context. Both kinds had support in train and holdouts, so this is a supported rejection rather than a constant-feature/support failure.',
 'retestAllowed':False,
 'nextDistinct':'Do not repackage MAIN/OPTION kind, transitions_15s, tempo, progress/stall, or trajectory coherence. Prefer a genuinely new independent HFT chronology for frozen EARLY pair/balance+floor/risk replication when available; otherwise test a semantically different naturally-supported EARLY information source.'
}
# R4 registry
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'; d=json.loads(p.read_text(encoding='utf-8'))
arr=d.setdefault('tests',[])
if not any(x.get('testId')==TEST_ID for x in arr): arr.append(entry)
p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')
# Novel registry
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json'; d2=json.loads(p2.read_text(encoding='utf-8'))
arr2=d2.setdefault('tests',[])
if not any(x.get('testId')==TEST_ID for x in arr2): arr2.append(entry)
p2.write_text(json.dumps(d2,ensure_ascii=False,indent=2),encoding='utf-8')
# progress note
prog=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260828_v29.md'
prog.write_text('''# R4 Information Layer Progress V29 — 2026-08-28 04:44\n\n- Exactly one novel test: `R4_MANAGEMENT_EARLY_EXECUTION_KIND_CONTEXT_V1_20260828_0438`.\n- Semantic novelty: prior HFT artifacts only reported MAIN/OPTION per-kind descriptives; this cycle first tests current execution-intent provenance as incremental EARLY lifecycle-quality INFORMATION above the already-kept pair/balance + floor/risk trajectory context.\n- Layers: portfolio/payoff = LOGIC baseline; pair/balance + floor/risk = LOGIC trajectory context; MAIN/OPTION kind = EXECUTION INFORMATION / responsibility provenance; NOT_ACTION_AUTHORITY.\n- Cohort: REPLICATION4 train (46 rows / 15 markets; MAIN 28, OPTION 18) -> independent REPLICATION6 (41 rows / 11 markets) + REPLICATION7_96 (153 rows / 38 markets), phase 120–180s. Six target×holdout comparisons were eligible.\n- Result: mean dAUC 0.0000; mean dAP 0.0000; mean log-loss improvement 0.0000; worst dAUC 0.0000. Exact zero increment across floor improvement, abs-net reduction, and joint quality in both holdouts.\n- Decision: `TESTED_REJECTED`. MAIN/OPTION source provenance adds no local quality information once current geometry/lifecycle and kept EARLY geometry trajectories are known. No model/threshold rescue and no action authority.\n- Seed/no-regression 5/5 PASS. Trainer refresh timed out once with no new artifact; latest valid snapshot remains Acquisition 0.7364 / Preservation 0.5879, frozen Echtgeld exactly 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Next: wait for genuinely new independent HFT chronology for frozen EARLY pair/balance + floor/risk replication, or move to another semantically different naturally-supported EARLY Management information source. Do not repackage kind/tempo/transitions/progress/coherence.\n''',encoding='utf-8')
# handoff append
hp=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
with hp.open('a',encoding='utf-8') as f:
 f.write('''\n\n## 2026-08-28 04:44 — EARLY execution-intent provenance context\n- Exactly one novel test: `R4_MANAGEMENT_EARLY_EXECUTION_KIND_CONTEXT_V1_20260828_0438` -> `TESTED_REJECTED`.\n- Duplicate audit first rejected `transitions_15s` as a candidate because it already exists in the canonical Management lifecycle baseline. MAIN/OPTION had only prior per-kind descriptive reporting, not incremental EARLY lifecycle-quality attribution.\n- Layer assignment: MAIN/OPTION kind = EXECUTION INFORMATION / responsibility provenance; existing portfolio/payoff = LOGIC baseline; kept pair/balance + floor/risk deltas = LOGIC trajectory context. Output remains context only, NOT_ACTION_AUTHORITY.\n- REPLICATION4 train: 46 rows / 15 markets (MAIN 28, OPTION 18). Independent holdouts: REPLICATION6 41 rows / 11 markets; REPLICATION7_96 153 rows / 38 markets. Six floor/abs-net/joint comparisons all eligible.\n- Result: mean dAUC=0, mean dAP=0, mean log-loss improvement=0, worst dAUC=0. Both kinds were represented, so this is supported rejection, not missing support.\n- Interpretation: once EARLY geometry/lifecycle plus pair/balance and floor/risk trajectories are known, whether the current checkpoint comes from MAIN or OPTION adds no reproducible local quality information. Do not threshold/model-sweep or route kind into actions.\n- Seed/no-regression 5/5 PASS. Trainer refresh timed out once and produced no new snapshot; latest valid remains Acquisition 0.7364 / Preservation 0.5879 with frozen Echtgeld exactly 8. R4 champion, live R3-S/R3.1 and 8781 unchanged.\n- Next: PREDECLARED_REPLICATION of frozen EARLY pair/balance + floor/risk interpretation only on genuinely new independent HFT chronology; if unavailable, choose a different naturally-supported EARLY information source. Do not repackage MAIN/OPTION kind, transitions, tempo, progress/stall, or coherence.\n''')
print(json.dumps({'ok':True,'testId':TEST_ID,'r4RegistryTests':len(arr),'novelRegistryTests':len(arr2),'progress':str(prog),'handoffUpdated':True}))
