from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict
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
out=[]
for mid,rr in by.items():
 ev=market_events(mid)
 for r in rr:out.append(simulate_root(ev,r))
n=len(out);rate=lambda k:sum(float(x[k]) for x in out)/n;aa=rate('actualAny');pa=rate('predAny');ac=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in out)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in out)/n/scale;pre=sum(x['preCheckpointPredFill']>1e-9 for x in out);legacy=json.loads((P/'r4_management_simulator_risk_semantics_replication_late20g_v1.json').read_text())
rep={'version':'R4_MANAGEMENT_SIMULATOR_CURRENT_RISK_LATE20G_POSTHOC_DIAGNOSTIC_V1','researchOnly':True,'postHoc':True,'roots':n,'actualAnyFillRate':aa,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-aa),'actualCompletedRate':ac,'predCompletedRate':pc,'completedRateAbsError':abs(pc-ac),'fillQtyNormalizedMAE':qmae,'preCheckpointPredFillRoots':pre,'legacyDepthClampReplication':{'anyFillRateAbsError':legacy['anyFillRateAbsError'],'completedRateAbsError':legacy['completedRateAbsError'],'fillQtyNormalizedMAE':legacy['fillQtyNormalizedMAE'],'preCheckpointPredFillRoots':legacy['preCheckpointPredFillRoots']},'interpretation':'Post-hoc diagnostic only. Current-doc queue semantics evaluated on already-seen Late20g solely to localize legacy depth-clamp failure.'};(P/'r4_management_simulator_current_risk_late20g_posthoc_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
