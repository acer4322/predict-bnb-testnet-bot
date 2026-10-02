from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque, Counter
import numpy as np, joblib

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
R=ROOT/'data'/'research'/'r3_v0'
M_ARB=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')
M_CROSS=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')
FEATURES=M_ARB['features']


def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.0

def build_market(c,mid):
    rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
    if len(rr)<4:return []
    up=down=cost=fees=0.; hist=deque(); prev_t=None; snaps=[]; first_safe=None
    for i,(role,side,t,px,sh) in enumerate(rr):
        role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
        if side=='UP': up+=sh
        else: down+=sh
        cost+=px*sh; fees+=fee(sh,px,role)
        pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
        if first_safe is None and fl>=0:first_safe=i
        hist.append((t,role,side,sh,ss,fl,ups))
        while hist and t-hist[0][0]>15000: hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
        f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
        snaps.append({'t':t,'floor':fl,'upside':ups,'ss':ss,'surplus':surplus,'f':f}); prev_t=t
    lim=first_safe if first_safe is not None else len(snaps); out=[]
    for i in range(lim):
        s=snaps[i]
        if s['floor']>=0 or s['surplus']=='FLAT' or s['ss']<5: continue
        j=i+1
        while j<len(snaps) and snaps[j]['t']-s['t']<=5000:j+=1
        if j<=i+1:continue
        fut=snaps[i+1:j]; last=fut[-1]; contraction=s['ss']-last['ss']; thr=max(5.,.1*s['ss'])
        teacher='CROSSING_PROTECTION' if max(x['floor'] for x in fut)>=0 else ('BUILD_WEAK_SIDE' if contraction>=thr and last['floor']>s['floor'] else 'ALLOW_ASYMMETRY')
        out.append((mid,s['t'],[s['f'][k] for k in FEATURES],teacher,s['floor'],s['ss']))
    return out

def probs(X):
    a=M_ARB['model'].predict_proba(X)[:,1]   # BUILD probability
    c=M_CROSS['model'].predict_proba(X)[:,1]
    return a,c

def run_config(data,cross_on,build_on,build_off,min_dwell):
    by={}
    for row in data: by.setdefault(row[0],[]).append(row)
    total=correct=0; trans=0; dwell=[]; pred_counts=Counter(); teach_counts=Counter(); market_stats=[]
    for mid,rows in by.items():
        X=np.asarray([r[2] for r in rows],float); pa,pc=probs(X); state='ALLOW_ASYMMETRY'; since=rows[0][1]; prev_t=rows[0][1]; mtrans=0; corr=0
        for k,r in enumerate(rows):
            t=r[1]; teacher=r[3]; teach_counts[teacher]+=1
            can_switch=(t-since)>=min_dwell
            new=state
            if pc[k]>=cross_on and can_switch: new='CROSSING_PROTECTION'
            elif state=='CROSSING_PROTECTION':
                if pc[k] < cross_on*0.65 and can_switch: new='BUILD_WEAK_SIDE' if pa[k]>=build_on else 'ALLOW_ASYMMETRY'
            elif state=='ALLOW_ASYMMETRY':
                if pa[k]>=build_on and can_switch: new='BUILD_WEAK_SIDE'
            elif state=='BUILD_WEAK_SIDE':
                if pa[k]<=build_off and can_switch: new='ALLOW_ASYMMETRY'
            if new!=state:
                dwell.append(max(0,t-since)); trans+=1; mtrans+=1; state=new; since=t
            pred_counts[state]+=1; total+=1; corr+=int(state==teacher); correct+=int(state==teacher); prev_t=t
        dwell.append(max(0,prev_t-since)); market_stats.append({'marketId':mid,'transitions':mtrans,'accuracy':corr/max(1,len(rows))})
    return {'crossOn':cross_on,'buildOn':build_on,'buildOff':build_off,'minDwellMs':min_dwell,'accuracy':correct/max(1,total),'transitionsPerMarket':trans/max(1,len(by)),'medianDwellMs':float(np.median(dwell)) if dwell else None,'predRates':{k:v/total for k,v in pred_counts.items()},'teacherRates':{k:v/total for k,v in teach_counts.items()}}

def main():
    c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
    for mid in mids:data.extend(build_market(c,mid))
    c.close(); uniq=sorted(set(r[0] for r in data)); test=set(uniq[int(.8*len(uniq)):]); data=[r for r in data if r[0] in test]
    cfgs=[]
    for cross in [.45,.55,.65,.75]:
      for bon,boff in [(.55,.45),(.6,.4),(.65,.35)]:
       for dwell in [0,2000,5000,10000]: cfgs.append(run_config(data,cross,bon,boff,dwell))
    cfgs=sorted(cfgs,key=lambda z:(-z['accuracy'],z['transitionsPerMarket']))
    out={'version':'R3_FORMATION_HYSTERESIS_CONTROLLER_V0','testMarkets':len(test),'testRows':len(data),'best':cfgs[:12],'allConfigs':cfgs}
    (R/'r3_formation_hysteresis_controller_v0_report.json').write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out['best'][:8],indent=2))
if __name__=='__main__':main()
