from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
from src.predict_bot.execution_tape_archive_v1 import load_archive
TAPE=ROOT/'data/execution_tape_v1/markets'

def first_rows():
 out=[]
 for i in range(4):
  d=json.loads((P/f'r4_management_simulator_economic_late20d_chunk{i}_v1.json').read_text())
  first={}
  for r in d['rows']:
   k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
   if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
  out+=list(first.values())
 return out

def level_series(mid):
 d=load_archive(TAPE/f'{mid}.json.xz');ups=sorted(d.get('updates') or [],key=lambda r:(int(r[1]),int(r[0])));book={'bids':{},'asks':{}};events=[]
 for u in ups:
  t=int(u[1]);cp=int(u[3]);chg=u[6] or {}
  if cp and u[4] is not None and u[5] is not None:
   book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
  else:
   for side in ('bids','asks'):
    for x in chg.get(side,[]) or []:
     p=float(x[0]);before=float(book[side].get(p,0.0));after=float(x[2]);
     if before>after:events.append((t,side,p,before-after))
     if after<=1e-9:book[side].pop(p,None)
     else:book[side][p]=after
  events.append((t,'SNAPSHOT',None,{'bids':dict(book['bids']),'asks':dict(book['asks'])}))
 return events

def depth_at(events,t,side,price):
 state=None
 for e in events:
  if e[0]>t:break
  if e[1]=='SNAPSHOT':state=e[3]
 return float((state or {}).get(side,{}).get(price,0.0))
def depletion(events,t0,t1,side,price):return sum(float(e[3]) for e in events if t0<e[0]<=t1 and e[1]==side and e[2] is not None and abs(float(e[2])-price)<1e-9)
def main():
 rows=first_rows();by=defaultdict(list)
 for r in rows:by[int(r['marketId'])].append(r)
 pred=[]
 for mid,rr in by.items():
  ev=level_series(mid)
  for r in rr:
   t0=int(r['t']);side=str(r['checkpointSide']);px=float(r['requested_px']);native_side='bids' if side=='UP' else 'asks';native_px=px if side=='UP' else round(1.0-px,10);qa=depth_at(ev,t0,native_side,native_px);dep=depletion(ev,t0,t0+5000,native_side,native_px);req=float(r['checkpointUnresolvedQty']);fill=max(0.0,min(req,dep-qa));pred.append({'marketId':mid,'rid':r['checkpointResponsibilityId'],'actualFill':float(r['rootFillShares5s']),'actualAny':int(float(r['rootFillShares5s'])>1e-9),'actualCompleted':int(r['rootCompleted5s']),'queueAhead':qa,'depletion5s':dep,'predFill':fill,'predAny':int(fill>1e-9),'predCompleted':int(fill>=req-1e-9)})
 n=len(pred);rate=lambda k:sum(float(x[k]) for x in pred)/n;act_any=rate('actualAny');pa=rate('predAny');act_c=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in pred)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in pred)/n/scale
 rep={'version':'R4_MANAGEMENT_SIMULATOR_MECHANISTIC_QUEUE_BASELINE_V1','researchOnly':True,'roots':n,'actualAnyFillRate':act_any,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-act_any),'actualCompletedRate':act_c,'predCompletedRate':pc,'completedRateAbsError':abs(pc-act_c),'fillQtyNormalizedMAE':qmae,'predictedPositiveRoots':sum(x['predAny'] for x in pred),'actualPositiveRoots':sum(x['actualAny'] for x in pred),'interpretation':'Conservative price-level L2 depletion baseline with current visible depth as queueAhead; no rest-age credit.','rows':pred};(P/'r4_management_simulator_mechanistic_queue_baseline_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
