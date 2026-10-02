from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUTDIR=ROOT/'data'/'research'/'r4_v0'/'hourly'
TZ=ZoneInfo('Asia/Taipei')
SEALED='2026-08-16'
VERSION='R4_EXECUTION_AWARE_RESERVE_BUDGET_V1'
# Frozen Echtgeld cohort is used only to define the execution-stress realization assumption.
WEAK_SIDE_REALIZATION=0.6811984126984127


def ro(p):
    c=sqlite3.connect(f'file:{p.as_posix()}?mode=ro',uri=True); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def f(x,d=0.0):
    try:
        y=float(x); return y if math.isfinite(y) else d
    except: return d

def geom(up,down,cu,cd):
    gross=up+down; paired=min(up,down); cost=cu+cd; floor=paired-cost
    au=cu/up if up>1e-9 else 0.; ad=cd/down if down>1e-9 else 0.
    edge=1-(au+ad) if up>1e-9 and down>1e-9 else 0.
    return {'gross':gross,'paired':paired,'cost':cost,'floor':floor,'edge':edge,'absnet':abs(up-down),'coverage':2*paired/gross if gross>1e-9 else 0.}

def load(n=1000):
    c=ro(DB); meta=[]
    for r in c.execute("select m.market_id,m.window_end_ms from target_markets m join target_market_results x on x.market_id=m.market_id where m.asset='BTC' and m.window_end_ms is not null and x.fill_count>0 order by m.window_end_ms desc limit ?",(n*2,)):
        dt=datetime.fromtimestamp(int(r['window_end_ms'])/1000,TZ).date().isoformat()
        if dt==SEALED: continue
        meta.append((int(r['market_id']),int(r['window_end_ms'])))
        if len(meta)>=n: break
    ids=[m for m,_ in meta]; q=','.join('?'*len(ids)); ev=defaultdict(list)
    for r in c.execute(f"select market_id,role,side,last_event_ms,average_price,shares from target_parent_orders where market_id in ({q}) and quote_type='BID'",ids):
        role=str(r['role'] or '').upper(); side=str(r['side'] or '').upper(); px=f(r['average_price'],-1); sh=f(r['shares'])
        if role in {'MAKER','TAKER'} and side in {'UP','DOWN'} and 0<=px<=1 and sh>0:
            ev[int(r['market_id'])].append({'t':int(r['last_event_ms']),'role':role,'side':side,'px':px,'sh':sh})
    c.close(); return sorted(meta,key=lambda z:z[1]),{m:sorted(v,key=lambda z:z['t']) for m,v in ev.items()}

def apply(up,down,cu,cd,side,px,sh):
    if side=='UP': up+=sh; cu+=sh*px
    else: down+=sh; cd+=sh*px
    return up,down,cu,cd

def stressed_post(pre_up,pre_down,pre_cu,pre_cd,z):
    # Execution mismatch model: if this fill is on the weak side, only the frozen-live realization fraction arrives.
    # If it is on the surplus side, the current fill realizes but the next balancing opportunity is assumed degraded;
    # budget therefore tests whether the candidate itself leaves a non-negative base under the observed weak-side impairment.
    pre=geom(pre_up,pre_down,pre_cu,pre_cd)
    weak='UP' if pre_up<pre_down else 'DOWN' if pre_down<pre_up else None
    sh=float(z['sh'])
    if weak and z['side']==weak:
        sh*=WEAK_SIDE_REALIZATION
    u,d,cu,cd=apply(pre_up,pre_down,pre_cu,pre_cd,z['side'],z['px'],sh)
    return geom(u,d,cu,cd)

def replay(events,gate=False,stress_path=False):
    up=down=cu=cd=0.; peak=0.; pos_ms=0; last_t=None; flags=0; suppressed=0.; reserve_spent_sum=0.
    for z in events:
        pre=geom(up,down,cu,cd)
        sh=float(z['sh'])
        if stress_path and pre['gross']>0:
            weak='UP' if up<down else 'DOWN' if down<up else None
            if weak and z['side']==weak: sh*=WEAK_SIDE_REALIZATION
        pu,pd,pcu,pcd=apply(up,down,cu,cd,z['side'],z['px'],sh)
        post=geom(pu,pd,pcu,pcd)
        reserve_spent=max(0.,pre['floor']-post['floor'])
        stress_cf=stressed_post(up,down,cu,cd,z) if pre['gross']>0 else post
        # Untuned semantic reserve rule: once a positive base exists, do not spend positive-floor reserve
        # on a candidate whose observed execution-impaired post-fill base would cross below zero.
        flag=pre['floor']>0 and reserve_spent>1e-12 and stress_cf['floor']<0
        if flag: flags+=1
        if gate and flag:
            suppressed+=sh
            post=pre
        else:
            up,down,cu,cd=pu,pd,pcu,pcd
            reserve_spent_sum+=reserve_spent
        if last_t is not None and pre['floor']>0: pos_ms+=max(0,z['t']-last_t)
        last_t=z['t']; peak=max(peak,post['floor'])
    g=geom(up,down,cu,cd)
    return {**g,'peakFloor':peak,'positiveDurationSec':pos_ms/1000.,'flags':flags,'suppressedShares':suppressed,'reserveSpent':reserve_spent_sum}

def metrics_for(meta,ev):
    rows=[]
    for mid,_ in meta:
        e=ev.get(mid,[])
        b=replay(e,False,False); g=replay(e,True,False); bs=replay(e,False,True); gs=replay(e,True,True)
        rows.append({'marketId':mid,'flags':g['flags'],'deltaFinalFloor':g['floor']-b['floor'],'deltaPositiveDurationSec':g['positiveDurationSec']-b['positiveDurationSec'],'deltaCoverage':g['coverage']-b['coverage'],'deltaAbsNet':g['absnet']-b['absnet'],'deltaReserveSpent':g['reserveSpent']-b['reserveSpent'],'stressDeltaFinalFloor':gs['floor']-bs['floor'],'stressDeltaPositiveDurationSec':gs['positiveDurationSec']-bs['positiveDurationSec'],'stressDeltaCoverage':gs['coverage']-bs['coverage'],'stressDeltaAbsNet':gs['absnet']-bs['absnet']})
    return rows

def med(k,rs):
    a=sorted(r[k] for r in rs); return a[len(a)//2] if a else None

def mean(k,rs): return sum(r[k] for r in rs)/len(rs) if rs else None

def main():
    meta,ev=load(1000); cut=int(len(meta)*.8); hold=meta[cut:]
    rows=metrics_for(hold,ev); active=[r for r in rows if r['flags']>0]
    m={
      'medianDeltaFinalFloorActive':med('deltaFinalFloor',active),'meanDeltaFinalFloorActive':mean('deltaFinalFloor',active),
      'marketsFinalFloorImproved':sum(r['deltaFinalFloor']>1e-9 for r in active),'marketsFinalFloorWorsened':sum(r['deltaFinalFloor']<-1e-9 for r in active),
      'medianDeltaPositiveDurationSec':med('deltaPositiveDurationSec',active),'medianDeltaCoverage':med('deltaCoverage',active),'meanDeltaCoverage':mean('deltaCoverage',active),
      'medianDeltaAbsNet':med('deltaAbsNet',active),'meanDeltaAbsNet':mean('deltaAbsNet',active),'medianDeltaReserveSpent':med('deltaReserveSpent',active),
      'stressMedianDeltaFinalFloor':med('stressDeltaFinalFloor',active),'stressMeanDeltaFinalFloor':mean('stressDeltaFinalFloor',active),
      'stressMedianDeltaPositiveDurationSec':med('stressDeltaPositiveDurationSec',active),'stressMedianDeltaCoverage':med('stressDeltaCoverage',active),'stressMedianDeltaAbsNet':med('stressDeltaAbsNet',active)
    }
    keep=bool(len(active)>=10 and (m['medianDeltaFinalFloorActive'] or 0)>0 and (m['medianDeltaPositiveDurationSec'] or 0)>=0 and (m['medianDeltaCoverage'] is not None and m['medianDeltaCoverage']>=-0.05) and (m['medianDeltaAbsNet'] is not None and m['medianDeltaAbsNet']<=5.0) and (m['stressMedianDeltaFinalFloor'] or 0)>0)
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':{'semanticRule':'pre_floor>0 AND reserve_spent_if_filled>0 AND execution_impaired_post_floor<0 => suppress candidate fill','thresholdTuned':False,'runtimeInputsStrictPast':True,'executionStressAssumption':{'weakSideRealization':WEAK_SIDE_REALIZATION,'source':'FROZEN_ECHTGELD_ENVIRONMENT_CALIBRATION_ONLY'}},'cohort':{'targetOrdinaryMarketsLoaded':len(meta),'chronologicalHoldoutMarkets':len(hold),'activeMarkets':len(active),'sealed20260816':True},'metrics':m,'keepGate':{'required':'>=10 active markets; median final-floor improvement >0; positive-duration not worse; median coverage loss >= -0.05; no material abs-net worsening; stress median final-floor improvement >0','pass':keep},'rows':rows,'guards':{'noNewEchtgeld':True,'noLiveR3Change':True,'no8781Change':True,'echtgeldUsedOnlyToDefineStress':True}}
    stamp=datetime.now(TZ).strftime('%Y%m%d_%H%M'); out=OUTDIR/f'r4_hourly_experiment_{stamp}_execution_aware_reserve_budget_v1.json'; out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'activeMarkets':len(active),'metrics':m,'keep':keep}))
if __name__=='__main__': main()
