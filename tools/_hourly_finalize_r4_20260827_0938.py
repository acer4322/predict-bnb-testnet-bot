from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
R4REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
NOVREG=ROOT/'data/research/hourly_novel_test_registry_v1.json'
ART=ROOT/'data/research/r4_v0/hourly/r4_late_queue_cross_readiness_context_v1.json'
PRE=ROOT/'data/research/r4_v0/hourly/r4_late_queue_cross_readiness_context_v1_preregistered.json'
HAND=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
PROG=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v10.md'
TOOL='tools/test_r4_late_queue_cross_readiness_context_v1.py'
TEST='R4_LATE_QUEUE_CROSS_READINESS_CONTEXT_V1_20260827_0938'
art=json.loads(ART.read_text(encoding='utf-8'))
entry={
 'experimentId':TEST,'testedAt':'2026-08-27T09:45:00+08:00','type':'FINAL60_QUEUE_OPPORTUNITY_CROSS_READINESS_CONTEXT','status':art['status'],
 'semanticKeys':['final 0-60s negative floor','receipt-clock queue opportunity','durable safe cross readiness','execution reachability belief','belief not action authority'],
 'candidate':'In final 0-60s negative-floor realistic-HFT states, test whether frozen receipt-clock queue_opportunity BELIEF adds incremental context beyond portfolio/payoff + Predict/strike + synchronized quote context for durable non-negative crossing within 5s.',
 'artifact':str(ART.relative_to(ROOT)).replace('\\','/'),'preregisteredArtifact':str(PRE.relative_to(ROOT)).replace('\\','/'),'candidateTool':TOOL,
 'primaryResult':{
   'fresh24Rows':art['coverage']['FRESH24']['rows'],'fresh24Positives':art['coverage']['FRESH24']['positives'],
   'unseen24Rows':art['coverage']['UNSEEN24']['rows'],'unseen24Positives':art['coverage']['UNSEEN24']['positives'],
   'replication3Rows':art['coverage']['REPLICATION3']['rows'],'replication3Positives':art['coverage']['REPLICATION3']['positives'],
   'freshTrainRows':art['chronologicalSplitSupport']['freshTrainRows'],'freshTrainPositives':art['chronologicalSplitSupport']['freshTrainPositives'],
   'freshTrainClasses':art['chronologicalSplitSupport']['freshTrainClasses'],'eligibleIndependentHoldouts':art['summary']['eligibleIndependentHoldouts'],
   'seedNoRegression5of5':True,'trainerAcqSpearman':0.7090364951754681,'trainerPresSpearman':0.62395604707533
 },
 'conclusion':'TESTED_INCONCLUSIVE. The semantically novel queue-opportunity CROSS_READINESS formulation is not identifiable on the current receipt-clock HFT cohorts: durable safe crossing within 5s is extremely rare, and the chronological FRESH24 training block has 0 positives across 1,232 rows before any model fit. Do not re-split chronology, widen horizon, lower support gates, or infer queue-belief efficacy from this cohort.',
 'promotion':'NONE_NOT_ACTION_AUTHORITY','retestAllowed':'ONLY_NEW_INDEPENDENT_CHRONOLOGY_OR_PREDECLARED_REPLICATION',
 'nextDistinct':'Do not rescue the same 5s cross-readiness target. Seek a semantically distinct late protection transition with adequate support, or acquire new independent receipt-clock chronology explicitly rich in negative-floor -> durable-cross events before predeclared replication.'
}
r=json.loads(R4REG.read_text(encoding='utf-8'))
ids={x.get('experimentId') or x.get('cycleId') for x in r.get('experiments',[])}
if TEST not in ids:r['experiments'].append(entry)
R4REG.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
n=json.loads(NOVREG.read_text(encoding='utf-8'))
tids={x.get('testId') for x in n.get('tests',[])}
if TEST not in tids:
 n['tests'].append({
  'testId':TEST,'testedAt':entry['testedAt'],'axis':'R4_FINAL60_NEGATIVE_FLOOR_QUEUE_OPPORTUNITY_CROSS_READINESS_CONTEXT','semanticKeys':entry['semanticKeys'],
  'hypothesis':entry['candidate'],'cohort':{'source':'existing non-live receipt-clock HFT queue-opportunity shadow','FRESH24':art['coverage']['FRESH24'],'UNSEEN24':art['coverage']['UNSEEN24'],'REPLICATION3':art['coverage']['REPLICATION3'],'special20260816Sealed':True},
  'primaryResult':entry['primaryResult'],'status':art['status'],'artifact':entry['artifact'],'preregisteredArtifact':entry['preregisteredArtifact'],'tool':TOOL,
  'conclusion':entry['conclusion'],'retestAllowed':'ONLY_NEW_INDEPENDENT_CHRONOLOGY_OR_PREDECLARED_REPLICATION'
 })
NOVREG.write_text(json.dumps(n,ensure_ascii=False,indent=2),encoding='utf-8')
block=f'''\n\n## 2026-08-27 09:45 — LATE QUEUE-OPPORTUNITY CROSS-READINESS CONTEXT V1 — INCONCLUSIVE\n- Semantic novelty: prior late queue test asked whether queue opportunity predicts positive-floor break risk; prior negative-floor CROSS_READINESS test added quote economics. This cycle uniquely tests the frozen receipt-clock `queue_opportunity` BELIEF for negative-floor -> durable safe crossing on realistic HFT trajectories.\n- Layer assignment: receipt-clock depth/churn = INFORMATION; queue_opportunity = BELIEF / execution-reachability context; synchronized quote = INFORMATION; Predict/strike = BELIEF context; portfolio/payoff/floor geometry = LOGIC state. Output is NOT_ACTION_AUTHORITY.\n- Fixed target: final 0-60s, current realized floor<0; positive iff floor reaches >=0 within next 5s and remains >=0 for subsequent observed checkpoints through current+5s. Future HFT path is offline label only.\n- Support: FRESH24 {art['coverage']['FRESH24']['rows']} rows / {art['coverage']['FRESH24']['positives']} positives; UNSEEN24 {art['coverage']['UNSEEN24']['rows']} / {art['coverage']['UNSEEN24']['positives']}; REPLICATION3 {art['coverage']['REPLICATION3']['rows']} / {art['coverage']['REPLICATION3']['positives']}. FRESH24 chronological training block had {art['chronologicalSplitSupport']['freshTrainRows']} rows and **0 positives**, so no model was fit.\n- Decision: TESTED_INCONCLUSIVE due chronological support/identifiability failure. No re-splitting, horizon widening, threshold/model sweep, or action coupling. Retest only on new independent chronology / predeclared replication.\n- Trainer refresh succeeded: 500 ordinary Target markets, acquisition Spearman 0.7090, preservation Spearman 0.6240; frozen Echtgeld remains exactly 8; 2026-08-16 SEALED. Seed/no-regression 5/5 PASS. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `data/research/r4_v0/hourly/r4_late_queue_cross_readiness_context_v1.json`; preregistration: `data/research/r4_v0/hourly/r4_late_queue_cross_readiness_context_v1_preregistered.json`; tool: `{TOOL}`.\n- Next distinct gap: do not rescue this sparse 5s target. Use a different late protection transition with adequate natural support, or wait for genuinely new receipt-clock chronology enriched in negative-floor durable-cross events before frozen replication.\n<!-- {TEST} -->\n'''
h=HAND.read_text(encoding='utf-8')
if TEST not in h:HAND.write_text(h+block,encoding='utf-8')
PROG.write_text(f'''# R4 Information Layer Progress 2026-08-27 V10\n\n## New result: final-60s queue-opportunity CROSS_READINESS — INCONCLUSIVE\n\nThis cycle tested a semantically new late route: receipt-clock `queue_opportunity` as an execution-reachability BELIEF for current negative-floor -> durable safe crossing within 5 seconds. The prior queue late test targeted positive-floor break risk; the prior negative-floor readiness test added quote economics rather than queue opportunity.\n\nLayer boundaries remained intact: depth/churn = INFORMATION; queue opportunity = BELIEF; synchronized quote = INFORMATION; Predict/strike = BELIEF context; portfolio/payoff geometry = LOGIC state; no action authority.\n\nThe current HFT chronology cannot identify the question. FRESH24 has {art['coverage']['FRESH24']['rows']} eligible rows but only {art['coverage']['FRESH24']['positives']} positives; UNSEEN24 has {art['coverage']['UNSEEN24']['positives']}; REPLICATION3 has {art['coverage']['REPLICATION3']['positives']}. More importantly, the chronological FRESH24 training block contains {art['chronologicalSplitSupport']['freshTrainRows']} rows and zero positives, so model fitting was intentionally not performed.\n\nClassification: TESTED_INCONCLUSIVE, support failure before model fit. Do not re-split chronology, widen the 5s horizon, lower gates, or infer queue efficacy. Retest only with new independent receipt-clock chronology or a predeclared replication.\n\nTrainer refresh: acquisition Spearman 0.7090, preservation 0.6240, 500 ordinary Target markets, frozen Echtgeld exactly 8, 2026-08-16 SEALED. Seed/no-regression 5/5 PASS.\n''',encoding='utf-8')
print(json.dumps({'ok':True,'r4Registry':str(R4REG.relative_to(ROOT)).replace('\\','/'),'novelRegistry':str(NOVREG.relative_to(ROOT)).replace('\\','/'),'handoffUpdated':True,'progress':str(PROG.relative_to(ROOT)).replace('\\','/')},ensure_ascii=False))
