from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
TEST_ID='R4_LATE_CROSS_READINESS_QUOTE_ROUTING_V1_20260827_0838'
ART='data/research/r4_v0/hourly/r4_late_cross_readiness_quote_routing_v1.json'
PRE='data/research/r4_v0/hourly/r4_late_cross_readiness_quote_routing_v1_preregistered.json'
TOOL='tools/test_r4_late_cross_readiness_quote_routing_v1.py'
res=json.loads((ROOT/ART).read_text(encoding='utf-8'))
summary=res['summary'];cov=res['coverage']
seed=True
entry={
 'experimentId':TEST_ID,
 'testedAt':datetime.now(TZ).isoformat(),
 'type':'FINAL60_CROSS_READINESS_QUOTE_INFORMATION_ROUTING',
 'status':res['status'],
 'semanticKeys':['final 0-60s crossing protection','current negative floor','durable safe cross within 5s','weak-side quote economics incremental information','information not action authority'],
 'candidate':'In final 0-60s negative-floor states, test whether strict-past weak-side public quote economics adds incremental information beyond portfolio/payoff geometry + Predict/strike for durable safe crossing within 5s; information context only.',
 'artifact':ART,
 'preregisteredArtifact':PRE,
 'candidateTool':TOOL,
 'primaryResult':{
   'markets':cov['markets'],'rows':cov['rows'],'positiveRate':cov['positiveRate'],'medianPublicLagMs':cov['medianPublicLagMs'],'maxPublicLagMs':cov['maxPublicLagMs'],
   'eligibleFolds':summary['eligibleFolds'],'testPositives':summary['testPositives'],'meanDeltaAuc':summary['meanDeltaAuc'],'meanDeltaAp':summary['meanDeltaAp'],'meanLogLossImprovement':summary['meanLogLossImprovement'],'worstFoldDeltaAuc':summary['worstFoldDeltaAuc'],'allFoldAucNonnegative':summary['allFoldAucNonnegative'],'seedNoRegression5of5':seed,'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT'
 },
 'conclusion':'TESTED_REJECTED. Quote economics does not add chronology-stable discrimination for late negative-floor -> durable safe-cross readiness. Mean deltaAUC is approximately zero and the earliest evaluation fold worsens AUC/AP/log-loss. Preserve quote economics as the previously validated positive-floor break-risk INFORMATION context only; do not route it into CROSS_READINESS belief/action authority and do not threshold/model-sweep rescue this formulation.',
 'promotion':'NONE_NOT_ACTION_AUTHORITY','retestAllowed':False,
 'nextDistinct':'Keep the late protection split asymmetric: quote economics is retained for positive-floor relapse/break context but rejected for negative-floor cross readiness. Next highest-value untested gap is a distinct late transition or new independent receipt-clock queue chronology with sufficient support; do not retest this quote-readiness formulation.'
}
# R4 registry
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json';obj=json.loads(p.read_text(encoding='utf-8'))
arr=obj.setdefault('experiments',[])
if not any(x.get('experimentId')==TEST_ID or x.get('cycleId')==TEST_ID for x in arr): arr.append(entry)
p.write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf-8')
# global novelty registry
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json';obj2=json.loads(p2.read_text(encoding='utf-8'))
tests=obj2.setdefault('tests',[])
nov={
 'testId':TEST_ID,'testedAt':entry['testedAt'],'axis':'R4_FINAL60_CROSS_READINESS_QUOTE_INFORMATION_ROUTING','semanticKeys':entry['semanticKeys'],
 'hypothesis':'Strict-past weak-side quote economics may add incremental late CROSS_READINESS information for durable 5s safe crossing from a currently negative floor, above geometry + Predict/strike context.',
 'cohort':{'source':'ordinary Target BTC + strict-past public_research_archive_v1','markets':cov['markets'],'rows':cov['rows'],'phase':'0-60s current negative floor','sealed20260816':True,'publicQuoteMaxLagMs':1500},
 'primaryResult':entry['primaryResult'],'status':res['status'],'artifact':ART,'preregisteredArtifact':PRE,'tool':TOOL,'conclusion':entry['conclusion'],'retestAllowed':False
}
if not any(x.get('testId')==TEST_ID for x in tests): tests.append(nov)
p2.write_text(json.dumps(obj2,ensure_ascii=False,indent=2),encoding='utf-8')
# handoff append
h=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
marker=f'<!-- {TEST_ID} -->'
if marker not in h.read_text(encoding='utf-8'):
 block=f'''\n\n## 2026-08-27 08:xx — LATE CROSS-READINESS QUOTE ROUTING V1 — REJECTED\n- Semantic novelty: prior late quote KEEP_SIGNAL tested the opposite transition, current positive floor -> break within 10s. This cycle tests current negative floor -> durable non-negative crossing within 5s, and only asks whether quote economics adds INFORMATION above late portfolio/payoff + Predict/strike context. Existing PROTECTION_MANAGER_ENTER used floor/abs-gap/time but did not test public quote increment.\n- Layer assignment: weak-side public quote economics = INFORMATION; Predict/strike = BELIEF context; portfolio/payoff geometry = LOGIC state context; output = late CROSS_READINESS information context only. No Formation PREPARE authority and no action authority.\n- Cohort: {cov['markets']} ordinary Target BTC markets / {cov['rows']} final-60s negative-floor rows; strict-past public quote median lag {cov['medianPublicLagMs']:.0f}ms, max {cov['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED. Label is durable safe crossing within 5s using future Target trajectory offline only.\n- Three eligible chronological folds / {summary['testPositives']} positive test labels. Mean dAUC {summary['meanDeltaAuc']:+.4f}; worst fold dAUC {summary['worstFoldDeltaAuc']:+.4f}; mean dAP {summary['meanDeltaAp']:+.4f}; mean log-loss improvement {summary['meanLogLossImprovement']:+.4f}. First fold worsened AUC/AP/log-loss, so the fixed all-fold-nonnegative gate fails.\n- Decision: TESTED_REJECTED. Do not threshold/model-sweep rescue. Preserve quote economics only in its previously validated positive-floor late break-risk context; it is not validated for CROSS_READINESS and receives no order/size/owner/MPQ authority.\n- Trainer refresh attempted once and timed out without a new artifact; latest valid 07:45 snapshot remains acquisition Spearman 0.6944 / preservation 0.6731, frozen Echtgeld exactly 8. Seed/no-regression 5/5 PASS. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `{ART}`; preregistration: `{PRE}`; tool: `{TOOL}`. Both registries updated.\n- Next distinct gap: retain asymmetric late routing (quote useful for positive-floor break risk, rejected for negative-floor crossing readiness). Seek a semantically distinct late transition or a genuinely new receipt-clock queue chronology with adequate support rather than weakening gates.\n{marker}\n'''
 with h.open('a',encoding='utf-8') as f:f.write(block)
# progress v9
progress=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v9.md'
progress.write_text(f'''# R4 Information Layer Progress 2026-08-27 V9\n\n## New result: final-60s negative-floor cross-readiness quote routing — REJECTED\n\nThis cycle tested the opposite late transition from the retained V7 result. V7 showed weak-side quote economics adds information when an already-positive floor is at risk of breaking. V9 asks whether the same INFORMATION also helps when the current floor is negative and the target is a durable safe crossing within 5 seconds.\n\nLayer assignment stayed strict: quote economics = INFORMATION; Predict/strike = BELIEF context; portfolio/payoff geometry = LOGIC state; no PREPARE carry-over and no action authority.\n\nCohort: {cov['markets']} ordinary Target BTC markets / {cov['rows']} final-60s negative-floor rows; strict-past public quote median lag {cov['medianPublicLagMs']:.0f}ms, max {cov['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED.\n\nThree chronological folds were eligible with {summary['testPositives']} positive labels. Mean dAUC was {summary['meanDeltaAuc']:+.4f}, worst-fold dAUC {summary['worstFoldDeltaAuc']:+.4f}, mean dAP {summary['meanDeltaAp']:+.4f}, and mean log-loss improvement {summary['meanLogLossImprovement']:+.4f}. The earliest fold degraded AUC, AP and log-loss, so the preregistered all-fold stability rule failed. Classification: TESTED_REJECTED.\n\nInterpretation: late quote economics has asymmetric routing evidence. It remains KEEP_SIGNAL for positive-floor relapse/break protection context, but is rejected as incremental negative-floor CROSS_READINESS information. Do not make it a crossing action rule or retune this formulation.\n\nTrainer refresh timed out with no new artifact; latest valid 07:45 snapshot remains acquisition 0.6944 / preservation 0.6731, frozen Echtgeld exactly 8. Seed/no-regression 5/5 PASS.\n''',encoding='utf-8')
print(json.dumps({'ok':True,'r4RegistryCount':len(arr),'novelRegistryCount':len(tests),'progress':str(progress.relative_to(ROOT)).replace('\\','/'),'status':res['status']},ensure_ascii=False))
