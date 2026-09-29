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
   if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
  out+=list(first.values())
 return out

def market_events(mid):
 d=load_archive(TAPE/f'{mid}.json.xz');ups=sorted(d.get('updates') or [],key=lambda r:(int(r[1]),int(r[0])));events=[];book={'bids':{},'asks':{}}
 for u in ups:
  t=int(u[1]);cp=int(u[3]);chg=u[6] or {}
  if cp and u[4] is not None and u[5] is not None:
   book={'bids':{float(k):float(v) for k,v in (u[4] or {}).items()},'asks':{float(k):float(v) for k,v in (u[5] or {}).items()}};events.append((t,0,'SNAPSHOT',None,None,dict(book['bids']),dict(book['asks'])))
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
   book=bids if e[3]=='bids' else asks;after=float(e[5]);p=float(e[4]);
   if after<=1e-9:book.pop(p,None)
   else:book[p]=after
 return bids,asks

def simulate_root(events,r):
 t0=int(r['t']);age=float(r.get('checkpointOldestOwnerAgeS') or 0);rest=t0-int(round(age*1000))+ENTRY;side=str(r['checkpointSide']);px=float(r['requested_px']);native_side='bids' if side=='UP' else 'asks';native_px=px if side=='UP' else round(1.0-px,10);oppagg='SELL' if side=='UP' else 'BUY';req=float(r['checkpointUnresolvedQty']);bids,asks=snapshot_at(events,rest);q=float((bids if native_side=='bids' else asks).get(native_px,0.0));cur_bids=dict(bids);cur_asks=dict(asks)
 def book_cross():
  if side=='UP':return bool(cur_asks and native_px>=min(cur_asks)-1e-12)
  return bool(cur_bids and native_px<=max(cur_bids)+1e-12)
 def process(e,remaining):
  nonlocal q,cur_bids,cur_asks
  if e[2]=='SNAPSHOT':cur_bids=dict(e[5]);cur_asks=dict(e[6]);return 0.0 if book_cross() else remaining
  if e[2]=='DEPTH':
   book=cur_bids if e[3]=='bids' else cur_asks;after=float(e[5]);p=float(e[4]);
   if after<=1e-9:book.pop(p,None)
   else:book[p]=after
   return 0.0 if book_cross() else remaining
  agg,tp,tq=e[3],float(e[4]),float(e[5])
  if agg!=oppagg:return remaining
  if (side=='UP' and tp<native_px-1e-9) or (side=='DOWN' and tp>native_px+1e-9):return 0.0
  if abs(tp-native_px)<=1e-9:
   before_q=q;q-=tq
   residual=max(0.0,tq-max(0.0,before_q))
   if residual>1e-9:remaining=max(0.0,remaining-min(remaining,residual));q=max(0.0,q)
  return remaining
 rem=req;prefill=0.0
 for e in events:
  if e[0]<=rest:continue
  if e[0]>t0:break
  b=rem;rem=process(e,rem);prefill+=max(0.0,b-rem)
 rem=req
 for e in events:
  if e[0]<=t0:continue
  if e[0]>t0+5000:break
  rem=process(e,rem)
 pred=req-rem
 return {'marketId':int(r['marketId']),'actualFill':float(r['rootFillShares5s']),'actualAny':int(float(r['rootFillShares5s'])>1e-9),'actualCompleted':int(r['rootCompleted5s']),'predFill':pred,'predAny':int(pred>1e-9),'predCompleted':int(rem<=1e-9),'preCheckpointPredFill':prefill}

def main():
 rows=first_rows();by=defaultdict(list)
 for r in rows:by[int(r['marketId'])].append(r)
 out=[]
 for mid,rr in by.items():
  ev=market_events(mid)
  for r in rr:out.append(simulate_root(ev,r))
 n=len(out);rate=lambda k:sum(float(x[k]) for x in out)/n;aa=rate('actualAny');pa=rate('predAny');ac=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in out)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in out)/n/scale;legacy=json.loads((P/'r4_management_simulator_risk_semantics_v1.json').read_text());cross=json.loads((P/'r4_management_simulator_true_match_crossing_v1.json').read_text());pre=sum(x['preCheckpointPredFill']>1e-9 for x in out)
 rep={'version':'R4_MANAGEMENT_SIMULATOR_CURRENT_RISK_SEMANTICS_V1','researchOnly':True,'roots':n,'actualAnyFillRate':aa,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-aa),'actualCompletedRate':ac,'predCompletedRate':pc,'completedRateAbsError':abs(pc-ac),'fillQtyNormalizedMAE':qmae,'preCheckpointPredFillRoots':pre,'comparison':{'legacyDepthClamp':{'anyFillRateAbsError':legacy['anyFillRateAbsError'],'completedRateAbsError':legacy['completedRateAbsError'],'fillQtyNormalizedMAE':legacy['fillQtyNormalizedMAE'],'preCheckpointPredFillRoots':legacy['rootsWithPreCheckpointPredFill']},'trueMatchCrossing':{'anyFillRateAbsError':cross['anyFillRateAbsError'],'completedRateAbsError':cross['completedRateAbsError'],'fillQtyNormalizedMAE':cross['fillQtyNormalizedMAE'],'preCheckpointPredFillRoots':cross['rootsWithPreCheckpointSpill']}},'interpretation':'Current-doc RiskAverseQueueModel semantics: no cancellation/depth advancement, same-price true trades advance queue, strict crossing/book crossing full fill.','rows':out};(P/'r4_management_simulator_current_risk_semantics_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
