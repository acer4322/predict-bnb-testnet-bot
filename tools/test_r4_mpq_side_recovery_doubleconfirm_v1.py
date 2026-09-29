from __future__ import annotations
import importlib.util,json,sys,joblib,math
from datetime import datetime
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'tools'/'test_r4_mpq_deliberate_side_override_ab_v1.py'
spec=importlib.util.spec_from_file_location('r4_ab_dc',P);m=importlib.util.module_from_spec(spec);assert spec and spec.loader
sys.modules[spec.name]=m;spec.loader.exec_module(m)
TZ=ZoneInfo('Asia/Taipei');VERSION='R4_MPQ_SIDE_RECOVERY_DOUBLECONFIRM_V1'
REC_PATH=ROOT/'data'/'research'/'r4_v0'/'r4_base_break_recovery_capacity_v2_r3_geometry.joblib'
STRESSES=['NONE','WEAK_DROP_ALTERNATE','WEAK_PARTIAL_HALF_ALTERNATE','MAKER_DROP_EVERY5']

def div(a,b): return float(a/b) if abs(float(b))>1e-12 else 0.0

def stressed_sh(pre,z,raw_sh,counters,stress):
    sh=float(raw_sh);role=z['role'];side=z['side'];weak='UP' if pre['up']<pre['down'] else 'DOWN' if pre['down']<pre['up'] else None
    if stress!='NONE' and role=='MAKER':
        counters['maker']+=1
        if weak and side==weak:
            counters['weak']+=1
            if stress=='WEAK_DROP_ALTERNATE' and counters['weak']%2==0: sh=0.0
            elif stress=='WEAK_PARTIAL_HALF_ALTERNATE' and counters['weak']%2==0: sh*=0.5
        if stress=='MAKER_DROP_EVERY5' and counters['maker']%5==0: sh=0.0
    elif role=='MAKER':
        counters['maker']+=1
        if weak and side==weak:counters['weak']+=1
    return sh

def rec_features(pre,post,z,hist,i,n,features):
    t=int(z['t']);gross=max(float(pre['gross']),1e-9);surplus='UP' if pre['up']>pre['down'] else 'DOWN' if pre['down']>pre['up'] else 'FLAT'
    relation='SURPLUS_SIDE' if z['side']==surplus else 'WEAK_SIDE_CROSS' if surplus!='FLAT' else 'FLAT'
    q15=[a for a in hist if t-int(a['t'])<=15000];q5=[a for a in q15 if t-int(a['t'])<=5000]
    old=m.geom(*q15[0]['preState']) if q15 else pre
    same=sum(float(a['sh']) for a in q15 if surplus!='FLAT' and a['side']==surplus)
    opp=sum(float(a['sh']) for a in q15 if surplus!='FLAT' and a['side']!=surplus)
    reserve=max(0.,pre['floor']-post['floor']);pre_up=max(pre['up']-pre['cost'],pre['down']-pre['cost']);post_up=max(post['up']-post['cost'],post['down']-post['cost'])
    pre_base=pre['paired'];post_base=post['paired'];pre_sur=pre['absnet'];post_sur=post['absnet']
    vals={
      'event_index_norm':i/max(1,n-1),'pre_floor_per_gross':div(pre['floor'],gross),'pre_edge':pre['edge'],'pre_coverage':pre['coverage'],
      'pre_absnet_ratio':div(pre['absnet'],gross),'pre_cost_per_gross':div(pre['cost'],gross),'pre_upside_per_gross':div(pre_up,gross),
      'reserve_spend_ratio':div(reserve,max(pre['floor'],1e-9)),'reserve_spend_per_gross':div(reserve,gross),
      'post_floor_per_gross':div(post['floor'],max(post['gross'],1e-9)),'post_edge':post['edge'],'post_absnet_ratio':div(post['absnet'],max(post['gross'],1e-9)),'post_coverage':post['coverage'],
      'candidate_price':float(z['px']),'candidate_shares_per_gross':div(float(z['sh']),gross),'candidate_role_taker':float(z['role']=='TAKER'),
      'relation_surplus_side':float(relation=='SURPLUS_SIDE'),'relation_weak_side_cross':float(relation=='WEAK_SIDE_CROSS'),
      'events_5s':float(len(q5)),'events_15s':float(len(q15)),'maker_events_15s':float(sum(a['role']=='MAKER' for a in q15)),'taker_events_15s':float(sum(a['role']=='TAKER' for a in q15)),
      'same_side_shares_15s_per_gross':div(same,gross),'opp_side_shares_15s_per_gross':div(opp,gross),
      'floor_change_15s_per_gross':div(pre['floor']-old['floor'],gross),'edge_change_15s':pre['edge']-old['edge'],
      'pre_surplus_shares_per_gross':div(pre_sur,gross),'pre_base_pair_per_gross':div(pre_base,gross),'pre_floor_to_upside':div(pre['floor'],pre_up),
      'pre_floor_per_base_share':div(pre['floor'],pre_base),'pre_upside_per_surplus':div(pre_up,pre_sur),
      'post_surplus_shares_per_gross':div(post_sur,max(post['gross'],1e-9)),'post_base_pair_per_gross':div(post_base,max(post['gross'],1e-9)),
      'post_floor_to_upside':div(post['floor'],post_up),'post_floor_per_base_share':div(post['floor'],post_base),'post_upside_per_surplus':div(post_up,post_sur),
      'same_side_events_15s':float(sum(a['side']==surplus for a in q15)) if surplus!='FLAT' else 0.0,
      'opp_side_events_15s':float(sum(a['side']!=surplus for a in q15)) if surplus!='FLAT' else 0.0,
      'shares_5s_per_gross':div(sum(float(a['sh']) for a in q5),gross),'shares_15s_per_gross':div(sum(float(a['sh']) for a in q15),gross)
    }
    return np.asarray([[float(vals.get(k,0.0)) for k in features]],dtype=float)

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

def replay(mid,events,winner,pub,side_model,rec_art,policy,stress):
    up=down=cu=cd=fees=0.;hist=[];ctr={'maker':0,'weak':0};flags=sideok=recok=overrides=missing=0
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
            if policy=='HARD_MPQ':suppress=True
            else:
                ps=m.pub_at(pub,mid,z['t'])
                if ps is None:missing+=1;suppress=True
                else:
                    Xs=m.feature_row((up,down,cu,cd,fees),hist,z,ps,i,len(events));pup=float(side_model.predict_proba(Xs)[0,1]);pred='UP' if pup>=.5 else 'DOWN';so=(pred==side)
                    if so:sideok+=1
                    if policy=='SIDE_ONLY':
                        suppress=not so
                        if so:overrides+=1
                    elif policy=='DOUBLE_CONFIRM':
                        Xr=rec_features(pre,post,z,hist,i,len(events),rec_art['features']);pr=float(rec_art['model'].predict_proba(Xr)[0,1]);ro=(pr>=.5)
                        if ro:recok+=1
                        allow=so and ro
                        suppress=not allow
                        if allow:overrides+=1
        if not suppress:
            up,down,cu,cd=pu,pd,pcu,pcd
            if role=='TAKER':fees+=sh*px*.02
            if sh>0:hist.append({'t':z['t'],'role':role,'side':side,'sh':sh,'px':px,'preState':(up-sh if side=='UP' else up,down-sh if side=='DOWN' else down,cu-sh*px if side=='UP' else cu,cd-sh*px if side=='DOWN' else cd)})
    g=m.geom(up,down,cu,cd);payout=up if winner=='UP' else down if winner=='DOWN' else 0.
    return {**g,'pnl':payout-(cu+cd),'flags':flags,'sideOk':sideok,'recoverOk':recok,'overrides':overrides,'missing':missing}

def md(a):return median(a) if a else None

def compare(ids,base,hard,side,double):
    out={}
    for name,pol in [('hard',hard),('side',side),('double',double)]:
        df=[pol[x]['floor']-base[x]['floor'] for x in ids];dp=[pol[x]['pnl']-base[x]['pnl'] for x in ids];dc=[pol[x]['coverage']-base[x]['coverage'] for x in ids];da=[pol[x]['absnet']-base[x]['absnet'] for x in ids]
        out[name]={'medianDeltaFloor':md(df),'medianDeltaPnl':md(dp),'medianDeltaCoverage':md(dc),'medianDeltaAbsNet':md(da),'floorImproved':sum(v>1e-9 for v in df),'floorWorsened':sum(v<-1e-9 for v in df),'overrideEvents':sum(pol[x].get('overrides',0) for x in ids)}
    hf=out['hard']['medianDeltaFloor'];hp=out['hard']['medianDeltaPnl']
    for name in ['side','double']:
        pf=out[name]['medianDeltaFloor'];pp=out[name]['medianDeltaPnl'];out[name]['floorRetentionVsHard']=pf/hf if hf and hf>1e-9 else None;out[name]['pnlRecoveryVsHard']=((pp-hp)/abs(hp)) if hp is not None and hp< -1e-9 else None
    return out

def main():
    side_art=joblib.load(m.MODEL);rec_art=joblib.load(REC_PATH);all_ids=[int(x) for x in side_art['testMarketIds']];events,winners=m.load_events(all_ids)
    active_by={};union=set()
    for stress in STRESSES:
        ids=[x for x in all_ids if x in events and winners.get(x) in {'UP','DOWN'} and has_flag(events[x],stress)];active_by[stress]=ids;union.update(ids)
    pub=m.load_public(sorted(union));results={}
    for stress,ids in active_by.items():
        base={x:replay(x,events[x],winners[x],pub,side_art['model'],rec_art,'BASELINE',stress) for x in ids}
        hard={x:replay(x,events[x],winners[x],pub,side_art['model'],rec_art,'HARD_MPQ',stress) for x in ids}
        side={x:replay(x,events[x],winners[x],pub,side_art['model'],rec_art,'SIDE_ONLY',stress) for x in ids}
        double={x:replay(x,events[x],winners[x],pub,side_art['model'],rec_art,'DOUBLE_CONFIRM',stress) for x in ids}
        c=compare(ids,base,hard,side,double);d=c['double'];eligible=len(ids)>=10
        passed=bool(eligible and d['medianDeltaFloor'] is not None and d['medianDeltaFloor']>0 and d['floorRetentionVsHard'] is not None and d['floorRetentionVsHard']>=.5 and (d['pnlRecoveryVsHard'] is None or d['pnlRecoveryVsHard']>=.25))
        results[stress]={'activeMarkets':len(ids),'metrics':c,'doublePass':passed}
    normal=results['NONE'];stress_eligible=[v for k,v in results.items() if k!='NONE' and v['activeMarkets']>=10];stress_pass=sum(v['doublePass'] for v in stress_eligible)
    overall=bool(normal['doublePass'] and len(stress_eligible)>=2 and stress_pass>=2)
    report={'version':VERSION,'createdAt':datetime.now(TZ).isoformat(),'candidate':'Frozen MPQ + independently-trained Deliberate Side + research Recovery Capacity double-confirm at natural 0.5 boundaries.','sideModel':str(m.MODEL.relative_to(ROOT)).replace('\\','/'),'recoveryModel':str(REC_PATH.relative_to(ROOT)).replace('\\','/'),'results':results,'gate':{'required':'normal double-confirm pass plus at least 2 eligible execution-stress variants pass','pass':overall},'guards':{'noThresholdSweep':True,'sideBoundary':0.5,'recoveryBoundary':0.5,'winnerEvaluationOnly':True,'noEchtgeldTraining':True,'noLiveR3Change':True,'no8781Change':True,'mpqFrozen':True,'recoveryHeadResearchOnly':True}}
    out=m.OUT/f"r4_mpq_side_recovery_doubleconfirm_v1_{datetime.now(TZ).strftime('%Y%m%d_%H%M%S')}.json";out.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'artifact':str(out.relative_to(ROOT)).replace('\\','/'),'results':results,'keep':overall},ensure_ascii=False))

if __name__=='__main__':main()
