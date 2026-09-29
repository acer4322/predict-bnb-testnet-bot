from __future__ import annotations
import json, sqlite3, math
from pathlib import Path
from collections import deque, Counter
import numpy as np, joblib

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
R=ROOT/'data'/'research'/'r3_v0'
OUT=R/'r3_formation_dream_sync_v0.json'
ARB=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['model']
CROSS=joblib.load(R/'r3_safe_crossing_ebm_full_v1.joblib')['model']
FEATURES=joblib.load(R/'r3_formation_arbitration_build_vs_allow_ebm_full_v1.joblib')['features']

CFG={'crossOn':0.35,'buildOn':0.48,'buildOff':0.38,'minDwellMs':1000}

def fee(sh,px,role): return sh*px*.02 if str(role).upper()=='TAKER' else 0.0

def build_market(c,mid):
    rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
    if len(rr)<6:return None
    up=down=cost=fees=0.; hist=deque(); prev_t=None; snaps=[]; first_safe=None
    for i,(role,side,t,px,sh) in enumerate(rr):
        role=str(role); side=str(side); t=int(t); px=float(px); sh=float(sh)
        if side=='UP':up+=sh
        else:down+=sh
        cost+=px*sh; fees+=fee(sh,px,role)
        pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); ss=abs(up-down); base=min(up,down); gross=up+down; surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'
        if first_safe is None and fl>=0:first_safe=i
        hist.append((t,role,side,sh,ss,fl,ups))
        while hist and t-hist[0][0]>15000:hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
        f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
        snaps.append((t,up,down,fl,ups,ss,surplus,f)); prev_t=t
    if first_safe is None or first_safe<3:return None
    # teacher states on PRE_SAFE, same definitions as sequence v2
    limit=first_safe; teacher=[]
    for i in range(limit):
        t,u,d,fl,ups,ss,surplus,f=snaps[i]
        if surplus=='FLAT' or ss<5: teacher.append('ALLOW_ASYMMETRY'); continue
        j=i+1
        while j<len(snaps) and snaps[j][0]-t<=5000:j+=1
        if j<=i+1: teacher.append('ALLOW_ASYMMETRY'); continue
        fut=snaps[i+1:j]; last=fut[-1]; fut_floor=[x[3] for x in fut]
        if max(fut_floor)>=0: st='CROSSING_PROTECTION'
        else:
            contraction=ss-last[5]; thr=max(5.,.1*ss)
            st='BUILD_WEAK_SIDE' if (contraction>=thr and last[3]>fl) else 'ALLOW_ASYMMETRY'
        teacher.append(st)
    # choose markets that exhibit all 3 and at least two ALLOW<->BUILD switches
    sw=sum(1 for a,b in zip(teacher,teacher[1:]) if {a,b}=={'ALLOW_ASYMMETRY','BUILD_WEAK_SIDE'})
    if sw<2 or 'CROSSING_PROTECTION' not in teacher:return None
    # R3 dream-controller states driven only by current strict-past feature snapshots
    state='ALLOW_ASYMMETRY'; last_change=snaps[0][0]; pred=[]; probs=[]
    for i in range(limit):
        t=snaps[i][0]; f=snaps[i][7]
        X=np.array([[float(f[k]) for k in FEATURES]],float)
        pb=float(ARB.predict_proba(X)[0,1]); pc=float(CROSS.predict_proba(X)[0,1])
        can=(t-last_change)>=CFG['minDwellMs']
        ns=state
        if can and pc>=CFG['crossOn']: ns='CROSSING_PROTECTION'
        elif state=='CROSSING_PROTECTION':
            if can and pc<CFG['crossOn']*0.7: ns='BUILD_WEAK_SIDE' if pb>=CFG['buildOn'] else 'ALLOW_ASYMMETRY'
        elif state=='BUILD_WEAK_SIDE':
            if can and pb<CFG['buildOff']: ns='ALLOW_ASYMMETRY'
        else:
            if can and pb>=CFG['buildOn']: ns='BUILD_WEAK_SIDE'
        if ns!=state: state=ns; last_change=t
        pred.append(state); probs.append((pb,pc))
    # metrics
    n=len(teacher); sync=sum(a==b for a,b in zip(teacher,pred))/n
    tc=Counter(teacher); pcnt=Counter(pred)
    def occ(c,k):return c[k]/n
    def transitions(seq): return sum(1 for a,b in zip(seq,seq[1:]) if a!=b)
    # transition alignment within ±2 checkpoints: predicted change matches teacher destination
    tch=[(i,teacher[i]) for i in range(1,n) if teacher[i]!=teacher[i-1]]
    pch=[(i,pred[i]) for i in range(1,n) if pred[i]!=pred[i-1]]
    matched=0
    for i,s in tch:
        if any(abs(j-i)<=2 and ps==s for j,ps in pch): matched+=1
    return {'marketId':mid,'n':n,'firstSafeMs':snaps[first_safe][0]-snaps[0][0],'teacherSwitches':transitions(teacher),'r3Switches':transitions(pred),'checkpointSync':sync,'transitionSync2cp':matched/max(1,len(tch)),'teacherOcc':{k:occ(tc,k) for k in ['ALLOW_ASYMMETRY','BUILD_WEAK_SIDE','CROSSING_PROTECTION']},'r3Occ':{k:occ(pcnt,k) for k in ['ALLOW_ASYMMETRY','BUILD_WEAK_SIDE','CROSSING_PROTECTION']},'teacher':teacher,'r3':pred,'sampleProb':probs[:20]}

def main():
    c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
    rows=[]
    for mid in mids:
        z=build_market(c,mid)
        if z: rows.append(z)
    c.close()
    rows=rows[-20:]  # recent clear-behavior cohort, descriptive only
    if not rows: raise SystemExit('no cohort')
    arr=lambda k:[x[k] for x in rows]
    summary={'version':'R3_FORMATION_DREAM_SYNC_V0','mode':'dream-state replay / target-selected clear formation markets / no pass threshold','config':CFG,'markets':len(rows),'meanCheckpointSync':float(np.mean(arr('checkpointSync'))),'medianCheckpointSync':float(np.median(arr('checkpointSync'))),'meanTransitionSync2cp':float(np.mean(arr('transitionSync2cp'))),'medianTeacherSwitches':float(np.median(arr('teacherSwitches'))),'medianR3Switches':float(np.median(arr('r3Switches'))),'meanTeacherOcc':{},'meanR3Occ':{}}
    for s in ['ALLOW_ASYMMETRY','BUILD_WEAK_SIDE','CROSSING_PROTECTION']:
        summary['meanTeacherOcc'][s]=float(np.mean([x['teacherOcc'][s] for x in rows])); summary['meanR3Occ'][s]=float(np.mean([x['r3Occ'][s] for x in rows]))
    OUT.write_text(json.dumps({'summary':summary,'markets':rows},indent=2),encoding='utf-8'); print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
