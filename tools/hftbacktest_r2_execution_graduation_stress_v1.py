from __future__ import annotations
import argparse,json,math,sys,sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any
import joblib, numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots,load_reference_paper
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'; ROBUST=joblib.load(OUT/'execution_robust_mpc_components_v1.joblib')
EPS=1e-9; CHUNK=float(mod.SHARES); TERMINAL={'FILLED','REJECTED','EXPIRED','CANCELED'}; TAKER_CONFIRM_MS=2200

def finite(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan

def maxdd(xs:list[float])->float:
    c=p=d=0.0
    for x in xs:c+=x;p=max(p,c);d=max(d,p-c)
    return d

def run_market(mid:int)->dict[str,Any]:
    paper=load_reference_paper(mid); snaps=load_public_snapshots(mid); snap_by={int(s['sampledAtMs']):s for s in snaps}
    mi=[{'atMs':int(o['placed_at_ms']),'side':str(o['side']).upper(),'shares':float(o.get('shares') or CHUNK)} for o in paper['orders']]
    ti=[{'atMs':int(x['filled_at_ms']),'side':str(x['side']).upper(),'shares':float(x.get('shares') or CHUNK),'observedPrice':float(x['price'])} for x in paper['takers'] if x.get('filled_at_ms') is not None and x.get('price') is not None]
    mi.sort(key=lambda x:x['atMs']);ti.sort(key=lambda x:x['atMs'])
    bm=defaultdict(list);btak=defaultdict(list)
    for x in mi:bm[x['atMs']].append(x)
    for x in ti:btak[x['atMs']].append(x)
    timeline=sorted(set([int(s['sampledAtMs']) for s in snaps]+[x['atMs'] for x in mi]+[x['atMs'] for x in ti]))
    events,_,feed=tape_v1.build_archive_events(mid,trade_offset='mid'); venue=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk');ex.initialize_bt(venue)
    book=mod.PublicBookTailer(mod.BOOK_DB); win=winners([mid]).get(mid)
    if win not in {'UP','DOWN'}:
        con=sqlite3.connect(ROOT/'data/echtgeld_engine_v1.db')
        try:
            row=con.execute('select winner from engine_settlements where market_id=? order by synced_at_ms desc limit 1',(int(mid),)).fetchone(); win=str(row[0]).upper() if row and row[0] else None
        finally: con.close()
    actual=mod.Inventory();desired={'UP':0.0,'DOWN':0.0};target_rev={'UP':0,'DOWN':0};active={'UP':None,'DOWN':None};maker_meta={};taker_meta={};route_block={'UP':False,'DOWN':False};pending={};owners={'UP':None,'DOWN':None};fills=[];life=[];next_num=1;taker_fees=0.0;asym_since=None;episode_action=None;cp_idx=0;last_maker_ms=None;last_maker_side=None;latest_snap={};area_t=None;exp_area=track_area=0.0
    def aside(s):return actual.maker_up if s=='UP' else actual.maker_down
    def cside(s):return (actual.maker_up+actual.taker_up) if s=='UP' else (actual.maker_down+actual.taker_down)
    def tnet():return desired['UP']-desired['DOWN']
    def anet():return cside('UP')-cside('DOWN')
    def area(now):
        nonlocal area_t,exp_area,track_area
        if area_t is not None and now>area_t:
            dt=(now-area_t)/1000;n=anet();exp_area+=abs(n)*dt;track_area+=abs(n-tnet())*dt
        area_t=now
    def submit_taker(side,qty,ask,now,kind):
        nonlocal next_num
        num=next_num;next_num+=1;maxp=min(.99,float(ask)+.02);ns,np_=ex.native_order(side,maxp)
        rc=int(venue.submit_buy_order(0,num,np_,float(qty),ex.hbt.GTC,ex.LIMIT,False)) if ns=='BUY' else int(venue.submit_sell_order(0,num,np_,float(qty),ex.hbt.GTC,ex.LIMIT,False))
        taker_meta[num]={'side':side,'qty':float(qty),'observedPrice':float(ask),'prevCum':0.0,'deadlineMs':now+TAKER_CONFIRM_MS,'cancelRequested':False,'terminal':None,'kind':kind,'submitRc':rc};return num
    def harvest(now):
        nonlocal taker_fees,last_maker_ms,last_maker_side
        for side in ('UP','DOWN'):
            num=active[side]
            if num is None:continue
            om=maker_meta[num];s=ex.order_snapshot(venue,num);cum=float(s.get('cumExecQty') or 0);old=float(om.get('prevCum') or 0)
            if cum>old+EPS:
                q=cum-old;native=s.get('execPrice');px=float(om['price'])
                if native is not None and math.isfinite(float(native)):px=float(native) if side=='UP' else 1-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000);actual.apply({'event_ms':fm,'role':'MAKER','side':side,'price':px,'shares':q});fills.append({'eventMs':fm,'role':'MAKER','side':side,'price':px,'shares':q,'fee':0.0});om['prevCum']=cum;last_maker_ms=fm;last_maker_side=side
            if str(s.get('status') or '') in TERMINAL:active[side]=None
        for num,om in taker_meta.items():
            if om.get('terminal'):continue
            s=ex.order_snapshot(venue,num);cum=float(s.get('cumExecQty') or 0);old=float(om.get('prevCum') or 0);st=str(s.get('status') or '')
            if cum>old+EPS:
                q=cum-old;native=s.get('execPrice');px=float(om['observedPrice'])
                if native is not None and math.isfinite(float(native)):px=float(native) if om['side']=='UP' else 1-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000);actual.apply({'event_ms':fm,'role':'TAKER','side':om['side'],'price':px,'shares':q});fee=mod.taker_fee(q,px,mod.FEE_BPS);taker_fees+=fee;fills.append({'eventMs':fm,'role':'TAKER','side':om['side'],'price':px,'shares':q,'fee':fee});om['prevCum']=cum
            if now>=om['deadlineMs'] and not om['cancelRequested'] and st in {'NEW','PARTIALLY_FILLED'}:
                cur=venue.orders(0).get(num)
                if cur is not None and bool(cur.cancellable):
                    try:venue.cancel(0,num,False);om['cancelRequested']=True
                    except Exception:pass
            if st in TERMINAL:om['terminal']=st
    def quote(side,off,oppp):
        bf=mod.outcome_book(book.book,None)
        if not bf:return None
        bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']);tick=int(math.floor((bid+1e-9)/mod.GRID))-off;tick=max(int(round(mod.MIN_PRICE/mod.GRID)),tick);px=round(tick*mod.GRID,2)
        if oppp is not None:
            while px+float(oppp)>mod.MAX_PAIR_PRICE_SUM+EPS:
                tick-=1
                if tick<int(round(mod.MIN_PRICE/mod.GRID)):return None
                px=round(tick*mod.GRID,2)
        return px
    def submit_maker(side,now):
        nonlocal next_num
        o=owners.get(side)
        if o is not None and o.get('state')=='RETURNED_UNRESOLVED' and int(o.get('targetRevision') or 0)==int(target_rev[side]):return
        if o is not None and int(o.get('targetRevision') or 0)!=int(target_rev[side]):owners[side]=None
        if route_block[side] or active[side] is not None:return
        deficit=desired[side]-aside(side)
        if deficit<=EPS:return
        err=(actual.maker_up-actual.maker_down)-(desired['UP']-desired['DOWN']);recovery='DOWN' if err>CHUNK-EPS else 'UP' if err<-CHUNK+EPS else None;off=1 if recovery is None else (0 if side==recovery else 3);qty=min(CHUNK,deficit)
        if recovery is not None and side!=recovery:qty=min(qty,CHUNK*.5)
        opp='DOWN' if side=='UP' else 'UP';oppp=float(maker_meta[active[opp]]['price']) if active[opp] is not None else None;px=quote(side,off,oppp)
        if px is None:return
        num=next_num;next_num+=1;rc=ex.submit_native(venue,num,side,px,qty);maker_meta[num]={'side':side,'price':px,'qty':qty,'prevCum':0.0,'submittedAtMs':now,'submitRc':int(rc)}
        if rc==0:active[side]=num
    def features(side,now,snap):
        bf=mod.outcome_book(book.book,None)
        if not bf:return None
        other='DOWN' if side=='UP' else 'UP';bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']);ask=float(bf['up_ask'] if side=='UP' else bf['down_ask']);obid=float(bf['down_bid'] if side=='UP' else bf['up_bid']);oask=float(bf['down_ask'] if side=='UP' else bf['up_ask']);num=active[side]
        child={'workingRecoveryExists':float(num is not None),'workingRecoveryAgeMs':None,'workingRecoveryOffsetTicks':None,'workingRecoveryRemainingQty':None}
        if num is not None:
            om=maker_meta[num];os=ex.order_snapshot(venue,num);child.update({'workingRecoveryAgeMs':now-int(om['submittedAtMs']),'workingRecoveryOffsetTicks':(bid-float(om['price']))/mod.GRID,'workingRecoveryRemainingQty':float(os.get('leavesQty') or 0)})
        err=anet()-tnet();need=min(CHUNK,abs(err));cc=cs=0.0
        for ev in reversed(fills):
            if ev['side']!=other or need<=EPS:continue
            q=min(need,float(ev['shares']));cc+=q*(float(ev['price'])+float(ev.get('fee') or 0)/max(float(ev['shares']),EPS));cs+=q;need-=q
        avg=cc/cs if cs>EPS else None;locked=1-avg-ask-mod.taker_fee(1.0,ask,mod.FEE_BPS) if avg is not None else None;sign=1.0 if side=='UP' else -1.0
        return {'workingRecoveryExists':child['workingRecoveryExists'],'workingRecoveryAgeMs':child['workingRecoveryAgeMs'],'workingRecoveryOffsetTicks':child['workingRecoveryOffsetTicks'],'workingRecoveryRemainingQty':child['workingRecoveryRemainingQty'],'absTrackingError':abs(err),'trackingError':err,'actualMakerNet':actual.maker_up-actual.maker_down,'actualCombinedGross':cside('UP')+cside('DOWN'),'secondsLeft':snap.get('secondsLeft'),'recoveryBid':bid,'recoveryAsk':ask,'recoverySpreadTicks':(ask-bid)/mod.GRID,'pairAskSum':ask+oask,'pairBidSum':bid+obid,'marginalSurplusChunkAvgCost':avg,'lockedPairEdgePerShare':locked,'lastMakerFillAgeMs':now-last_maker_ms if last_maker_ms is not None else None,'lastMakerFillSideIsRecovery':float(last_maker_side==side),'directionTowardRecovery':finite(snap.get('directionScore'))*sign if not math.isnan(finite(snap.get('directionScore'))) else None,'spotReturn1sTowardRecovery':finite(snap.get('spotReturn1sBps'))*sign if not math.isnan(finite(snap.get('spotReturn1sBps'))) else None,'spotReturn3sTowardRecovery':finite(snap.get('spotReturn3sBps'))*sign if not math.isnan(finite(snap.get('spotReturn3sBps'))) else None,'spotQueueTowardRecovery':finite(snap.get('spotQueueImbalance'))*sign if not math.isnan(finite(snap.get('spotQueueImbalance'))) else None,'spotTaker1sTowardRecovery':finite(snap.get('spotTakerImbalance1s'))*sign if not math.isnan(finite(snap.get('spotTakerImbalance1s'))) else None,'futuresReturn1sTowardRecovery':finite(snap.get('futuresReturn1sBps'))*sign if not math.isnan(finite(snap.get('futuresReturn1sBps'))) else None,'futuresReturn3sTowardRecovery':finite(snap.get('futuresReturn3sBps'))*sign if not math.isnan(finite(snap.get('futuresReturn3sBps'))) else None,'futuresQueueTowardRecovery':finite(snap.get('futuresQueueImbalance'))*sign if not math.isnan(finite(snap.get('futuresQueueImbalance'))) else None,'futuresTaker1sTowardRecovery':finite(snap.get('futuresTakerImbalance1s'))*sign if not math.isnan(finite(snap.get('futuresTakerImbalance1s'))) else None,'asymmetryAgeMs':now-asym_since if asym_since is not None else 0.0,'observationDelayMs':0.0,'hasPriorObservation':0.0,'elapsedSincePriorMs':0.0,'recoveryStatusNew':0.0,'recoveryStatusPartial':0.0}
    def classify(f):
        x=np.asarray([[finite(f.get(k)) for k in ROBUST['features']]],float);votes=0
        for k in ('deltaTargetErrorArea5s','deltaTargetErrorArea10s','deltaTargetErrorArea20s'):
            y=float(ROBUST['models'][k].predict(x)[0]);m=.5*float(ROBUST['trainResidualStd'][k]);votes+=int(y<-m)
        edge=f.get('lockedPairEdgePerShare');spr=f.get('recoverySpreadTicks');ok=votes==3 and edge is not None and math.isfinite(float(edge)) and float(edge)>=-.02 and spr is not None and math.isfinite(float(spr)) and float(spr)<=1.5
        return 'REPLACE_ROUTE' if ok else 'WAIT_FOR_CLARITY'
    def lifecycle(now,snap):
        nonlocal asym_since,episode_action,cp_idx
        err=anet()-tnet();asym=desired['UP']>EPS and desired['DOWN']>EPS and (actual.maker_up+actual.maker_down)>EPS and abs(err)>=CHUNK-EPS
        if not asym:asym_since=None;episode_action=None;cp_idx=0;return
        if asym_since is None:asym_since=now;episode_action=None;cp_idx=0
        if episode_action is not None:return
        cps=(0,1000,2000,3000,5000,8000);age=now-asym_since
        if cp_idx>=len(cps):episode_action='RETURN_TO_CONTROLLER';life.append({'atMs':now,'action':'RETURN_TO_CONTROLLER','trackingError':err});return
        if age<cps[cp_idx]:return
        side='DOWN' if err>0 else 'UP'
        if route_block[side]:return
        f=features(side,now,snap)
        if f is None:return
        a=classify(f);life.append({'atMs':now,'side':side,'action':a,'trackingError':err,'delayMs':cps[cp_idx]})
        if a=='WAIT_FOR_CLARITY':cp_idx+=1;return
        episode_action=a
        if a=='REPLACE_ROUTE':
            route_block[side]=True;num=active[side];qty=min(CHUNK,abs(err));bf=mod.outcome_book(book.book,None)
            if num is None:
                if bf:pending[side]={'state':'TAKER_SENT','side':side,'takerOrderNum':submit_taker(side,qty,float(bf['up_ask'] if side=='UP' else bf['down_ask']),now,'PAIR_COMPLETION_REPLACE'),'triggerAtMs':now}
            else:
                pending[side]={'state':'CANCEL_REQUESTED','side':side,'childOrderNum':num,'triggerAtMs':now};os=ex.order_snapshot(venue,num);cur=venue.orders(0).get(num)
                if str(os.get('status') or '') in {'NEW','PARTIALLY_FILLED'} and cur is not None and bool(cur.cancellable):
                    try:venue.cancel(0,num,False)
                    except Exception:pass
    def service(now):
        for side,p in list(pending.items()):
            if p['state']=='CANCEL_REQUESTED':
                num=p['childOrderNum'];st=str(ex.order_snapshot(venue,num).get('status') or '')
                if st not in TERMINAL:continue
                err=anet()-tnet();needed=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP');qty=min(CHUNK,abs(err)) if needed else 0
                if qty>EPS:
                    bf=mod.outcome_book(book.book,None)
                    if bf:p.update({'state':'TAKER_SENT','takerOrderNum':submit_taker(side,qty,float(bf['up_ask'] if side=='UP' else bf['down_ask']),now,'PAIR_COMPLETION_REPLACE')})
                else:p['state']='DONE';route_block[side]=False
            elif p['state']=='TAKER_SENT':
                om=taker_meta[p['takerOrderNum']]
                if not om.get('terminal'):continue
                err=anet()-tnet();needed=(err>EPS and side=='DOWN') or (err<-EPS and side=='UP');rem=min(CHUNK,abs(err)) if needed else 0
                if rem<=EPS:p['state']='DONE'
                else:owners[side]={'state':'RETURNED_UNRESOLVED','remainingDeficit':rem,'targetRevision':int(target_rev[side])};p['state']='RETURNED_UNRESOLVED'
                route_block[side]=False
    try:
        for t in timeline:
            if t in snap_by:latest_snap=snap_by[t]
            if int(venue.current_timestamp//1_000_000)<t:ex.advance_to(venue,t)
            area(t);harvest(t);service(t);book.advance(mid,t)
            for x in bm.get(t,[]):desired[x['side']]+=x['shares'];target_rev[x['side']]+=1
            for x in btak.get(t,[]):submit_taker(x['side'],x['shares'],x['observedPrice'],t,'FROZEN_R2')
            lifecycle(t,latest_snap)
            err=(actual.maker_up-actual.maker_down)-(desired['UP']-desired['DOWN']);rec='DOWN' if err>CHUNK-EPS else 'UP' if err<-CHUNK+EPS else None;order=(rec,('UP' if rec=='DOWN' else 'DOWN')) if rec else ('UP','DOWN')
            for side in order:
                if side:submit_maker(side,t)
        terminal=int(feed['lastReceivedMs'])
        if int(venue.current_timestamp//1_000_000)<terminal:ex.advance_to(venue,terminal)
        area(terminal);harvest(terminal);service(terminal)
        cu,cd=cside('UP'),cside('DOWN');payout=cu if win=='UP' else cd if win=='DOWN' else None;cost=actual.maker_up_cost+actual.maker_down_cost+actual.taker_up_cost+actual.taker_down_cost+taker_fees;pnl=float(payout-cost) if payout is not None else None
        return {'marketId':mid,'winner':win,'paperMakerIntents':len(mi),'paperTakerIntents':len(ti),'realizedPnl':pnl,'combinedUp':cu,'combinedDown':cd,'combinedFinalAbsNet':abs(cu-cd),'finalAbsTrackingError':abs((cu-cd)-tnet()),'targetErrorAreaShareSeconds':track_area,'combinedExposureAreaShareSeconds':exp_area,'makerFilledShares':actual.maker_up+actual.maker_down,'makerDesiredShares':desired['UP']+desired['DOWN'],'takerFilledShares':actual.taker_up+actual.taker_down,'takerFeesUsdt':taker_fees,'totalCostUsdt':cost,'winnerPayoutUsdt':payout,'actionCounts':{k:sum(x.get('action')==k for x in life) for k in ['WAIT_FOR_CLARITY','REPLACE_ROUTE','RETURN_TO_CONTROLLER']},'boundary':'Supplemental frozen-intent stress replay only. Strategy intent trajectory is frozen R2 paper tape because exact consumer/receipt archive did not exist for these historical markets. Execution uses candidate V1 maker skew + robust gate + HftBacktest Execution Tape V1. Not formal graduation evidence.'}
    finally:book.close();venue.close()
def main():
    a=argparse.ArgumentParser();a.add_argument('--market-ids',required=True);a.add_argument('--output',default='r2_execution_graduation_stress_v1.json');z=a.parse_args();rows=[]
    for i,mid in enumerate([int(x) for x in z.market_ids.split(',') if x.strip()],1):
        r=run_market(mid);rows.append(r);print(json.dumps({'progress':i,'marketId':mid,'pnl':r['realizedPnl'],'absNet':r['combinedFinalAbsNet'],'actions':r['actionCounts']},ensure_ascii=False),flush=True)
    p=[float(r['realizedPnl']) for r in rows if r['realizedPnl'] is not None];agg={'markets':len(rows),'totalRealizedPnl':sum(p),'wins':sum(x>EPS for x in p),'losses':sum(x<-EPS for x in p),'winRate':sum(x>EPS for x in p)/len(p) if p else None,'maxDrawdown':maxdd(p),'meanAbsNet':sum(float(r['combinedFinalAbsNet']) for r in rows)/len(rows) if rows else None,'meanAbsTrackingError':sum(float(r['finalAbsTrackingError']) for r in rows)/len(rows) if rows else None,'actionCounts':{k:sum(r['actionCounts'][k] for r in rows) for k in ['WAIT_FOR_CLARITY','REPLACE_ROUTE','RETURN_TO_CONTROLLER']}}
    out=OUT/z.output;out.write_text(json.dumps({'version':'R2_EXECUTION_GRADUATION_STRESS_V1','candidateFreeze':'data/research/execution_aware_fill_lifecycle_v0/r2_execution_graduation_candidate_freeze_v1.json','formalGraduationEvidence':False,'aggregate':agg,'rows':rows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'report':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
