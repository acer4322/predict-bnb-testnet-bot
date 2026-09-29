from __future__ import annotations
import json, sqlite3, math, sys, statistics
from pathlib import Path
from collections import deque
import numpy as np, joblib
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']

EXPERTS={'formation_arbitration_build_vs_allow':0,'safe_crossing':1}

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
        if first_safe is None and fl>=0: first_safe=i
        hist.append((t,role,side,sh,ss,fl,ups))
        while hist and t-hist[0][0]>15000: hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
        f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev_t is None else float(t-prev_t),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
        snaps.append((t,fl,ss,f)); prev_t=t
    limit=first_safe if first_safe is not None else len(snaps); out=[]
    for i in range(limit):
        t,fl,ss,f=snaps[i]
        if fl>=0 or ss<5: continue
        j=i+1
        while j<len(snaps) and snaps[j][0]-t<=5000: j+=1
        if j<=i+1: continue
        fut=snaps[i+1:j]; last=fut[-1]
        contraction=ss-last[2]; threshold=max(5.,.10*ss)
        y_build=int(contraction>=threshold and last[1]>fl)
        y_cross=int(max(x[1] for x in fut)>=0)
        out.append((mid,[f[k] for k in FEATURES],y_build,y_cross))
    return out

def score(m,X,y):
    p=m.predict_proba(X)[:,1]; pred=(p>=.5).astype(int)
    return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def global_terms(model):
    exp=model.explain_global(); d=exp.data()
    names=d.get('names',[]); scores=d.get('scores',[])
    return sorted([(str(n),float(s)) for n,s in zip(names,scores)],key=lambda z:z[1],reverse=True)

def stability(model, Xs):
    # report per-split AUC and mean prediction; feature-shape stability is represented by same fitted terms applied chronologically.
    out=[]
    for name,X,y in Xs:
        p=model.predict_proba(X)[:,1]
        out.append({'split':name,'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'meanPrediction':float(np.mean(p)),'positiveRate':float(np.mean(y))})
    return out

def main():
    expert=sys.argv[1] if len(sys.argv)>1 else 'formation_arbitration_build_vs_allow'
    if expert not in EXPERTS: raise SystemExit('bad expert')
    c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
    for mid in mids: data.extend(build_market(c,mid))
    c.close(); uniq=sorted(set(x[0] for x in data)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); splitsets=[('train',set(uniq[:a])),('validation',set(uniq[a:b])),('test',set(uniq[b:]))]
    label_idx=2 if expert=='formation_arbitration_build_vs_allow' else 3
    mats=[]
    for name,ms in splitsets:
        dd=[x for x in data if x[0] in ms]; X=np.asarray([x[1] for x in dd],float); y=np.asarray([x[label_idx] for x in dd],int); mats.append((name,X,y))
    # full-scale but bounded interaction budget for interpretability
    model=ExplainableBoostingClassifier(feature_names=FEATURES,interactions=20,max_rounds=10000,outer_bags=14,inner_bags=0,learning_rate=.035,min_samples_leaf=3,max_bins=256,max_interaction_bins=64,random_state=20260824,n_jobs=-1)
    model.fit(mats[0][1],mats[0][2])
    model_path=OUT/f'r3_{expert}_ebm_full_v1.joblib'; joblib.dump({'model':model,'features':FEATURES,'expert':expert},model_path)
    rep={'version':'R3_FORMATION_EBM_FULL_V1','expert':expert,'markets':len(uniq),'rows':len(data),'features':FEATURES,'interactionBudget':20,'maxRounds':10000,'splits':{name:score(model,X,y) for name,X,y in mats},'topGlobalTerms':global_terms(model)[:40],'chronologicalStability':stability(model,mats)}
    report_path=OUT/f'r3_{expert}_ebm_full_v1_report.json'
    report_path.write_text(json.dumps(rep,indent=2),encoding='utf-8'); print(json.dumps(rep,indent=2))
if __name__=='__main__': main()
