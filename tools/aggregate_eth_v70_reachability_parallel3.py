from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
RET=ROOT/'data/research/lan_worker_returns'
JOBS=['eth-v70-reach-m1823769-v1','eth-v70-reach-m1827223-v1','eth-v70-reach-m1827903-v1']
rows=[]
for jid in JOBS:
 p=RET/jid/'result.json'
 x=json.loads(p.read_text(encoding='utf-8'))
 if len(x.get('rows') or [])!=1:raise RuntimeError(f'{jid}: expected one row')
 rows.extend(x['rows'])
z=[q for x in rows for q in x['reachability']]
agg={'markets':len(rows),'fallbackClocksCovered':len(z),'marketsWithFallbackClock':sum(bool(x['reachability']) for x in rows),'globalSupersedeBlocks':sum(int(x['candidate']['v65GlobalSupersedeBlocks']) for x in rows),'sameSideRepairAtFallback':sum(q['sameSideRepair'] for q in z),'dualResponsibilityReachable':sum(q['dualResponsibilityReachable'] for q in z),'readyWithoutRepairHandoff':sum(q['readyWithoutRepairHandoff'] for q in z),'requiresRepairCarrierHandoff':sum(q['requiresRepairCarrierHandoff'] for q in z),'rescuesSubminimumRepair':sum(q['rescuesSubminimumRepair'] for q in z),'sideMismatch':sum(q['repairSide'] is not None and not q['sameSideRepair'] for q in z),'noRepairResponsibility':sum(q['repairParentId'] is None or q['repairGap']<=1e-9 for q in z),'noExpandOverflowAfterRepair':sum(q['sameSideRepair'] and q['expandOverflowQty']<=1e-9 for q in z),'truthMismatch':sum(float(x['candidate'].get('authorizedSubmitWithTruthRoleMismatch') or 0) for x in rows),'overOwned':sum(float(x['candidate'].get('overOwnedSubmitViolations') or 0) for x in rows),'repairDrift':sum(float(x['candidate'].get('repairToExpandAtFirstFill') or 0) for x in rows),'responsibilityOverfill':sum(float(x['candidate'].get('v51ResponsibilityOverfill') or 0) for x in rows)}
gates={'allThreeMarketsCompleted':agg['markets']==3,'existingV65FallbackClockCovered':agg['fallbackClocksCovered']>0,'zeroTruthMismatch':agg['truthMismatch']==0,'zeroOverOwned':agg['overOwned']==0,'zeroRepairDrift':agg['repairDrift']==0,'zeroResponsibilityOverfill':agg['responsibilityOverfill']<=1e-9}
decision='PROCEED_ONLY_ON_OBSERVED_REACHABLE_MARKET_TO_COMPOSITE_FUNCTIONAL_SMOKE' if agg['dualResponsibilityReachable']>0 else 'REJECT_V64_FALLBACK_CLOCK_AS_COMPOSITE_BIND_POINT_USE_OUR_REPAIR_DECISION_PARENT_COUNTERFACTUAL_NEXT'
out={'version':'ETH_REPAIR_V70_COMPOSITE_CARRIER_REACHABILITY_SMOKE_PARALLEL3_AGGREGATE','date':'2026-09-03','researchOnly':True,'behaviorChangeRelativeToV65':False,'actionAuthority':False,'aggregate':agg,'gates':gates,'functionalPass':all(gates.values()),'decision':decision,'rows':rows,'sourceJobs':JOBS,'boundary':['Three single-market HFT lanes aggregated to reproduce the preregistered fixed 3-market smoke.','Individual single-market allThreeMarketsCompleted gates are intentionally ignored; aggregate gate is authoritative.','No composite submit; V65 behavior unchanged.','No Target-clock authority, threshold/qty/delay tuning, H100, 8781 or dream fill.']}
op=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_V70_COMPOSITE_CARRIER_REACHABILITY_SMOKE_20260903.json';op.write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8');print(json.dumps({'ok':True,'output':str(op.relative_to(ROOT)),'functionalPass':out['functionalPass'],'decision':decision,'aggregate':agg,'gates':gates},ensure_ascii=False))
