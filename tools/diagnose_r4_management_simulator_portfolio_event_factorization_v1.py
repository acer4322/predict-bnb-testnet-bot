from __future__ import annotations
import json
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1];RET=ROOT/'data/research/lan_worker_returns'
FILES=[RET/'r4-portfolio-events-fresh21/fresh21.json',RET/'r4-portfolio-events-unseen23/unseen23.json',RET/'r4-portfolio-events-next20-a/a.json',RET/'r4-portfolio-events-next20-b/b.json']
H=[5,15,30]
def fam(e):
 et=str(e.get('eventType') or '');rel=str(e.get('sideRelation') or '')
 if et=='RESPONSIBILITY_OPENED':return 'NEW_WEAK_ROOT' if rel=='WEAK' else 'NEW_DOM_ROOT' if rel=='DOMINANT' else 'NEW_ROOT_OTHER'
 if et in {'PARTIAL_FILL','FULL_FILL'}:return 'WEAK_FILL' if rel=='WEAK' else 'DOM_FILL' if rel=='DOMINANT' else 'FILL_OTHER'
 if et=='TAKER_EXECUTION':return 'WEAK_TAKER' if rel=='WEAK' else 'DOM_TAKER' if rel=='DOMINANT' else 'TAKER_OTHER'
 if et=='CANCEL_REQUESTED':return 'CANCEL'
 if et=='RESPONSIBILITY_COMPLETED':return 'COMPLETE'
 if et=='ACK_NEW':return 'ACK'
 return None
rows=[]
for p in FILES:
 d=json.loads(p.read_text(encoding='utf-8'));rows+=d['rows']
out={'version':'R4_MANAGEMENT_SIMULATOR_PORTFOLIO_EVENT_FACTORIZATION_DIAGNOSTIC_V1','researchOnly':True,'roots':len(rows),'horizons':{}}
for h in H:
 ctr=defaultdict(lambda:{'roots':0,'events':0,'qty':0.0,'positiveRoots':0})
 for r in rows:
  seen=defaultdict(int)
  for e in r.get('portfolioEvents30s') or []:
   if int(e.get('dtMs') or 0)>h*1000:continue
   f=fam(e)
   if not f:continue
   ctr[f]['events']+=1;ctr[f]['qty']+=float(e.get('qty') or 0);seen[f]+=1
  for f in seen:ctr[f]['positiveRoots']+=1
 for f in list(ctr):ctr[f]['roots']=len(rows);ctr[f]['positiveRate']=ctr[f]['positiveRoots']/len(rows);ctr[f]['eventsPerRoot']=ctr[f]['events']/len(rows);ctr[f]['meanQtyPerRoot']=ctr[f]['qty']/len(rows)
 out['horizons'][str(h)]=dict(sorted(ctr.items()))
print(json.dumps(out,indent=2));(ROOT/'data/research/r4_v0/p0_provenance_v1/r4_management_simulator_portfolio_event_factorization_diagnostic_v1.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
