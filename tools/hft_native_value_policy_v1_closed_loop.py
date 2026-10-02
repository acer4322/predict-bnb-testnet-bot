from __future__ import annotations
import argparse, copy, json, math, sys
from pathlib import Path
from typing import Any
import joblib
import numpy as np
import pandas as pd

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import HftBookAdapter, load_public_snapshots, new_controller
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod

OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
ART=OUT/'hft_native_value_surface_v1_frozen.joblib'
EPS=1e-9; TERMINAL={'FILLED','REJECTED','EXPIRED','CANCELED'}; QTY=float(mod.SHARES); GRID=float(mod.GRID)

def finite(x):
    try:
        v=float(x); return v if math.isfinite(v) else np.nan
    except Exception:return np.nan

def max_drawdown(xs):
    peak=cur=dd=0.0
    for x in xs:
        cur+=x;peak=max(peak,cur);dd=max(dd,peak-cur)
    return dd

def post_floor_delta(state:dict[str,Any],side:str,price:float,shares:float=QTY)->float:
    q=float(shares);p=float(price);net=float(state.get('combined_net') or 0.0);floor=float(state.get('worst_case_floor') or 0.0);best=float(state.get('best_case_pnl') or floor)
    pup,pdn=(best,floor) if net>=0 else (floor,best)
    if side=='UP':pup+=q*(1-p);pdn-=q*p
    else:pdn+=q*(1-p);pup-=q*p
    return min(pup,pdn)-floor

def run_market(mid:int, mode:str)->dict[str,Any]:
    snaps=load_public_snapshots(mid)
    if not snaps: raise RuntimeError(f'no snapshots {mid}')
    a=HftBookAdapter(mid,1092,273,'risk','mid'); c=new_controller(a); actual=mod.Inventory(); win=winners([mid]).get(mid)
    art=joblib.load(ART); feats=list(art['features']); fill_model=art['fill_model']; mark_model=art['markout_model']
    active={'UP':None,'DOWN':None}; meta={}; taker_meta={}; proposals=[]; actions=[]; maker_fills=[]; taker_fills=[]; fill_log=[]; taker_fees=0.0
    orig_add=c._add_order; orig_taker=c._record_taker; last_area=None; exposure_area=0.0

    def area(now):
        nonlocal last_area,exposure_area
        if last_area is not None and now>last_area:
            n=(actual.maker_up+actual.taker_up)-(actual.maker_down+actual.taker_down); exposure_area+=abs(n)*(now-last_area)/1000.0
        last_area=now
    def harvest(now):
        nonlocal taker_fees
        for side in ('UP','DOWN'):
            num=active[side]
            if num is None:continue
            om=meta[int(num)]; s=ex.order_snapshot(a.bt,int(num));cum=float(s.get('cumExecQty') or 0);old=float(om.get('prevCum') or 0)
            if cum>old+EPS:
                q=cum-old;native=s.get('execPrice');px=float(om['price'])
                if native is not None and math.isfinite(float(native)):px=float(native) if side=='UP' else 1-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000);actual.apply({'event_ms':fm,'role':'MAKER','side':side,'price':px,'shares':q});om['prevCum']=cum;maker_fills.append({'atMs':fm,'side':side,'price':px,'shares':q,'orderNum':num});fill_log.append({'eventMs':fm,'role':'MAKER','side':side,'shares':q})
            if str(s.get('status') or 'NONE') in TERMINAL:active[side]=None
        for num,om in taker_meta.items():
            if om.get('terminal'):continue
            s=ex.order_snapshot(a.bt,int(num));cum=float(s.get('cumExecQty') or 0);old=float(om.get('prevCum') or 0)
            if cum>old+EPS:
                q=cum-old;native=s.get('execPrice');px=float(om['price'])
                if native is not None and math.isfinite(float(native)):px=float(native) if om['side']=='UP' else 1-float(native)
                fm=int((s.get('exchangeTs') or now*1_000_000)//1_000_000);actual.apply({'event_ms':fm,'role':'TAKER','side':om['side'],'price':px,'shares':q});fee=mod.taker_fee(q,px,mod.FEE_BPS);taker_fees+=fee;om['prevCum']=cum;taker_fills.append({'atMs':fm,'side':om['side'],'price':px,'shares':q,'fee':fee})
            if str(s.get('status') or 'NONE') in TERMINAL:om['terminal']=str(s.get('status'))

    def state_for(side:str,action_offset:int,action_price:float,now:int,snapshot:dict[str,Any]):
        bf=mod.outcome_book(c.book.book,None)
        if not bf:return None
        bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']);ask=float(bf['up_ask'] if side=='UP' else bf['down_ask'])
        vals={'side_is_up':float(side=='UP'),'action_offset':float(action_offset),'action_price':float(action_price),'current_bid':bid,'current_ask':ask,'current_spread_ticks':(ask-bid)/GRID,'active_opp_count':float(active['DOWN' if side=='UP' else 'UP'] is not None)}
        try:
            p=actual.features(now);p.pop('_combined_net',None);vals.update(p)
        except:pass
        mf=[e for e in fill_log if e['role']=='MAKER' and int(e['eventMs'])<=now]
        vals['last_maker_age_ms']=(now-max(int(e['eventMs']) for e in mf)) if mf else np.nan
        for sd,key in (('UP','last_maker_up_age_ms'),('DOWN','last_maker_down_age_ms')):
            z=[int(e['eventMs']) for e in mf if e['side']==sd];vals[key]=(now-max(z)) if z else np.nan
        for sec in (1,5,10):
            z=[e for e in mf if int(e['eventMs'])>=now-sec*1000];vals[f'maker_fills_{sec}s']=float(len(z));vals[f'maker_shares_{sec}s']=float(sum(float(e['shares']) for e in z))
        return vals

    def choose(side:str,now:int,snapshot:dict[str,Any]):
        bf=mod.outcome_book(c.book.book,None)
        if not bf:return ('WAIT',None,None,None)
        bid=float(bf['up_bid'] if side=='UP' else bf['down_bid'])
        candidates=[]
        for off in (0,1,2):
            px=round(bid-off*GRID,2)
            if px<float(mod.MIN_PRICE) or px>0.99:continue
            vals=state_for(side,off,px,now,snapshot)
            if vals is None:continue
            x=pd.DataFrame([{f:finite(vals.get(f)) for f in feats}],columns=feats);pf=float(fill_model.predict_proba(x)[0,1]);pm=float(mark_model.predict(x)[0]);df=post_floor_delta(vals,side,px,QTY);score=pf*(pm+df);candidates.append((score,off,px,pf,pm,df))
        if not candidates:return ('WAIT',None,None,None)
        best=max(candidates,key=lambda q:q[0])
        if mode=='BASE_OFFSET1':
            b=[x for x in candidates if x[1]==1]
            if not b:b=[min(candidates,key=lambda x:abs(x[1]-1))]
            x=b[0];return (x[1],x[2],x[0],{'pFill':x[3],'predMarkout':x[4],'deltaFloor':x[5]})
        if best[0]<=0:return ('WAIT',None,best[0],{'pFill':best[3],'predMarkout':best[4],'deltaFloor':best[5]})
        return (best[1],best[2],best[0],{'pFill':best[3],'predMarkout':best[4],'deltaFloor':best[5]})

    def submit(side,px,now,proposal):
        num=int(a.next_num);a.next_num+=1;rc=ex.submit_native(a.bt,num,side,float(px),QTY);meta[num]={'side':side,'price':float(px),'qty':QTY,'prevCum':0.0,'submittedAtMs':now,'proposalDecisionId':proposal.get('decisionId'),'submitRc':int(rc)}
        if rc==0:active[side]=num
        return num,rc

    def add_wrap(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack=True,bypass_guard=False):
        # Preserve frozen R2 paper trajectory internally, but treat its Maker add only as a proposal for HFT policy.
        before=set(c.orders);made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        if not made:return False
        new=list(set(c.orders)-before);paper_price=float(c.orders[new[0]].price) if new else None;prop={'atMs':int(now),'side':side,'decisionId':decision_id,'reason':reason,'p':float(p),'paperPrice':paper_price};proposals.append(prop)
        if active[side] is not None:
            actions.append({**prop,'action':'KEEP_EXISTING','score':None});return made
        act,px,score,parts=choose(side,int(now),snapshot)
        if act=='WAIT':actions.append({**prop,'action':'WAIT','score':score,'parts':parts});return made
        num,rc=submit(side,px,int(now),prop);actions.append({**prop,'action':f'OFFSET_{act}','price':px,'score':score,'parts':parts,'orderNum':num,'submitRc':rc});return made

    def taker_wrap(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect):
        # Preserve paper brain state and execute Taker proposal unchanged in both A/B arms.
        orig_taker(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect)
        maxp=min(.99,float(price)+.02);num=int(a.next_num);a.next_num+=1;native_side,native_px=ex.native_order(side,maxp)
        rc=int(a.bt.submit_buy_order(0,num,native_px,QTY,ex.hbt.GTC,ex.LIMIT,False)) if native_side=='BUY' else int(a.bt.submit_sell_order(0,num,native_px,QTY,ex.hbt.GTC,ex.LIMIT,False));taker_meta[num]={'side':side,'price':float(price),'prevCum':0.0,'submitRc':rc,'terminal':None}

    c._add_order=add_wrap;c._record_taker=taker_wrap
    try:
        for s in snaps:
            t=int(s['sampledAtMs'])
            if int(a.bt.current_timestamp//1_000_000)<t:ex.advance_to(a.bt,t)
            area(t);harvest(t);c._step(dict(s))
        terminal=int(a.meta['lastReceivedMs'])
        if int(a.bt.current_timestamp//1_000_000)<terminal:ex.advance_to(a.bt,terminal)
        area(terminal);harvest(terminal)
    finally:a.close()
    up=actual.maker_up+actual.taker_up;dn=actual.maker_down+actual.taker_down;payout=up if win=='UP' else dn if win=='DOWN' else None;cost=actual.maker_up_cost+actual.maker_down_cost+actual.taker_up_cost+actual.taker_down_cost+taker_fees;pnl=float(payout-cost) if payout is not None else None
    gross=actual.maker_up+actual.maker_down;pc=2*min(actual.maker_up,actual.maker_down)/gross if gross>EPS else 1.0
    return {'marketId':mid,'mode':mode,'winner':win,'pnl':pnl,'proposals':len(proposals),'actionsCount':len(actions),'waits':sum(x['action']=='WAIT' for x in actions),'keeps':sum(x['action']=='KEEP_EXISTING' for x in actions),'makerSubmits':sum(x['action'].startswith('OFFSET_') for x in actions),'makerFills':len(maker_fills),'makerFilledShares':gross,'makerFinalAbsNet':abs(actual.maker_up-actual.maker_down),'makerPairedCoverage':pc,'takerFills':len(taker_fills),'combinedFinalAbsNet':abs(up-dn),'combinedExposureAreaShareSeconds':exposure_area,'actions':actions,'makerFillEvents':maker_fills}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--market-ids',required=True);ap.add_argument('--mode',choices=['BASE_OFFSET1','VALUE_V1'],required=True);ap.add_argument('--output',required=True);a=ap.parse_args();mids=[int(x) for x in a.market_ids.split(',') if x.strip()];rows=[]
    for i,m in enumerate(mids,1):
        r=run_market(m,a.mode);rows.append(r);print(json.dumps({'progress':i,'marketId':m,'mode':a.mode,'pnl':r['pnl'],'proposals':r['proposals'],'submits':r['makerSubmits'],'waits':r['waits'],'fills':r['makerFills'],'absNet':r['combinedFinalAbsNet']},ensure_ascii=False),flush=True)
    pn=[float(r['pnl']) for r in rows if r['pnl'] is not None];agg={'markets':len(rows),'pnl':sum(pn),'positive':sum(x>EPS for x in pn),'winRate':sum(x>EPS for x in pn)/len(pn) if pn else None,'maxDrawdown':max_drawdown(pn),'proposals':sum(r['proposals'] for r in rows),'makerSubmits':sum(r['makerSubmits'] for r in rows),'waits':sum(r['waits'] for r in rows),'makerFills':sum(r['makerFills'] for r in rows),'makerFilledShares':sum(r['makerFilledShares'] for r in rows),'meanMakerAbsNet':sum(r['makerFinalAbsNet'] for r in rows)/len(rows),'meanCombinedAbsNet':sum(r['combinedFinalAbsNet'] for r in rows)/len(rows),'meanPairedCoverage':sum(r['makerPairedCoverage'] for r in rows)/len(rows),'combinedExposureArea':sum(r['combinedExposureAreaShareSeconds'] for r in rows)}
    out=OUT/a.output;out.write_text(json.dumps({'version':'HFT_NATIVE_VALUE_POLICY_V1_CLOSED_LOOP','researchOnly':True,'dreamFillUsedForPnl':False,'targetRuntimeInput':False,'mode':a.mode,'aggregate':agg,'rows':rows},ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8');print(json.dumps({'ok':True,'output':str(out),'aggregate':agg},ensure_ascii=False))
if __name__=='__main__':main()
