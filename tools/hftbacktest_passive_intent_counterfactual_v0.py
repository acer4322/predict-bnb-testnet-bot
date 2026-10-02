from __future__ import annotations
import argparse,json,math,sys
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import run_market
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
EPS=1e-9

def cancel_if_live(bt:Any,num:int)->bool:
 o=bt.orders(0).get(int(num))
 if o is None:return False
 if int(o.status) in {int(ex.NEW),int(ex.PARTIALLY_FILLED)} and bool(o.cancellable):
  try: bt.cancel(0,int(num),False); return True
  except Exception:return False
 return False

def sim_keep(events,side,old_px,qty,placed,cp,end)->dict[str,Any]:
 bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,placed); rc=ex.submit_native(bt,1,side,old_px,qty); ex.advance_to(bt,cp); s0=ex.order_snapshot(bt,1); ex.advance_to(bt,end); s1=ex.order_snapshot(bt,1)
  before=float(s0.get('cumExecQty') or 0); final=float(s1.get('cumExecQty') or 0); add=max(0.0,final-before)
  return {'submitRc':rc,'beforeCum':before,'beforeLeaves':s0.get('leavesQty'),'beforeStatus':s0.get('status'),'endCum':final,'endLeaves':s1.get('leavesQty'),'endStatus':s1.get('status'),'additionalFill':add,'additionalCost':add*old_px}
 finally: bt.close()

def sim_move(events,side,old_px,new_px,qty,placed,cp,end)->dict[str,Any]:
 bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
 try:
  ex.advance_to(bt,placed); rc0=ex.submit_native(bt,1,side,old_px,qty); ex.advance_to(bt,cp); s0=ex.order_snapshot(bt,1); before=float(s0.get('cumExecQty') or 0); cancelled=cancel_if_live(bt,1)
  # Sequential cancel/replace: let the response latency elapse, then size replacement from actual leaves.
  cancel_settle=min(end,cp+273); ex.advance_to(bt,cancel_settle); sc=ex.order_snapshot(bt,1); old_after=float(sc.get('cumExecQty') or 0); old_add=max(0.0,old_after-before); leaves=max(0.0,qty-old_after)
  rc1=None; new_fill=0.0; sn={}
  if leaves>EPS and cancel_settle<end:
   rc1=ex.submit_native(bt,2,side,new_px,leaves); ex.advance_to(bt,end); sn=ex.order_snapshot(bt,2); new_fill=float(sn.get('cumExecQty') or 0)
  else: ex.advance_to(bt,end)
  add=old_add+new_fill
  return {'originalSubmitRc':rc0,'cancelRequested':cancelled,'beforeCum':before,'cancelSettleCum':old_after,'oldAdditionalFill':old_add,'replacementPrice':new_px,'replacementQty':leaves,'replacementSubmitRc':rc1,'replacementFill':new_fill,'replacementStatus':sn.get('status') if sn else None,'additionalFill':add,'additionalCost':old_add*old_px+new_fill*new_px}
 finally: bt.close()

CURRENT_ARGS=None

def candidates(mid:int,limit:int)->list[dict[str,Any]]:
 rep=run_market(mid); rows=rep.get('orderStateRows') or []
 bycp={}
 for r in rows: bycp.setdefault(int(r['checkpointMs']),[]).append(r)
 seen=set(); out=[]
 for r in rows:
  ctx=str(r.get('context') or '')
  if not ctx.startswith('BEFORE_ADD:'): continue
  oid=str(r.get('orderId') or '')
  if oid in seen: continue
  if str(r.get('hftStatus')) not in {'NEW','PARTIALLY_FILLED'} or float(r.get('remainingQty') or 0)<=EPS: continue
  side=str(r['side']); action_side=ctx.split(':')[1] if ':' in ctx else ''
  if action_side!=side: continue
  old=float(r['price']); bid=r.get('currentBid')
  if bid is None or not math.isfinite(float(bid)): continue
  # Limit MOVE to one tick toward current best. Respect R2 pair-price cap using simultaneously active opposite rows.
  opp='DOWN' if side=='UP' else 'UP'; opp_prices=[float(x['price']) for x in bycp.get(int(r['checkpointMs']),[]) if str(x.get('side'))==opp and str(x.get('hftStatus')) in {'NEW','PARTIALLY_FILLED'} and x.get('price') is not None]
  cap=float(mod.MAX_PAIR_PRICE_SUM)-max(opp_prices) if opp_prices else 1.0
  raw_move=min(float(bid),cap) if getattr(CURRENT_ARGS,'move_mode','one_tick')=='best' else min(float(bid),old+float(mod.GRID),cap); new=round(math.floor((raw_move+1e-9)/mod.GRID)*mod.GRID,2)
  if new<=old+EPS: continue
  seen.add(oid); z=dict(r); z['movePrice']=new; z['oppMaxPrice']=max(opp_prices) if opp_prices else None; out.append(z)
  if len(out)>=limit: break
 return out

def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--market-ids',required=True); ap.add_argument('--max-states',type=int,default=30); ap.add_argument('--horizon-ms',type=int,default=5000); ap.add_argument('--move-mode',choices=['one_tick','best'],default='one_tick'); a=ap.parse_args(); global CURRENT_ARGS; CURRENT_ARGS=a; mids=[int(x) for x in a.market_ids.split(',') if x.strip()]; states=[]
 for mid in mids:
  states.extend(candidates(mid,max(0,a.max_states-len(states))))
  if len(states)>=a.max_states:break
 results=[]; event_cache={}
 for i,r in enumerate(states,1):
  mid=int(r['marketId']); events=event_cache.get(mid)
  if events is None: events,_,_=tape_v1.build_archive_events(mid,trade_offset='mid'); event_cache[mid]=events
  placed=int(r['placedAtMs']); cp=int(r['checkpointMs']); end=cp+int(a.horizon_ms); qty=float(r.get('originalQty') or (float(r.get('cumExecQty') or 0)+float(r.get('remainingQty') or 0)) or 18.0); old=float(r['price']); new=float(r['movePrice']); side=str(r['side'])
  k=sim_keep(events,side,old,qty,placed,cp,end); m=sim_move(events,side,old,new,qty,placed,cp,end)
  row={'marketId':mid,'checkpointMs':cp,'orderId':r['orderId'],'context':r['context'],'side':side,'oldPrice':old,'movePrice':new,'orderAgeMs':r.get('orderAgeMs'),'quoteOffsetTicks':r.get('quoteOffsetTicks'),'remainingQtyObserved':r.get('remainingQty'),'oppMaxPrice':r.get('oppMaxPrice'),'keep':k,'move':m,'deltaFillMoveMinusKeep':m['additionalFill']-k['additionalFill'],'deltaCostMoveMinusKeep':m['additionalCost']-k['additionalCost']}
  results.append(row); print(json.dumps({'progress':i,'marketId':mid,'age':r.get('orderAgeMs'),'old':old,'move':new,'keepFill':k['additionalFill'],'moveFill':m['additionalFill'],'delta':row['deltaFillMoveMinusKeep']},ensure_ascii=False),flush=True)
 n=len(results); better=sum(x['deltaFillMoveMinusKeep']>EPS for x in results); worse=sum(x['deltaFillMoveMinusKeep']<-EPS for x in results); equal=n-better-worse
 agg={'states':n,'moveBetter':better,'keepBetter':worse,'equal':equal,'moveBetterRate':better/n if n else None,'meanKeepAdditionalFill':sum(x['keep']['additionalFill'] for x in results)/n if n else None,'meanMoveAdditionalFill':sum(x['move']['additionalFill'] for x in results)/n if n else None,'meanDeltaFill':sum(x['deltaFillMoveMinusKeep'] for x in results)/n if n else None,'totalKeepAdditionalFill':sum(x['keep']['additionalFill'] for x in results),'totalMoveAdditionalFill':sum(x['move']['additionalFill'] for x in results),'meanExtraCostWhenMove':sum(x['deltaCostMoveMinusKeep'] for x in results)/n if n else None}
 out=OUT/'passive_intent_counterfactual_v0.json'; out.write_text(json.dumps({'version':'PASSIVE_INTENT_COUNTERFACTUAL_V0','researchOnly':True,'dreamFillAllowed':False,'actionSpace':['KEEP_CURRENT_CHILD',('MOVE_TO_BEST_PASSIVE' if a.move_mode=='best' else 'MOVE_PASSIVE_1T')],'moveMode':a.move_mode,'horizonMs':a.horizon_ms,'markets':mids,'aggregate':agg,'rows':results},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8'); print(json.dumps({'ok':True,'report':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
