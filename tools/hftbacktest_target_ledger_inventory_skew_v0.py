from __future__ import annotations

import json, math, sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.hftbacktest_r2_execution_school_v0 import load_public_snapshots, load_reference_paper
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from src.predict_bot import unified_controller_paper_v2 as mod

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
OUT=D/'target_ledger_inventory_skew_v0_report.json'
EPS=1e-9; CHUNK=float(mod.SHARES)
MIDS=[1520549,1521630,1521634,1521898,1522206,1522236,1522287,1522364,1522567,1523086,1524387,1524491,1524504,1524659]
CONFIGS=['SYM_OFFSET1','TRACK_SKEW_0_2','TRACK_SKEW_0_3','TRACK_SKEW_SPREAD_SAFE','TRACK_SKEW_0_3_SIZE_HALF','TRACK_SKEW_0_3_RECOVERY_ONLY']


def offset_for(cfg:str,side:str,error:float,bf:dict[str,float])->int:
    if cfg=='SYM_OFFSET1' or abs(error)<CHUNK-EPS:return 1
    recovery='DOWN' if error>0 else 'UP'
    if cfg=='TRACK_SKEW_0_2':return 0 if side==recovery else 2
    if cfg in {'TRACK_SKEW_0_3','TRACK_SKEW_0_3_SIZE_HALF','TRACK_SKEW_0_3_RECOVERY_ONLY'}:return 0 if side==recovery else 3
    # Spread-safe: preserve one-tick queue depth on the recovery side when spread is already wide.
    bid=float(bf['up_bid'] if side=='UP' else bf['down_bid']); ask=float(bf['up_ask'] if side=='UP' else bf['down_ask'])
    spread=(ask-bid)/mod.GRID
    if side==recovery:return 0 if spread<=1.5 else 1
    return 3


def quote(book:dict[str,dict[float,float]],side:str,opp_price:float|None,offset:int)->float|None:
    bf=mod.outcome_book(book,None)
    if not bf:return None
    bid=float(bf['up_bid'] if side=='UP' else bf['down_bid'])
    tick=int(math.floor((bid+1e-9)/mod.GRID))-max(0,int(offset))
    tick=max(int(round(mod.MIN_PRICE/mod.GRID)),tick); px=round(tick*mod.GRID,2)
    if opp_price is not None:
        while px+float(opp_price)>mod.MAX_PAIR_PRICE_SUM+EPS:
            tick-=1
            if tick<int(round(mod.MIN_PRICE/mod.GRID)):return None
            px=round(tick*mod.GRID,2)
    return px


def run_market(mid:int,cfg:str)->dict[str,Any]:
    paper=load_reference_paper(mid)
    intents=sorted([{'atMs':int(o['placed_at_ms']),'side':str(o['side']).upper(),'shares':float(o.get('shares') or CHUNK)} for o in paper['orders']],key=lambda x:x['atMs'])
    snaps=load_public_snapshots(mid); timeline=sorted(set([int(s['sampledAtMs']) for s in snaps]+[x['atMs'] for x in intents]))
    by=defaultdict(list)
    for x in intents:by[x['atMs']].append(x)
    events,_,meta=tape_v1.build_archive_events(mid,trade_offset='mid')
    bt=ex.new_bt(events,entry_latency_ms=1092,response_latency_ms=273,queue_model='risk'); ex.initialize_bt(bt)
    book=mod.PublicBookTailer(mod.BOOK_DB); win=winners([mid]).get(mid)
    desired={'UP':0.0,'DOWN':0.0}; actual={'UP':0.0,'DOWN':0.0}; cost=0.0
    active={'UP':None,'DOWN':None}; mm={}; nxt=1; submits=rejects=0
    exposure=tracking_area=0.0; last_t=None; offset_counts=defaultdict(int)
    def harvest(now:int):
        nonlocal cost
        for side in ('UP','DOWN'):
            num=active[side]
            if num is None:continue
            om=mm[num]; s=ex.order_snapshot(bt,num); cum=float(s.get('cumExecQty') or 0.0); old=float(om.get('prevCum') or 0.0)
            if cum>old+EPS:
                dq=cum-old; native=s.get('execPrice'); px=float(om['price'])
                if native is not None and math.isfinite(float(native)):px=float(native) if side=='UP' else 1.0-float(native)
                actual[side]+=dq; cost+=dq*px; om['prevCum']=cum
            if s.get('status') in {'FILLED','REJECTED','EXPIRED','CANCELED'}:active[side]=None
    try:
        for t in timeline:
            if int(bt.current_timestamp//1_000_000)<=t:ex.advance_to(bt,t)
            harvest(t)
            if last_t is not None:
                dt=max(0,t-last_t)/1000.0
                an=actual['UP']-actual['DOWN']; tn=desired['UP']-desired['DOWN']
                exposure+=abs(an)*dt; tracking_area+=abs(an-tn)*dt
            last_t=t; book.advance(mid,t)
            for x in by.get(t,[]):desired[x['side']]+=x['shares']
            an=actual['UP']-actual['DOWN']; tn=desired['UP']-desired['DOWN']; err=an-tn
            bf=mod.outcome_book(book.book,None)
            if not bf:continue
            # Deficit-first submission order: when tracking error exists, service the recovery side first.
            recovery='DOWN' if err>CHUNK-EPS else 'UP' if err<-CHUNK+EPS else None
            order=(recovery, 'UP' if recovery=='DOWN' else 'DOWN') if recovery else ('UP','DOWN')
            for side in order:
                if side is None or active[side] is not None:continue
                deficit=desired[side]-actual[side]
                if deficit<=EPS:continue
                opp='DOWN' if side=='UP' else 'UP'; opp_px=float(mm[int(active[opp])]['price']) if active[opp] is not None else None
                off=offset_for(cfg,side,err,bf); px=quote(book.book,side,opp_px,off)
                if px is None:continue
                qty=min(CHUNK,deficit)
                if recovery is not None and side!=recovery:
                    if cfg=='TRACK_SKEW_0_3_SIZE_HALF': qty=min(qty,CHUNK*0.5)
                    elif cfg=='TRACK_SKEW_0_3_RECOVERY_ONLY': continue
                num=nxt; nxt+=1; rc=ex.submit_native(bt,num,side,px,qty); submits+=1; rejects+=int(rc!=0); offset_counts[str(off)]+=1
                mm[num]={'side':side,'price':px,'qty':qty,'prevCum':0.0,'submittedAtMs':t,'submitRc':int(rc)}; active[side]=num
        terminal=int(meta['lastReceivedMs'])
        if int(bt.current_timestamp//1_000_000)<=terminal:ex.advance_to(bt,terminal)
        harvest(terminal)
        if last_t is not None:
            dt=max(0,terminal-last_t)/1000.0; an=actual['UP']-actual['DOWN']; tn=desired['UP']-desired['DOWN']; exposure+=abs(an)*dt; tracking_area+=abs(an-tn)*dt
        payout=actual[win] if win in {'UP','DOWN'} else None; pnl=float(payout-cost) if payout is not None else None
        return {'marketId':mid,'winner':win,'desiredUp':desired['UP'],'desiredDown':desired['DOWN'],'actualUp':actual['UP'],'actualDown':actual['DOWN'],'desiredShares':desired['UP']+desired['DOWN'],'filledShares':actual['UP']+actual['DOWN'],'realizationRate':(actual['UP']+actual['DOWN'])/(desired['UP']+desired['DOWN']) if desired['UP']+desired['DOWN']>EPS else None,'finalAbsNet':abs(actual['UP']-actual['DOWN']),'finalTrackingError':(actual['UP']-actual['DOWN'])-(desired['UP']-desired['DOWN']),'targetErrorAreaShareSeconds':tracking_area,'exposureAreaShareSeconds':exposure,'submits':submits,'rejects':rejects,'offsetCounts':dict(offset_counts),'makerOnlyPnl':pnl}
    finally:book.close(); bt.close()


def aggregate(rows):
    pnls=[float(r['makerOnlyPnl']) for r in rows if r['makerOnlyPnl'] is not None]; des=sum(r['desiredShares'] for r in rows); fill=sum(r['filledShares'] for r in rows)
    return {'markets':len(rows),'totalMakerOnlyPnl':sum(pnls),'wins':sum(x>EPS for x in pnls),'losses':sum(x<-EPS for x in pnls),'winRate':sum(x>EPS for x in pnls)/len(pnls),'realizationRate':fill/des,'meanFinalAbsNet':sum(r['finalAbsNet'] for r in rows)/len(rows),'maxFinalAbsNet':max(r['finalAbsNet'] for r in rows),'meanAbsFinalTrackingError':sum(abs(r['finalTrackingError']) for r in rows)/len(rows),'totalTargetErrorAreaShareSeconds':sum(r['targetErrorAreaShareSeconds'] for r in rows),'totalExposureAreaShareSeconds':sum(r['exposureAreaShareSeconds'] for r in rows),'submits':sum(r['submits'] for r in rows),'rejects':sum(r['rejects'] for r in rows)}


def main()->int:
    rep={'version':'TARGET_LEDGER_INVENTORY_SKEW_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'cohort':'FORWARD23_REFERENCE_ZERO_TAKER14','dreamFillUsedForPnl':False,'configs':[],'semantics':'Avellaneda-Stoikov-inspired execution skew translated to binary target tracking: when actual net deviates from desired target net by >=1 chunk, recovery side gets shallower quote and worsening side gets deeper quote. Strict-past desired/actual only; no winner/Target future.','guardrails':['Fixed four configs chosen before results; no PnL sweep.','Maker-only shadow test; not formal graduation.','No cancel/reprice rule added; one active child per side remains.']}
    for cfg in CONFIGS:
        rows=[]
        for i,m in enumerate(MIDS,1):
            r=run_market(m,cfg); rows.append(r); print(json.dumps({'config':cfg,'progress':i,'marketId':m,'pnl':r['makerOnlyPnl'],'absNet':r['finalAbsNet'],'trackErr':r['finalTrackingError'],'offsets':r['offsetCounts']},ensure_ascii=False),flush=True)
        rep['configs'].append({'name':cfg,'aggregate':aggregate(rows),'rows':rows})
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(OUT),'configs':[{'name':x['name'],**x['aggregate']} for x in rep['configs']]},ensure_ascii=False,allow_nan=True))
    return 0
if __name__=='__main__':raise SystemExit(main())
