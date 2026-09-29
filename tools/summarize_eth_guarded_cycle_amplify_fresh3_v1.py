from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data'/'research'/'lan_worker_returns'
IDS=[
 'eth-guarded-cycle-amplify-fresh-1917197-20260904-v1',
 'eth-guarded-cycle-amplify-fresh-1917298-20260904-v1',
 'eth-guarded-cycle-amplify-fresh-1917324-20260904-v1',
]
OUT=ROOT/'data'/'research'/'r4_v0'/'p0_provenance_v1'/'ETH_GUARDED_CYCLE_AMPLIFY_FRESH3_RESULT_20260904.json'
rows=[];missing=[]
for jid in IDS:
 p=RET/jid/'result.json'
 if not p.exists(): missing.append(jid); continue
 d=json.loads(p.read_text(encoding='utf-8'))
 if not d.get('rows'): raise SystemExit(f'no rows {jid}')
 r=d['rows'][0]
 rows.append({
  'jobId':jid,'marketId':int(r['marketId']),
  'baseline':r['baseline'],'candidate':r['candidate'],
  'candidateSafety':r['candidateSafety'],'scheduler':r['scheduler'],
  'schedulerOriginAdmissions':int(r.get('schedulerOriginAdmissions') or 0),
  'prospectiveChecks':int(r.get('prospectiveChecks') or 0),
  'prospectiveBlocks':int(r.get('prospectiveBlocks') or 0),
  'decision':d.get('decision'),'gates':d.get('gates'),
 })
if missing:
 print(json.dumps({'complete':False,'missing':missing,'found':len(rows)},indent=2)); raise SystemExit(3)

def sm(side,key): return sum(float(r[side].get(key) or 0) for r in rows)
all_safety=all(all(abs(float(v))<=1e-9 for v in r['candidateSafety'].values()) for r in rows)
agg={
 'markets':len(rows),
 'baselineFills':int(sm('baseline','fills')),'candidateFills':int(sm('candidate','fills')),
 'baselineSubmits':int(sm('baseline','submits')),'candidateSubmits':int(sm('candidate','submits')),
 'baselineRounds':int(sm('baseline','repairExpandRepairRounds')),'candidateRounds':int(sm('candidate','repairExpandRepairRounds')),
 'baselinePnl':sm('baseline','pnlDiagnosticOnly'),'candidatePnl':sm('candidate','pnlDiagnosticOnly'),
 'pnlDelta':sm('candidate','pnlDiagnosticOnly')-sm('baseline','pnlDiagnosticOnly'),
 'baselineFloorSum':sm('baseline','floor'),'candidateFloorSum':sm('candidate','floor'),
 'floorDelta':sm('candidate','floor')-sm('baseline','floor'),
 'baselinePositiveMarkets':sum(float(r['baseline'].get('pnlDiagnosticOnly') or 0)>0 for r in rows),
 'candidatePositiveMarkets':sum(float(r['candidate'].get('pnlDiagnosticOnly') or 0)>0 for r in rows),
 'schedulerOriginAdmissions':sum(r['schedulerOriginAdmissions'] for r in rows),
 'schedulerReevaluations':sum(int(r['scheduler'].get('reevaluations') or 0) for r in rows),
 'prospectiveBlocks':sum(r['prospectiveBlocks'] for r in rows),
 'allSafetyZero':all_safety,
}
gates={
 'allThreeComplete':len(rows)==3,
 'allSafetyZero':all_safety,
 'roundsNonDecreasing':agg['candidateRounds']>=agg['baselineRounds'],
 'fillsNonDecreasing':agg['candidateFills']>=agg['baselineFills'],
 'schedulerExercised':agg['schedulerReevaluations']>0,
 'legalSchedulerAdmissionExercised':agg['schedulerOriginAdmissions']>0,
}
if all(gates.values()): decision='PASS_GUARDED_CYCLE_AMPLIFICATION_FRESH3_TO_LARGER_FUNCTIONAL'
elif all_safety and agg['schedulerOriginAdmissions']==0: decision='SAFE_BUT_ACTION_SUPPORT_INCONCLUSIVE'
else: decision='REJECT_OR_DIAGNOSE_GUARDED_CYCLE_AMPLIFICATION'
out={'version':'ETH_GUARDED_CYCLE_AMPLIFY_FRESH3_RESULT','date':'2026-09-04','researchOnly':True,'decision':decision,'aggregate':agg,'gates':gates,'rows':rows,'boundary':['fresh functional cohort','realistic HFT only','PnL diagnostic/post-hoc only','no threshold/qty/price tuning','no 8781']}
OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out,indent=2,ensure_ascii=False))
