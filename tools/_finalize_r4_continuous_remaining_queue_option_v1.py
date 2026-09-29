from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TEST_ID='R4_CONTINUOUS_REMAINING_QUEUE_OPTION_V1_20260827_1438'
ART='data/research/r4_v0/hourly/r4_continuous_remaining_queue_option_v1.json'
PRE='data/research/r4_v0/hourly/r4_continuous_remaining_queue_option_v1_preregistered.json'
entry={
 'testId':TEST_ID,'testedAt':'2026-08-27T14:46:11.142300+08:00','status':'TESTED_INCONCLUSIVE',
 'semanticAxis':'EXECUTION_LIFECYCLE / CONTINUOUS_REMAINING_EXECUTABLE_OPTION',
 'semanticKeys':['expected confirmed remaining shares','5s queue option realization fraction','actual queue progress','responsibility established','continuous calibration'],
 'artifact':ART,'preregistration':PRE,'decision':'TESTED_INCONCLUSIVE','actionAuthority':False,
 'primaryResult':{'sourceEligibleRows':32,'firstAttemptReproducedRows':13,'firstAttemptPositive5sRows':0,'firstAttemptAllRealizedFractionsZero':True,'resumeRowsAttempted':6,'resumeRowsWithLabels':0,'resumeRowsNullLabels':6,'modelFit':False,'seedNoRegression5of5':True,'trainerRefresh':'TIMEOUT_NO_NEW_ARTIFACT','latestValidAcquisitionSpearman':0.7236022923373205,'latestValidPreservationSpearman':0.6347121087383016},
 'summary':'Continuous remaining-executable-option target was not reproducibly identifiable from the current queue-option replay path. First 13 reproduced labels were all zero; a resume on 6 later source-eligible markets yielded null candidateChildKeepLabels for all 6. Preregistered support/reproducibility rule failed before model fitting.',
 'conclusion':'TESTED_INCONCLUSIVE. Do not change horizon/model or infer KEEP/PULL/REPRICE authority. Reopen only after a material data-pipeline fix that materializes deterministic receipt-clock per-order fill-frontier labels keyed by stable order identity/candidate checkpoint.',
 'retestAllowed':'ONLY_MATERIAL_DATA_PIPELINE_FIX_OR_NEW_INDEPENDENT_CHRONOLOGY',
 'nextDistinct':'Build deterministic per-order receipt-clock fill-frontier labels without rerunning controller state. If unavailable, move to a different execution-lifecycle semantic rather than proxying continuous shares from action outcomes.'
}
for rel,key in [('data/research/r4_v0/r4_hourly_experiment_registry_v1.json','experiments'),('data/research/hourly_novel_test_registry_v1.json','tests')]:
 p=ROOT/rel; d=json.loads(p.read_text(encoding='utf-8')); arr=d[key]
 if not any(str(x.get('testId') or x.get('experimentId') or '')==TEST_ID for x in arr): arr.append(entry)
 p.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8')

section='''\n\n## 2026-08-27 14:46 — CONTINUOUS REMAINING QUEUE OPTION V1 — INCONCLUSIVE\n- Exactly one novel test completed: `R4_CONTINUOUS_REMAINING_QUEUE_OPTION_V1_20260827_1438`.\n- Semantic novelty: prior remaining-queue-option R4 test was a binary KEEP-vs-reinsert joint-quality classifier. This test instead preregistered a continuous execution resource: `confirmed fill shares in next 5s / strict-past remaining shares` for the same already-live resting recovery child. No action comparison or action coupling.\n- Layer assignment: queue depth/depletion = EXECUTION INFORMATION; continuous remaining executable shares = EXECUTION_LIFECYCLE BELIEF candidate; responsibility/portfolio = LOGIC context; NOT_ACTION_AUTHORITY.\n- Source cohort contained 32 previously eligible realistic-HFT queue-option markets. First bounded replay reached 13 markets before timeout; all 13 reproduced 5s confirmed-fill fractions were exactly 0. A resume skipped those 13 and started only on unrun source-eligible markets; 6/6 consecutive markets returned `candidateChildKeepLabels=null` before the second bounded timeout.\n- Fixed preregistration said label non-reproducibility/support failure => INCONCLUSIVE before model fit. Therefore no Ridge model was fit, no threshold/model rescue was attempted, and no KEEP/PULL/REPRICE inference is valid.\n- Decision: `TESTED_INCONCLUSIVE` due deterministic-label identifiability/replay failure, not evidence that queue progress lacks value. Reopen this exact hypothesis only after MATERIAL_DATA_PIPELINE_FIX or new independent chronology.\n- Broad stability: seed/no-regression 5/5 PASS. Trainer refresh timed out with no new artifact; latest valid snapshot remains 13:39, Acquisition Spearman 0.7236 / Preservation 0.6347, frozen Echtgeld exactly 8. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifacts: `data/research/r4_v0/hourly/r4_continuous_remaining_queue_option_v1.json`, preregistration `data/research/r4_v0/hourly/r4_continuous_remaining_queue_option_v1_preregistered.json`. Both registries updated.\n- Next highest-value gap: materialize a deterministic receipt-clock per-order fill-frontier label table keyed by stable order identity + candidate checkpoint, so future confirmed remaining shares can be read offline without rerunning controller replay. Only then may a predeclared material-pipeline-fix replication reopen this continuous-option hypothesis. If such a table cannot be made reliable, move to a different execution-lifecycle semantic rather than constructing another proxy.\n<!-- R4_CONTINUOUS_REMAINING_QUEUE_OPTION_V1_20260827_1438 -->\n'''
h=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'; txt=h.read_text(encoding='utf-8')
if TEST_ID not in txt: h.write_text(txt+section,encoding='utf-8')
prog=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v15.md'
prog.write_text('# R4 Information Layer Progress V15\n'+section,encoding='utf-8')
print(json.dumps({'ok':True,'registriesUpdated':True,'handoffUpdated':True,'progress':str(prog.relative_to(ROOT)).replace('\\\\','/')},ensure_ascii=False))
