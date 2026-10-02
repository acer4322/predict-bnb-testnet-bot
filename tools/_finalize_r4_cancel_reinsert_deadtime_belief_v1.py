from pathlib import Path
import json
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
TZ=ZoneInfo('Asia/Taipei')
now=datetime.now(TZ).isoformat()
test_id='R4_CANCEL_REINSERT_DEADTIME_BELIEF_V1_20260827_1537'
art='data/research/r4_v0/hourly/r4_cancel_reinsert_deadtime_belief_v1.json'
pre='data/research/r4_v0/hourly/r4_cancel_reinsert_deadtime_belief_v1_preregistered.json'
# R4 registry
p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'; d=json.load(open(p,encoding='utf-8'))
entry={'cycleId':'R4_HOURLY_20260827_1537','type':'CANCEL_REINSERT_DEADTIME_BELIEF','status':'TESTED_REJECTED','semanticKeys':['cancel ack deadtime','cancel-reinsert transition latency','execution lifecycle belief','strict-past latency calibration'],'change':'Predict cancel-request to replacement-submit dead-time from strict-past order/market state versus rolling historical-median baseline; prediction-only, no execution authority.','artifact':art,'preregistration':pre,'promotion':'REJECTED_NOT_ACTION_AUTHORITY','retestAllowedOnly':['NEW_INDEPENDENT_CHRONOLOGY','MATERIAL_DATA_PIPELINE_FIX','PREDECLARED_REPLICATION']}
if not any(x.get('cycleId')==entry['cycleId'] or x.get('artifact')==art for x in d['experiments']): d['experiments'].append(entry)
p.write_text(json.dumps(d,indent=2),encoding='utf-8')
# global novelty ledger
p2=ROOT/'data/research/hourly_novel_test_registry_v1.json'; n=json.load(open(p2,encoding='utf-8'))
e2={'testId':test_id,'testedAt':now,'axis':'CANCEL_REINSERT_TRANSITION_LATENCY','semanticKeys':['cancel ack deadtime','reinsert latency','execution lifecycle belief','strict-past state'],'status':'TESTED_REJECTED','artifact':art,'preregistration':pre,'notes':'Distinct from fixed-latency queue-value policy and prior R4 queue-progress/fill-share targets; predicts transition latency itself. OOS Ridge worsened MAE vs rolling historical median and had negative Spearman.'}
if not any(x.get('testId')==test_id for x in n['tests']): n['tests'].append(e2)
p2.write_text(json.dumps(n,indent=2),encoding='utf-8')
# handoff
h=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
section=f'''\n\n## 2026-08-27 15:37 — CANCEL->REINSERT DEADTIME BELIEF V1 — REJECTED\n- Exactly one novel test completed: `{test_id}`.\n- Semantic novelty: prior R2 queue-value work treated observed ~2.3s cancel/reinsert dead-time as a fixed policy constant; prior R4 queue tests studied queue progress, KEEP-vs-reinsert joint quality, and future fill shares. This test predicts transition latency itself and has no KEEP/PULL/REPRICE or fill-share target.\n- Layer assignment: strict-past order/market state = EXECUTION INFORMATION; predicted cancel->replacement dead-time = EXECUTION_LIFECYCLE BELIEF candidate; responsibility/portfolio = LOGIC context; NOT_ACTION_AUTHORITY.\n- Cohort: 29 eligible non-live realistic-HFT counterfactual rows with reproducible cancelRequestedAtMs/reinsertedAtMs; expanding chronology produced 17 OOS predictions. No Echtgeld fitting; 2026-08-16 remains SEALED by source cohort.\n- Actual dead-time ranged 2041-2604ms (median 2343ms). Rolling historical-median baseline MAE 161.32ms; strict-past Ridge candidate MAE 168.00ms (improvement -4.14%); Spearman -0.103. EARLY block MAE improvement -8.69%; LATE -0.06%. Fixed support gate passed, but both overall MAE and rank discrimination worsened.\n- Decision: `TESTED_REJECTED`. Do not model/threshold-sweep rescue or turn this into execution authority. Current evidence favors treating cancel/reinsert dead-time as a relatively stable runtime/venue constant rather than a state-conditioned R4 belief.\n- Seed/no-regression: 5/5 PASS. Champion/live R3-S/R3.1/8781 unchanged.\n- Artifact: `{art}`; preregistration: `{pre}`; tool: `tools/test_r4_cancel_reinsert_deadtime_belief_v1.py`. Both registries updated.\n- Next highest-value distinct gap: build a deterministic receipt-clock per-order fill-frontier table as a MATERIAL_DATA_PIPELINE_FIX, then only if label reproducibility is restored reopen the preregistered continuous remaining-executable-option hypothesis. If not, move away from queue-option proxy families rather than inventing another latency/age classifier.\n'''
with open(h,'a',encoding='utf-8') as f:f.write(section)
# progress V16
prog=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260827_v16.md'
prog.write_text('# R4 Information / Belief Layer Progress V16 — 2026-08-27\n\n'+section.strip()+'\n',encoding='utf-8')
print(json.dumps({'ok':True,'r4RegistryCount':len(d['experiments']),'novelRegistryCount':len(n['tests']),'progress':str(prog.relative_to(ROOT)).replace('\\\\','/')}))
