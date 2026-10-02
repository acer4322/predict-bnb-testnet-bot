from __future__ import annotations
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];P=ROOT/'data/research/r4_v0/p0_provenance_v1'
paths=[P/'r4_p0b_lifecycle_dataset_fresh21_v1.json']+[P/f'r4_management_simulator_multistep_unseen_chunk{i}_v1.json' for i in range(3)]+[P/f'r4_management_simulator_multistep_rep22_chunk{i}_v1.json' for i in range(3)]
rows=[]
for p in paths:
 if not p.exists():continue
 d=json.loads(p.read_text(encoding='utf-8'));rows+=d.get('rows',[])
first={}
for r in rows:
 k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
 if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
partial=[];full=[];zero=[]
for r in first.values():
 req=float(r.get('checkpointRequestedQty') or 0);q=float(r.get('rootFillShares5s') or 0);comp=int(r.get('rootCompleted5s') or 0)
 z={'marketId':int(r['marketId']),'responsibilityId':str(r['checkpointResponsibilityId']),'requestedQty':req,'fillQty5s':q,'fraction':q/req if req>0 else None,'completed5s':comp,'price':float(r.get('requested_px') or 0),'secondsLeft':float(r.get('seconds_left') or 0)}
 if q<=1e-9:zero.append(z)
 elif comp or q>=req-1e-9:full.append(z)
 else:partial.append(z)
rep={'version':'R4_MANAGEMENT_SIMULATOR_PARTIAL_FILL_SUPPORT_AUDIT_V1','researchOnly':True,'sourceFiles':[str(p.relative_to(ROOT)) for p in paths if p.exists()],'firstCheckpointRoots':len(first),'zeroFillRoots':len(zero),'fullFillRoots':len(full),'partialFillRoots':len(partial),'partialFillRateAmongPositive':len(partial)/max(1,len(partial)+len(full)),'partialRows':partial,'interpretation':'Support audit only; no model scoring or cohort selection.'};(P/'r4_management_simulator_partial_fill_support_audit_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='partialRows'},indent=2))
