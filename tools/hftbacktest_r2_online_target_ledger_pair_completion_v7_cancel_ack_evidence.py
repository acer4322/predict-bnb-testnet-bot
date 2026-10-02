from __future__ import annotations

import argparse, copy, json, math, sys
from pathlib import Path
from typing import Any, Callable
import joblib
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import HftBookAdapter,load_public_snapshots,load_reference_paper,new_controller
from tools.hftbacktest_target_ledger_executor_v0 import r2_passive_quote
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod

OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
MODEL_PATH=OUT/'sequential_arbitration_option_v1.joblib'
EPS=1e-9; CHUNK=float(mod.SHARES); TERMINAL={'FILLED','REJECTED','EXPIRED','CANCELED'}; TAKER_CONFIRM_MS=2200


def finite(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan


def max_drawdown(xs:list[float])->float:
    peak=cur=dd=0.0
    for x in xs:
        cur+=x; peak=max(peak,cur); dd=max(dd,peak-cur)
    return dd


def run_market(
    mid:int,
    execution_observer:Callable[[dict[str,Any]],Any]|None=None,
    r21_inbox_provider:Callable[[dict[str,Any]],dict[str,Any]|None]|None=None,
    lifecycle_action_override:Callable[[dict[str,Any]],str|None]|None=None,
)->dict[str,Any]:
    snaps=load_public_snapshots(mid)
    if not snaps: raise RuntimeError(f'no snapshots for {mid}')
    paper=load_reference_paper(mid); win=winners([mid]).get(mid)
    art=joblib.load(MODEL_PATH); variant=art['currentOnly']; feats=list(variant['features']); models=variant['models']; thresholds=art['thresholds']
    a=HftBookAdapter(mid,1092,273,'risk','mid'); c=new_controller(a); actual=mod.Inventory()
    desired={'UP':0.0,'DOWN':0.0}; active={'UP':None,'DOWN':None}; maker_meta={}; taker_meta={}; route_block={'UP':False,'DOWN':False}; pending_replace={}; replace_history=[]; remainder_owner={'UP':None,'DOWN':None}; target_revision={'UP':0,'DOWN':0}; ownership_events=[]
    maker_intents=[]; maker_fills=[]; maker_submits=[]; taker_attempts=[]; taker_fills=[]; lifecycle=[]; decisions=[]; fill_log=[];r21_inbox_traces=[]
    last_maker_fill_ms=None; last_maker_fill_side=None; asym_since=None; last_eval_ms=None; episode_action=None; episode_checkpoint_idx=0; taker_fees=0.0; last_area=None; exposure_area=0.0; tracking_area=0.0
    orig_add=c._add_order; orig_taker=c._record_taker

    def actual_side(side): return actual.maker_up if side=='UP' else actual.maker_down
    def combined_side(side): return (actual.maker_up+actual.taker_up) if side=='UP' else (actual.maker_down+actual.taker_down)
    def target_net(): return desired['UP']-desired['DOWN']
    def actual_net(): return combined_side('UP')-combined_side('DOWN')

    def execution_state(now:int,snap:dict[str,Any])->dict[str,Any]:
        portfolio=actual.features(now);portfolio.pop('_combined_net',None);children={}
        for side in ('UP','DOWN'):
            num=active.get(side)
            if num is None:children[side]=None;continue
            meta=maker_meta[int(num)];order=ex.order_snapshot(a.bt,int(num));cancel_pending=any(p.get('state')=='CANCEL_REQUESTED' and int(p.get('childOrderNum') or -1)==int(num) for p in pending_replace.values())
            children[side]={'orderNum':int(num),'side':side,'price':meta.get('price'),'requestedQty':meta.get('qty'),'submittedAtMs':meta.get('submittedAtMs'),'status':order.get('status'),'cumExecQty':float(order.get('cumExecQty') or 0.0),'leavesQty':float(order.get('leavesQty') or 0.0),'cancelPending':cancel_pending}
        return {'asOfMs':now,'publicState':copy.deepcopy(snap),'outcomeBook':mod.outcome_book(c.book.book,None) or {},'actualPortfolio':portfolio,'desiredMakerShares':copy.deepcopy(desired),'trackingError':actual_net()-target_net(),'targetRevision':copy.deepcopy(target_revision),'activeMakerChildren':children,'openTakerChildren':[],'recentActualFills10s':[copy.deepcopy(x) for x in fill_log if now-int(x.get('eventMs') or 0)<=10_000],'routeBlock':copy.deepcopy(route_block),'remainderOwner':copy.deepcopy(remainder_owner),'pendingReplace':copy.deepcopy(pending_replace),'behaviorMemory':{}}

    def area(now:int):
        nonlocal last_area,exposure_area,tracking_area
        if last_area is not None and now>last_area:
            dt=(now-last_area)/1000.0; n=actual_net(); exposure_area+=abs(n)*dt; tracking_area+=abs(n-target_net())*dt
        last_area=now

    def submit_taker(side:str,qty:float,ask:float,now:int,kind:str)->int:
        maxp=min(.99,float(ask)+.02); num=int(a.next_num); a.next_num+=1; native_side,native_px=ex.native_order(side,maxp)
        if native_side=='BUY': rc=int(a.bt.submit_buy_order(0,num,native_px,float(qty),ex.hbt.GTC,ex.LIMIT,False))
        else: rc=int(a.bt.submit_sell_order(0,num,native_px,float(qty),ex.hbt.GTC,ex.LIMIT,False))
        taker_meta[num]={'side':side,'qty':float(qty),'observedPrice':float(ask),'prevCum':0.0,'submittedAtMs':now,'deadlineMs':now+TAKER_CONFIRM_MS,'cancelRequested':False,'terminal':None,'kind':kind,'submitRc':rc,'lifecycleState':('SUBMITTED' if rc==0 else 'TERMINAL_ZERO_FILL'),'filledQty':0.0,'remainingQty':float(qty)}
        taker_attempts.append({'orderNum':num,**taker_meta[num]}); lifecycle.append({'atMs':now,'side':side,'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':taker_meta[num]['lifecycleState'],'requestedQty':float(qty),'kind':kind}); return num

    def harvest_maker(now:int):
        nonlocal last_maker_fill_ms,last_maker_fill_side
        for side in ('UP','DOWN'):
            num=active[side]
            if num is None: continue
            om=maker_meta[int(num)]; s=ex.order_snapshot(a.bt,int(num)); cum=float(s.get('cumExecQty') or 0.0); old=float(om.get('prevCum') or 0.0)
            if cum>old+EPS:
                q=cum-old; native=s.get('execPrice'); px=float(om['price'])
                if native is not None and math.isfinite(float(native)): px=float(native) if side=='UP' else 1.0-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000); actual.apply({'event_ms':fm,'role':'MAKER','side':side,'price':px,'shares':q}); om['prevCum']=cum
                maker_fills.append({'atMs':fm,'observedAtMs':now,'orderNum':int(num),'side':side,'price':px,'deltaShares':q,'cumShares':cum,'status':s.get('status')}); fill_log.append({'eventMs':fm,'role':'MAKER','side':side,'price':px,'shares':q,'fee':0.0}); last_maker_fill_ms=fm; last_maker_fill_side=side
            st=str(s.get('status') or 'NONE')
            if st in TERMINAL:
                om['terminalStatus']=st; om['terminalAtMs']=now; active[side]=None

    def harvest_taker(now:int):
        nonlocal taker_fees
        for num,om in taker_meta.items():
            if om.get('terminal'): continue
            s=ex.order_snapshot(a.bt,num); cum=float(s.get('cumExecQty') or 0.0); old=float(om.get('prevCum') or 0.0)
            st=str(s.get('status') or 'NONE')
            if om.get('lifecycleState')=='SUBMITTED' and st in {'NEW','PARTIALLY_FILLED'}:
                om['lifecycleState']='ACKED_OPEN'; lifecycle.append({'atMs':now,'side':om['side'],'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':'ACKED_OPEN','status':st,'kind':om['kind']})
            if cum>old+EPS:
                q=cum-old; native=s.get('execPrice'); px=float(om['observedPrice'])
                if native is not None and math.isfinite(float(native)): px=float(native) if om['side']=='UP' else 1.0-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000); actual.apply({'event_ms':fm,'role':'TAKER','side':om['side'],'price':px,'shares':q}); fee=mod.taker_fee(q,px,mod.FEE_BPS); taker_fees+=fee; om['prevCum']=cum; om['filledQty']=cum; om['remainingQty']=max(0.0,float(om['qty'])-cum)
                om['lifecycleState']='FILLED' if om['remainingQty']<=EPS else 'PARTIAL'
                lifecycle.append({'atMs':fm,'side':om['side'],'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':om['lifecycleState'],'filledQty':cum,'remainingQty':om['remainingQty'],'status':st,'kind':om['kind']})
                taker_fills.append({'atMs':fm,'observedAtMs':now,'orderNum':num,'side':om['side'],'price':px,'shares':q,'feeUsdt':fee,'kind':om['kind']}); fill_log.append({'eventMs':fm,'role':'TAKER','side':om['side'],'price':px,'shares':q,'fee':fee})
            if now>=int(om['deadlineMs']) and not om.get('cancelRequested') and st in {'NEW','PARTIALLY_FILLED'}:
                cur=a.bt.orders(0).get(num)
                if cur is not None and bool(cur.cancellable):
                    try:
                        a.bt.cancel(0,num,False);om['cancelRequested']=True;om['cancelRequestedAtMs']=now;om['lifecycleState']='CANCEL_REQUESTED';lifecycle.append({'atMs':now,'side':om['side'],'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':'CANCEL_REQUESTED','filledQty':float(om.get('filledQty') or 0.0),'remainingQty':float(om.get('remainingQty') or om['qty']),'kind':om['kind']})
                    except Exception:pass
            st=str(s.get('status') or 'NONE')
            if st in TERMINAL:
                om['terminal']=st;om['terminalAtMs']=now
                filled=float(om.get('prevCum') or 0.0); rem=max(0.0,float(om['qty'])-filled); om['filledQty']=filled;om['remainingQty']=rem
                terminal_state='FILLED' if rem<=EPS else ('TERMINAL_ZERO_FILL' if filled<=EPS else 'PARTIAL')
                om['lifecycleState']=terminal_state
                lifecycle.append({'atMs':now,'side':om['side'],'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':terminal_state,'filledQty':filled,'remainingQty':rem,'terminalStatus':st,'kind':om['kind']})

    def harvest(now:int): harvest_maker(now);harvest_taker(now)

    def submit_maker(side:str,now:int):
        owner=remainder_owner.get(side)
        if owner is not None and owner.get('state')=='RETURNED_UNRESOLVED' and int(owner.get('targetRevision') or 0)==int(target_revision[side]):
            ownership_events.append({'atMs':now,'side':side,'action':'BLOCK_REESCALATION_UNCHANGED_TARGET','remainingDeficit':float(owner.get('remainingDeficit') or 0.0),'targetRevision':int(target_revision[side]),'sourceReplaceIndex':owner.get('sourceReplaceIndex')})
            return
        if owner is not None and owner.get('state')=='RETURNED_UNRESOLVED' and int(owner.get('targetRevision') or 0)!=int(target_revision[side]):
            ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_RELEASE_ON_TARGET_REVISION','oldTargetRevision':int(owner.get('targetRevision') or 0),'newTargetRevision':int(target_revision[side]),'remainingDeficit':float(owner.get('remainingDeficit') or 0.0)})
            remainder_owner[side]=None
        if route_block[side] or active[side] is not None:return
        deficit=desired[side]-actual_side(side)
        if deficit<=EPS:return
        opp='DOWN' if side=='UP' else 'UP'; oppp=float(maker_meta[int(active[opp])]['price']) if active[opp] is not None else None; px=r2_passive_quote(c.book.book,side,oppp)
        if px is None:return
        qty=min(CHUNK,deficit); num=int(a.next_num);a.next_num+=1;rc=ex.submit_native(a.bt,num,side,px,qty)
        maker_meta[num]={'side':side,'price':px,'qty':qty,'prevCum':0.0,'submittedAtMs':now,'submitRc':int(rc)};maker_submits.append({'orderNum':num,**maker_meta[num]})
        if rc==0: active[side]=num

    def build_features(side:str,now:int,snap:dict[str,Any])->dict[str,Any]|None:
        bf=mod.outcome_book(c.book.book,None)
        if not bf:return None
        other='DOWN' if side=='UP' else 'UP'; bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']);ask=float(bf['up_ask'] if side=='UP' else bf['down_ask']);obid=float(bf['down_bid'] if side=='UP' else bf['up_bid']);oask=float(bf['down_ask'] if side=='UP' else bf['up_ask'])
        num=active[side]; child={'workingRecoveryExists':float(num is not None),'workingRecoveryAgeMs':None,'workingRecoveryOffsetTicks':None,'workingRecoveryRemainingQty':None,'recoveryStatusNew':0.0,'recoveryStatusPartial':0.0}
        if num is not None:
            om=maker_meta[int(num)];os=ex.order_snapshot(a.bt,int(num));st=str(os.get('status') or 'NONE');child.update({'workingRecoveryAgeMs':now-int(om['submittedAtMs']),'workingRecoveryOffsetTicks':(bid-float(om['price']))/mod.GRID,'workingRecoveryRemainingQty':float(os.get('leavesQty') or 0.0),'recoveryStatusNew':float(st=='NEW'),'recoveryStatusPartial':float(st=='PARTIALLY_FILLED')})
        err=actual_net()-target_net(); qty=min(CHUNK,abs(err)); need=qty;cc=cs=0.0
        for ev in reversed(fill_log):
            if ev['side']!=other or need<=EPS:continue
            q=min(need,float(ev['shares'])); feeps=float(ev.get('fee') or 0.0)/max(float(ev['shares']),EPS);cc+=q*(float(ev['price'])+feeps);cs+=q;need-=q
        avg=cc/cs if cs>EPS else None; fee1=mod.taker_fee(1.0,ask,mod.FEE_BPS); locked=1.0-avg-ask-fee1 if avg is not None else None; sign=1.0 if side=='UP' else -1.0
        f={'workingRecoveryExists':child['workingRecoveryExists'],'workingRecoveryAgeMs':child['workingRecoveryAgeMs'],'workingRecoveryOffsetTicks':child['workingRecoveryOffsetTicks'],'workingRecoveryRemainingQty':child['workingRecoveryRemainingQty'],'absTrackingError':abs(err),'trackingError':err,'actualMakerNet':actual.maker_up-actual.maker_down,'actualCombinedGross':combined_side('UP')+combined_side('DOWN'),'secondsLeft':snap.get('secondsLeft'),'recoveryBid':bid,'recoveryAsk':ask,'recoverySpreadTicks':(ask-bid)/mod.GRID,'pairAskSum':ask+oask,'pairBidSum':bid+obid,'marginalSurplusChunkAvgCost':avg,'lockedPairEdgePerShare':locked,'lastMakerFillAgeMs':(now-last_maker_fill_ms) if last_maker_fill_ms is not None else None,'lastMakerFillSideIsRecovery':float(last_maker_fill_side==side),'directionTowardRecovery':finite(snap.get('directionScore'))*sign if not math.isnan(finite(snap.get('directionScore'))) else None,'spotReturn1sTowardRecovery':finite(snap.get('spotReturn1sBps'))*sign if not math.isnan(finite(snap.get('spotReturn1sBps'))) else None,'spotReturn3sTowardRecovery':finite(snap.get('spotReturn3sBps'))*sign if not math.isnan(finite(snap.get('spotReturn3sBps'))) else None,'spotQueueTowardRecovery':finite(snap.get('spotQueueImbalance'))*sign if not math.isnan(finite(snap.get('spotQueueImbalance'))) else None,'spotTaker1sTowardRecovery':finite(snap.get('spotTakerImbalance1s'))*sign if not math.isnan(finite(snap.get('spotTakerImbalance1s'))) else None,'futuresReturn1sTowardRecovery':finite(snap.get('futuresReturn1sBps'))*sign if not math.isnan(finite(snap.get('futuresReturn1sBps'))) else None,'futuresReturn3sTowardRecovery':finite(snap.get('futuresReturn3sBps'))*sign if not math.isnan(finite(snap.get('futuresReturn3sBps'))) else None,'futuresQueueTowardRecovery':finite(snap.get('futuresQueueImbalance'))*sign if not math.isnan(finite(snap.get('futuresQueueImbalance'))) else None,'futuresTaker1sTowardRecovery':finite(snap.get('futuresTakerImbalance1s'))*sign if not math.isnan(finite(snap.get('futuresTakerImbalance1s'))) else None,'asymmetryAgeMs':(now-asym_since) if asym_since is not None else 0.0,'observationDelayMs':(now-asym_since) if asym_since is not None else 0.0,'hasPriorObservation':float(last_eval_ms is not None),'elapsedSincePriorMs':(now-last_eval_ms) if last_eval_ms is not None else 0.0,'recoveryStatusNew':child['recoveryStatusNew'],'recoveryStatusPartial':child['recoveryStatusPartial']}
        return f

    def classify(f:dict[str,Any])->tuple[str,float,float,float]:
        x=np.asarray([[finite(f.get(k)) for k in feats]],float);pa=float(models['act'].predict_proba(x)[0,1]);pw=float(models['wait'].predict_proba(x)[0,1]);pr=float(models['replace'].predict_proba(x)[0,1])
        if pa>=float(thresholds['act']): action='REPLACE_ROUTE' if pr>=float(thresholds['replace']) else 'KEEP_EXECUTING'
        else: action='WAIT_FOR_CLARITY' if pw>=float(thresholds['wait']) else 'RETURN_TO_CONTROLLER'
        return action,pa,pw,pr

    def lifecycle_step(now:int,snap:dict[str,Any]):
        nonlocal asym_since,last_eval_ms,episode_action,episode_checkpoint_idx
        checkpoints=(0,1000,2000,3000,5000,8000)
        err=actual_net()-target_net(); both=desired['UP']>EPS and desired['DOWN']>EPS; asymmetric=both and (actual.maker_up+actual.maker_down)>EPS and abs(err)>=CHUNK-EPS
        if asymmetric:
            if asym_since is None:
                asym_since=now; last_eval_ms=None; episode_action=None; episode_checkpoint_idx=0
        else:
            asym_since=None;last_eval_ms=None;episode_action=None;episode_checkpoint_idx=0
            for _side in ('UP','DOWN'):
                if remainder_owner.get(_side) is not None:
                    ownership_events.append({'atMs':now,'side':_side,'action':'OWNERSHIP_CLEARED_ASYMMETRY_RESOLVED','remainingDeficit':float(remainder_owner[_side].get('remainingDeficit') or 0.0)})
                    remainder_owner[_side]=None
            return
        if episode_action is not None:return
        age=now-int(asym_since)
        if episode_checkpoint_idx>=len(checkpoints):
            episode_action='RETURN_TO_CONTROLLER';lifecycle.append({'atMs':now,'side':'DOWN' if err>0 else 'UP','action':'RETURN_TO_CONTROLLER','forcedAtHorizon':True,'trackingError':err,'asymmetryAgeMs':age});return
        due=checkpoints[episode_checkpoint_idx]
        if age<due:return
        side='DOWN' if err>0 else 'UP'
        if route_block[side]:return
        f=build_features(side,now,snap)
        if f is None:return
        f['observationDelayMs']=float(due);f['asymmetryAgeMs']=float(age)
        action,pa,pw,pr=classify(f);default_action=action
        if lifecycle_action_override is not None:
            override=lifecycle_action_override({'atMs':now,'side':side,'defaultAction':default_action,'features':copy.deepcopy(f),'checkpointDelayMs':due,'trackingError':err,'activeChildOrderNum':active[side],'targetRevision':int(target_revision[side]),'executionState':execution_state(now,snap)})
            if override is not None:action=str(override)
        if action not in {'WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER'}:raise ValueError(f'unsupported lifecycle action override: {action}')
        lifecycle.append({'atMs':now,'side':side,'action':action,'defaultAction':default_action,'pAct':pa,'pWait':pw,'pReplaceIfAct':pr,'trackingError':err,'asymmetryAgeMs':age,'checkpointDelayMs':due,'childOrderNum':active[side],'childStatus':('NONE' if active[side] is None else ex.order_snapshot(a.bt,int(active[side])).get('status'))})
        last_eval_ms=now
        if action=='WAIT_FOR_CLARITY':
            episode_checkpoint_idx+=1
            return
        episode_action=action
        if action=='REPLACE_ROUTE':
            num=active[side];route_block[side]=True
            if num is None:
                bf=mod.outcome_book(c.book.book,None);ask=float(bf['up_ask'] if side=='UP' else bf['down_ask']);qty=min(CHUNK,abs(err));tn=submit_taker(side,qty,ask,now,'PAIR_COMPLETION_REPLACE');rec={'state':'TAKER_SENT','side':side,'takerOrderNum':tn,'triggerAtMs':now,'requestedQty':qty,'initialTrackingError':err};pending_replace[side]=rec;replace_history.append(rec);return
            os=ex.order_snapshot(a.bt,int(num));cur=a.bt.orders(0).get(int(num));rec={'state':'CANCEL_REQUESTED','side':side,'childOrderNum':int(num),'triggerAtMs':now,'cancelRequestedAtMs':None,'requestedQty':min(CHUNK,abs(err)),'initialTrackingError':err,'childLeavesAtTrigger':float(os.get('leavesQty') or 0.0)};pending_replace[side]=rec;replace_history.append(rec)
            if str(os.get('status') or 'NONE') in {'NEW','PARTIALLY_FILLED'} and cur is not None and bool(cur.cancellable):
                try:a.bt.cancel(0,int(num),False);pending_replace[side]['cancelRequestedAtMs']=now
                except Exception:pass
        # KEEP_EXECUTING and RETURN_TO_CONTROLLER intentionally make no venue mutation;
        # the episode remains censored until the asymmetric state truly clears.

    def service_replace(now:int):
        for side,p in list(pending_replace.items()):
            if p['state']=='CANCEL_REQUESTED':
                num=int(p['childOrderNum']); st=str(ex.order_snapshot(a.bt,num).get('status') or 'NONE')
                if st not in TERMINAL:continue
                ack_snap=ex.order_snapshot(a.bt,num); p['cancelAckEvidence']='VENUE_TERMINAL_OBSERVED'; p['cancelTerminalStatus']=st; p['cancelTerminalObservedAtMs']=now; p['cancelWaitMs']=now-int(p.get('cancelRequestedAtMs') or p.get('triggerAtMs') or now); p['childCumExecAtTerminal']=float(ack_snap.get('cumExecQty') or 0.0); p['childLeavesAtTerminal']=float(ack_snap.get('leavesQty') or 0.0)
                err=actual_net()-target_net();p['actualNetAtCancelAck']=actual_net();p['targetNetAtCancelAck']=target_net();p['trackingErrorAtCancelAck']=err;p['targetRevisionAtCancelAck']=int(target_revision[side]); needed=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP');qty=min(CHUNK,abs(err)) if needed else 0.0
                if qty>EPS:
                    bf=mod.outcome_book(c.book.book,None)
                    if bf:
                        ask=float(bf['up_ask'] if side=='UP' else bf['down_ask']);tn=submit_taker(side,qty,ask,now,'PAIR_COMPLETION_REPLACE');p.update({'state':'TAKER_SENT','takerOrderNum':tn,'ackAtMs':now,'recomputedQty':qty,'recomputedAsk':ask})
                else:
                    p.update({'state':'RESOLVED_DURING_CANCEL','ackAtMs':now,'recomputedQty':0.0});route_block[side]=False
                    if remainder_owner.get(side) is not None:
                        ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_CLEARED_RESOLVED_DURING_CANCEL'})
                        remainder_owner[side]=None
            elif p['state']=='TAKER_SENT':
                om=taker_meta[int(p['takerOrderNum'])]
                if om.get('terminal'):
                    err=actual_net()-target_net(); needed=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP'); remaining=min(CHUNK,abs(err)) if needed else 0.0
                    p['terminalAtMs']=now;p['filledShares']=float(om.get('prevCum') or 0.0);p['takerLifecycleState']=om.get('lifecycleState');p['remainingDeficitAfterTerminal']=remaining
                    if remaining<=EPS:
                        p['state']='DONE';p['completion']='ACTUAL_FILL_SATISFIED_TARGET'
                        if remainder_owner.get(side) is not None:
                            ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_CLEARED_TARGET_SATISFIED','sourceReplaceIndex':replace_history.index(p)})
                            remainder_owner[side]=None
                    else:
                        p['state']='RETURN_TO_CONTROLLER_UNRESOLVED';p['completion']='NOT_COMPLETED';p['returnedAtMs']=now
                        cause='TERMINAL_ZERO_FILL' if float(om.get('prevCum') or 0.0)<=EPS else ('FILLED_CHILD_TARGET_MOVED' if float(om.get('remainingQty') or 0.0)<=EPS else 'PARTIAL_CHILD_REMAINDER')
                        p['unresolvedCause']=cause
                        remainder_owner[side]={'state':'RETURNED_UNRESOLVED','owner':'CONTROLLER_RETURN','remainingDeficit':remaining,'returnedAtMs':now,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'cause':cause,'takerOrderNum':int(p['takerOrderNum'])}
                        ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_RETURNED_UNRESOLVED','remainingDeficit':remaining,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'cause':cause,'takerOrderNum':int(p['takerOrderNum'])})
                        lifecycle.append({'atMs':now,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'TAKER_NOT_COMPLETED','unresolvedCause':cause,'takerOrderNum':int(p['takerOrderNum']),'takerState':om.get('lifecycleState'),'filledShares':float(om.get('prevCum') or 0.0),'remainingDeficit':remaining})
                    route_block[side]=False

    def add_wrap(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
        before=set(c.orders);made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        if made:
            new=list(set(c.orders)-before)
            if new:
                o=c.orders[new[0]];desired[side]+=float(o.shares);target_revision[side]+=1;maker_intents.append({'atMs':now,'side':side,'shares':float(o.shares),'decisionId':decision_id,'intentId':o.id,'reason':reason,'desiredAfter':desired[side]})
        return made

    def taker_wrap(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect):
        # Frozen theory semantics only; actual execution remains separate and this opened cohort is development-only.
        orig_taker(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect);submit_taker(side,CHUNK,float(price),now,'FROZEN_R2')

    c._add_order=add_wrap;c._record_taker=taker_wrap
    try:
        for s in snaps:
            t=int(s['sampledAtMs'])
            if int(a.bt.current_timestamp//1_000_000)<t:ex.advance_to(a.bt,t)
            area(t);harvest(t);service_replace(t);controller_snapshot=dict(s)
            if r21_inbox_provider is not None:
                inbox=r21_inbox_provider(copy.deepcopy(controller_snapshot))
                if inbox is not None:
                    if not isinstance(inbox,dict) or inbox.get('version')!='R2.1' or inbox.get('mode')!='INFORMATION_ONLY' or inbox.get('actionAuthority') is not False:raise ValueError('invalid R2.1 information-only inbox')
                    events=inbox.get('incidents') or [];future=[str(x.get('eventId')) for x in events if not isinstance(x,dict) or int(x.get('atMs') or 0)>t]
                    if future:raise ValueError(f'R2.1 inbox contains future events: {future}')
                    controller_snapshot['r21ExecutionIncidentInbox']=copy.deepcopy(inbox);r21_inbox_traces.append({'atMs':t,'incidentCount':len(events),'incidentTypes':sorted({str(x.get('incidentType')) for x in events}),'actionAuthority':False})
            c._step(controller_snapshot)
            if c.last_decision is not None and int(c.last_decision.get('decisionMs') or -1)==t:decisions.append(copy.deepcopy(c.last_decision))
            lifecycle_step(t,s)
            for side in ('UP','DOWN'):submit_maker(side,t)
            if execution_observer is not None:execution_observer({'atMs':t,'trigger':'PUBLIC_SNAPSHOT','executionState':execution_state(t,s),'desiredPortfolio':copy.deepcopy(desired),'targetRevision':copy.deepcopy(target_revision)})
        terminal=int(a.meta['lastReceivedMs'])
        if int(a.bt.current_timestamp//1_000_000)<terminal:ex.advance_to(a.bt,terminal)
        area(terminal);harvest(terminal);service_replace(terminal)
        # Data-end is not a venue cancel ACK. Preserve ownership and mark uncertainty explicitly.
        for side,p in list(pending_replace.items()):
            if p.get('state')=='CANCEL_REQUESTED':
                p['state']='PENDING_AT_DATA_END';p['dataEndAtMs']=terminal;p['completion']='VENUE_STATUS_UNKNOWN_OWNERSHIP_PRESERVED';p['cancelAckEvidence']='DATA_END_NO_TERMINAL_OBSERVED';p['cancelWaitMs']=terminal-int(p.get('cancelRequestedAtMs') or p.get('triggerAtMs') or terminal);p['timeoutSemantics']='TIMEOUT_NEVER_RELEASES_OWNERSHIP_WITHOUT_VENUE_TERMINAL_EVIDENCE'
                remainder_owner[side]={'state':'PENDING_AT_DATA_END','owner':'CANCEL_PENDING_CHILD','remainingDeficit':min(CHUNK,abs(actual_net()-target_net())),'returnedAtMs':terminal,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'cause':'CANCEL_PENDING_AT_DATA_END','childOrderNum':int(p['childOrderNum']),'childLeavesAtTrigger':float(p.get('childLeavesAtTrigger') or 0.0)}
                ownership_events.append({'atMs':terminal,'side':side,'action':'OWNERSHIP_PRESERVED_CANCEL_PENDING_AT_DATA_END','targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'childOrderNum':int(p['childOrderNum'])})
                lifecycle.append({'atMs':terminal,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'CANCEL_PENDING_AT_DATA_END','childOrderNum':int(p['childOrderNum']),'ownership':'PRESERVED_NO_RELEASE'})
                route_block[side]=True
        strategy_port=c.inventory.features(terminal);strategy_port.pop('_combined_net',None);actual_port=actual.features(terminal);actual_port.pop('_combined_net',None)
    finally:a.close()
    md=desired['UP']+desired['DOWN'];mf=actual.maker_up+actual.maker_down;cu=combined_side('UP');cd=combined_side('DOWN');payout=cu if win=='UP' else cd if win=='DOWN' else None;cost=actual.maker_up_cost+actual.maker_down_cost+actual.taker_up_cost+actual.taker_down_cost+taker_fees;pnl=float(payout-cost) if payout is not None else None
    return {'version':'R2_ONLINE_TARGET_LEDGER_PAIR_COMPLETION_V7_CANCEL_ACK_EVIDENCE','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'marketId':mid,'winner':win,'paperReference':{'makerOrders':len(paper['orders']),'takerFills':len(paper['takers'])},'strategyRollout':{'makerIntents':len(maker_intents),'desiredMakerShares':md,'finalPaperStrategyPortfolio':strategy_port},'actualExecution':{'makerFilledShares':mf,'makerRealizationRate':mf/md if md>EPS else None,'takerFilledShares':actual.taker_up+actual.taker_down,'takerFeesUsdt':taker_fees,'combinedFinalAbsNet':abs(cu-cd),'finalAbsTrackingError':abs((cu-cd)-target_net()),'combinedExposureAreaShareSeconds':exposure_area,'targetErrorAreaShareSeconds':tracking_area,'realizedPnl':pnl,'finalPortfolio':actual_port},'lifecycle':{'model':'SEQUENTIAL_ARBITRATION_OPTION_V1/currentOnly','thresholds':thresholds,'decisionCount':len(lifecycle),'actionCounts':{k:sum(x['action']==k for x in lifecycle) for k in ['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER']},'replaceEpisodes':replace_history,'takerChildStateCounts':{k:sum(1 for om in taker_meta.values() if om.get('lifecycleState')==k) for k in ['SUBMITTED','ACKED_OPEN','PARTIAL','FILLED','CANCEL_REQUESTED','TERMINAL_ZERO_FILL']},'cancelPendingAtDataEnd':sum(1 for p in replace_history if p.get('state')=='PENDING_AT_DATA_END'),'cancelAckEvidenceCounts':{k:sum(1 for p in replace_history if p.get('cancelAckEvidence')==k) for k in ['VENUE_TERMINAL_OBSERVED','DATA_END_NO_TERMINAL_OBSERVED']},'unresolvedTakerReturns':sum(1 for p in replace_history if p.get('state')=='RETURN_TO_CONTROLLER_UNRESOLVED'),'unresolvedCauseCounts':{k:sum(1 for p in replace_history if p.get('unresolvedCause')==k) for k in ['TERMINAL_ZERO_FILL','PARTIAL_CHILD_REMAINDER','FILLED_CHILD_TARGET_MOVED']},'remainderOwnershipAtEnd':copy.deepcopy(remainder_owner),'ownershipEvents':ownership_events,'ownershipEventCounts':{k:sum(1 for x in ownership_events if x.get('action')==k) for k in ['OWNERSHIP_RETURNED_UNRESOLVED','BLOCK_REESCALATION_UNCHANGED_TARGET','OWNERSHIP_RELEASE_ON_TARGET_REVISION','OWNERSHIP_CLEARED_TARGET_SATISFIED','OWNERSHIP_CLEARED_ASYMMETRY_RESOLVED','OWNERSHIP_CLEARED_RESOLVED_DURING_CANCEL','OWNERSHIP_PRESERVED_CANCEL_PENDING_AT_DATA_END']},'decisions':lifecycle},'makerIntents':maker_intents,'makerSubmits':maker_submits,'makerFills':maker_fills,'takerAttempts':taker_attempts,'takerFills':taker_fills,'boundary':'Opened development only. Runtime arbitration inputs are current/strict-past public + actual HFT execution state. No Target/winner/PnL runtime input. Frozen Strategy Brain paper own-state is still preserved internally, so this is not graduation-eligible.'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='r2_online_target_ledger_pair_completion_v5_remainder_ownership.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    for i,mid in enumerate(mids,1):
        r=run_market(mid);rows.append(r);print(json.dumps({'progress':i,'marketId':mid,'paperMaker':r['paperReference']['makerOrders'],'onlineMaker':r['strategyRollout']['makerIntents'],'makerReal':r['actualExecution']['makerRealizationRate'],'actions':r['lifecycle']['actionCounts'],'absTrack':r['actualExecution']['finalAbsTrackingError'],'pnl':r['actualExecution']['realizedPnl']},ensure_ascii=False),flush=True)
    pnls=[float(r['actualExecution']['realizedPnl']) for r in rows if r['actualExecution']['realizedPnl'] is not None];md=sum(float(r['strategyRollout']['desiredMakerShares']) for r in rows);mf=sum(float(r['actualExecution']['makerFilledShares']) for r in rows);agg={'markets':len(rows),'paperMakerOrders':sum(r['paperReference']['makerOrders'] for r in rows),'onlineMakerIntents':sum(r['strategyRollout']['makerIntents'] for r in rows),'makerDesiredShares':md,'makerFilledShares':mf,'makerRealizationRate':mf/md if md>EPS else None,'totalRealizedPnl':sum(pnls),'wins':sum(x>EPS for x in pnls),'losses':sum(x<-EPS for x in pnls),'winRate':sum(x>EPS for x in pnls)/len(pnls) if pnls else None,'maxCumulativeDrawdown':max_drawdown(pnls),'meanFinalAbsTrackingError':sum(float(r['actualExecution']['finalAbsTrackingError']) for r in rows)/len(rows) if rows else None,'meanCombinedFinalAbsNet':sum(float(r['actualExecution']['combinedFinalAbsNet']) for r in rows)/len(rows) if rows else None,'targetErrorAreaShareSeconds':sum(float(r['actualExecution']['targetErrorAreaShareSeconds']) for r in rows),'combinedExposureAreaShareSeconds':sum(float(r['actualExecution']['combinedExposureAreaShareSeconds']) for r in rows),'actionCounts':{k:sum(int(r['lifecycle']['actionCounts'][k]) for r in rows) for k in ['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER']},'takerFeesUsdt':sum(float(r['actualExecution']['takerFeesUsdt']) for r in rows),'takerChildStateCounts':{k:sum(int(r['lifecycle'].get('takerChildStateCounts',{}).get(k,0)) for r in rows) for k in ['SUBMITTED','ACKED_OPEN','PARTIAL','FILLED','CANCEL_REQUESTED','TERMINAL_ZERO_FILL']},'cancelPendingAtDataEnd':sum(int(r['lifecycle'].get('cancelPendingAtDataEnd',0)) for r in rows),'cancelAckEvidenceCounts':{k:sum(int(r['lifecycle'].get('cancelAckEvidenceCounts',{}).get(k,0)) for r in rows) for k in ['VENUE_TERMINAL_OBSERVED','DATA_END_NO_TERMINAL_OBSERVED']},'unresolvedTakerReturns':sum(int(r['lifecycle'].get('unresolvedTakerReturns',0)) for r in rows),'unresolvedCauseCounts':{k:sum(int(r['lifecycle'].get('unresolvedCauseCounts',{}).get(k,0)) for r in rows) for k in ['TERMINAL_ZERO_FILL','PARTIAL_CHILD_REMAINDER','FILLED_CHILD_TARGET_MOVED']},'ownershipEventCounts':{k:sum(int(r['lifecycle'].get('ownershipEventCounts',{}).get(k,0)) for r in rows) for k in ['OWNERSHIP_RETURNED_UNRESOLVED','BLOCK_REESCALATION_UNCHANGED_TARGET','OWNERSHIP_RELEASE_ON_TARGET_REVISION','OWNERSHIP_CLEARED_TARGET_SATISFIED','OWNERSHIP_CLEARED_ASYMMETRY_RESOLVED','OWNERSHIP_CLEARED_RESOLVED_DURING_CANCEL','OWNERSHIP_PRESERVED_CANCEL_PENDING_AT_DATA_END']}}
    out=OUT/a.output;out.write_text(json.dumps({'version':'R2_ONLINE_TARGET_LEDGER_PAIR_COMPLETION_V7_CANCEL_ACK_EVIDENCE_REPORT','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'aggregate':agg,'rows':rows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
