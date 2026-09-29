from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1';TAPE=ROOT/'data/execution_tape_v1/markets';ENTRY=1092
from src.predict_bot.execution_tape_archive_v1 import load_archive
from tools import hftbacktest_true_match_calibration_v0 as tm

def first_rows():
 out=[]
 for i in range(4):
  d=json.loads((P/f'r4_management_simulator_economic_late20d_chunk{i}_v1.json').read_text())
  first={}
  for r in d['rows']:
   k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
   if k not in first or int(r['t'])<int(first[k]['t']): first[k]=r
  out+=list(first.values())
 return out

def market_events(mid):
 d=load_archive(TAPE/f'{mid}.json.xz');ups=sorted(d.get('updates') or [],key=lambda r:(int(r[1]),int(r[0])))
 events=[];book={'bids':{},'asks':{}}
 for u in ups:
  t=int(u[1]);cp=int(u[3]);chg=u[6] or {}
  if cp and u[4] is not None and u[5] is not None:
   book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}}
   events.append((t,0,'SNAPSHOT',None,None,dict(book['bids']),dict(book['asks'])))
  else:
   for side in ('bids','asks'):
    for x in chg.get(side,[]) or []:
     p=float(x[0]);after=float(x[2]);events.append((t,0,'DEPTH',side,p,after,None))
     if after<=1e-9:book[side].pop(p,None)
     else:book[side][p]=after
 for raw in d.get('matches') or []:
  n=tm.normalize_match(raw)
  if n is not None:events.append((int(n['tsMs'])+500,1,'TRADE',str(n['nativeAggressor']),float(n['nativeYesPrice']),float(n['qty']),None))
 events.sort(key=lambda x:(x[0],x[1]));return events

def snapshot_at(events,t):
 bids={};asks={}
 for e in events:
  if e[0]>t:break
  if e[2]=='SNAPSHOT':bids=dict(e[5]);asks=dict(e[6])
  elif e[2]=='DEPTH':
   side,p,after=e[3],e[4],float(e[5]);book=bids if side=='bids' else asks
   if after<=1e-9:book.pop(p,None)
   else:book[p]=after
 return bids,asks

def simulate_root(events,r):
 t0=int(r['t']);age=float(r.get('checkpointOldestOwnerAgeS') or 0);submit=t0-int(round(age*1000));rest=submit+ENTRY;side=str(r['checkpointSide']);px=float(r['requested_px']);native_side='bids' if side=='UP' else 'asks';native_px=px if side=='UP' else round(1.0-px,10);oppagg='SELL' if side=='UP' else 'BUY';req=float(r['checkpointUnresolvedQty'])
 bids,asks=snapshot_at(events,rest);q=float((bids if native_side=='bids' else asks).get(native_px,0.0));filled=0.0;prefill=0.0
 # track bests as of rest
 cur_bids=dict(bids);cur_asks=dict(asks)
 def book_cross():
  if side=='UP': return bool(cur_asks and native_px>=min(cur_asks)-1e-12)
  return bool(cur_bids and native_px<=max(cur_bids)+1e-12)
 def process(e,remaining):
  nonlocal q,cur_bids,cur_asks
  typ=e[2]
  if typ=='SNAPSHOT': cur_bids=dict(e[5]);cur_asks=dict(e[6]);q=min(q,float((cur_bids if native_side=='bids' else cur_asks).get(native_px,0.0)));return remaining if not book_cross() else 0.0
  if typ=='DEPTH':
   s,p,after=e[3],e[4],float(e[5]);book=cur_bids if s=='bids' else cur_asks
   if after<=1e-9:book.pop(p,None)
   else:book[p]=after
   if s==native_side and abs(float(p)-native_px)<1e-9:q=min(q,max(0.0,after))
   return remaining if not book_cross() else 0.0
  agg,tp,tq=e[3],float(e[4]),float(e[5])
  if agg!=oppagg:return remaining
  # strict crossing trade -> full fill
  if (side=='UP' and tp<native_px-1e-9) or (side=='DOWN' and tp>native_px+1e-9):return 0.0
  if abs(tp-native_px)<=1e-9:
   q0=q;q-=tq
   if q<0 and remaining>1e-9:
    residual=min(remaining,max(0.0,-q));remaining-=residual;q=0.0
  return remaining
 # pre-checkpoint strict-past queue reconstruction
 remaining=req
 for e in events:
  if e[0]<=rest:continue
  if e[0]>t0:break
  before=remaining;remaining=process(e,remaining);prefill+=max(0.0,before-remaining)
 # condition on actual checkpoint unresolved qty; keep reconstructed q but reset remaining to actual unresolved
 remaining=req
 for e in events:
  if e[0]<=t0:continue
  if e[0]>t0+5000:break
  remaining=process(e,remaining)
 pred=req-remaining
 return {'marketId':int(r['marketId']),'actualFill':float(r['rootFillShares5s']),'actualAny':int(float(r['rootFillShares5s'])>1e-9),'actualCompleted':int(r['rootCompleted5s']),'predFill':pred,'predAny':int(pred>1e-9),'predCompleted':int(remaining<=1e-9),'preCheckpointPredFill':prefill,'queueAheadAtCheckpoint':q}

def main():
 rows=first_rows();by=defaultdict(list)
 for r in rows:by[int(r['marketId'])].append(r)
 out=[]
 for mid,rr in by.items():
  ev=market_events(mid)
  for r in rr:out.append(simulate_root(ev,r))
 n=len(out);rate=lambda k:sum(float(x[k]) for x in out)/n;aa=rate('actualAny');pa=rate('predAny');ac=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in out)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in out)/n/scale;base=json.loads((P/'r4_management_simulator_true_match_crossing_v1.json').read_text());imps={'anyFillErrorImprovement':base['anyFillRateAbsError']-abs(pa-aa),'completedErrorImprovement':base['completedRateAbsError']-abs(pc-ac),'fillQtyNMAEImprovement':base['fillQtyNormalizedMAE']-qmae};wins=sum(v>0 for v in imps.values())
 rep={'version':'R4_MANAGEMENT_SIMULATOR_RISK_SEMANTICS_V1','researchOnly':True,'roots':n,'actualAnyFillRate':aa,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-aa),'actualCompletedRate':ac,'predCompletedRate':pc,'completedRateAbsError':abs(pc-ac),'fillQtyNormalizedMAE':qmae,'rootsWithPreCheckpointPredFill':sum(x['preCheckpointPredFill']>1e-9 for x in out),'comparisonVsTrueMatchCrossing':imps,'keepRiskSemantics':wins>=2 and sum(x['preCheckpointPredFill']>1e-9 for x in out)<=3,'interpretation':'Mechanistic approximation of HftBacktest RiskAverseQueueModel + PartialFillExchange semantics at a single order price level; diagnostic cohort reused, not graduation.','rows':out};(P/'r4_management_simulator_risk_semantics_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
