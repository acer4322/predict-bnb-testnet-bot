from __future__ import annotations
import importlib.util,json,sys,joblib
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_mpq_deliberate_side_override_ab_v1.py'
spec=importlib.util.spec_from_file_location('r4_ab_sur',P);m=importlib.util.module_from_spec(spec);assert spec and spec.loader
sys.modules[spec.name]=m;spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei');VERSION='R4_MPQ_SURPLUS_DELIBERATE_OVERRIDE_V1'
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

def has_flag(events,stress):
    up=down=cu=cd=0.;ctr={'maker':0,'weak':0}
    for z0 in events:
        z=dict(z0);pre=m.geom(up,down,cu,cd);sh=stressed_sh(pre,z,z['sh'],ctr,stress);px=float(z['px']);side=z['side']
        pu,pd,pcu,pcd=up,down,cu,cd
        if sh>0:
            if side=='UP':pu+=sh;pcu+=sh*px
            else:pd+=sh;pcd+=sh*px
        post=m.geom(pu,pd,pcu,pcd);reserve=max(0.,pre['floor']-post['floor'])
        if sh>0 and pre['floor']>0 and reserve>1e-12 and post['edge']<0:return True
        up,down,cu,cd=pu,pd,pcu,pcd
    return False

def replay(mid,events,winner,pub,model,policy,stress):
    up=down=cu=cd=fees=0.;hist=[];ctr={'maker':0,'weak':0};flags=overrides=surplus_flags=cross_flags=missing=0
    for i,z0 in enumerate(events):
        z=dict(z0);pre=m.geom(up,down,cu,cd);sh=stressed_sh(pre,z,z['sh'],ctr,stress);z['sh']=sh;px=float(z['px']);side=z['side'];role=z['role']
        pu,pd,pcu,pcd=up,down,cu,cd
        if sh>0:
            if side=='UP':pu+=sh;pcu+=sh*px
            else:pd+=sh;pcd+=sh*px
        post=m.geom(pu,pd,pcu,pcd);reserve=max(0.,pre['floor']-post['floor']);flag=sh>0 and pre['floor']>0 and reserve>1e-12 and post['edge']<0
        suppress=False
        if flag:
            flags+=1
            surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
            relation='SURPLUS_SIDE' if side==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
            if relation=='SURPLUS_SIDE':surplus_flags+=1
            elif relation=='WEAK_SIDE_CROSS':cross_flags+=1
            if policy=='HARD_MPQ':suppress=True
            elif policy=='SURPLUS_SIDE_OVERRIDE':
                # Crossing/flat remains hard-protected. Only deliberate surplus expansion may override.
                if relation!='SURPLUS_SIDE':suppress=True
                else:
                    ps=m.pub_at(pub,mid,z['t'])
                    if ps is None:missing+=1;suppress=True
                    else:
                        X=m.feature_row((up,down,cu,cd,fees),hist,z,ps,i,len(events));pup=float(model.predict_proba(X)[0,1]);pred='UP' if pup>=.5 else 'DOWN'
                        if pred==side:overrides+=1;suppress=False
                        else:suppress=True
        if not suppress:
            pre_state=(up,down,cu,cd)
            up,down,cu,cd=pu,pd,pcu,pcd
            if role=='TAKER':fees+=sh*px*.02
            if sh>0:hist.append({'t':z['t'],'role':role,'side':side,'sh':sh,'px':px,'preState':pre_state})
    g=m.geom(up,down,cu,cd);payout=up if winner=='UP' else down if winner=='DOWN' else 0.
    return {**g,'pnl':payout-(cu+cd),'flags':flags,'overrides':overrides,'surplusFlags':surplus_flags,'crossFlags':cross_flags,'missing':missing}

def md(a):return median(a) if a else None

def main():
    art=joblib.load(m.MODEL);model=art['model'];all_ids=[int(x) for x in art['testMarketIds']];events,winners=m.load_events(all_ids)
    active_by={};union=set()
    for stress in STRESSES:
        ids=[x for x in all_ids if x in events and winners.get(x) in {'UP','DOWN'} and has_flag(events[x],stress)];active_by[stress]=ids;union.update(ids)
    pub=m.load_public(sorted(union));results={}
    for stress,ids in active_by.items():
        base={x:replay(x,events[x],winners[x],pub,model,'BASELINE',stress) for x in ids}
        hard={x:replay(x,events[x],winners[x],pub,model,'HARD_MPQ',stress) for x in ids}
        over={x:replay(x,events[x],winners[x],pub,model,'SURPLUS_SIDE_OVERRIDE',stress) for x in ids}
        hf=[hard[x]['floor']-base[x]['floor'] for x in ids];of=[over[x]['floor']-base[x]['floor'] for x in ids];hp=[hard[x]['pnl']-base[x]['pnl'] for x in ids];op=[over[x]['pnl']-base[x]['pnl'] for x in ids]
        mhf=md(hf);mof=md(of);mhp=md(hp);mop=md(op);ret=mof/mhf if mhf and mhf>1e-9 else None;rec=(mop-mhp)/abs(mhp) if mhp is not None and mhp< -1e-9 else None
        eligible=len(ids)>=10;passed=bool(eligible and mof is not None and mof>0 and ret is not None and ret>=.5 and (rec is None or rec>=.25))
        results[stress]={'activeMarkets':len(ids),'hardMedianDeltaFloor':mhf,'overrideMedianDeltaFloor':mof,'floorRetention':ret,'hardMedianDeltaPnl':mhp,'overrideMedianDeltaPnl':mop,'pnlRecoveryFraction':rec,'overrideEvents':sum(over[x]['overrides'] for x in ids),'surplusFlags':sum(over[x]['surplusFlags'] for x in ids),'crossFlags':sum(over[x]['crossFlags'] for x in ids),'pass':passed}
    normal=results['NONE'];stress_eligible=[v for k,v in results.items() if k!='NONE' and v['activeMarkets']>=10];overall=bool(normal['pass'] and len(stress_eligible)>=2 and sum(v['pass'] for v in stress_eligible)>=2)
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':'MPQ hard protection for WEAK_SIDE_CROSS/FLAT; only SURPLUS_SIDE may override when independent strict-past Deliberate Side agrees at natural 0.5 boundary.','sideModel':str(m.MODEL.relative_to(ROOT)).replace('\\','/'),'results':results,'gate':{'required':'normal pass plus at least 2 eligible execution-stress variants pass same floor-retention/PnL-recovery rule','pass':overall},'guards':{'noThresholdSweep':True,'sideBoundary':0.5,'winnerEvaluationOnly':True,'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'mpqFrozen':True}}
    out=m.OUT/f"r4_mpq_surplus_deliberate_override_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'results':results,'keep':overall},ensure_ascii=False))

if __name__=='__main__':main()
