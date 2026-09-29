from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot import unified_controller_paper_v2 as mod
GRID=float(mod.GRID);EPS=1e-9
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_wait1s_counterfactual_v0.json'

def quote(mid,t,side):
 b=mod.PublicBookTailer(ex.BOOK_DB)
 try:
  if not b.reset(mid,t):return None
  bf=mod.outcome_book(b.book,None)
  if not bf:return None
  bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']);tick=int(math.floor((bid+1e-9)/GRID))-1
  tick=max(int(round(mod.MIN_PRICE/GRID)),tick);return round(tick*GRID,2)
 finally:b.close()

def mid(mid,t,side):
 b=mod.PublicBookTailer(ex.BOOK_DB)
 try:
  if not b.reset(mid,t):return None
  b.advance(mid,t)
  bf=mod.outcome_book(b.book,None)
  if not bf:return None
  return (float(bf['up_bid'] if side=='UP' else bf['down_bid'])+float(bf['up_ask'] if side=='UP' else bf['down_ask']))/2
 finally:b.close()

def sim(events,mid,side,start_px,qty,submit_ms,end_ms):
 bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk');ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,submit_ms);rc=ex.submit_native(bt,1,side,start_px,qty);ex.advance_to(bt,end_ms);s=ex.order_snapshot(bt,1)
  fill=float(s.get('cumExecQty') or 0.0);ts=s.get('exchangeTs');fm=int(ts//1_000_000) if ts else None;mo=None
  if fm and fill>EPS:
   m0=mid_fn(mid,fm,side);m1=mid_fn(mid,fm+1000,side);mo=((m1-m0)/GRID) if m0 is not None and m1 is not None else None
  return {'submitRc':int(rc),'price':start_px,'fillShares':fill,'fillRate':fill/qty if qty>EPS else 0.0,'fillMs':fm,'fillLatencyFromIntentMs':fm-(submit_ms if submit_ms else 0) if fm else None,'markout1sTicks':mo}
 finally:bt.close()

def mid_fn(mid_,t,side):return mid(mid_,t,side)
def stats(xs):
 z=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 if not z:return {'n':0}
 z.sort();return {'n':len(z),'mean':sum(z)/len(z),'median':z[len(z)//2],'min':z[0],'max':z[-1]}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--max-orders-per-market',type=int,default=20);ap.add_argument('--wait-ms',type=int,default=1000);ap.add_argument('--horizon-ms',type=int,default=5000);ap.add_argument('--out',type=Path,default=OUT);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 for mid_ in mids:
  events,_,_=tape_v1.build_archive_events(mid_,trade_offset='mid');paper=load_reference_paper(mid_)
  for o in paper['orders'][:a.max_orders_per_market]:
   side=str(o['side']).upper();t=int(o['placed_at_ms']);qty=float(o.get('shares') or 18.0);end=t+a.horizon_ms
   p0=quote(mid_,t,side);p1=quote(mid_,t+a.wait_ms,side)
   if p0 is None or p1 is None:continue
   now=sim(events,mid_,side,p0,qty,t,end);wait=sim(events,mid_,side,p1,qty,t+a.wait_ms,end)
   rows.append({'marketId':mid_,'intentMs':t,'side':side,'qty':qty,'now':now,'wait1s':wait})
  print(json.dumps({'marketId':mid_,'rows':len(rows)},ensure_ascii=False),flush=True)
 def agg(k):
  vs=[r[k] for r in rows];return {'orders':len(vs),'fillRate':stats([v['fillRate'] for v in vs]),'filledShares':sum(v['fillShares'] for v in vs),'markout1sTicks':stats([v['markout1sTicks'] for v in vs])}
 rep={'version':'R2_WAIT1S_COUNTERFACTUAL_V0','researchOnly':True,'dreamFillAllowed':False,'waitMs':a.wait_ms,'commonTerminalHorizonMs':a.horizon_ms,'markets':mids,'rows':rows,'aggregate':{'PLACE_NOW':agg('now'),'WAIT1S_THEN_PLACE':agg('wait1s')},'guardrails':['Frozen R2 intent side/qty/time anchors unchanged.','WAIT variant re-observes public book after 1s and places best-bid-minus-1; common terminal time preserves non-execution cost.','Actual HftBacktest fills only.','No winner/PnL/Target input.','No wait-time sweep.']}
 a.out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'report':str(a.out),'aggregate':rep['aggregate']},ensure_ascii=False))
if __name__=='__main__':main()
