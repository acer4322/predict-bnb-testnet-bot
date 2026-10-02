from __future__ import annotations
import importlib.util,json,sys,joblib
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_mpq_reserve_capped_tranche_v1.py'
spec=importlib.util.spec_from_file_location('r4_tranche',P);t=importlib.util.module_from_spec(spec);assert spec and spec.loader
sys.modules[spec.name]=t;spec.loader.exec_module(t)
TZ=ZoneInfo('Asia/Taipei');VERSION='R4_MPQ_RELATION_AWARE_TRANCHE_V1'

def replay(events,winner,policy,stress):
    state=(0.,0.,0.,0.);ctr={'maker':0,'weak':0};flags=tranches=cross_hard=surplus_tranche=0;supp=0.
    for z0 in events:
        z=dict(z0);pre=t.m.geom(*state);sh=t.stressed_sh(pre,z,z['sh'],ctr,stress);post_state,post=t.post_with_qty(state,z,sh);reserve=max(0.,pre['floor']-post['floor'])
        flag=sh>0 and pre['floor']>0 and reserve>1e-12 and post['edge']<0;q=sh
        if flag:
            flags+=1
            surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
            relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
            if policy=='HARD_MPQ':q=0.;supp+=sh
            elif policy=='RELATION_TRANCHE':
                if relation=='SURPLUS_SIDE':
                    q=t.safe_qty_to_zero_floor(state,z,sh)
                    if q<sh-1e-9:tranches+=1;surplus_tranche+=1;supp+=sh-q
                else:
                    q=0.;supp+=sh
                    if relation=='WEAK_SIDE_CROSS':cross_hard+=1
        state,cur=t.post_with_qty(state,z,q)
    g=t.m.geom(*state);up,down,cu,cd=state;payout=up if winner=='UP' else down if winner=='DOWN' else 0.
    return {**g,'pnl':payout-(cu+cd),'flags':flags,'tranches':tranches,'crossHard':cross_hard,'surplusTranche':surplus_tranche,'suppressedShares':supp}

def md(a):return median(a) if a else None

def main():
    art=joblib.load(t.m.MODEL);all_ids=[int(x) for x in art['testMarketIds']];events,winners=t.m.load_events(all_ids);results={}
    for stress in t.STRESSES:
        ids=[x for x in all_ids if x in events and winners.get(x) in {'UP','DOWN'} and t.has_flag(events[x],stress)]
        base={x:replay(events[x],winners[x],'BASELINE',stress) for x in ids};hard={x:replay(events[x],winners[x],'HARD_MPQ',stress) for x in ids};rel={x:replay(events[x],winners[x],'RELATION_TRANCHE',stress) for x in ids}
        hf=[hard[x]['floor']-base[x]['floor'] for x in ids];rf=[rel[x]['floor']-base[x]['floor'] for x in ids];hp=[hard[x]['pnl']-base[x]['pnl'] for x in ids];rp=[rel[x]['pnl']-base[x]['pnl'] for x in ids]
        mhf=md(hf);mrf=md(rf);mhp=md(hp);mrp=md(rp);ret=mrf/mhf if mhf and mhf>1e-9 else None;rec=(mrp-mhp)/abs(mhp) if mhp is not None and mhp< -1e-9 else None
        eligible=len(ids)>=10;passed=bool(eligible and mrf is not None and mrf>0 and ret is not None and ret>=.5 and (rec is None or rec>=.25))
        results[stress]={'activeMarkets':len(ids),'hardMedianDeltaFloor':mhf,'relationTrancheMedianDeltaFloor':mrf,'floorRetention':ret,'hardMedianDeltaPnl':mhp,'relationTrancheMedianDeltaPnl':mrp,'pnlRecoveryFraction':rec,'hardMedianDeltaCoverage':md([hard[x]['coverage']-base[x]['coverage'] for x in ids]),'relationTrancheMedianDeltaCoverage':md([rel[x]['coverage']-base[x]['coverage'] for x in ids]),'hardMedianDeltaAbsNet':md([hard[x]['absnet']-base[x]['absnet'] for x in ids]),'relationTrancheMedianDeltaAbsNet':md([rel[x]['absnet']-base[x]['absnet'] for x in ids]),'surplusTrancheEvents':sum(rel[x]['surplusTranche'] for x in ids),'crossHardEvents':sum(rel[x]['crossHard'] for x in ids),'medianSuppressedShares':md([rel[x]['suppressedShares'] for x in ids]),'pass':passed}
    normal=results['NONE'];stress_eligible=[v for k,v in results.items() if k!='NONE' and v['activeMarkets']>=10];overall=bool(normal['pass'] and len(stress_eligible)>=2 and sum(v['pass'] for v in stress_eligible)>=2)
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':'Frozen MPQ: WEAK_SIDE_CROSS/FLAT hard-suppressed; SURPLUS_SIDE candidate may execute only up to the exact floor=0 reserve boundary. No learned or tuned threshold.','results':results,'gate':{'required':'normal pass plus at least 2 eligible execution-stress variants pass the same floor-retention/PnL-recovery rule','pass':overall},'guards':{'floorBoundary':0.0,'noThresholdSweep':True,'winnerEvaluationOnly':True,'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'mpqFrozen':True}}
    out=t.m.OUT/f"r4_mpq_relation_aware_tranche_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'results':results,'keep':overall},ensure_ascii=False))
if __name__=='__main__':main()
