from __future__ import annotations
import importlib.util,json,sys,joblib
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_mpq_deliberate_side_override_ab_v1.py'
spec=importlib.util.spec_from_file_location('r4_ab_tranche',P);m=importlib.util.module_from_spec(spec);assert spec and spec.loader
sys.modules[spec.name]=m;spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei');VERSION='R4_MPQ_RESERVE_CAPPED_TRANCHE_V1'
STRESSES=['NONE','WEAK_DROP_ALTERNATE','WEAK_PARTIAL_HALF_ALTERNATE','MAKER_DROP_EVERY5']

def stressed_sh(pre,z,raw_sh,counters,stress):
    sh=float(raw_sh);role=z['role'];side=z['side'];weak='UP' if pre['up']<pre['down'] else 'DOWN' if pre['down']<pre['up'] else None
    if role=='MAKER':
        counters['maker']+=1
        if weak and side==weak:
            counters['weak']+=1
            if stress=='WEAK_DROP_ALTERNATE' and counters['weak']%2==0:sh=0.0
            elif stress=='WEAK_PARTIAL_HALF_ALTERNATE' and counters['weak']%2==0:sh*=0.5
        if stress=='MAKER_DROP_EVERY5' and counters['maker']%5==0:sh=0.0
    return sh

def post_with_qty(state,z,q):
    up,down,cu,cd=state;px=float(z['px']);side=z['side'];q=max(0.0,float(q))
    if side=='UP':up+=q;cu+=q*px
    else:down+=q;cd+=q*px
    return (up,down,cu,cd),m.geom(up,down,cu,cd)

def safe_qty_to_zero_floor(state,z,full_q):
    full_q=max(0.0,float(full_q));pre=m.geom(*state)
    if full_q<=0:return 0.0
    _,gfull=post_with_qty(state,z,full_q)
    if gfull['floor']>=0:return full_q
    lo,hi=0.0,full_q
    for _ in range(64):
        mid=(lo+hi)/2.0;_,gm=post_with_qty(state,z,mid)
        if gm['floor']>=0:lo=mid
        else:hi=mid
    return lo

def has_flag(events,stress):
    state=(0.,0.,0.,0.);ctr={'maker':0,'weak':0}
    for z0 in events:
        z=dict(z0);pre=m.geom(*state);sh=stressed_sh(pre,z,z['sh'],ctr,stress);post_state,post=post_with_qty(state,z,sh);reserve=max(0.,pre['floor']-post['floor'])
        if sh>0 and pre['floor']>0 and reserve>1e-12 and post['edge']<0:return True
        state=post_state
    return False

def replay(events,winner,policy,stress):
    state=(0.,0.,0.,0.);ctr={'maker':0,'weak':0};flags=0;tranches=0;supp=0.;executed=0.;requested=0.;min_post_floor=1e99
    for z0 in events:
        z=dict(z0);pre=m.geom(*state);sh=stressed_sh(pre,z,z['sh'],ctr,stress);requested+=sh
        post_state,post=post_with_qty(state,z,sh);reserve=max(0.,pre['floor']-post['floor']);flag=sh>0 and pre['floor']>0 and reserve>1e-12 and post['edge']<0
        q=sh
        if flag:
            flags+=1
            if policy=='HARD_MPQ':q=0.0;supp+=sh
            elif policy=='TRANCHE':
                q=safe_qty_to_zero_floor(state,z,sh)
                if q<sh-1e-9:tranches+=1;supp+=sh-q
        state,cur=post_with_qty(state,z,q);executed+=q;min_post_floor=min(min_post_floor,cur['floor'])
    g=m.geom(*state);up,down,cu,cd=state;payout=up if winner=='UP' else down if winner=='DOWN' else 0.
    return {**g,'pnl':payout-(cu+cd),'flags':flags,'tranches':tranches,'suppressedShares':supp,'executedShares':executed,'requestedShares':requested,'minPostFloor':min_post_floor if min_post_floor<1e98 else 0.0}

def md(a):return median(a) if a else None

def main():
    art=joblib.load(m.MODEL);all_ids=[int(x) for x in art['testMarketIds']];events,winners=m.load_events(all_ids);results={}
    for stress in STRESSES:
        ids=[x for x in all_ids if x in events and winners.get(x) in {'UP','DOWN'} and has_flag(events[x],stress)]
        base={x:replay(events[x],winners[x],'BASELINE',stress) for x in ids};hard={x:replay(events[x],winners[x],'HARD_MPQ',stress) for x in ids};tr={x:replay(events[x],winners[x],'TRANCHE',stress) for x in ids}
        hf=[hard[x]['floor']-base[x]['floor'] for x in ids];tf=[tr[x]['floor']-base[x]['floor'] for x in ids];hp=[hard[x]['pnl']-base[x]['pnl'] for x in ids];tp=[tr[x]['pnl']-base[x]['pnl'] for x in ids]
        mhf=md(hf);mtf=md(tf);mhp=md(hp);mtp=md(tp);ret=mtf/mhf if mhf and mhf>1e-9 else None;rec=(mtp-mhp)/abs(mhp) if mhp is not None and mhp< -1e-9 else None
        eligible=len(ids)>=10;passed=bool(eligible and mtf is not None and mtf>0 and ret is not None and ret>=.5 and (rec is None or rec>=.25))
        results[stress]={'activeMarkets':len(ids),'hardMedianDeltaFloor':mhf,'trancheMedianDeltaFloor':mtf,'floorRetention':ret,'hardMedianDeltaPnl':mhp,'trancheMedianDeltaPnl':mtp,'pnlRecoveryFraction':rec,'hardMedianDeltaCoverage':md([hard[x]['coverage']-base[x]['coverage'] for x in ids]),'trancheMedianDeltaCoverage':md([tr[x]['coverage']-base[x]['coverage'] for x in ids]),'hardMedianDeltaAbsNet':md([hard[x]['absnet']-base[x]['absnet'] for x in ids]),'trancheMedianDeltaAbsNet':md([tr[x]['absnet']-base[x]['absnet'] for x in ids]),'trancheEvents':sum(tr[x]['tranches'] for x in ids),'medianSuppressedSharesHard':md([hard[x]['suppressedShares'] for x in ids]),'medianSuppressedSharesTranche':md([tr[x]['suppressedShares'] for x in ids]),'pass':passed}
    normal=results['NONE'];stress_eligible=[v for k,v in results.items() if k!='NONE' and v['activeMarkets']>=10];overall=bool(normal['pass'] and len(stress_eligible)>=2 and sum(v['pass'] for v in stress_eligible)>=2)
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':'When frozen MPQ flags a fill, execute only the maximum candidate quantity whose immediate post-fill worst-case floor remains >=0; hard MPQ suppresses the whole fill. Zero is the fixed economic boundary; no tuned fraction.','results':results,'gate':{'required':'normal pass plus at least 2 eligible execution-stress variants pass floor-retention>=50% and PnL-recovery>=25% when hard MPQ has a median PnL loss','pass':overall},'guards':{'zeroBoundaryTuned':False,'floorBoundary':0.0,'noThresholdSweep':True,'winnerEvaluationOnly':True,'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'mpqFrozen':True}}
    out=m.OUT/f"r4_mpq_reserve_capped_tranche_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'results':results,'keep':overall},ensure_ascii=False))

if __name__=='__main__':main()
