from __future__ import annotations
import json,sys
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
from tools.test_r4_management_simulator_current_risk_semantics_v1 import market_events,snapshot_at

def simulate_checkpoint(events,r):
 t0=int(r['t']);side=str(r['checkpointSide']);px=float(r['requested_px']);native_side='bids' if side=='UP' else 'asks';native_px=px if side=='UP' else round(1.0-px,10);oppagg='SELL' if side=='UP' else 'BUY';req=float(r['checkpointUnresolvedQty']);bids,asks=snapshot_at(events,t0);q=float((bids if native_side=='bids' else asks).get(native_px,0.0));cur_bids=dict(bids);cur_asks=dict(asks)
 def book_cross():
  if side=='UP':return bool(cur_asks and native_px>=min(cur_asks)-1e-12)
  return bool(cur_bids and native_px<=max(cur_bids)+1e-12)
 rem=req
 if book_cross():rem=0.0
 else:
  for e in events:
   if e[0]<=t0:continue
   if e[0]>t0+5000:break
   if e[2]=='SNAPSHOT':
    cur_bids=dict(e[5]);cur_asks=dict(e[6])
    if book_cross():rem=0.0;break
   elif e[2]=='DEPTH':
    book=cur_bids if e[3]=='bids' else cur_asks;after=float(e[5]);p=float(e[4])
    if after<=1e-9:book.pop(p,None)
    else:book[p]=after
    if book_cross():rem=0.0;break
   else:
    agg,tp,tq=e[3],float(e[4]),float(e[5])
    if agg!=oppagg:continue
    if (side=='UP' and tp<native_px-1e-9) or (side=='DOWN' and tp>native_px+1e-9):rem=0.0;break
    if abs(tp-native_px)<=1e-9:
     before=q;q-=tq;res=max(0.0,tq-max(0.0,before))
     if res>1e-9:rem=max(0.0,rem-min(rem,res));q=max(0.0,q)
     if rem<=1e-9:break
 pred=req-rem
 return {'marketId':int(r['marketId']),'actualFill':float(r['rootFillShares5s']),'actualAny':int(float(r['rootFillShares5s'])>1e-9),'actualCompleted':int(r['rootCompleted5s']),'predFill':pred,'predAny':int(pred>1e-9),'predCompleted':int(rem<=1e-9),'queueAheadAtCheckpoint':float((bids if native_side=='bids' else asks).get(native_px,0.0))}

def main():
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
  for r in rr:out.append(simulate_checkpoint(ev,r))
 n=len(out);rate=lambda k:sum(float(x[k]) for x in out)/n;aa=rate('actualAny');pa=rate('predAny');ac=rate('actualCompleted');pc=rate('predCompleted');scale=max(1.,sum(abs(x['actualFill']) for x in out)/n);qmae=sum(abs(x['predFill']-x['actualFill']) for x in out)/n/scale
 rep={'version':'R4_MANAGEMENT_SIMULATOR_CHECKPOINT_ANCHOR_LATE20G_DIAGNOSTIC_V1','researchOnly':True,'postHoc':True,'roots':n,'actualAnyFillRate':aa,'predAnyFillRate':pa,'anyFillRateAbsError':abs(pa-aa),'actualCompletedRate':ac,'predCompletedRate':pc,'completedRateAbsError':abs(pc-ac),'fillQtyNormalizedMAE':qmae,'preCheckpointPredFillRoots':0,'interpretation':'Diagnostic: initialize queue-ahead from receipt-clock public depth at the management checkpoint; do not replay inferred pre-checkpoint carrier history.','rows':out}
 (P/'r4_management_simulator_checkpoint_anchor_late20g_diagnostic_v1.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in rep.items() if k!='rows'},indent=2))
if __name__=='__main__':main()
