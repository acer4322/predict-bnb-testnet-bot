from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
from tools.test_r4_management_simulator_current_risk_semantics_v1 import market_events,simulate_root
rows=[]
for i in range(4):
 d=json.loads((P/f'r4_management_simulator_risk_rep_late20g_chunk{i}_v1.json').read_text());first={}
 for r in d['rows']:
  k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
  if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
 rows+=list(first.values())
by=defaultdict(list)
for r in rows:by[int(r['marketId'])].append(r)
mis=[];ok=[]
for mid,rr in by.items():
 ev=market_events(mid)
 for r in rr:
  z=simulate_root(ev,r);row={'marketId':mid,'rid':r['checkpointResponsibilityId'],'ageS':float(r.get('checkpointOldestOwnerAgeS') or 0),'ownerCount':int(r.get('checkpointOwnerCount') or 0),'activeIntentCount':len(r.get('checkpointActiveIntentIds') or []),'rootNewCarrierIntents5s':int(r.get('rootNewCarrierIntents5s') or 0),'pendingCancelCount':len(r.get('pendingCancelIntentIds') or []),'events5s':float(r.get('events_5s') or 0),'events15s':float(r.get('events_15s') or 0),'price':float(r.get('requested_px') or 0),'side':r.get('checkpointSide'),'preCheckpointPredFill':z['preCheckpointPredFill'],'actualConfirmedAtCheckpoint':float(r.get('checkpointConfirmedQty') or 0)}
  (mis if z['preCheckpointPredFill']>1e-9 and row['actualConfirmedAtCheckpoint']<=1e-9 else ok).append(row)
def med(xs,k):
 vals=sorted(float(x[k]) for x in xs);return vals[len(vals)//2] if vals else None
rep={'version':'R4_MANAGEMENT_SIMULATOR_PRECHECKPOINT_MISMATCH_LATE20G_V1','researchOnly':True,'mismatchRoots':len(mis),'matchedOrNoMismatchRoots':len(ok),'mismatchMedians':{k:med(mis,k) for k in ['ageS','ownerCount','activeIntentCount','pendingCancelCount','events5s','events15s','price']},'otherMedians':{k:med(ok,k) for k in ['ageS','ownerCount','activeIntentCount','pendingCancelCount','events5s','events15s','price']},'mismatchRows':mis,'interpretation':'Diagnostic only: identify whether pre-checkpoint false fills cluster by carrier age/ownership/cancel context.'};(P/'r4_management_simulator_precheckpoint_mismatch_late20g_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
