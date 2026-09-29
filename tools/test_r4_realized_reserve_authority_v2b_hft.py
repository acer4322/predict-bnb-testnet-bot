from __future__ import annotations

import argparse
import copy
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import HftBookAdapter, load_public_snapshots, load_reference_paper, new_controller
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod

OUT = ROOT / 'data/research/execution_aware_fill_lifecycle_v0'
EPS = 1e-9
TAKER_CONFIRM_MS = 2200
TERMINAL = {'FILLED','REJECTED','EXPIRED','CANCELED'}


class CommittedInventory(mod.Inventory):
    """Strategy target ledger.

    Positive MAKER events mean accepted strategy commitments, not venue fills.
    RELEASE events are represented as signed MAKER events so historical target net
    remains reconstructable, while fill-like recency helpers ignore releases.
    """
    def apply_commit(self, *, event_ms:int, role:str, side:str, price:float, shares:float, intent_id:str) -> None:
        self.apply({'event_ms':int(event_ms),'role':str(role),'side':str(side),'price':float(price),'shares':float(shares),'event_type':'COMMIT','intent_id':intent_id})

    def release(self, *, event_ms:int, role:str, side:str, price:float, shares:float, intent_id:str, reason:str) -> None:
        q=float(shares)
        if q <= EPS:
            return
        self.apply({'event_ms':int(event_ms),'role':str(role),'side':str(side),'price':float(price),'shares':-q,'event_type':'RELEASE','intent_id':intent_id,'reason':reason})

    def _recent(self, now:int, role:str, window:int):
        return [e for e in super()._recent(now, role, window) if str(e.get('event_type') or 'COMMIT') != 'RELEASE' and float(e.get('shares') or 0.0) > 0]

    def _last_age(self, now:int, role:str, side:str|None=None) -> float:
        for e in reversed(self.events):
            if str(e.get('event_type') or 'COMMIT') == 'RELEASE' or float(e.get('shares') or 0.0) <= 0:
                continue
            if e['role']==role and (side is None or e['side']==side):
                return float(now-int(e['event_ms']))
        return math.nan

    def _streak(self, role:str) -> float:
        side=None; n=0
        for e in reversed(self.events):
            if str(e.get('event_type') or 'COMMIT') == 'RELEASE' or float(e.get('shares') or 0.0) <= 0:
                continue
            if e['role'] != role:
                continue
            if side is None:
                side=e['side']
            if e['side'] != side:
                break
            n += 1
        return float(n)


def _commit_overlap_episode(c: mod.UnifiedControllerPaperV2, order: mod.PaperOrder, now:int, pre_net:float, pre_gross:float) -> None:
    if not bool(order.occupied_before):
        return
    pre_pc = 2.0*min(c.inventory.maker_up - (order.shares if order.side=='UP' else 0.0), c.inventory.maker_down - (order.shares if order.side=='DOWN' else 0.0))/pre_gross if pre_gross>EPS else 1.0
    post_net = c.inventory.maker_up-c.inventory.maker_down
    predom = 'UP' if pre_net>EPS else 'DOWN' if pre_net<-EPS else None
    if predom==order.side and abs(pre_net)>=mod.SHARES-EPS and abs(post_net)>abs(pre_net)+1:
        if c.episode is None or str(c.episode.get('side')) != order.side:
            c.episode={'kind':'OVERLAP','side':order.side,'risk_start_ms':int(now),'risk_pre_abs':abs(pre_net),'start_ms':int(now),'pre_abs':abs(pre_net),'start_abs':abs(post_net),'expansion':abs(post_net)-abs(pre_net),'pre_pc':pre_pc,'unresolved':False}
        else:
            c.episode.update({'kind':'OVERLAP','risk_start_ms':int(now),'risk_pre_abs':abs(pre_net),'start_ms':int(now),'pre_abs':abs(pre_net),'start_abs':abs(post_net),'expansion':abs(post_net)-abs(pre_net),'pre_pc':pre_pc,'unresolved':False})
        c.readiness=False
        c.current_metrics['excursions'] += 1
        c.run_metrics['excursions'] += 1


def run_market(mid:int, *, taker_confirm_ms:int=TAKER_CONFIRM_MS) -> dict[str,Any]:
    snaps=load_public_snapshots(mid)
    if not snaps:
        raise RuntimeError(f'no snapshots for {mid}')
    paper=load_reference_paper(mid)
    win=winners([mid]).get(mid)
    a=HftBookAdapter(mid,1092,273,'risk','mid')
    c=new_controller(a)
    c.inventory=CommittedInventory()
    actual=mod.Inventory()
    actual_taker_fees=0.0

    maker_exec:dict[int,dict[str,Any]]={}
    taker_exec:dict[int,dict[str,Any]]={}
    order_num_by_id:dict[str,int]={}
    commitment_events:list[dict[str,Any]]=[]
    release_events:list[dict[str,Any]]=[]
    maker_fills:list[dict[str,Any]]=[]
    taker_attempts:list[dict[str,Any]]=[]
    taker_fills:list[dict[str,Any]]=[]
    cancels:list[dict[str,Any]]=[]
    decisions:list[dict[str,Any]]=[]
    pending_restore:dict[str,Any]|None=None

    orig_add=c._add_order

    def request_cancel_num(num:int, now:int, reason:str) -> None:
        om=maker_exec.get(num)
        if om is None or om.get('terminal'):
            return
        if om.get('cancelRequestedAtMs') is None:
            om['cancelRequestedAtMs']=int(now);om['cancelReason']=reason
            cancels.append({'atMs':int(now),'orderNum':num,'intentId':om['intentId'],'side':om['side'],'reason':reason})
        cur=a.bt.orders(0).get(int(num))
        if cur is not None and bool(cur.cancellable):
            try:
                a.bt.cancel(0,int(num),False);om['cancelSubmitted']=True
            except Exception:
                pass

    def retry_cancels(now:int) -> None:
        for num,om in maker_exec.items():
            if om.get('terminal') or om.get('cancelRequestedAtMs') is None or om.get('cancelSubmitted'):
                continue
            request_cancel_num(num,now,str(om.get('cancelReason') or 'RETRY_CANCEL'))
        for num,om in taker_exec.items():
            if om.get('terminal') or not om.get('cancelRequested') or om.get('cancelSubmitted'):
                continue
            cur=a.bt.orders(0).get(int(num))
            if cur is not None and bool(cur.cancellable):
                try:a.bt.cancel(0,int(num),False);om['cancelSubmitted']=True
                except Exception:pass

    def release_maker(om:dict[str,Any], now:int, q:float, reason:str) -> None:
        if q<=EPS:return
        c.inventory.release(event_ms=now,role='MAKER',side=om['side'],price=om['price'],shares=q,intent_id=om['intentId'],reason=reason)
        release_events.append({'atMs':int(now),'role':'MAKER','side':om['side'],'shares':float(q),'price':float(om['price']),'intentId':om['intentId'],'reason':reason})

    def harvest(now:int) -> None:
        nonlocal actual_taker_fees
        retry_cancels(now)
        for num,om in list(maker_exec.items()):
            if om.get('terminal'):
                continue
            s=a.snap_num(num);cum=float(s.get('cumExecQty') or 0.0);old=float(om.get('prevCum') or 0.0)
            if cum>old+EPS:
                q=cum-old;native=s.get('execPrice');px=float(om['price'])
                if native is not None and math.isfinite(float(native)):px=float(native) if om['side']=='UP' else 1.0-float(native)
                fill_ms=int((s.get('exchangeTs') or int(now)*1_000_000)//1_000_000)
                actual.apply({'event_ms':fill_ms,'role':'MAKER','side':om['side'],'price':px,'shares':q})
                om['prevCum']=cum
                maker_fills.append({'atMs':fill_ms,'observedAtMs':int(now),'orderNum':num,'intentId':om['intentId'],'side':om['side'],'price':px,'deltaShares':q,'cumShares':cum,'status':s.get('status'),'duringCancel':bool(om.get('cancelRequestedAtMs') is not None)})
            st=str(s.get('status') or 'NONE')
            if st in TERMINAL:
                rem=max(0.0,float(om['qty'])-cum)
                if st!='FILLED' and rem>EPS:
                    release_maker(om,int(now),rem,f'HFT_{st}')
                om['terminal']=st;om['terminalAtMs']=int(now)
                key=tuple(om['key'])
                cur=c.orders.get(key)
                if cur is not None and str(cur.id)==str(om['intentId']):
                    c.orders.pop(key,None);c.last_closed[key]=int(now)
        for num,om in list(taker_exec.items()):
            if om.get('terminal'):continue
            s=a.snap_num(num);cum=float(s.get('cumExecQty') or 0.0);old=float(om.get('prevCum') or 0.0)
            if cum>old+EPS:
                q=cum-old;native=s.get('execPrice');px=float(om['observedPrice'])
                if native is not None and math.isfinite(float(native)):px=float(native) if om['side']=='UP' else 1.0-float(native)
                fill_ms=int((s.get('exchangeTs') or int(now)*1_000_000)//1_000_000)
                # Taker target state advances only on actual completion/fill in V1.
                c.inventory.apply_commit(event_ms=fill_ms,role='TAKER',side=om['side'],price=px,shares=q,intent_id=om['intentId'])
                actual.apply({'event_ms':fill_ms,'role':'TAKER','side':om['side'],'price':px,'shares':q})
                fee=mod.taker_fee(q,px,mod.FEE_BPS);actual_taker_fees += fee;c.taker_fee_spent += fee
                om['prevCum']=cum;c.current_metrics['takerFills']+=1;c.run_metrics['takerFills']+=1;c.last_taker_ms=fill_ms
                taker_fills.append({'atMs':fill_ms,'observedAtMs':int(now),'orderNum':num,'intentId':om['intentId'],'side':om['side'],'price':px,'shares':q,'feeUsdt':fee,'status':s.get('status')})
            st=str(s.get('status') or 'NONE')
            if st in TERMINAL:
                om['terminal']=st;om['terminalAtMs']=int(now)

    blocked_surplus=[]
    def add_wrap(side:str,now:int,snapshot_ns:int,decision_id:str,reason:str,p:float,snapshot:dict[str,Any],allow_stack:bool=True,bypass_guard:bool=False)->bool:
        af=actual.features(int(now)); rf=float(af.get('worst_case_floor') or 0.0); au=float(actual.maker_up+actual.taker_up); ad=float(actual.maker_down+actual.taker_down); surplus='UP' if au>ad+EPS else 'DOWN' if ad>au+EPS else 'FLAT'
        cf=float(c.inventory.features(int(now)).get('worst_case_floor') or 0.0)
        if cf>0 and rf<=0 and surplus==side:
            blocked_surplus.append({'atMs':int(now),'side':side,'committedFloor':cf,'realizedFloor':rf,'actualUp':au,'actualDown':ad,'reason':reason})
            return False
        before=set(c.orders)
        pre_net=c.inventory.maker_up-c.inventory.maker_down;pre_g=c.inventory.maker_up+c.inventory.maker_down
        made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        if not made:return False
        new=list(set(c.orders)-before)
        if not new:return made
        key=new[0];order=c.orders[key]
        num,rc=a.submit(key,order)
        if rc!=0:
            c.orders.pop(key,None);c.last_closed[key]=int(now)
            commitment_events.append({'atMs':int(now),'role':'MAKER','side':side,'shares':0.0,'price':float(order.price),'intentId':order.id,'reason':reason,'submitRc':int(rc),'accepted':False})
            return False
        maker_exec[num]={'key':list(key),'intentId':order.id,'side':side,'price':float(order.price),'qty':float(order.shares),'prevCum':0.0,'submittedAtMs':int(now),'reason':reason,'submitRc':int(rc),'terminal':None,'cancelRequestedAtMs':None,'cancelSubmitted':False}
        order_num_by_id[order.id]=num
        c.inventory.apply_commit(event_ms=now,role='MAKER',side=side,price=order.price,shares=order.shares,intent_id=order.id)
        commitment_events.append({'atMs':int(now),'role':'MAKER','side':side,'shares':float(order.shares),'price':float(order.price),'intentId':order.id,'reason':reason,'submitRc':int(rc),'accepted':True,'orderNum':num})
        _commit_overlap_episode(c,order,now,pre_net,pre_g)
        return True

    def cancel_wrap(key:tuple[str,int],at_ms:int,reason:str)->None:
        order=c.orders.get(key)
        if order is None:return
        num=order_num_by_id.get(order.id)
        if num is None:
            c.orders.pop(key,None);c.last_closed[key]=int(at_ms);return
        request_cancel_num(num,int(at_ms),reason)
        # Keep Strategy working child + target commitment until venue terminal ACK.

    def fill_wrap(snapshot:dict[str,Any],now:int):
        harvest(int(now));return []

    def taker_wrap(side:str,price:float,now:int,decision_id:str,snapshot:dict[str,Any],raw:dict[str,Any],p1:float,p3:float,ppass:float,pred_effect:str)->None:
        nonlocal pending_restore
        pre_episode=copy.deepcopy(c.episode);pre_readiness=bool(c.readiness);pre_last_taker=int(c.last_taker_ms)
        max_price=min(.99,float(price)+.02);num,rc=a.submit_taker(side,max_price,mod.SHARES)
        om={'intentId':decision_id+':TAKER_INTENT','side':side,'observedPrice':float(price),'qty':float(mod.SHARES),'prevCum':0.0,'submittedAtMs':int(now),'submitRc':int(rc),'terminal':None,'cancelRequested':False,'cancelSubmitted':False}
        taker_exec[num]=om
        attempt={'atMs':int(now),'side':side,'observedAsk':float(price),'maxPrice':max_price,'orderNum':num,'submitRc':int(rc),'decisionId':decision_id}
        taker_attempts.append(attempt)
        if rc!=0:
            om['terminal']='SUBMIT_REJECT';attempt['result']='SUBMIT_REJECT';pending_restore={'episode':pre_episode,'readiness':pre_readiness,'lastTakerMs':pre_last_taker};return
        target=int(now)+int(taker_confirm_ms);a.advance(mid,target);harvest(target)
        s=a.snap_num(num);cum=float(s.get('cumExecQty') or 0.0)
        if cum < mod.SHARES-EPS:
            om['cancelRequested']=True
            cur=a.bt.orders(0).get(num)
            if cur is not None and bool(cur.cancellable):
                try:a.bt.cancel(0,num,False);om['cancelSubmitted']=True
                except Exception:pass
            pending_restore={'episode':pre_episode,'readiness':pre_readiness,'lastTakerMs':pre_last_taker}
        attempt.update({'result':'FILLED' if cum>EPS else 'NO_FILL','filledShares':cum,'confirmMs':target,'status':s.get('status'),'partial':bool(EPS<cum<mod.SHARES-EPS)})

    c._add_order=add_wrap;c._cancel_order=cancel_wrap;c._fill_orders=fill_wrap;c._record_taker=taker_wrap

    try:
        for s in snaps:
            if int(a.bt.current_timestamp//1_000_000)>int(s['sampledAtMs']):
                continue
            c._step(dict(s))
            if pending_restore is not None:
                c.episode=copy.deepcopy(pending_restore['episode']);c.readiness=bool(pending_restore['readiness']);c.last_taker_ms=int(pending_restore['lastTakerMs']);pending_restore=None
            now=int(s['sampledAtMs']);harvest(now)
            if c.last_decision is not None and int(c.last_decision.get('decisionMs') or -1)==now:decisions.append(copy.deepcopy(c.last_decision))
        terminal=int(a.meta['lastReceivedMs']);a.advance(mid,terminal);harvest(terminal)
        target_features=c.inventory.features(terminal);target_features.pop('_combined_net',None)
        actual_features=actual.features(terminal);actual_features.pop('_combined_net',None)
    finally:
        a.close()

    pnl=None
    if win in {'UP','DOWN'}:
        payout=(actual.maker_up+actual.taker_up) if win=='UP' else (actual.maker_down+actual.taker_down)
        pnl=float(actual.cash+payout-actual_taker_fees)
    maker_commit=sum(float(x['shares']) for x in commitment_events if x.get('accepted') and x['role']=='MAKER')
    maker_release=sum(float(x['shares']) for x in release_events if x['role']=='MAKER')
    maker_fill=sum(float(x['deltaShares']) for x in maker_fills)
    return {
        'version':'R4_REALIZED_RESERVE_AUTHORITY_V2B_PHANTOM_SAFE_ONLY','researchOnly':True,'liveTradingChanges':False,'marketId':mid,'winner':win,
        'strategyBrain':mod.VERSION,
        'semantics':{
            'makerTargetLedger':'COMMIT_ON_ACCEPTED_HFT_CHILD_SUBMIT; RELEASE_UNFILLED_ONLY_ON_VENUE_TERMINAL_CANCEL_REJECT_EXPIRE',
            'makerActualLedger':'HFTBACKTEST_FILLS_ONLY',
            'cancelSemantics':'COMMITMENT_AND_STRATEGY_WORKING_CHILD_PERSIST_UNTIL_VENUE_TERMINAL_ACK',
            'takerTargetLedger':'ACTUAL_HFT_FILL_ONLY_V1; failed/partial attempt restores pre-attempt episode after current controller step',
            'dreamFillUsedForPnl':False,'targetRuntimeInput':False,
        },
        'paperReference':{'makerOrders':len(paper['orders']),'makerShares':sum(float(x.get('shares') or 0) for x in paper['orders']),'takerFills':len(paper['takers'])},
        'strategyRollout':{'decisions':len(decisions),'makerCommitments':sum(1 for x in commitment_events if x.get('accepted') and x['role']=='MAKER'),'makerCommittedSharesGross':maker_commit,'makerReleasedShares':maker_release,'makerTargetSharesNetOfRelease':maker_commit-maker_release,'takerAttempts':len(taker_attempts),'targetPortfolio':target_features,'activeStrategyOrdersAtEnd':len(c.orders),'runMetrics':dict(c.run_metrics)},
        'actualExecution':{'makerFillEvents':len(maker_fills),'makerFilledShares':maker_fill,'makerRealizationVsNetTarget':maker_fill/(maker_commit-maker_release) if maker_commit-maker_release>EPS else None,'takerFillEvents':len(taker_fills),'takerFilledShares':sum(float(x['shares']) for x in taker_fills),'takerFeesUsdt':actual_taker_fees,'realizedPnl':pnl,'actualPortfolio':actual_features},
        'intentCountDeltaVsPaper':sum(1 for x in commitment_events if x.get('accepted') and x['role']=='MAKER')-len(paper['orders']),
        'commitmentEvents':commitment_events,'releaseEvents':release_events,'makerFillEvents':maker_fills,'takerAttempts':taker_attempts,'takerFillEvents':taker_fills,'cancelEvents':cancels,'decisionRows':decisions,
        'blockedSurplusDuringUnsafeRealized':blocked_surplus,'boundary':'Research-only dual ledger plus realized-reserve authority: surplus-side Maker commitments are blocked while confirmed realized floor<=0; weak-side build remains allowed. No paper fill timestamps are runtime inputs.'
    }


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()]
    rows=[]
    for i,mid in enumerate(mids,1):
        r=run_market(mid);rows.append(r);print(json.dumps({'progress':i,'marketId':mid,'paperMaker':r['paperReference']['makerOrders'],'commitMaker':r['strategyRollout']['makerCommitments'],'intentDelta':r['intentCountDeltaVsPaper'],'makerRealization':r['actualExecution']['makerRealizationVsNetTarget'],'pnl':r['actualExecution']['realizedPnl'],'takerAttempts':r['strategyRollout']['takerAttempts']},ensure_ascii=False),flush=True)
    pnls=[float(r['actualExecution']['realizedPnl']) for r in rows if r['actualExecution']['realizedPnl'] is not None]
    agg={'markets':len(rows),'paperMakerOrders':sum(r['paperReference']['makerOrders'] for r in rows),'committedMakerIntents':sum(r['strategyRollout']['makerCommitments'] for r in rows),'intentDeltaVsPaper':sum(r['intentCountDeltaVsPaper'] for r in rows),'totalPnl':sum(pnls),'wins':sum(x>EPS for x in pnls),'losses':sum(x<-EPS for x in pnls),'winRate':sum(x>EPS for x in pnls)/len(pnls) if pnls else None,'takerAttempts':sum(r['strategyRollout']['takerAttempts'] for r in rows),'makerCommittedGross':sum(r['strategyRollout']['makerCommittedSharesGross'] for r in rows),'makerReleased':sum(r['strategyRollout']['makerReleasedShares'] for r in rows),'makerFilled':sum(r['actualExecution']['makerFilledShares'] for r in rows)}
    out=ROOT/'data/research/r4_v0/hourly/r4_realized_reserve_authority_v2b_phantom_safe_hft.json';out.write_text(json.dumps({'version':'R4_REALIZED_RESERVE_AUTHORITY_V2B_PHANTOM_SAFE_ONLY_HFT_REPORT','aggregate':agg,'rows':rows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
