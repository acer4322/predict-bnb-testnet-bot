from __future__ import annotations
import argparse,json,math,sys
from collections import defaultdict
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
EPS=1e-9
TH=[2,3,5,10]
PERSIST=[1000,2200,3000,5000]

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-id',type=int,required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
 dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets'
 rep,audit=mk10.run_market(int(a.market_id));seen=set();by=defaultdict(list)
 for r0 in rep.get('orderStateRows') or []:
  if str(r0.get('context') or '')!='POST_DECISION_ACTIVE':continue
  if str(r0.get('hftStatus') or '') not in {'NEW','PARTIALLY_FILLED'}:continue
  key=(str(r0.get('orderId')),int(r0.get('checkpointMs') or 0))
  if key in seen:continue
  seen.add(key);r=dict(r0);p=r.get('portfolio') if isinstance(r.get('portfolio'),dict) else {};net=float(p.get('combined_net') or 0.0);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
  if weak and str(r.get('side'))==weak:by[str(r.get('orderId'))].append(r)
 configs={}
 for t in TH:
  for pers in PERSIST:
   events=[]
   for oid,seq0 in by.items():
    seq=sorted(seq0,key=lambda r:int(r.get('checkpointMs') or 0));candidate_start=None;last_cum=None;armed=True
    for i,r in enumerate(seq):
     tm=int(r.get('checkpointMs') or 0);cum=float(r.get('cumExecQty') or 0.0);off=r.get('quoteOffsetTicks');off=float(off) if off is not None and math.isfinite(float(off)) else math.nan
     progressed=last_cum is not None and cum>last_cum+EPS
     if progressed:
      candidate_start=None;armed=True
     last_cum=cum
     cond=bool(math.isfinite(off) and off>=t)
     if not cond:
      candidate_start=None;armed=True;continue
     if candidate_start is None:candidate_start=tm
     if armed and tm-candidate_start>=pers:
      # confirmation row itself remains strict-past; future labels are scoring only
      future=seq[i+1:]
      rev3=any(int(z.get('checkpointMs') or 0)<=tm+3000 and (z.get('quoteOffsetTicks') is None or not math.isfinite(float(z.get('quoteOffsetTicks'))) or float(z.get('quoteOffsetTicks'))<t) for z in future)
      rev5=any(int(z.get('checkpointMs') or 0)<=tm+5000 and (z.get('quoteOffsetTicks') is None or not math.isfinite(float(z.get('quoteOffsetTicks'))) or float(z.get('quoteOffsetTicks'))<t) for z in future)
      nextprog=None
      for z in future:
       if float(z.get('cumExecQty') or 0.0)>cum+EPS:
        nextprog=int(z.get('checkpointMs') or 0)-tm;break
      p=r.get('portfolio') if isinstance(r.get('portfolio'),dict) else {};gap=abs(float(p.get('combined_net') or 0.0));rem=float(r.get('remainingQty') or 0.0)
      events.append({'orderId':oid,'confirmMs':tm,'ageMs':float(r.get('orderAgeMs') or 0.0),'offset':off,'cumExecQty':cum,'remainingQty':rem,'gap':gap,'oversize':bool(gap>EPS and rem>gap+EPS),'fill5s':int(r.get('labelAnyFill5s') or 0),'eventual':int(float(r.get('eventualAdditionalFillShares') or 0)>EPS),'revert3s':int(rev3),'revert5s':int(rev5),'nextProgressDelayMs':nextprog,'confirmationDelayMs':tm-candidate_start})
      armed=False
   n=len(events);k=f'{t}t_{pers}ms';configs[k]={'thresholdTicks':t,'persistenceMs':pers,'events':n,'orders':len(set(e['orderId'] for e in events)),'eventsPerOrder':(n/len(set(e['orderId'] for e in events)) if events else None),'fill5sCount':sum(e['fill5s'] for e in events),'eventualCount':sum(e['eventual'] for e in events),'revert3sCount':sum(e['revert3s'] for e in events),'revert5sCount':sum(e['revert5s'] for e in events),'oversizeCount':sum(e['oversize'] for e in events),'fill5sRate':(float(np.mean([e['fill5s'] for e in events])) if events else None),'eventualRate':(float(np.mean([e['eventual'] for e in events])) if events else None),'revert3sRate':(float(np.mean([e['revert3s'] for e in events])) if events else None),'revert5sRate':(float(np.mean([e['revert5s'] for e in events])) if events else None),'meanAgeMs':(float(np.mean([e['ageMs'] for e in events])) if events else None),'meanOffsetTicks':(float(np.mean([e['offset'] for e in events])) if events else None),'meanGap':(float(np.mean([e['gap'] for e in events])) if events else None),'meanRemainingQty':(float(np.mean([e['remainingQty'] for e in events])) if events else None)}
 out={'version':'R4_ADAPTIVE_EXECUTION_PERSISTENCE_V2','researchOnly':True,'actionAuthority':False,'marketId':int(a.market_id),'makerQtyUnique':audit.get('makerQtyUnique'),'takerDynamicNon10Observed':audit.get('takerDynamicNon10Observed'),'weakRepairOrders':len(by),'configs':configs,'boundary':'A fill-progress increase resets the stale candidate. Threshold/persistence are structural shadow probes only; no PnL or future outcome enters runtime state.'}
 Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps({'marketId':a.market_id,'weakRepairOrders':len(by),'configs':configs},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
