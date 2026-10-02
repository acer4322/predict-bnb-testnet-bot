from __future__ import annotations
import argparse,json,math,sqlite3,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import hftbacktest_r2_execution_school_v0 as base
from tools import hftbacktest_r3_r31_maker10_adapter_v1 as mk10
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.run_r4_threeway10_benchmark_chunk_v1 import score
from tools.analyze_r4_economic_overrepair_replay_v1 import audit_rep
EPS=1e-9;TERMINAL={'FILLED','CANCELED','REJECTED','EXPIRED'}

def run_refresh(mid:int,threshold_ticks:float,persistence_ms:int,block_taker_pending:bool=True):
 orig_new=base.new_controller;events=[]
 def refresh_new(a):
  c=orig_new(a);orig_step=c._step
  pending={};candidate_start={};last_cum={}
  def live_same_reservation(side:str,exclude_oid:str|None=None):
   z=[];total=0.0
   for key,o in list(c.orders.items()):
    if str(o.side)!=side or (exclude_oid and str(o.id)==exclude_oid):continue
    s=a.snap(key);st=str(s.get('status') or '')
    if st in {'NEW','PARTIALLY_FILLED'}:
     q=max(0.0,float(s.get('leavesQty') or 0.0));total+=q;z.append({'orderId':str(o.id),'remaining':q,'price':float(o.price),'status':st})
   return total,z
  def step(snapshot):
   now=int(snapshot.get('sampledAtMs') or 0);ns=int(snapshot.get('timestampNs') or now*1_000_000)
   # During cancel uncertainty, keep ownership and block same-side stacking. Active repair is also blocked until terminal certainty.
   current_add=c._add_order;current_taker=c._record_taker
   def add_gate(side,*args,**kwargs):
    if any(str(p.get('side'))==str(side) for p in pending.values()):
     events.append({'marketId':mid,'atMs':now,'event':'ADD_BLOCK_CANCEL_PENDING','side':side});return False
    return current_add(side,*args,**kwargs)
   def taker_gate(*args,**kwargs):
    if block_taker_pending and pending:
     events.append({'marketId':mid,'atMs':now,'event':'TAKER_BLOCK_CANCEL_PENDING','pending':len(pending)});return False
    return current_taker(*args,**kwargs)
   c._add_order=add_gate;c._record_taker=taker_gate
   try:orig_step(snapshot)
   finally:c._add_order=current_add;c._record_taker=current_taker
   # Reconcile requested cancels after the controller applied current HFT terminal/fill state.
   for oid,p in list(pending.items()):
    key=tuple(p['key']);s=a.snap(key);st=str(s.get('status') or '')
    still=any(str(o.id)==oid for o in c.orders.values())
    if st not in TERMINAL and still:continue
    f=c.inventory.features(now);net=float(f.get('combined_net') or 0.0);gap=abs(net);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None;side=str(p['side']);reserved,carriers=live_same_reservation(side,oid);unowned=max(0.0,gap-reserved) if weak==side else 0.0
    events.append({'marketId':mid,'atMs':now,'event':'CANCEL_TERMINAL_RECONCILE','orderId':oid,'side':side,'terminalStatus':st,'weakSideNow':weak,'gapNow':gap,'otherLiveReserved':reserved,'unownedRemainder':unowned,'cancelLatencyMs':now-int(p['requestedAtMs']),'otherCarriers':carriers})
    pending.pop(oid,None);candidate_start.pop(oid,None);last_cum.pop(oid,None)
    if weak!=side or unowned<=EPS:
     events.append({'marketId':mid,'atMs':now,'event':'REFRESH_NO_REPLACEMENT','side':side,'reason':'OBLIGATION_CHANGED_OR_ALREADY_OWNED','unownedRemainder':unowned});continue
    qty=min(10.0,float(unowned));qr=c._quote(side,1)
    if qr is None:
     events.append({'marketId':mid,'atMs':now,'event':'REFRESH_WAIT_NO_QUOTE','side':side,'qty':qty});continue
    _,px=qr
    if qty*float(px)<1.0-1e-9:
     events.append({'marketId':mid,'atMs':now,'event':'DUST_REPAIR_PENDING','side':side,'qty':qty,'price':float(px),'notional':qty*float(px)});continue
    old=float(base.mod.SHARES);base.mod.SHARES=float(qty)
    try:made=bool(current_add(side,now,ns,f'ADAPTIVE_REFRESH:{mid}:{now}','ADAPTIVE_REFRESH_REPAIR',0.0,snapshot,allow_stack=True,bypass_guard=True))
    finally:base.mod.SHARES=old
    events.append({'marketId':mid,'atMs':now,'event':'REFRESH_REPLACEMENT' if made else 'REFRESH_REPLACEMENT_BLOCKED','side':side,'qty':qty,'price':float(px),'gapNow':gap,'otherLiveReserved':reserved,'unownedRemainder':unowned})
   if pending:return
   # Stale-candidate detection is stateful and progress-resettable. It runs only during an existing repair episode.
   if c.episode is None:return
   f=c.inventory.features(now);net=float(f.get('combined_net') or 0.0);gap=abs(net);weak='DOWN' if net>EPS else 'UP' if net<-EPS else None
   if not weak:return
   bf=base.mod.outcome_book(c.book.book,None) or {};bid=float(bf.get('up_bid' if weak=='UP' else 'down_bid') or math.nan)
   candidates=[]
   for key,o in list(c.orders.items()):
    if str(o.side)!=weak:continue
    s=a.snap(key);st=str(s.get('status') or '')
    if st not in {'NEW','PARTIALLY_FILLED'}:continue
    oid=str(o.id);cum=float(s.get('cumExecQty') or 0.0);prev=last_cum.get(oid)
    if prev is not None and cum>prev+EPS:
     candidate_start.pop(oid,None);events.append({'marketId':mid,'atMs':now,'event':'STALE_RESET_BY_FILL_PROGRESS','orderId':oid,'side':weak,'deltaCum':cum-prev})
    last_cum[oid]=cum
    off=(bid-float(o.price))/base.mod.GRID if math.isfinite(bid) else math.nan
    if not math.isfinite(off) or off<float(threshold_ticks)-1e-9:
     if oid in candidate_start:events.append({'marketId':mid,'atMs':now,'event':'STALE_RESET_BY_PRICE_RECOVERY','orderId':oid,'side':weak,'offsetTicks':off})
     candidate_start.pop(oid,None);continue
    candidate_start.setdefault(oid,now);age=now-int(candidate_start[oid]);
    if age>=int(persistence_ms):candidates.append((float(off),int(o.placed_at_ms),key,o,s,candidate_start[oid]))
   if not candidates:return
   # One mutation at a time: refresh the most drifted, then oldest carrier.
   off,placed,key,o,s,started=max(candidates,key=lambda z:(z[0],-z[1]));oid=str(o.id);a.cancel(key);pending[oid]={'side':weak,'key':list(key),'requestedAtMs':now,'price':float(o.price),'remainingAtRequest':float(s.get('leavesQty') or 0.0),'gapAtRequest':gap,'offsetTicks':off,'candidateStartedMs':int(started)};events.append({'marketId':mid,'atMs':now,'event':'REFRESH_CANCEL_REQUEST','orderId':oid,'side':weak,'price':float(o.price),'remaining':float(s.get('leavesQty') or 0.0),'gapAtRequest':gap,'offsetTicks':off,'candidatePersistenceMs':now-int(started),'thresholdTicks':float(threshold_ticks)})
  c._step=step;c.adaptive_refresh_events=events;return c
 base.new_controller=refresh_new
 try:rep,audit=mk10.run_market(mid)
 finally:base.new_controller=orig_new
 rep['adaptiveMakerRefresh']={'version':'R4_ADAPTIVE_MAKER_REFRESH_CAUSAL_V1','thresholdTicks':float(threshold_ticks),'persistenceMs':int(persistence_ms),'blockTakerWhileCancelPending':bool(block_taker_pending),'events':events,'stats':{'cancelRequests':sum(e.get('event')=='REFRESH_CANCEL_REQUEST' for e in events),'terminalReconciles':sum(e.get('event')=='CANCEL_TERMINAL_RECONCILE' for e in events),'replacements':sum(e.get('event')=='REFRESH_REPLACEMENT' for e in events),'noReplacement':sum(e.get('event')=='REFRESH_NO_REPLACEMENT' for e in events),'dustPending':sum(e.get('event')=='DUST_REPAIR_PENDING' for e in events),'takerPendingBlocks':sum(e.get('event')=='TAKER_BLOCK_CANCEL_PENDING' for e in events),'progressResets':sum(e.get('event')=='STALE_RESET_BY_FILL_PROGRESS' for e in events),'priceRecoveryResets':sum(e.get('event')=='STALE_RESET_BY_PRICE_RECOVERY' for e in events)}}
 return rep,audit

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--ids-json',required=True);ap.add_argument('--data-root',required=True);ap.add_argument('--threshold-ticks',type=float,required=True);ap.add_argument('--persistence-ms',type=int,required=True);ap.add_argument('--output',required=True);ap.add_argument('--no-block-taker-pending',action='store_true');a=ap.parse_args();dr=Path(a.data_root).resolve();base.STRATEGY_DB=dr/'strategy_target_compare_v1.db';base.mod.BOOK_DB=dr/'wallet_maker_book_inference.db';ex.BOOK_DB=dr/'wallet_maker_book_inference.db';tape_v1.ARCHIVE_DIR=dr/'execution_tape_v1/markets';settle=dr/'target_wallet_official_v1.db';ids=[int(x) for x in json.loads(Path(a.ids_json).read_text())];rows=[];errors=[]
 def winner(mid):
  c=sqlite3.connect(settle);r=c.execute("select winner from target_market_results where market_id=? and asset='BTC'",(mid,)).fetchone();c.close();
  if not r or str(r[0]) not in {'UP','DOWN'}:raise RuntimeError(f'no settlement {mid}')
  return str(r[0])
 for mid in ids:
  try:
   A,_=mk10.run_market(mid);B,_=run_refresh(mid,a.threshold_ticks,a.persistence_ms,not a.no_block_taker_pending);w=winner(mid);sa=score(A,w);sb=score(B,w);_,oa=audit_rep(mid,'BASELINE',A);_,ob=audit_rep(mid,'ADAPTIVE_REFRESH',B);st=B.get('adaptiveMakerRefresh',{}).get('stats',{});row={'marketId':mid,'baseline':sa,'candidate':sb,'deltaPnl':sb['pnlUsdt']-sa['pnlUsdt'],'deltaFloor':sb['finalFloor']-sa['finalFloor'],'deltaAbsNet':sb['finalAbsNet']-sa['finalAbsNet'],'deltaMakerFilledShares':sb['makerFilledShares']-sa['makerFilledShares'],'deltaTakerFilledShares':sb['takerFilledShares']-sa['takerFilledShares'],'overrepairBaseline':oa,'overrepairCandidate':ob,'refreshStats':st};rows.append(row);print(json.dumps({'marketId':mid,'deltaPnl':row['deltaPnl'],'deltaFloor':row['deltaFloor'],'deltaAbsNet':row['deltaAbsNet'],'refreshStats':st},ensure_ascii=False),flush=True)
  except Exception as e:errors.append({'marketId':mid,'error':f'{type(e).__name__}:{e}'});print(json.dumps(errors[-1]),flush=True)
 ds=[r['deltaPnl'] for r in rows];out={'version':'R4_ADAPTIVE_MAKER_REFRESH_CAUSAL_V1','researchOnly':True,'actionAuthority':False,'thresholdTicks':a.threshold_ticks,'persistenceMs':a.persistence_ms,'blockTakerWhileCancelPending':not a.no_block_taker_pending,'rows':rows,'errors':errors,'aggregate':{'markets':len(rows),'errors':len(errors),'totalDeltaPnl':float(sum(ds)),'improvements':sum(x>1e-9 for x in ds),'degradations':sum(x<-1e-9 for x in ds),'ties':sum(abs(x)<=1e-9 for x in ds),'worstDeltaPnl':min(ds,default=None),'bestDeltaPnl':max(ds,default=None),'meanDeltaFloor':float(np.mean([r['deltaFloor'] for r in rows])) if rows else None,'meanDeltaAbsNet':float(np.mean([r['deltaAbsNet'] for r in rows])) if rows else None,'cancelRequests':sum(int(r['refreshStats'].get('cancelRequests') or 0) for r in rows),'replacements':sum(int(r['refreshStats'].get('replacements') or 0) for r in rows),'noReplacement':sum(int(r['refreshStats'].get('noReplacement') or 0) for r in rows),'takerPendingBlocks':sum(int(r['refreshStats'].get('takerPendingBlocks') or 0) for r in rows),'meanDeltaMakerFilledShares':float(np.mean([r['deltaMakerFilledShares'] for r in rows])) if rows else None,'meanDeltaTakerFilledShares':float(np.mean([r['deltaTakerFilledShares'] for r in rows])) if rows else None,'hardOverRepairBaseline':sum(int(r['overrepairBaseline'].get('hardOverRepair') or 0) for r in rows),'hardOverRepairCandidate':sum(int(r['overrepairCandidate'].get('hardOverRepair') or 0) for r in rows),'positiveFloorBreakBaseline':sum(int(r['overrepairBaseline'].get('positiveFloorBreak') or 0) for r in rows),'positiveFloorBreakCandidate':sum(int(r['overrepairCandidate'].get('positiveFloorBreak') or 0) for r in rows),'expensivePairBaseline':sum(int(r['overrepairBaseline'].get('expensivePairProxy') or 0) for r in rows),'expensivePairCandidate':sum(int(r['overrepairCandidate'].get('expensivePairProxy') or 0) for r in rows),'poorRepairBaseline':sum(int(r['overrepairBaseline'].get('poorRepairProxy') or 0) for r in rows),'poorRepairCandidate':sum(int(r['overrepairCandidate'].get('poorRepairProxy') or 0) for r in rows)}};Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8');print(json.dumps(out['aggregate'],ensure_ascii=False),flush=True)
if __name__=='__main__':main()
