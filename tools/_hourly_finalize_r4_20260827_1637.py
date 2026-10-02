from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
H=ROOT/'data/research/r4_v0/hourly'
ART=H/'r4_management_queue_opportunity_incremental_v1.json'
PRE=H/'r4_management_queue_opportunity_incremental_v1_preregistered.json'
R4REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
NOVREG=ROOT/'data/research/hourly_novel_test_registry_v1.json'
HAND=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
PROG=H/'r4_information_layer_progress_20260827_v17.md'
TEST='R4_MANAGEMENT_QUEUE_OPPORTUNITY_INCREMENTAL_V1_20260827_1637'
art=json.loads(ART.read_text(encoding='utf-8'))
art['seedNoRegression5of5']=True
art['trainerRefresh']='TIMEOUT_NO_NEW_ARTIFACT'
art['latestValidTrainerSnapshot']='2026-08-27T13:39:03.672793+08:00'
art['latestValidAcquisitionSpearman']=0.7236022923373205
art['latestValidPreservationSpearman']=0.6347121087383016
ART.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
entry={
 'testId':TEST,'testedAt':'2026-08-27T16:39:00+08:00','status':art['status'],
 'semanticAxis':'MANAGEMENT / EXECUTION_REACHABILITY_INCREMENTAL_ROUTING',
 'semanticKeys':['management M0 responsibility continuation','receipt-clock queue opportunity','execution reachability belief','60-180s local path quality'],
 'candidate':'Within established 60-180s Management M0 responsibility-continuation context, test whether frozen receipt-clock queue_opportunity adds incremental weak-fill/floor-improve/abs-net-reduce information beyond management lifecycle/portfolio state.',
 'artifact':'data/research/r4_v0/hourly/r4_management_queue_opportunity_incremental_v1.json',
 'preregistration':'data/research/r4_v0/hourly/r4_management_queue_opportunity_incremental_v1_preregistered.json',
 'decision':art['status'],'actionAuthority':False,
 'primaryResult':{'freshRows':art['coverage']['FRESH24']['rows'],'unseenRows':art['coverage']['UNSEEN24']['rows'],'replicationRows':art['coverage']['REPLICATION3']['rows'],'targetPassCount':art['decision'].get('targetPassCount'),'meanDeltaAuc':art['decision'].get('meanDelta',{}).get('auc'),'meanDeltaAp':art['decision'].get('meanDelta',{}).get('ap'),'meanLogLossImprovement':art['decision'].get('meanDelta',{}).get('logLossImprovement'),'worstAucDelta':art['decision'].get('worstAucDelta'),'seedNoRegression5of5':True},
 'summary':'Queue opportunity was incrementally positive for weak-fill and abs-net-reduction in both independent holdouts, but REPLICATION3 floor-improvement AUC/AP regressed and worst AUC delta -0.0357 breached the preregistered -0.02 stability guard. Reject as broad Management belief routing; no action authority.',
 'retestAllowed':False,
 'nextDistinct':'Do not blend queue_opportunity broadly into Management M0. The most valuable unresolved execution-lifecycle gap remains deterministic per-order receipt-clock fill-frontier labels; after a MATERIAL_DATA_PIPELINE_FIX, reopen only the preregistered continuous remaining-option hypothesis. Otherwise seek a different semantic source rather than another queue score threshold.'
}
r=json.loads(R4REG.read_text(encoding='utf-8'))
if not any((x.get('testId')==TEST or x.get('experimentId')==TEST or x.get('cycleId')==TEST) for x in r.get('experiments',[])):
 r['experiments'].append(entry);R4REG.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
n=json.loads(NOVREG.read_text(encoding='utf-8'))
if not any(x.get('testId')==TEST for x in n.get('tests',[])):
 ne=dict(entry); ne['axis']='R4_MANAGEMENT_EXECUTION_REACHABILITY_INCREMENTAL_ROUTING'; ne['hypothesis']=entry['candidate']; ne['cohort']={'source':'matched FRESH24 train / UNSEEN24 + REPLICATION3 receipt-clock HFT shadows','phase':'60-180s','special20260816Sealed':True,'echtgeldTraining':False}; ne['conclusion']=entry['summary']; n['tests'].append(ne);NOVREG.write_text(json.dumps(n,ensure_ascii=False,indent=2),encoding='utf-8')
block=f'''\n\n## 2026-08-27 16:39 — MANAGEMENT M0 × QUEUE-OPPORTUNITY INCREMENTAL ROUTING V1 — REJECTED\n- Exactly one novel test completed: `{TEST}`. Duplicate audit found prior queue-opportunity tests for PREPARE/need, late protection/cross-readiness, prearm and queue lifecycle, and prior Management M0/progress tests without queue opportunity; no prior test combined frozen queue execution-reachability BELIEF above established 60-180s Management M0 context.\n- Layer assignment: receipt-clock depth/churn = EXECUTION INFORMATION; `queue_opportunity_score` = BELIEF / execution reachability; Management M0 responsibility/lifecycle = MANAGEMENT BELIEF context; portfolio/payoff = LOGIC state; output NOT_ACTION_AUTHORITY.\n- Matched receipt-clock HFT support after 60-180s filter: FRESH24 {art['coverage']['FRESH24']['rows']} rows / {art['coverage']['FRESH24']['markets']} markets, UNSEEN24 {art['coverage']['UNSEEN24']['rows']} / {art['coverage']['UNSEEN24']['markets']}, REPLICATION3 {art['coverage']['REPLICATION3']['rows']} / {art['coverage']['REPLICATION3']['markets']}. FRESH24 trained fixed logistic baseline vs +queue; UNSEEN24 and REPLICATION3 were independent holdouts.\n- Two of three targets passed all AUC/AP/log-loss directions in both holdouts: weak-fill and abs-net-reduction. Across all six comparisons mean dAUC +{art['decision']['meanDelta']['auc']:.4f}, mean dAP +{art['decision']['meanDelta']['ap']:.4f}, mean log-loss improvement +{art['decision']['meanDelta']['logLossImprovement']:.4f}. However floor-improvement on REPLICATION3 regressed (dAUC -0.0357 and dAP -0.0437), breaching the preregistered worst-AUC guard -0.02.\n- Decision: `TESTED_REJECTED`. This does not erase queue-opportunity as an execution-reachability signal; it rejects broad injection into Management M0 because floor-quality transfer is not stable. No threshold/model rescue and no action authority.\n- Seed/no-regression 5/5 PASS. Trainer refresh attempted once and timed out with no new artifact; latest valid 13:39 snapshot remains Acquisition 0.7236 / Preservation 0.6347, frozen Echtgeld exactly 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `data/research/r4_v0/hourly/r4_management_queue_opportunity_incremental_v1.json`; preregistration: `data/research/r4_v0/hourly/r4_management_queue_opportunity_incremental_v1_preregistered.json`; tool: `tools/test_r4_management_queue_opportunity_incremental_v1.py`. Both registries updated.\n- Next distinct gap: do not tune/blend queue opportunity into Management M0. Highest-value unresolved execution-lifecycle gap remains deterministic per-order receipt-clock fill-frontier labels; only after a MATERIAL_DATA_PIPELINE_FIX should the preregistered continuous remaining-executable-option hypothesis be reopened.\n'''
old=HAND.read_text(encoding='utf-8')
if TEST not in old: HAND.write_text(old+block,encoding='utf-8')
PROG.write_text('# R4 Information / Belief Layer Progress V17 — 2026-08-27\n'+block,encoding='utf-8')
print(json.dumps({'ok':True,'status':art['status'],'registryUpdated':True,'handoffUpdated':True,'progress':str(PROG.relative_to(ROOT)).replace('\\','/')},ensure_ascii=False))
