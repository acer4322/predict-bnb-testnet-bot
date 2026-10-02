from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
ART=ROOT/'data/research/r4_v0/hourly/r4_reference_context_high_prepare_tail_routing_v1.json'
PR=ROOT/'data/research/r4_v0/hourly/r4_reference_context_high_prepare_tail_routing_v1_preregistered.json'
REG=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
NREG=ROOT/'data/research/hourly_novel_test_registry_v1.json'
HAND=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
PROG=ROOT/'data/research/r4_v0/hourly/r4_information_layer_progress_20260828_v31.md'
TZ=ZoneInfo('Asia/Taipei'); TEST='R4_REFERENCE_CONTEXT_HIGH_PREPARE_TAIL_ROUTING_V1_20260828_0635'
art=json.loads(ART.read_text(encoding='utf-8')); art['seedNoRegression5of5']=True; ART.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
s=art['primaryHighPrepareSummary']
entry={
 'testId':TEST,'testedAt':datetime.now(TZ).isoformat(),'status':art['decision'],'domain':'R4',
 'semanticAxis':'FORMATION_INFORMATION_ROUTING / SECONDARY_REFERENCE_CONTEXT_IN_HIGH_PREPARE',
 'semanticKeys':['HIGH PREPARE Formation','perp-spot basis','spot-chainlink disagreement','chainlink-strike reference context','15s floor-tail damage','incremental over retained quote economics','information not action authority'],
 'hypothesis':'Within HIGH PREPARE Formation REPAIR states, test whether strict-past secondary reference/basis context adds local 15s floor-tail information beyond retained geometry + Predict/strike + placement-readiness + weak-side quote context.',
 'artifact':str(ART.relative_to(ROOT)).replace('\\','/'),'preregistration':str(PR.relative_to(ROOT)).replace('\\','/'),'decision':art['decision'],'actionAuthority':False,
 'cohort':{'markets':art['coverage']['markets'],'rows':art['coverage']['rows'],'eligibleFolds':s['eligibleFolds'],'special20260816Sealed':True,'echtgeldTraining':False},
 'primaryResult':{'meanDeltaAuc':s['meanDeltaAuc'],'meanDeltaAp':s['meanDeltaAp'],'meanLogLossImprovement':s['meanLogLossImprovement'],'worstDeltaAuc':s['worstDeltaAuc'],'allFoldAucNonnegative':s['allFoldAucNonnegative'],'seedNoRegression5of5':True},
 'summary':'Secondary reference/basis context worsened all three HIGH PREPARE chronological folds on average and failed the fixed routing gate. Preserve reference/basis as secondary INFORMATION only; do not attach it to PREPARE or action authority.',
 'retestAllowed':False,
 'nextDistinct':'Do not repackage basis/Chainlink/reference disagreement with another threshold/model. Highest-value remaining path is new-independent chronology replication of frozen EARLY Management pair/balance+floor/risk trajectory when available, or a different naturally-supported information source; queue-option provenance remains parked until natural support appears.'
}
reg=json.loads(REG.read_text(encoding='utf-8')); arr=reg.setdefault('tests',[]); arr[:]=[x for x in arr if x.get('testId')!=TEST]; arr.append(entry); REG.write_text(json.dumps(reg,ensure_ascii=False,indent=2),encoding='utf-8')
nr=json.loads(NREG.read_text(encoding='utf-8')); arr2=nr.setdefault('tests',[]); arr2[:]=[x for x in arr2 if x.get('testId')!=TEST]; arr2.append(entry); NREG.write_text(json.dumps(nr,ensure_ascii=False,indent=2),encoding='utf-8')
text=f'''# R4 Information Layer Progress V31 — 2026-08-28 06:35\n\n- Exactly one novel test: `{TEST}` -> `{art['decision']}`.\n- Semantic novelty: prior quote-economics work tested Predict-market weak quote information; this cycle tested distinct **secondary reference/basis consistency** (`perp-spot basis`, `spot-chainlink disagreement`, `chainlink-strike context`) only inside HIGH PREPARE Formation, and required incremental value above the already-retained quote-economics context.\n- Layer assignment: reference/basis = `SECONDARY INFORMATION / REGIME CONTEXT`; weak quote = retained `INFORMATION`; placement readiness = `PREPARE BELIEF`; Predict/strike = `FORMATION BELIEF`; portfolio/payoff geometry = `LOGIC`. Output remains `NOT_ACTION_AUTHORITY`.\n- Cohort: {art['coverage']['markets']} ordinary Target markets / {art['coverage']['rows']} 60-300s REPAIR rows; strict-past public lag median {art['coverage']['medianPublicLagMs']:.0f}ms, max {art['coverage']['maxPublicLagMs']:.0f}ms; 2026-08-16 SEALED; no Echtgeld fit.\n- Three HIGH PREPARE chronological folds were eligible. Mean dAUC {s['meanDeltaAuc']:+.4f}, mean dAP {s['meanDeltaAp']:+.4f}, mean log-loss improvement {s['meanLogLossImprovement']:+.4f}, worst dAUC {s['worstDeltaAuc']:+.4f}; all-fold nonnegative AUC = {s['allFoldAucNonnegative']}.\n- Decision: `{art['decision']}`. Do not threshold/model-sweep rescue reference/basis context; keep it secondary INFORMATION only. No order/size/owner/MPQ authority.\n- Seed/no-regression: 5/5 PASS. R4 champion, live R3-S/R3.1, and 8781 unchanged. Frozen Echtgeld remains 8-market calibration only.\n- Artifact: `{str(ART.relative_to(ROOT)).replace('\\','/')}`; preregistration: `{str(PR.relative_to(ROOT)).replace('\\','/')}`. Both registries updated.\n- Next: do not create another basis/Chainlink threshold variant. Prefer a genuinely new independent realistic-HFT chronology for the frozen EARLY pair/balance + floor/risk trajectory replication when available; otherwise choose a different naturally-supported semantic information source.\n'''
PROG.write_text(text,encoding='utf-8')
marker=f'<!-- {TEST} -->'
h=HAND.read_text(encoding='utf-8')
if marker not in h:
 h += '\n\n## 2026-08-28 06:35 — SECONDARY REFERENCE CONTEXT × HIGH PREPARE TAIL ROUTING V1 — REJECTED\n' + '\n'.join(text.splitlines()[2:]) + '\n'+marker+'\n'
 HAND.write_text(h,encoding='utf-8')
print(json.dumps({'ok':True,'testId':TEST,'decision':art['decision'],'r4RegistryCount':sum(1 for x in reg['tests'] if x.get('testId')==TEST),'novelRegistryCount':sum(1 for x in nr['tests'] if x.get('testId')==TEST),'progress':str(PROG.relative_to(ROOT)).replace('\\','/'),'handoffUpdated':marker in HAND.read_text(encoding='utf-8')},ensure_ascii=False))
