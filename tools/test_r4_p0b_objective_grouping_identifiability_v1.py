from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
SRC=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json'

def main():
 d=json.loads(SRC.read_text(encoding='utf-8')); rows=d.get('rows') or []
 keep=[r for r in rows if r.get('knownRole') in {'PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING'}]
 out=[]
 for r in keep:
  sp=r.get('candidateStrictPast') or {}; maps=(r.get('creditAudit') or {}).get('mappings') or []
  target_logicals={str(m.get('targetLogical')) for m in maps if m.get('targetLogical') is not None}
  roots=sp.get('candidateSideRoots') or []
  visible_targets=[x for x in roots if str(x.get('logical')) in target_logicals]
  unresolved=[x for x in roots if float(x.get('unresolvedQty') or 0)>1e-9]
  pending=[x for x in roots if float(x.get('reservedCommitment') or 0)>1e-9]
  out.append({
   'marketId':r.get('marketId'),'candidateKey':r.get('candidateKey'),'groundTruth':'SAME_OBJECTIVE' if r.get('knownRole')=='PREPOSITION_REPAIR_SUBSTITUTE' else 'DIFFERENT_OBJECTIVE',
   'targetLifecycleClasses':[m.get('targetLifecycleClass') for m in maps],
   'explicitTargetVisibleAtCandidate':bool(visible_targets),
   'visibleTargetCount':len(visible_targets),'sameSideRootCount':len(roots),'sameSideUnresolvedRootCount':len(unresolved),'sameSideReservedRootCount':len(pending),
   'currentResidualAfterReservation':sp.get('currentResidualAfterReservation'),'activePendingIntentCount':sp.get('activePendingIntentCount')
  })
 same=[x for x in out if x['groundTruth']=='SAME_OBJECTIVE']; diff=[x for x in out if x['groundTruth']=='DIFFERENT_OBJECTIVE']
 # deterministic merge evidence = explicit target already visible. Any broader same-side unresolved heuristic is measured for conflicts only.
 explicit_tp=sum(x['explicitTargetVisibleAtCandidate'] for x in same); explicit_fp=sum(x['explicitTargetVisibleAtCandidate'] for x in diff)
 for n in (1,2,3):
  tp=sum(x['sameSideUnresolvedRootCount']>=n for x in same); fp=sum(x['sameSideUnresolvedRootCount']>=n for x in diff)
  # no threshold promotion; diagnostics only
  pass
 rep={
  'version':'R4_P0B_OBJECTIVE_GROUPING_IDENTIFIABILITY_V1','researchOnly':True,'actionAuthority':False,
  'counts':{'sameObjective':len(same),'differentObjective':len(diff),'total':len(out)},
  'explicitTargetAtCandidate':{'samePositive':explicit_tp,'differentFalsePositive':explicit_fp,'sameCoverage':explicit_tp/len(same) if same else None},
  'heuristicConflictDiagnostics':{
    'sameSideUnresolvedExists':{'same':sum(x['sameSideUnresolvedRootCount']>0 for x in same),'different':sum(x['sameSideUnresolvedRootCount']>0 for x in diff)},
    'sameSideReservedExists':{'same':sum(x['sameSideReservedRootCount']>0 for x in same),'different':sum(x['sameSideReservedRootCount']>0 for x in diff)},
    'residualEquals18':{'same':sum(abs(float(x['currentResidualAfterReservation'] or 0)-18)<1e-9 for x in same),'different':sum(abs(float(x['currentResidualAfterReservation'] or 0)-18)<1e-9 for x in diff)}
  },
  'decision':'KEEP_UNKNOWN_AS_FIRST_CLASS_STATE' if explicit_tp<len(same) else 'DETERMINISTIC_GROUPING_POSSIBLE',
  'interpretation':'Cross-root SAME_OBJECTIVE cannot be fully determined at candidate time from explicit lineage. Same-side unresolved/reserved quantity also occurs in DIFFERENT_OBJECTIVE cases, so side/root occupancy cannot safely merge objectives. Preserve separate objective identity with UNKNOWN relation and use a later strict-past grouping belief only as non-authoritative evidence.',
  'rows':out
 }
 path=P/'r4_p0b_objective_grouping_identifiability_v1.json'; path.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({k:rep[k] for k in ('counts','explicitTargetAtCandidate','heuristicConflictDiagnostics','decision')},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
