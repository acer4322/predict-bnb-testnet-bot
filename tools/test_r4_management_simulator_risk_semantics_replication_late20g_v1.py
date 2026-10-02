from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
from tools.test_r4_management_simulator_risk_semantics_v1 import market_events,simulate_root
rows=[];exact=0;markets=0
for i in range(4):
 d=json.loads((P/f'r4_management_simulator_risk_rep_late20g_chunk{i}_v1.json').read_text());exact+=int(d['executionExact']);markets+=int(d['count']);first={}
 for r in d['rows']:
  k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
  if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
 rows+=list(first.values())
by=defaultdict(list)
for r in rows:by[int(r['marketId'])].append(r)
out=[]
for mid,rr in by.items():
 ev=market_events(mid)
 for r in rr:out.append(simulate_root(ev,r))
n=len(out)
if n<20:raise SystemExit(f'INSUFFICIENT_ROOTS {n}')
rate=lambda k:sum(float(x[k]) for x in out)/n
aa=rate('actualAny');pa=rate('predAny');ac=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in out)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in out)/n/scale;pre=sum(x['preCheckpointPredFill']>1e-9 for x in out)
checks={'executionCoreExact':exact==20 and markets==20,'rootSupport':n>=20,'anyFillRateError':abs(pa-aa)<=.12,'completedRateError':abs(pc-ac)<=.12,'fillQtyNMAE':qmae<=.35,'preCheckpointConsistency':pre<=2}
rep={'version':'R4_MANAGEMENT_SIMULATOR_RISK_SEMANTICS_REPLICATION_LATE20G_V1','status':'REPLICATION_PASS' if all(checks.values()) else 'REPLICATION_FAIL','researchOnly':True,'markets':markets,'executionCoreExact':f'{exact}/{markets}','roots':n,'actualAnyFillRate':aa,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-aa),'actualCompletedRate':ac,'predCompletedRate':pc,'completedRateAbsError':abs(pc-ac),'fillQtyNormalizedMAE':qmae,'preCheckpointPredFillRoots':pre,'checks':checks,'gatePass':all(checks.values()),'interpretation':'Frozen single-price mechanistic approximation of HftBacktest RiskAverseQueueModel + PartialFillExchange replicated on chronology not used for method selection.','rows':out};(P/'r4_management_simulator_risk_semantics_replication_late20g_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='rows'},indent=2))
