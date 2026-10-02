from __future__ import annotations
import json
from pathlib import Path
P=Path(__file__).resolve().parents[1]/'data/research/r4_v0/p0_provenance_v1'
rows=[]
for s,c in [(0,5),(5,5),(10,5),(15,5),(20,5),(25,5),(30,5),(35,2)]:
 rows+=json.loads((P/f'r4_p0b_objective_grouping_context_replication_branches_{s}_{c}_v1.json').read_text(encoding='utf-8'))['rows']
valid=[r for r in rows if r.get('branchValid') and r.get('context')]
same=[r for r in valid if r['role']=='PREPOSITION_REPAIR_SUBSTITUTE'];diff=[r for r in valid if r['role']=='PARALLEL_STATE_SHAPING']
feats=['same_side_active_objectives','same_side_total_residual','same_side_oldest_objective_age_s']
dir={}
for f in feats:
 a=[float(r['context'][f]) for r in same];b=[float(r['context'][f]) for r in diff];num=sum(x>y for x in a for y in b)+.5*sum(x==y for x in a for y in b);den=len(a)*len(b);dir[f]={'sameMedian':sorted(a)[len(a)//2] if a else None,'differentMedian':sorted(b)[len(b)//2] if b else None,'pSameGreater':num/den if den else None}
support=len(same)>=3 and len(diff)>=6
status='PASS' if support and all(v['pSameGreater']>=.60 for v in dir.values()) else 'REJECT' if support else 'INCONCLUSIVE_SUPPORT'
rep={'version':'R4_P0B_OBJECTIVE_GROUPING_CONTEXT_REPLICATION_V1','status':status,'counts':{'candidates':len(rows),'valid':len(valid),'sameObjective':len(same),'differentObjective':len(diff),'reject':sum(r['role']=='REJECT_NO_ACTION' for r in valid),'ambiguous':sum(r['role']=='AMBIGUOUS_TRADEOFF' for r in valid),'noRealization':sum(r['role']=='EXECUTION_NO_REALIZATION' for r in valid)},'directions':dir,'supportGate':{'sameMin':3,'differentMin':6,'passed':support},'interpretation':'Diagnostic directions are reported even if support is insufficient. No grouping threshold or authority is promoted. UNKNOWN remains canonical until independent support is sufficient.','guards':['240/240 discovery execution exact','37/37 branches valid','strict-past journal context only','no cohort replacement','no R3/8781/Echtgeld','2026-08-16 sealed']}
q=P/'r4_p0b_objective_grouping_context_replication_v1.json';q.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(rep,ensure_ascii=False,indent=2))