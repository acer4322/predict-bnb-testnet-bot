from __future__ import annotations
import argparse,json,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_reference_paper
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
EPS=1e-9

def sim(events,side,px,qty,start,end):
 bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,start); rc=ex.submit_native(bt,1,side,px,qty); ex.advance_to(bt,end); s=ex.order_snapshot(bt,1); return {'submitRc':rc,'fill':float(s.get('cumExecQty') or 0.0),'leaves':s.get('leavesQty'),'status':s.get('status'),'price':px,'cost':float(s.get('cumExecQty') or 0.0)*px}
 finally: bt.close()

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--max-orders',type=int,default=60); ap.add_argument('--horizon-ms',type=int,default=5000); a=ap.parse_args(); mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; rows=[]
 for mid in mids:
  events,_,_=tape_v1.build_archive_events(mid,trade_offset='mid'); paper=load_reference_paper(mid)
  for o in paper['orders']:
   if len(rows)>=a.max_orders:break
   side=str(o['side']).upper(); old=round(float(o['price']),2); qty=float(o.get('shares') or 18.0); start=int(o['placed_at_ms']); end=start+a.horizon_ms
   plus=round(min(0.99,old+0.01),2)
   base=sim(events,side,old,qty,start,end); aggr=sim(events,side,plus,qty,start,end)
   rows.append({'marketId':mid,'placedAtMs':start,'side':side,'qty':qty,'oldPrice':old,'plus1Price':plus,'base':base,'plus1':aggr,'deltaFill':aggr['fill']-base['fill'],'deltaCost':aggr['cost']-base['cost']}); print(json.dumps({'progress':len(rows),'marketId':mid,'side':side,'old':old,'plus1':plus,'baseFill':base['fill'],'plus1Fill':aggr['fill'],'delta':aggr['fill']-base['fill']},ensure_ascii=False),flush=True)
  if len(rows)>=a.max_orders:break
 n=len(rows); b=sum(r['base']['fill'] for r in rows); g=sum(r['plus1']['fill'] for r in rows); better=sum(r['deltaFill']>EPS for r in rows); worse=sum(r['deltaFill']<-EPS for r in rows); fullb=sum(r['base']['fill']>=r['qty']-EPS for r in rows); fullg=sum(r['plus1']['fill']>=r['qty']-EPS for r in rows)
 agg={'orders':n,'baseFilledShares':b,'plus1FilledShares':g,'deltaFilledShares':g-b,'baseRealizationRate':b/sum(r['qty'] for r in rows) if rows else None,'plus1RealizationRate':g/sum(r['qty'] for r in rows) if rows else None,'baseFullFillRate':fullb/n if n else None,'plus1FullFillRate':fullg/n if n else None,'plus1Better':better,'baseBetter':worse,'equal':n-better-worse,'meanExtraCost':sum(r['deltaCost'] for r in rows)/n if n else None}
 out=OUT/'initial_placement_counterfactual_v0.json'; out.write_text(json.dumps({'version':'INITIAL_PLACEMENT_COUNTERFACTUAL_V0','researchOnly':True,'dreamFillAllowed':False,'horizonMs':a.horizon_ms,'markets':mids,'aggregate':agg,'rows':rows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'report':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
