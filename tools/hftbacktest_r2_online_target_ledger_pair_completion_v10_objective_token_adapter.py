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

class ActualFillFeedbackAdapter:
    """Expose only confirmed HftBacktest fills to the frozen controller inventory interface.

    Paper/dream fill callbacks may still advance the frozen order-lifecycle bookkeeping, but
    their inventory mutations are suppressed. Unfilled commitments therefore never become
    realized alpha inventory.
    """
    def __init__(self, realized):
        self.realized=realized; self._suppress=0; self.suppressed=[]
    def __getattr__(self,name): return getattr(self.realized,name)
    def features(self,now): return self.realized.features(now)
    def reset(self): return self.realized.reset()
    def apply(self,e):
        if self._suppress>0:
            self.suppressed.append(dict(e)); return
        self.realized.apply(e)
    def suppress_begin(self): self._suppress+=1
    def suppress_end(self): self._suppress=max(0,self._suppress-1)


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
    trace_option_transitions:bool=False,
    trace_execution_states:bool=False,
    own_state_poll_ms:int|None=None,
    lifecycle_action_override:Callable[[dict[str,Any]],str|None]|None=None,
    taker_submit_fault_override:Callable[[dict[str,Any]],str|None]|None=None,
    maker_submit_fault_override:Callable[[dict[str,Any]],str|None]|None=None,
    fault_reentry_enabled:bool=False,
    behavior_policy_override:Callable[[dict[str,Any]],dict[str,Any]|None]|None=None,
    behavior_ownstate_reentry:bool=False,
    fault_containment_freeze_before_redecision:bool=False,
    fault_no_fill_stall_ms:int|None=None,
    taker_confirm_ms_override:int|None=None,
    taker_price_buffer_ticks_override:int|None=None,
)->dict[str,Any]:
    if own_state_poll_ms is not None and int(own_state_poll_ms)<=0:
        raise ValueError('own_state_poll_ms must be positive when enabled')
    if fault_no_fill_stall_ms is not None and int(fault_no_fill_stall_ms)<=0:
        raise ValueError('fault_no_fill_stall_ms must be positive when enabled')
    if taker_confirm_ms_override is not None and int(taker_confirm_ms_override)<=0:
        raise ValueError('taker_confirm_ms_override must be positive when enabled')
    if taker_price_buffer_ticks_override is not None and int(taker_price_buffer_ticks_override)<0:
        raise ValueError('taker_price_buffer_ticks_override must be nonnegative when enabled')
    snaps=load_public_snapshots(mid)
    if not snaps: raise RuntimeError(f'no snapshots for {mid}')
    paper=load_reference_paper(mid); win=winners([mid]).get(mid)
    art=joblib.load(MODEL_PATH); variant=art['currentOnly']; feats=list(variant['features']); models=variant['models']; thresholds=art['thresholds']
    a=HftBookAdapter(mid,1092,273,'risk','mid'); c=new_controller(a); actual=mod.Inventory(); feedback=ActualFillFeedbackAdapter(actual); c.inventory=feedback
    desired={'UP':0.0,'DOWN':0.0}; responsibility_tokens=[]; responsibility_events=[]; paper_lineage={}; active={'UP':None,'DOWN':None}; maker_meta={}; taker_meta={}; route_block={'UP':False,'DOWN':False}; pending_replace={}; replace_history=[]; remainder_owner={'UP':None,'DOWN':None}; target_revision={'UP':0,'DOWN':0}; ownership_events=[]
    maker_intents=[]; maker_fills=[]; maker_submits=[]; taker_attempts=[]; taker_fills=[]; lifecycle=[]; decisions=[]; fill_log=[]
    behavior_override_events=[]; pending_behavior_execution_action=None; behavior_freeze_new_intents=False; behavior_freeze_frozen_taker_intents=False; behavior_freeze_maker_execution_children=False
    behavior_memory={'lastAction':None,'lastActionAtMs':None,'lastActionSide':None,'lastRequestedQty':0.0,'lastOutcome':None,'lastFilledQty':0.0,'lastRemainingQty':0.0,'lastFault':None,'sameRouteRetryCount':0,'consecutiveReject':0,'consecutiveNoFill':0,'consecutivePartial':0,'history':[]}
    option_transitions=[]; execution_states=[]; terminal_execution_state=None; last_option_action=None; own_state_reentries=[]
    last_maker_fill_ms=None; last_maker_fill_side=None; asym_since=None; last_eval_ms=None; episode_action=None; episode_checkpoint_idx=0; taker_fees=0.0; last_area=None; exposure_area=0.0; tracking_area=0.0
    orig_add=c._add_order; orig_taker=c._record_taker; orig_fill_orders=c._fill_orders

    def actual_side(side): return actual.maker_up if side=='UP' else actual.maker_down
    def combined_side(side): return (actual.maker_up+actual.taker_up) if side=='UP' else (actual.maker_down+actual.taker_down)
    def target_net(): return desired['UP']-desired['DOWN']
    def actual_net(): return combined_side('UP')-combined_side('DOWN')

    def execution_state(now:int,snap:dict[str,Any],decision:dict[str,Any]|None,terminal:bool=False)->dict[str,Any]:
        actual_port=actual.features(now);actual_port.pop('_combined_net',None)
        book=mod.outcome_book(c.book.book,None) or {}
        maker_children={}
        for side in ('UP','DOWN'):
            num=active.get(side)
            if num is None:
                maker_children[side]=None
                continue
            meta=maker_meta.get(int(num),{});order=({'status':'NEW','cumExecQty':0.0,'leavesQty':float(meta.get('qty') or 0.0)} if meta.get('syntheticNoFill') else ex.order_snapshot(a.bt,int(num)))
            maker_children[side]={
                'orderNum':int(num),'side':side,'price':meta.get('price'),'requestedQty':meta.get('qty'),
                'submittedAtMs':meta.get('submittedAtMs'),'ageMs':now-int(meta.get('submittedAtMs') or now),
                'status':order.get('status'),'cumExecQty':float(order.get('cumExecQty') or 0.0),
                'leavesQty':float(order.get('leavesQty') or 0.0),'cancelPending':str(order.get('status') or '')=='CANCEL_REQUESTED',
            }
        taker_children=[]
        for num,meta in taker_meta.items():
            if meta.get('terminal'): continue
            if meta.get('syntheticNoFill'):
                order={'status':'NEW','cumExecQty':0.0,'leavesQty':float(meta.get('remainingQty') or meta.get('qty') or 0.0)}
            else:
                order=ex.order_snapshot(a.bt,int(num))
            taker_children.append({
                'orderNum':int(num),'side':meta.get('side'),'kind':meta.get('kind'),
                'lifecycleState':meta.get('lifecycleState'),'submittedAtMs':meta.get('submittedAtMs'),
                'ageMs':now-int(meta.get('submittedAtMs') or now),'requestedQty':meta.get('qty'),
                'filledQty':float(meta.get('filledQty') or 0.0),'remainingQty':float(meta.get('remainingQty') or 0.0),
                'status':order.get('status'),'cumExecQty':float(order.get('cumExecQty') or 0.0),
                'leavesQty':float(order.get('leavesQty') or 0.0),'cancelRequested':bool(meta.get('cancelRequested')),
                'injectedFault':meta.get('injectedFault'),
            })
        public_fields=(
            'secondsLeft','directionScore','spotReturn1sBps','spotReturn3sBps','spotQueueImbalance',
            'spotTakerImbalance1s','futuresReturn1sBps','futuresReturn3sBps','futuresQueueImbalance',
            'futuresTakerImbalance1s','bookReceivedAtMs','sampledAtMs',
        )
        decision_state=None
        if decision is not None:
            decision_state={
                key:copy.deepcopy(decision.get(key))
                for key in ('decisionId','decisionMs','phase','desiredPortfolioAction','executionChoice','primaryReason','direction','models')
            }
        return {
            'atMs':int(now),'terminal':bool(terminal),'decision':decision_state,
            'publicState':{key:copy.deepcopy(snap.get(key)) for key in public_fields},
            'outcomeBook':{key:copy.deepcopy(value) for key,value in book.items()},
            'actualPortfolio':actual_port,'actualSharesBySide':{'UP':combined_side('UP'),'DOWN':combined_side('DOWN')},'desiredMakerShares':copy.deepcopy(desired),
            'targetRevision':copy.deepcopy(target_revision),'targetNet':target_net(),'actualNet':actual_net(),
            'trackingError':actual_net()-target_net(),'activeMakerChildren':maker_children,
            'openTakerChildren':taker_children,'routeBlock':copy.deepcopy(route_block),
            'pendingReplace':copy.deepcopy(pending_replace),'remainderOwner':copy.deepcopy(remainder_owner),
            'responsibilityTokens':[copy.deepcopy(token) for token in responsibility_tokens if float(token.get('remainingShares') or 0.0)>EPS],
            'recentActualFills10s':[copy.deepcopy(event) for event in fill_log if int(event.get('eventMs') or 0)>=now-10_000],
            'behaviorMemory':copy.deepcopy(behavior_memory),
        }

    def area(now:int):
        nonlocal last_area,exposure_area,tracking_area
        if last_area is not None and now>last_area:
            dt=(now-last_area)/1000.0; n=actual_net(); exposure_area+=abs(n)*dt; tracking_area+=abs(n-target_net())*dt
        last_area=now

    def submit_taker(side:str,qty:float,ask:float,now:int,kind:str,intent_context:dict[str,Any]|None=None)->int:
        buffer_ticks=int(2 if taker_price_buffer_ticks_override is None else taker_price_buffer_ticks_override)
        maxp=min(.99,float(ask)+buffer_ticks*mod.GRID); num=int(a.next_num); a.next_num+=1; native_side,native_px=ex.native_order(side,maxp)
        fault_mode=None
        if taker_submit_fault_override is not None:
            fault_mode=taker_submit_fault_override({'atMs':now,'side':side,'qty':float(qty),'ask':float(ask),'kind':kind,'orderNum':num,'nativeSide':native_side,'nativePrice':native_px,'intentContext':copy.deepcopy(intent_context)})
        synthetic_no_fill=fault_mode=='NO_FILL_STALL'
        if fault_mode=='SUBMIT_REJECT':
            rc=1
        elif synthetic_no_fill:
            # Research-only deterministic fault: emulate venue ACK/open with zero execution until deadline.
            # No synthetic fill is ever credited to inventory.
            rc=0
        elif native_side=='BUY': rc=int(a.bt.submit_buy_order(0,num,native_px,float(qty),ex.hbt.GTC,ex.LIMIT,False))
        else: rc=int(a.bt.submit_sell_order(0,num,native_px,float(qty),ex.hbt.GTC,ex.LIMIT,False))
        rejected=rc!=0
        base_confirm_ms=int(taker_confirm_ms_override or TAKER_CONFIRM_MS)
        no_fill_wait=int(fault_no_fill_stall_ms or base_confirm_ms) if synthetic_no_fill else base_confirm_ms
        taker_meta[num]={'side':side,'qty':float(qty),'observedPrice':float(ask),'prevCum':0.0,'submittedAtMs':now,'deadlineMs':now+no_fill_wait,'cancelRequested':False,'terminal':('REJECTED' if rejected else None),'terminalAtMs':(now if rejected else None),'kind':kind,'intentContext':copy.deepcopy(intent_context),'submitRc':rc,'injectedFault':fault_mode,'syntheticNoFill':synthetic_no_fill,'lifecycleState':('ACKED_OPEN' if synthetic_no_fill else ('SUBMITTED' if rc==0 else 'TERMINAL_ZERO_FILL')),'filledQty':0.0,'remainingQty':float(qty)}
        prior_same=behavior_memory.get('lastAction')==kind and behavior_memory.get('lastActionSide')==side
        prior_failed=behavior_memory.get('lastOutcome') in {'TERMINAL_ZERO_FILL','PARTIAL','SUBMIT_REJECT','NO_FILL_STALL'}
        behavior_memory.update({'lastAction':kind,'lastActionAtMs':now,'lastActionSide':side,'lastRequestedQty':float(qty),'lastOutcome':('SUBMIT_REJECT' if rejected else 'SUBMITTED'),'lastFilledQty':0.0,'lastRemainingQty':float(qty),'lastFault':fault_mode,'sameRouteRetryCount':(int(behavior_memory.get('sameRouteRetryCount') or 0)+1 if prior_same and prior_failed else 0)})
        if rejected: behavior_memory['consecutiveReject']=int(behavior_memory.get('consecutiveReject') or 0)+1
        behavior_memory['history']=(behavior_memory.get('history') or [])[-7:]+[{'atMs':now,'action':kind,'side':side,'requestedQty':float(qty),'outcome':behavior_memory['lastOutcome'],'fault':fault_mode}]
        taker_attempts.append({'orderNum':num,**taker_meta[num]}); lifecycle.append({'atMs':now,'side':side,'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':taker_meta[num]['lifecycleState'],'requestedQty':float(qty),'kind':kind,'submitRc':rc,'injectedFault':fault_mode}); return num

    def harvest_maker(now:int):
        nonlocal last_maker_fill_ms,last_maker_fill_side
        for side in ('UP','DOWN'):
            num=active[side]
            if num is None: continue
            om=maker_meta[int(num)]
            if om.get('syntheticNoFill'):
                if now>=int(om.get('deadlineMs') or now):
                    om['terminalStatus']='EXPIRED';om['terminalAtMs']=now;active[side]=None
                    behavior_memory.update({'lastAction':'MAKER_CHILD','lastActionAtMs':now,'lastActionSide':side,'lastRequestedQty':float(om.get('qty') or 0.0),'lastOutcome':'TERMINAL_ZERO_FILL','lastFilledQty':0.0,'lastRemainingQty':float(om.get('qty') or 0.0),'lastFault':'MAKER_NO_FILL_STALL','consecutiveNoFill':int(behavior_memory.get('consecutiveNoFill') or 0)+1})
                    behavior_memory['history']=(behavior_memory.get('history') or [])[-7:]+[{'atMs':now,'action':'MAKER_CHILD','side':side,'requestedQty':float(om.get('qty') or 0.0),'outcome':'TERMINAL_ZERO_FILL','filledQty':0.0,'remainingQty':float(om.get('qty') or 0.0),'fault':'MAKER_NO_FILL_STALL'}]
                    lifecycle.append({'atMs':now,'side':side,'action':'MAKER_CHILD_STATE','orderNum':int(num),'makerState':'TERMINAL_ZERO_FILL','filledQty':0.0,'remainingQty':float(om.get('qty') or 0.0),'injectedFault':'NO_FILL_STALL'})
                    lifecycle.append({'atMs':now,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'MAKER_NO_FILL_STALL','remainingDeficit':max(0.0,float(desired[side])-actual_side(side))})
                continue
            s=ex.order_snapshot(a.bt,int(num)); cum=float(s.get('cumExecQty') or 0.0); old=float(om.get('prevCum') or 0.0)
            if cum>old+EPS:
                q=cum-old; native=s.get('execPrice'); px=float(om['price'])
                if native is not None and math.isfinite(float(native)): px=float(native) if side=='UP' else 1.0-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000); pre_net=actual.maker_up-actual.maker_down; pre_g=actual.maker_up+actual.maker_down; pre_pc=2*min(actual.maker_up,actual.maker_down)/pre_g if pre_g>EPS else 1.0; actual.apply({'event_ms':fm,'role':'MAKER','side':side,'price':px,'shares':q});
                remq=q
                for tok in responsibility_tokens:
                    if remq<=EPS: break
                    if tok.get('side')!=side or float(tok.get('remainingShares') or 0.0)<=EPS: continue
                    if int(tok.get('activeHftOrderNum') or -1)!=int(num): continue
                    used=min(float(tok['remainingShares']),remq); tok['remainingShares']=float(tok['remainingShares'])-used; remq-=used
                    if used>EPS: responsibility_events.append({'atMs':fm,'side':side,'action':'OBJECTIVE_TOKEN_REALIZED_BY_HFT_FILL','tokenId':tok['tokenId'],'sourcePaperOrderId':tok['sourcePaperOrderId'],'shares':used,'remainingTokenShares':tok['remainingShares'],'hftOrderNum':int(num)})
                post_net=actual.maker_up-actual.maker_down; om['prevCum']=cum
                # Actual-fill feedback may start/refresh the same overlap-risk episode semantics, but only from confirmed fills.
                if bool(om.get('occupiedBeforeActual')):
                    predom='UP' if pre_net>EPS else 'DOWN' if pre_net<-EPS else None
                    if predom==side and abs(pre_net)>=CHUNK-EPS and abs(post_net)>abs(pre_net)+1:
                        c.episode={'kind':'OVERLAP','side':side,'risk_start_ms':fm,'risk_pre_abs':abs(pre_net),'start_ms':fm,'pre_abs':abs(pre_net),'start_abs':abs(post_net),'expansion':abs(post_net)-abs(pre_net),'pre_pc':pre_pc,'unresolved':False}; c.readiness=False
                maker_fills.append({'atMs':fm,'observedAtMs':now,'orderNum':int(num),'side':side,'price':px,'deltaShares':q,'cumShares':cum,'status':s.get('status')}); fill_log.append({'eventMs':fm,'role':'MAKER','side':side,'price':px,'shares':q,'fee':0.0}); last_maker_fill_ms=fm; last_maker_fill_side=side
            st=str(s.get('status') or 'NONE')
            if st in TERMINAL:
                om['terminalStatus']=st; om['terminalAtMs']=now; active[side]=None

    def harvest_taker(now:int):
        nonlocal taker_fees
        for num,om in taker_meta.items():
            if om.get('terminal'): continue
            if om.get('syntheticNoFill'):
                if now>=int(om['deadlineMs']):
                    om['terminal']='EXPIRED';om['terminalAtMs']=now;om['lifecycleState']='TERMINAL_ZERO_FILL';om['filledQty']=0.0;om['remainingQty']=float(om['qty'])
                    behavior_memory.update({'lastAction':om.get('kind'),'lastActionAtMs':now,'lastActionSide':om.get('side'),'lastRequestedQty':float(om.get('qty') or 0.0),'lastOutcome':'TERMINAL_ZERO_FILL','lastFilledQty':0.0,'lastRemainingQty':float(om.get('qty') or 0.0),'lastFault':'NO_FILL_STALL','consecutiveNoFill':int(behavior_memory.get('consecutiveNoFill') or 0)+1})
                    behavior_memory['history']=(behavior_memory.get('history') or [])[-7:]+[{'atMs':now,'action':om.get('kind'),'side':om.get('side'),'requestedQty':float(om.get('qty') or 0.0),'outcome':'TERMINAL_ZERO_FILL','filledQty':0.0,'remainingQty':float(om.get('qty') or 0.0),'fault':'NO_FILL_STALL'}]
                    lifecycle.append({'atMs':now,'side':om['side'],'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':'TERMINAL_ZERO_FILL','filledQty':0.0,'remainingQty':float(om['qty']),'terminalStatus':'EXPIRED','kind':om['kind'],'injectedFault':'NO_FILL_STALL'})
                continue
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
                behavior_memory.update({'lastAction':om.get('kind'),'lastActionAtMs':now,'lastActionSide':om.get('side'),'lastRequestedQty':float(om.get('qty') or 0.0),'lastOutcome':terminal_state,'lastFilledQty':filled,'lastRemainingQty':rem,'lastFault':om.get('injectedFault')})
                if terminal_state=='TERMINAL_ZERO_FILL': behavior_memory['consecutiveNoFill']=int(behavior_memory.get('consecutiveNoFill') or 0)+1
                else: behavior_memory['consecutiveNoFill']=0
                if terminal_state=='PARTIAL': behavior_memory['consecutivePartial']=int(behavior_memory.get('consecutivePartial') or 0)+1
                else: behavior_memory['consecutivePartial']=0
                if terminal_state!='TERMINAL_ZERO_FILL' and om.get('injectedFault')!='SUBMIT_REJECT': behavior_memory['consecutiveReject']=0
                behavior_memory['history']=(behavior_memory.get('history') or [])[-7:]+[{'atMs':now,'action':om.get('kind'),'side':om.get('side'),'requestedQty':float(om.get('qty') or 0.0),'outcome':terminal_state,'filledQty':filled,'remainingQty':rem,'fault':om.get('injectedFault')} ]
                lifecycle.append({'atMs':now,'side':om['side'],'action':'TAKER_CHILD_STATE','orderNum':num,'takerState':terminal_state,'filledQty':filled,'remainingQty':rem,'terminalStatus':st,'kind':om['kind']})

    def harvest(now:int): harvest_maker(now);harvest_taker(now)

    def submit_maker(side:str,now:int):
        if behavior_freeze_new_intents or behavior_freeze_maker_execution_children:
            behavior_override_events.append({'atMs':now,'trigger':'EXECUTION_GATE','proposal':{'blocked':'MAKER_EXECUTION_CHILD','side':side}})
            return
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
        qty=min(CHUNK,deficit); num=int(a.next_num);a.next_num+=1
        fault_mode=None
        if maker_submit_fault_override is not None:
            fault_mode=maker_submit_fault_override({'atMs':now,'side':side,'qty':float(qty),'price':float(px),'kind':'MAKER_CHILD','orderNum':num})
        synthetic_no_fill=fault_mode=='NO_FILL_STALL'
        rc=(1 if fault_mode=='SUBMIT_REJECT' else (0 if synthetic_no_fill else ex.submit_native(a.bt,num,side,px,qty)))
        base_confirm_ms=int(taker_confirm_ms_override or TAKER_CONFIRM_MS)
        no_fill_wait=int(fault_no_fill_stall_ms or base_confirm_ms) if synthetic_no_fill else base_confirm_ms
        maker_meta[num]={'side':side,'price':px,'qty':qty,'prevCum':0.0,'submittedAtMs':now,'submitRc':int(rc),'occupiedBeforeActual':bool(actual_side(side)>EPS),'injectedFault':fault_mode,'syntheticNoFill':synthetic_no_fill,'deadlineMs':now+no_fill_wait}
        maker_submits.append({'orderNum':num,**maker_meta[num]})
        if rc==0:
            active[side]=num
            if synthetic_no_fill:
                behavior_memory.update({'lastAction':'MAKER_CHILD','lastActionAtMs':now,'lastActionSide':side,'lastRequestedQty':float(qty),'lastOutcome':'SUBMITTED','lastFilledQty':0.0,'lastRemainingQty':float(qty),'lastFault':'MAKER_NO_FILL_STALL'})
        else:
            behavior_memory.update({'lastAction':'MAKER_CHILD','lastActionAtMs':now,'lastActionSide':side,'lastRequestedQty':float(qty),'lastOutcome':'SUBMIT_REJECT','lastFilledQty':0.0,'lastRemainingQty':float(qty),'lastFault':'MAKER_SUBMIT_REJECT','consecutiveReject':int(behavior_memory.get('consecutiveReject') or 0)+1})
            behavior_memory['history']=(behavior_memory.get('history') or [])[-7:]+[{'atMs':now,'action':'MAKER_CHILD','side':side,'requestedQty':float(qty),'outcome':'SUBMIT_REJECT','fault':'MAKER_SUBMIT_REJECT'}]
            lifecycle.append({'atMs':now,'side':side,'action':'MAKER_CHILD_STATE','orderNum':num,'makerState':'TERMINAL_ZERO_FILL','requestedQty':float(qty),'submitRc':int(rc),'injectedFault':'SUBMIT_REJECT'})
            lifecycle.append({'atMs':now,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'MAKER_SUBMIT_REJECT','remainingDeficit':max(0.0,float(desired[side])-actual_side(side))})

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
        action,pa,pw,pr=classify(f)
        default_action=action
        if pending_behavior_execution_action is not None:
            action_map={'WAIT':'WAIT_FOR_CLARITY','KEEP_PASSIVE':'KEEP_EXECUTING','ACTIVE_REPAIR':'REPLACE_ROUTE','RETURN_OR_RETIRE':'RETIRE_OBLIGATION','RETURN_TO_PASSIVE':'RETURN_TO_PASSIVE_REPAIR'}
            if pending_behavior_execution_action not in action_map:
                raise ValueError(f'unsupported behavior execution mode: {pending_behavior_execution_action}')
            action=action_map[pending_behavior_execution_action]
        if lifecycle_action_override is not None:
            current_decision=None
            if c.last_decision is not None and int(c.last_decision.get('decisionMs') or -1)==now:
                current_decision=copy.deepcopy(c.last_decision)
            override=lifecycle_action_override({'atMs':now,'side':side,'defaultAction':default_action,'features':copy.deepcopy(f),'checkpointDelayMs':due,'trackingError':err,'activeChildOrderNum':active[side],'targetRevision':int(target_revision[side]),'executionState':execution_state(now,snap,current_decision)})
            if override is not None:action=str(override)
        if action not in {'WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER','RETIRE_OBLIGATION','RETURN_TO_PASSIVE_REPAIR'}:
            raise ValueError(f'unsupported lifecycle action override: {action}')
        lifecycle.append({'atMs':now,'side':side,'action':action,'defaultAction':default_action,'pAct':pa,'pWait':pw,'pReplaceIfAct':pr,'trackingError':err,'asymmetryAgeMs':age,'checkpointDelayMs':due,'childOrderNum':active[side],'childStatus':('NONE' if active[side] is None else ex.order_snapshot(a.bt,int(active[side])).get('status')),'stateFeatures':copy.deepcopy(f)})
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
        elif action=='RETIRE_OBLIGATION':
            num=active[side];route_block[side]=True;remaining=min(CHUNK,abs(err))
            if num is None:
                rec={'state':'RETIRED_TO_CONTROLLER','side':side,'triggerAtMs':now,'requestedQty':remaining,'initialTrackingError':err,'completion':'EXPLICIT_RETIRE_NO_LIVE_CHILD'};pending_replace[side]=rec;replace_history.append(rec)
                remainder_owner[side]={'state':'RETURNED_UNRESOLVED','owner':'CONTROLLER_RETURN','remainingDeficit':remaining,'returnedAtMs':now,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(rec),'cause':'EXPLICIT_RETIRE','takerOrderNum':None}
                ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_RETIRED_TO_CONTROLLER','remainingDeficit':remaining,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(rec),'cause':'EXPLICIT_RETIRE'})
                lifecycle.append({'atMs':now,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'EXPLICIT_RETIRE','remainingDeficit':remaining,'targetRevision':int(target_revision[side])});route_block[side]=False
                return
            os=ex.order_snapshot(a.bt,int(num));cur=a.bt.orders(0).get(int(num));rec={'state':'RETIRE_CANCEL_REQUESTED','side':side,'childOrderNum':int(num),'triggerAtMs':now,'cancelRequestedAtMs':None,'requestedQty':remaining,'initialTrackingError':err,'childLeavesAtTrigger':float(os.get('leavesQty') or 0.0)};pending_replace[side]=rec;replace_history.append(rec)
            if str(os.get('status') or 'NONE') in {'NEW','PARTIALLY_FILLED'} and cur is not None and bool(cur.cancellable):
                try:a.bt.cancel(0,int(num),False);pending_replace[side]['cancelRequestedAtMs']=now
                except Exception:pass
        elif action=='RETURN_TO_PASSIVE_REPAIR':
            num=active[side];remaining=min(CHUNK,abs(err));route_block[side]=False
            if num is not None:
                lifecycle.append({'atMs':now,'side':side,'action':'PASSIVE_RETURN_BLOCKED_LIVE_CHILD','childOrderNum':int(num),'remainingDeficit':remaining,'targetRevision':int(target_revision[side])})
                return
            remainder_owner[side]={'state':'PASSIVE_REPAIR_RETURN','owner':'R2_PASSIVE_REPAIR','remainingDeficit':remaining,'returnedAtMs':now,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':None,'cause':'EXPLICIT_PASSIVE_RETURN','takerOrderNum':None}
            ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_RETURNED_TO_PASSIVE_REPAIR','remainingDeficit':remaining,'targetRevision':int(target_revision[side]),'cause':'EXPLICIT_PASSIVE_RETURN'})
        # KEEP_EXECUTING and RETURN_TO_CONTROLLER intentionally make no venue mutation;
        # the episode remains censored until the asymmetric state truly clears.

    def service_replace(now:int):
        nonlocal asym_since,last_eval_ms,episode_action,episode_checkpoint_idx
        for side,p in list(pending_replace.items()):
            if p['state']=='CANCEL_REQUESTED':
                num=int(p['childOrderNum']); st=str(ex.order_snapshot(a.bt,num).get('status') or 'NONE')
                if st not in TERMINAL:continue
                ack_snap=ex.order_snapshot(a.bt,num); p['cancelAckEvidence']='VENUE_TERMINAL_OBSERVED'; p['cancelTerminalStatus']=st; p['cancelTerminalObservedAtMs']=now; p['cancelWaitMs']=now-int(p.get('cancelRequestedAtMs') or p.get('triggerAtMs') or now); p['childCumExecAtTerminal']=float(ack_snap.get('cumExecQty') or 0.0); p['childLeavesAtTerminal']=float(ack_snap.get('leavesQty') or 0.0)
                err=actual_net()-target_net(); p['actualNetAtCancelAck']=actual_net(); p['targetNetAtCancelAck']=target_net(); p['trackingErrorAtCancelAck']=err; p['targetRevisionAtCancelAck']=int(target_revision[side]); needed=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP');qty=min(CHUNK,abs(err)) if needed else 0.0
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
                    if fault_reentry_enabled and remaining>EPS:
                        # Research-only recovery epoch: the failed execution route becomes observable state,
                        # then the lifecycle head gets one new decision sequence on the same target revision.
                        episode_action=None;episode_checkpoint_idx=0;last_eval_ms=None;asym_since=now
                        lifecycle.append({'atMs':now,'side':side,'action':'FAULT_RECOVERY_REENTRY_ARMED','reason':p.get('unresolvedCause'),'failedAction':'REPLACE_ROUTE','targetRevision':int(target_revision[side]),'remainingDeficit':remaining})
            elif p['state']=='RETIRE_CANCEL_REQUESTED':
                num=int(p['childOrderNum']);snap=ex.order_snapshot(a.bt,num);st=str(snap.get('status') or 'NONE')
                if st not in TERMINAL:continue
                p['cancelAckEvidence']='VENUE_TERMINAL_OBSERVED';p['cancelTerminalStatus']=st;p['cancelTerminalObservedAtMs']=now;p['cancelWaitMs']=now-int(p.get('cancelRequestedAtMs') or p.get('triggerAtMs') or now);p['childCumExecAtTerminal']=float(snap.get('cumExecQty') or 0.0);p['childLeavesAtTerminal']=float(snap.get('leavesQty') or 0.0)
                err=actual_net()-target_net();needed=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP');remaining=min(CHUNK,abs(err)) if needed else 0.0
                if remaining>EPS:
                    p['state']='RETIRED_TO_CONTROLLER';p['completion']='EXPLICIT_RETIRE_AFTER_CANCEL_ACK';p['returnedAtMs']=now;p['remainingDeficitAfterTerminal']=remaining
                    remainder_owner[side]={'state':'RETURNED_UNRESOLVED','owner':'CONTROLLER_RETURN','remainingDeficit':remaining,'returnedAtMs':now,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'cause':'EXPLICIT_RETIRE','takerOrderNum':None}
                    ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_RETIRED_TO_CONTROLLER','remainingDeficit':remaining,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'cause':'EXPLICIT_RETIRE'})
                    lifecycle.append({'atMs':now,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'EXPLICIT_RETIRE','remainingDeficit':remaining,'targetRevision':int(target_revision[side]),'cancelAckEvidence':'VENUE_TERMINAL_OBSERVED'})
                else:
                    p['state']='RESOLVED_DURING_CANCEL';p['completion']='ACTUAL_FILL_RESOLVED_BEFORE_RETIRE';p['returnedAtMs']=now
                    if remainder_owner.get(side) is not None:
                        ownership_events.append({'atMs':now,'side':side,'action':'OWNERSHIP_CLEARED_RESOLVED_DURING_CANCEL'});remainder_owner[side]=None
                route_block[side]=False

    def fill_orders_wrap(snapshot,now):
        # V10: preserve exact paper-order identity when dream lifecycle removes an order.
        # A token is created only when that paper order still has a concrete live HFT child carrying
        # the same side-level execution responsibility. The token never enters alpha inventory.
        before_orders={k:copy.deepcopy(o) for k,o in c.orders.items()}
        before_ids={o.id for o in before_orders.values()}
        feedback.suppress_begin()
        try:
            out=orig_fill_orders(snapshot,now)
        finally:
            feedback.suppress_end()
        after_ids={o.id for o in c.orders.values()}
        removed=[o for o in before_orders.values() if o.id in (before_ids-after_ids)]
        for o in removed:
            lin=paper_lineage.get(o.id) or {}
            side=str(o.side); hnum=active.get(side)
            if hnum is None: continue
            hs=ex.order_snapshot(a.bt,int(hnum)); hst=str(hs.get('status') or 'NONE')
            if hst in TERMINAL: continue
            tok={'tokenId':f"{o.id}:RESP",'sourcePaperOrderId':o.id,'side':side,'reason':lin.get('reason'),'decisionId':lin.get('decisionId'),'priceTick':int(o.price_tick),'price':float(o.price),'createdAtMs':int(now),'shares':float(o.shares),'remainingShares':float(o.shares),'activeHftOrderNum':int(hnum)}
            responsibility_tokens.append(tok)
            responsibility_events.append({'atMs':now,'side':side,'action':'OBJECTIVE_TOKEN_CREATED','tokenId':tok['tokenId'],'sourcePaperOrderId':o.id,'reason':tok['reason'],'decisionId':tok['decisionId'],'priceTick':tok['priceTick'],'shares':tok['shares'],'activeHftOrderNum':int(hnum)})
        return out

    def add_wrap(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
        if behavior_freeze_new_intents:
            behavior_override_events.append({'atMs':now,'trigger':'INTENT_GATE','proposal':{'blocked':'MAKER_INTENT','side':side,'reason':reason}})
            return False
        before=set(c.orders);made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        if made:
            new=list(set(c.orders)-before)
            if new:
                o=c.orders[new[0]]; shares=float(o.shares); paper_lineage[o.id]={'reason':reason,'decisionId':decision_id,'priceTick':int(o.price_tick),'placedAtMs':int(now)}
                covered=0.0; matched=None
                for tok in responsibility_tokens:
                    if float(tok.get('remainingShares') or 0.0)<=EPS: continue
                    if tok.get('side')!=side or tok.get('reason')!=reason or int(tok.get('priceTick') or -999999)!=int(o.price_tick): continue
                    hnum=tok.get('activeHftOrderNum')
                    if hnum is None or active.get(side) is None or int(hnum)!=int(active[side]): continue
                    hs=ex.order_snapshot(a.bt,int(hnum));
                    if str(hs.get('status') or 'NONE') in TERMINAL: continue
                    covered=min(float(tok['remainingShares']),shares); matched=tok; break
                net_new=max(0.0,shares-covered)
                if covered>EPS and matched is not None:
                    matched['remainingShares']=float(matched['remainingShares'])-covered
                    responsibility_events.append({'atMs':now,'side':side,'action':'COALESCE_SAME_OBJECTIVE_REISSUE','tokenId':matched['tokenId'],'sourcePaperOrderId':matched['sourcePaperOrderId'],'newPaperOrderId':o.id,'shares':covered,'reason':reason,'priceTick':int(o.price_tick),'activeHftOrderNum':int(active[side]),'remainingTokenShares':matched['remainingShares']})
                if net_new>EPS:
                    desired[side]+=net_new;target_revision[side]+=1
                maker_intents.append({'atMs':now,'side':side,'shares':shares,'decisionId':decision_id,'intentId':o.id,'reason':reason,'priceTick':int(o.price_tick),'desiredAfter':desired[side],'targetIncrementShares':net_new,'responsibilityCoveredShares':covered,'matchedResponsibilityTokenId':None if matched is None else matched['tokenId']})
        return made

    def taker_wrap(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect):
        if behavior_freeze_new_intents or behavior_freeze_frozen_taker_intents:
            behavior_override_events.append({'atMs':now,'trigger':'INTENT_GATE','proposal':{'blocked':'TAKER_INTENT','side':side,'price':float(price)}})
            return
        # Keep the frozen controller's intent-side bookkeeping, but never credit the unconfirmed Taker to inventory.
        feedback.suppress_begin()
        try: orig_taker(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect)
        finally: feedback.suppress_end()
        tracking_error=actual_net()-target_net(); recovery_side='DOWN' if tracking_error>EPS else ('UP' if tracking_error<-EPS else None)
        submit_taker(side,CHUNK,float(price),now,'FROZEN_R2',{'predEffect':pred_effect,'decisionId':decision_id,'pTaker1s':float(p1),'pTaker3s':float(p3),'pPassiveRepair':float(ppass),'trackingErrorBefore':float(tracking_error),'actualNetBefore':float(actual_net()),'targetNetBefore':float(target_net()),'signedRecoverySide':recovery_side,'intentSideAlignedWithTrackingRepair':bool(recovery_side is not None and side==recovery_side)})

    c._fill_orders=fill_orders_wrap;c._add_order=add_wrap;c._record_taker=taker_wrap

    def asof_own_state_snapshot(source:dict[str,Any],now:int)->dict[str,Any]:
        """Carry the last observed public state forward only to process an own-state event."""
        snap=copy.deepcopy(source);source_ms=int(source.get('sampledAtMs') or now)
        seconds=finite(source.get('secondsLeft'))
        if math.isfinite(seconds):snap['secondsLeft']=max(0.0,seconds-max(0,now-source_ms)/1000.0)
        snap['sampledAtMs']=int(now);snap['ownStateEventReentry']=True;snap['publicStateAsOfMs']=source_ms
        return snap

    def controller_step(now:int,snap:dict[str,Any],trigger:str)->None:
        nonlocal last_option_action,pending_behavior_execution_action,behavior_freeze_new_intents,behavior_freeze_frozen_taker_intents,behavior_freeze_maker_execution_children,episode_action,episode_checkpoint_idx,asym_since,last_eval_ms
        c._step(dict(snap));current_decision=None
        if c.last_decision is not None and int(c.last_decision.get('decisionMs') or -1)==now:
            current_decision=copy.deepcopy(c.last_decision);current_decision['executionTrigger']=trigger;decisions.append(current_decision)
        pending_behavior_execution_action=None
        if behavior_policy_override is not None:
            before_desired=copy.deepcopy(desired)
            state=execution_state(now,snap,current_decision)
            proposal=behavior_policy_override({'atMs':now,'trigger':trigger,'executionState':copy.deepcopy(state),'desiredPortfolio':copy.deepcopy(desired),'targetRevision':copy.deepcopy(target_revision)})
            if proposal is not None:
                proposal=dict(proposal)
                for side,key in (('UP','desiredUP'),('DOWN','desiredDOWN')):
                    if key in proposal and proposal[key] is not None:
                        requested=max(0.0,float(proposal[key])); bounded=max(float(actual_side(side)),requested)
                        if abs(float(desired[side])-bounded)>EPS:
                            desired[side]=bounded;target_revision[side]+=1
                for side,key in (('UP','deltaUP'),('DOWN','deltaDOWN')):
                    if key in proposal and proposal[key] is not None:
                        requested=max(float(actual_side(side)),float(desired[side])+float(proposal[key]))
                        if abs(float(desired[side])-requested)>EPS:
                            desired[side]=requested;target_revision[side]+=1
                mode=proposal.get('executionMode')
                if mode is not None:
                    pending_behavior_execution_action=str(mode)
                if 'freezeNewEconomicIntents' in proposal:
                    behavior_freeze_new_intents=bool(proposal.get('freezeNewEconomicIntents'))
                if 'freezeFrozenR2TakerIntents' in proposal:
                    behavior_freeze_frozen_taker_intents=bool(proposal.get('freezeFrozenR2TakerIntents'))
                if 'freezeMakerExecutionChildren' in proposal:
                    behavior_freeze_maker_execution_children=bool(proposal.get('freezeMakerExecutionChildren'))
                # Research-only all-side stale-child retirement hook.  It can request cancellation
                # of a concrete live Maker child on either side, but ownership is never released
                # until service_replace observes venue-terminal evidence.  Desired inventory is not
                # mutated here; this is execution-state reassessment only.
                retire_side=proposal.get('retireMakerSide')
                if retire_side is not None:
                    retire_side=str(retire_side).upper()
                    if retire_side not in {'UP','DOWN'}:
                        raise ValueError(f'unsupported retireMakerSide: {retire_side}')
                    num=active.get(retire_side)
                    if num is not None and not route_block[retire_side] and retire_side not in pending_replace:
                        os=ex.order_snapshot(a.bt,int(num));cur=a.bt.orders(0).get(int(num));st=str(os.get('status') or 'NONE')
                        if st in {'NEW','PARTIALLY_FILLED'} and cur is not None and bool(cur.cancellable):
                            err_now=actual_net()-target_net(); remaining_now=min(CHUNK,abs(err_now))
                            rec={'state':'RETIRE_CANCEL_REQUESTED','side':retire_side,'childOrderNum':int(num),'triggerAtMs':now,'cancelRequestedAtMs':None,'requestedQty':remaining_now,'initialTrackingError':err_now,'childLeavesAtTrigger':float(os.get('leavesQty') or 0.0),'source':'BEHAVIOR_ALLSIDE_STALE_REASSESS'}
                            pending_replace[retire_side]=rec;replace_history.append(rec);route_block[retire_side]=True
                            try:a.bt.cancel(0,int(num),False);pending_replace[retire_side]['cancelRequestedAtMs']=now
                            except Exception:pass
                            lifecycle.append({'atMs':now,'side':retire_side,'action':'ALLSIDE_STALE_RETIRE_CANCEL_REQUESTED','childOrderNum':int(num),'trackingError':err_now,'targetRevision':int(target_revision[retire_side])})
                behavior_override_events.append({'atMs':now,'trigger':trigger,'beforeDesired':before_desired,'afterDesired':copy.deepcopy(desired),'targetRevision':copy.deepcopy(target_revision),'proposal':copy.deepcopy(proposal),'actualPortfolio':copy.deepcopy(state.get('actualPortfolio')),'trackingErrorBefore':state.get('trackingError')})
                confirmed_fault=behavior_memory.get('lastOutcome') in {'TERMINAL_ZERO_FILL','PARTIAL','SUBMIT_REJECT','NO_FILL_STALL'}
                if fault_containment_freeze_before_redecision and confirmed_fault and episode_action is not None:
                    episode_action=None;episode_checkpoint_idx=0;asym_since=now;last_eval_ms=None
                    lifecycle.append({'atMs':now,'side':'NONE','action':'BEHAVIOR_FAULT_REENTRY','reason':'CONFIRMED_EXECUTION_FAULT_BEFORE_REDECISION','lastOutcome':behavior_memory.get('lastOutcome'),'lastFault':behavior_memory.get('lastFault')})
                if behavior_ownstate_reentry and trigger=='OWN_STATE_EVENT' and episode_action is not None:
                    episode_action=None;episode_checkpoint_idx=0;asym_since=now;last_eval_ms=None
                    lifecycle.append({'atMs':now,'side':'NONE','action':'BEHAVIOR_OWNSTATE_REENTRY','reason':'MATERIAL_OWN_STATE_EVENT_AFTER_PRIOR_ACTION'})
        if trace_option_transitions and current_decision is not None:
            current_action=str(current_decision.get('desiredPortfolioAction') or 'NONE')
            if current_action!=last_option_action:
                state=execution_state(now,snap,current_decision);state['optionIndex']=len(option_transitions)
                state['previousDesiredPortfolioAction']=last_option_action;state['executionTrigger']=trigger
                option_transitions.append(state);last_option_action=current_action
        if trace_execution_states:
            state=execution_state(now,snap,current_decision);state['executionTrigger']=trigger
            execution_states.append(state)
        lifecycle_step(now,snap)
        for side in ('UP','DOWN'):submit_maker(side,now)

    def poll_own_state_until(target:int,source:dict[str,Any])->None:
        if own_state_poll_ms is None:return
        poll_ms=int(own_state_poll_ms);now=int(a.bt.current_timestamp//1_000_000)+poll_ms
        while now<target:
            ex.advance_to(a.bt,now);area(now)
            fill_before=len(fill_log);return_before=sum(x.get('action')=='RETURN_TO_CONTROLLER' for x in lifecycle)
            harvest(now);service_replace(now)
            observed_fills=fill_log[fill_before:]
            returned=sum(x.get('action')=='RETURN_TO_CONTROLLER' for x in lifecycle)>return_before
            if observed_fills or returned:
                event_snap=asof_own_state_snapshot(source,now)
                controller_step(now,event_snap,'OWN_STATE_EVENT')
                own_state_reentries.append({'atMs':now,'publicStateAsOfMs':int(source.get('sampledAtMs') or now),'fillEvents':[copy.deepcopy(event) for event in observed_fills],'lifecycleReturnObserved':bool(returned)})
            now+=poll_ms

    try:
        last_public_snap=None
        for s in snaps:
            t=int(s['sampledAtMs'])
            if last_public_snap is not None:poll_own_state_until(t,last_public_snap)
            if int(a.bt.current_timestamp//1_000_000)<t:ex.advance_to(a.bt,t)
            area(t);harvest(t);service_replace(t);controller_step(t,s,'PUBLIC_SNAPSHOT');last_public_snap=s
        terminal=int(a.meta['lastReceivedMs'])
        if last_public_snap is not None:poll_own_state_until(terminal,last_public_snap)
        if int(a.bt.current_timestamp//1_000_000)<terminal:ex.advance_to(a.bt,terminal)
        area(terminal);harvest(terminal);service_replace(terminal)
        # Data-end is not a venue cancel ACK. Preserve ownership and mark uncertainty explicitly.
        for side,p in list(pending_replace.items()):
            if p.get('state') in {'CANCEL_REQUESTED','RETIRE_CANCEL_REQUESTED'}:
                p['state']='PENDING_AT_DATA_END';p['dataEndAtMs']=terminal;p['completion']='VENUE_STATUS_UNKNOWN_OWNERSHIP_PRESERVED';p['cancelAckEvidence']='DATA_END_NO_TERMINAL_OBSERVED';p['cancelWaitMs']=terminal-int(p.get('cancelRequestedAtMs') or p.get('triggerAtMs') or terminal);p['timeoutSemantics']='TIMEOUT_NEVER_RELEASES_OWNERSHIP_WITHOUT_VENUE_TERMINAL_EVIDENCE'
                remainder_owner[side]={'state':'PENDING_AT_DATA_END','owner':'CANCEL_PENDING_CHILD','remainingDeficit':min(CHUNK,abs(actual_net()-target_net())),'returnedAtMs':terminal,'targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'cause':'CANCEL_PENDING_AT_DATA_END','childOrderNum':int(p['childOrderNum']),'childLeavesAtTrigger':float(p.get('childLeavesAtTrigger') or 0.0)}
                ownership_events.append({'atMs':terminal,'side':side,'action':'OWNERSHIP_PRESERVED_CANCEL_PENDING_AT_DATA_END','targetRevision':int(target_revision[side]),'sourceReplaceIndex':replace_history.index(p),'childOrderNum':int(p['childOrderNum'])})
                lifecycle.append({'atMs':terminal,'side':side,'action':'RETURN_TO_CONTROLLER','reason':'CANCEL_PENDING_AT_DATA_END','childOrderNum':int(p['childOrderNum']),'ownership':'PRESERVED_NO_RELEASE'})
                route_block[side]=True
        strategy_port=c.inventory.features(terminal);strategy_port.pop('_combined_net',None);actual_port=actual.features(terminal);actual_port.pop('_combined_net',None)
        if trace_option_transitions:
            terminal_execution_state=execution_state(terminal,snaps[-1],None,terminal=True)
    finally:a.close()
    md=desired['UP']+desired['DOWN'];mf=actual.maker_up+actual.maker_down;cu=combined_side('UP');cd=combined_side('DOWN');payout=cu if win=='UP' else cd if win=='DOWN' else None;cost=actual.maker_up_cost+actual.maker_down_cost+actual.taker_up_cost+actual.taker_down_cost+taker_fees;pnl=float(payout-cost) if payout is not None else None
    return {'version':'R2_ONLINE_TARGET_LEDGER_PAIR_COMPLETION_V10_OBJECTIVE_TOKEN_ADAPTER','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'marketId':mid,'winner':win,'paperReference':{'makerOrders':len(paper['orders']),'takerFills':len(paper['takers'])},'strategyRollout':{'makerIntents':len(maker_intents),'behaviorOverrideEvents':behavior_override_events,'targetIncrementIntents':sum(float(x.get('targetIncrementShares') or 0.0)>EPS for x in maker_intents),'duplicateTargetIncrementCensors':sum(x.get('action')=='COALESCE_SAME_OBJECTIVE_REISSUE' for x in responsibility_events),'desiredMakerShares':md,'finalControllerPortfolio':strategy_port,'suppressedDreamInventoryEvents':len(feedback.suppressed),'responsibilityTokensAtEnd':copy.deepcopy(responsibility_tokens),'responsibilityEvents':responsibility_events},'actualExecution':{'makerFilledShares':mf,'makerRealizationRate':mf/md if md>EPS else None,'takerFilledShares':actual.taker_up+actual.taker_down,'takerFeesUsdt':taker_fees,'combinedFinalAbsNet':abs(cu-cd),'finalAbsTrackingError':abs((cu-cd)-target_net()),'combinedExposureAreaShareSeconds':exposure_area,'targetErrorAreaShareSeconds':tracking_area,'realizedPnl':pnl,'finalPortfolio':actual_port},'lifecycle':{'model':'SEQUENTIAL_ARBITRATION_OPTION_V1/currentOnly','thresholds':thresholds,'decisionCount':len(lifecycle),'actionCounts':{k:sum(x['action']==k for x in lifecycle) for k in ['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETIRE_OBLIGATION','RETURN_TO_PASSIVE_REPAIR','RETURN_TO_CONTROLLER']},'replaceEpisodes':replace_history,'takerChildStateCounts':{k:sum(1 for om in taker_meta.values() if om.get('lifecycleState')==k) for k in ['SUBMITTED','ACKED_OPEN','PARTIAL','FILLED','CANCEL_REQUESTED','TERMINAL_ZERO_FILL']},'cancelPendingAtDataEnd':sum(1 for p in replace_history if p.get('state')=='PENDING_AT_DATA_END'),'cancelAckEvidenceCounts':{k:sum(1 for p in replace_history if p.get('cancelAckEvidence')==k) for k in ['VENUE_TERMINAL_OBSERVED','DATA_END_NO_TERMINAL_OBSERVED']},'unresolvedTakerReturns':sum(1 for p in replace_history if p.get('state')=='RETURN_TO_CONTROLLER_UNRESOLVED'),'unresolvedCauseCounts':{k:sum(1 for p in replace_history if p.get('unresolvedCause')==k) for k in ['TERMINAL_ZERO_FILL','PARTIAL_CHILD_REMAINDER','FILLED_CHILD_TARGET_MOVED','EXPLICIT_RETIRE']},'remainderOwnershipAtEnd':copy.deepcopy(remainder_owner),'ownershipEvents':ownership_events,'ownershipEventCounts':{k:sum(1 for x in ownership_events if x.get('action')==k) for k in ['OWNERSHIP_RETURNED_UNRESOLVED','OWNERSHIP_RETIRED_TO_CONTROLLER','OWNERSHIP_RETURNED_TO_PASSIVE_REPAIR','BLOCK_REESCALATION_UNCHANGED_TARGET','OWNERSHIP_RELEASE_ON_TARGET_REVISION','OWNERSHIP_CLEARED_TARGET_SATISFIED','OWNERSHIP_CLEARED_ASYMMETRY_RESOLVED','OWNERSHIP_CLEARED_RESOLVED_DURING_CANCEL','OWNERSHIP_PRESERVED_CANCEL_PENDING_AT_DATA_END']},'decisions':lifecycle},'makerIntents':maker_intents,'makerSubmits':maker_submits,'makerFills':maker_fills,'takerAttempts':taker_attempts,'takerFills':taker_fills,'strictPastOptionTransitions':option_transitions,'strictPastExecutionStates':execution_states,'terminalExecutionState':terminal_execution_state,'boundary':'Opened development only. Objective-token adapter: alpha inventory uses confirmed HftBacktest fills only. A removed paper order creates responsibility only when a concrete live HFT child carrying the same execution responsibility remains live. Same-objective reissues are coalesced only against that child. Optional research lifecycle overrides may RETIRE an execution obligation only after terminal cancel evidence; unchanged target revisions remain blocked until control returns to Frozen R2. V5/V7 cancel/remainder/Taker completion semantics preserved. Not graduation-eligible.'}


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--output',default='r2_online_target_ledger_pair_completion_v10_objective_token_adapter.json');a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    for i,mid in enumerate(mids,1):
        r=run_market(mid);rows.append(r);print(json.dumps({'progress':i,'marketId':mid,'paperMaker':r['paperReference']['makerOrders'],'onlineMaker':r['strategyRollout']['makerIntents'],'makerReal':r['actualExecution']['makerRealizationRate'],'actions':r['lifecycle']['actionCounts'],'absTrack':r['actualExecution']['finalAbsTrackingError'],'pnl':r['actualExecution']['realizedPnl']},ensure_ascii=False),flush=True)
    pnls=[float(r['actualExecution']['realizedPnl']) for r in rows if r['actualExecution']['realizedPnl'] is not None];md=sum(float(r['strategyRollout']['desiredMakerShares']) for r in rows);mf=sum(float(r['actualExecution']['makerFilledShares']) for r in rows);agg={'markets':len(rows),'paperMakerOrders':sum(r['paperReference']['makerOrders'] for r in rows),'onlineMakerIntents':sum(r['strategyRollout']['makerIntents'] for r in rows),'targetIncrementIntents':sum(r['strategyRollout'].get('targetIncrementIntents',0) for r in rows),'duplicateTargetIncrementCensors':sum(r['strategyRollout'].get('duplicateTargetIncrementCensors',0) for r in rows),'makerDesiredShares':md,'makerFilledShares':mf,'makerRealizationRate':mf/md if md>EPS else None,'totalRealizedPnl':sum(pnls),'wins':sum(x>EPS for x in pnls),'losses':sum(x<-EPS for x in pnls),'winRate':sum(x>EPS for x in pnls)/len(pnls) if pnls else None,'maxCumulativeDrawdown':max_drawdown(pnls),'meanFinalAbsTrackingError':sum(float(r['actualExecution']['finalAbsTrackingError']) for r in rows)/len(rows) if rows else None,'meanCombinedFinalAbsNet':sum(float(r['actualExecution']['combinedFinalAbsNet']) for r in rows)/len(rows) if rows else None,'targetErrorAreaShareSeconds':sum(float(r['actualExecution']['targetErrorAreaShareSeconds']) for r in rows),'combinedExposureAreaShareSeconds':sum(float(r['actualExecution']['combinedExposureAreaShareSeconds']) for r in rows),'actionCounts':{k:sum(int(r['lifecycle']['actionCounts'][k]) for r in rows) for k in ['WAIT_FOR_CLARITY','KEEP_EXECUTING','REPLACE_ROUTE','RETURN_TO_CONTROLLER']},'takerFeesUsdt':sum(float(r['actualExecution']['takerFeesUsdt']) for r in rows),'takerChildStateCounts':{k:sum(int(r['lifecycle'].get('takerChildStateCounts',{}).get(k,0)) for r in rows) for k in ['SUBMITTED','ACKED_OPEN','PARTIAL','FILLED','CANCEL_REQUESTED','TERMINAL_ZERO_FILL']},'cancelPendingAtDataEnd':sum(int(r['lifecycle'].get('cancelPendingAtDataEnd',0)) for r in rows),'cancelAckEvidenceCounts':{k:sum(int(r['lifecycle'].get('cancelAckEvidenceCounts',{}).get(k,0)) for r in rows) for k in ['VENUE_TERMINAL_OBSERVED','DATA_END_NO_TERMINAL_OBSERVED']},'unresolvedTakerReturns':sum(int(r['lifecycle'].get('unresolvedTakerReturns',0)) for r in rows),'unresolvedCauseCounts':{k:sum(int(r['lifecycle'].get('unresolvedCauseCounts',{}).get(k,0)) for r in rows) for k in ['TERMINAL_ZERO_FILL','PARTIAL_CHILD_REMAINDER','FILLED_CHILD_TARGET_MOVED']},'ownershipEventCounts':{k:sum(int(r['lifecycle'].get('ownershipEventCounts',{}).get(k,0)) for r in rows) for k in ['OWNERSHIP_RETURNED_UNRESOLVED','BLOCK_REESCALATION_UNCHANGED_TARGET','OWNERSHIP_RELEASE_ON_TARGET_REVISION','OWNERSHIP_CLEARED_TARGET_SATISFIED','OWNERSHIP_CLEARED_ASYMMETRY_RESOLVED','OWNERSHIP_CLEARED_RESOLVED_DURING_CANCEL','OWNERSHIP_PRESERVED_CANCEL_PENDING_AT_DATA_END']}}
    out=OUT/a.output;out.write_text(json.dumps({'version':'R2_ONLINE_TARGET_LEDGER_PAIR_COMPLETION_V10_OBJECTIVE_TOKEN_ADAPTER_REPORT','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'aggregate':agg,'rows':rows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
