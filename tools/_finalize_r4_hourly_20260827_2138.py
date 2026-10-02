from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
ROOT=Path(__file__).resolve().parents[1]
TEST_ID='R4_MANAGEMENT_TRANSITION_DISAGREEMENT_UNCERTAINTY_V1_20260827_2138'
ART='data/research/r4_v0/hourly/r4_management_transition_disagreement_uncertainty_v1.json'
PRE='data/research/r4_v0/hourly/r4_management_transition_disagreement_uncertainty_v1_preregistered.json'
TOOL='tools/test_r4_management_transition_disagreement_uncertainty_v1.py'
res=json.loads((ROOT/ART).read_text(encoding='utf-8'))
entry={
 'testId':TEST_ID,'testedAt':'2026-08-27T21:38:00+08:00','status':'TESTED_REJECTED','domain':'R4',
 'semanticAxis':'MANAGEMENT / PARALLEL_TRANSITION_BELIEF_UNCERTAINTY_CALIBRATION',
 'semanticKeys':['transition specialist disagreement','management uncertainty context','flat manager error detection','chronological calibration','not consensus override'],
 'hypothesis':'Test whether strict-past disagreement among OOS transition specialists adds incremental calibration information about existing flat-manager error risk beyond manager confidence and management state.',
 'artifact':ART,'preregistration':PRE,'tool':TOOL,'decision':'TESTED_REJECTED','actionAuthority':False,
 'cohort':res['cohort'],'primaryResult':res['summary'],
 'summary':'Three independent evaluation blocks all had ample error/correct support, but transition-specialist disagreement p_transition_std added essentially zero error-detection information: mean deltaAUC about -0.00008, mean log-loss and Brier also worsened slightly. Reject disagreement as a standalone Management uncertainty belief; do not use it for override/abstention/action authority.',
 'retestAllowed':False,
 'nextDistinct':'Do not repackage transition specialist consensus/disagreement as another manager router. Highest-value next semantic gap: test whether the already-kept transition-only signals (operational stall, realized geometry trajectory, queue context) improve a shared continuous/local path-quality state when kept role-separated, or identify a different naturally supported information source; keep queue-option provenance replication closed until natural candidate support appears.'
}
for rel in ['data/research/r4_v0/r4_hourly_experiment_registry_v1.json','data/research/hourly_novel_test_registry_v1.json']:
 p=ROOT/rel; d=json.loads(p.read_text(encoding='utf-8')); arr=d.setdefault('tests',[])
 arr=[x for x in arr if x.get('testId')!=TEST_ID]; arr.append(entry); d['tests']=arr
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

handoff=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
text=handoff.read_text(encoding='utf-8')
block=f'''\n\n## 2026-08-27 21:38 — MANAGEMENT TRANSITION DISAGREEMENT UNCERTAINTY V1 — REJECTED\n- Exactly one novel bounded R4 test completed: `{TEST_ID}`; preregistered before execution.\n- Semantic novelty: prior specialist-consensus work tested whether agreement should override Management output. This test never changes the manager output; it asks whether disagreement itself is a calibrated uncertainty BELIEF for detecting when the existing flat manager is wrong.\n- Layer assignment: specialist transition probabilities = MANAGEMENT BELIEF; `p_transition_std` = MANAGEMENT UNCERTAINTY BELIEF candidate; flat-manager probabilities + portfolio/lifecycle state = MANAGEMENT/LOGIC context; output = CALIBRATION_CONTEXT_ONLY_NOT_ACTION_AUTHORITY.\n- Cohort: existing strict-OOS Management stacking table; block 1 train, blocks 2/3/4 independent chronological evaluation. All three support gates passed: block errors/correct = 268/282, 341/364, 302/373. 2026-08-16 remains SEALED; no Echtgeld fit/ingestion.\n- Result: mean deltaAUC `{res['summary']['meanDeltaAuc']:.6f}`, worst deltaAUC `{res['summary']['worstDeltaAuc']:.6f}`, mean deltaAP `{res['summary']['meanDeltaAp']:.6f}`, mean log-loss improvement `{res['summary']['meanLogLossImprovement']:.6f}`, mean Brier improvement `{res['summary']['meanBrierImprovement']:.6f}`. Manager-error and correct rows had nearly identical transition-disagreement levels in every block.\n- Decision: `TESTED_REJECTED`. Do not threshold/model-sweep rescue, do not turn disagreement into consensus/abstention override, and grant no action/order/size/owner/MPQ authority.\n- Seed/no-regression: 5/5 PASS. Champion/live R3-S/R3.1/8781 unchanged; frozen Echtgeld remains untouched.\n- Artifact: `{ART}`; preregistration: `{PRE}`; tool: `{TOOL}`. Both registries updated.\n- Next distinct gap: do not repackage specialist consensus/disagreement. Prefer a role-separated Management quality-state experiment using already-kept transition-only signals, or a different naturally supported information source. Queue-option provenance remains waiting for natural independent candidate support.\n'''
if TEST_ID not in text: handoff.write_text(text+block,encoding='utf-8')
progress=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v22.md'
progress.write_text('# R4 Information / Belief Layer Progress V22 — 2026-08-27\n'+block,encoding='utf-8')
print(json.dumps({'ok':True,'testId':TEST_ID,'registriesUpdated':2,'handoffUpdated':True,'progress':str(progress.relative_to(ROOT)).replace('\\','/')}))
