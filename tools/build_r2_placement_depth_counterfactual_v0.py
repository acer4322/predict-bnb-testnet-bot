from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_placement_depth_counterfactual_v0.json'
GRID=float(mod.GRID);EPS=1e-9

def side_mid(book,side):
 bf=mod.outcome_book(book,None)
 if not bf:return None
 b=bf['up_bid'] if side=='UP' else bf['down_bid'];a=bf['up_ask'] if side=='UP' else bf['down_ask']
 return (float(b)+float(a))/2

def quote_at(mid:int,t:int,side:str,offset:int):
 b=mod.PublicBookTailer(ex.BOOK_DB)
 try:
  if not b.reset(mid,t):return None
  bf=mod.outcome_book(b.book,None)
  if not bf:return None
  bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']);tick=int(math.floor((bid+1e-9)/GRID))-offset
  tick=max(int(round(mod.MIN_PRICE/GRID)),tick);return round(tick*GRID,2)
 finally:b.close()

def markout(mid:int,side:str,fill_ms:int):
 b=mod.PublicBookTailer(ex.BOOK_DB)
 try:
  if not b.reset(mid,fill_ms):return None
  m0=side_mid(b.book,side)
  b.advance(mid,fill_ms+1000);m1=side_mid(b.book,side)
  if m0 is None or m1 is None:return None
  return (m1-m0)/GRID
 finally:b.close()

def sim(events,side,px,qty,start,horizon):
 bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk');ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,start);rc=ex.submit_native(bt,1,side,px,qty);ex.advance_to(bt,start+horizon);s=ex.order_snapshot(bt,1)
  fill=float(s.get('cumExecQty') or 0.0);ts=s.get('exchangeTs');fill_ms=int(ts//1_000_000) if ts else None
  return {'submitRc':int(rc),'fillShares':fill,'fillRate':fill/qty if qty>EPS else 0.0,'fillMs':fill_ms,'fillLatencyMs':fill_ms-start if fill_ms else None,'status':s.get('status')}
 finally:bt.close()

def stats(xs):
 z=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
 if not z:return {'n':0}
 z.sort();return {'n':len(z),'mean':sum(z)/len(z),'median':z[len(z)//2],'min':z[0],'max':z[-1]}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--max-orders',type=int,default=90);ap.add_argument('--max-orders-per-market',type=int,default=0);ap.add_argument('--horizon-ms',type=int,default=5000);ap.add_argument('--out',type=Path,default=OUT);a=ap.parse_args()
 mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
 for mid in mids:
  events,_,_=tape_v1.build_archive_events(mid,trade_offset='mid');paper=load_reference_paper(mid)
  market_n=0
  for o in paper['orders']:
   if len(rows)>=a.max_orders:break
   if a.max_orders_per_market>0 and market_n>=a.max_orders_per_market: break
   side=str(o['side']).upper();start=int(o['placed_at_ms']);qty=float(o.get('shares') or 18.0);variants={}
   for off in (1,2,3):
    px=quote_at(mid,start,side,off)
    if px is None:continue
    z=sim(events,side,px,qty,start,a.horizon_ms);z['price']=px;z['offset']=off;z['markout1sTicks']=markout(mid,side,z['fillMs']) if z.get('fillMs') and z['fillShares']>EPS else None
    variants[str(off)]=z
   rows.append({'marketId':mid,'placedAtMs':start,'side':side,'qty':qty,'referencePrice':float(o['price']),'variants':variants});market_n+=1
   if len(rows)%20==0:print(json.dumps({'progress':len(rows),'marketId':mid},ensure_ascii=False),flush=True)
  if len(rows)>=a.max_orders:break
 agg={}
 for off in ('1','2','3'):
  vs=[r['variants'][off] for r in rows if off in r['variants']];agg[off]={'orders':len(vs),'fillRate':stats([v['fillRate'] for v in vs]),'filledShares':sum(v['fillShares'] for v in vs),'fillLatencyMs':stats([v['fillLatencyMs'] for v in vs]),'markout1sTicks':stats([v['markout1sTicks'] for v in vs])}
 rep={'version':'R2_PLACEMENT_DEPTH_COUNTERFACTUAL_V0','researchOnly':True,'dreamFillAllowed':False,'markets':mids,'orders':len(rows),'horizonMs':a.horizon_ms,'aggregate':agg,'rows':rows,'guardrails':['Same frozen R2 placement timestamps/sides/qty.','Only quote depth differs: best bid minus 1/2/3 ticks.','All fills are HftBacktest Execution Tape V1.','Markout is label-only public-book observation.','No winner/PnL/Target input.','No parameter selection from this reused cohort.']}
 a.out.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'report':str(a.out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
