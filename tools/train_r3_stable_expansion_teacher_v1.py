from __future__ import annotations
import sqlite3, json
from pathlib import Path
from collections import deque
import numpy as np, joblib
from sklearn.ensemble import HistGradientBoostingRegressor, HistGradientBoostingClassifier
from sklearn.metrics import mean_absolute_error, roc_auc_score, balanced_accuracy_score

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']

def fee(sh,px,role): return sh*px*0.02 if role=='TAKER' else 0.0

def build(c,mid):
    rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
    if len(rr)<5:return []
    up=down=cost=fees=0.; hist=deque(); prev=None; snaps=[]
    for i,(role,side,t,px,sh) in enumerate(rr):
        role,side=str(role),str(side); t=int(t); px=float(px); sh=float(sh)
        if side=='UP': up+=sh
        else: down+=sh
        cost+=px*sh; fees+=fee(sh,px,role)
        pu,pd=up-cost-fees,down-cost-fees; fl=min(pu,pd); ups=max(pu,pd); surplus='UP' if up>down else 'DOWN' if down>up else 'FLAT'; ss=abs(up-down); base=min(up,down); gross=up+down
        hist.append((t,role,side,sh,ss,fl,ups))
        while hist and t-hist[0][0]>15000: hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old5=r5[0] if r5 else hist[0]
        f={'floor':fl,'upside':ups,'upside_gap':ups-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':px,'last_shares':sh,'last_role_taker':float(role=='TAKER'),'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),'surplus_change_5s':float(ss-old5[4]),'floor_change_5s':float(fl-old5[5]),'upside_change_5s':float(ups-old5[6]),'floor_to_upside_ratio':fl/ups if abs(ups)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':ups/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
        snaps.append((t,surplus,ss,fl,ups,f)); prev=t
    out=[]
    for i,(t,surplus,ss,fl,ups,f) in enumerate(snaps):
        if surplus=='FLAT' or ss<5: continue
        future=[]
        for z in snaps[i+1:]:
            if z[0]-t>5000: break
            future.append(z)
        if not future: continue
        max_ss=max(z[2] for z in future); min_fl=min(z[3] for z in future); end=future[-1]
        max_up=max(z[4] for z in future); upside_gain=max(0.,max_up-ups); floor_spend=max(0.,fl-min_fl); out.append({'marketId':mid,'x':[float(f[k]) for k in FEATURES],'upsideGain5s':upside_gain,'floorSpend5s':floor_spend,'stableExpand':int(upside_gain>0 and upside_gain-floor_spend>0),'utility5s':upside_gain-floor_spend})
    return out

def score_cls(model,X,y):
    p=model.predict_proba(X)[:,1]; return {'n':len(y),'positiveRate':float(np.mean(y)),'predictedRate':float(np.mean(p>=.5)),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,p>=.5))}

def main():
    c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
    for m in mids:data.extend(build(c,m))
    c.close(); uniq=sorted(set(d['marketId'] for d in data)); a=int(.6*len(uniq)); b=int(.8*len(uniq)); groups=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])]
    mats=[]
    for g in groups:
        dd=[d for d in data if d['marketId'] in g]; X=np.asarray([d['x'] for d in dd]); y=np.asarray([d['stableExpand'] for d in dd]); u=np.asarray([d['utility5s'] for d in dd]); mats.append((X,y,u))
    Xtr,ytr,utr=mats[0]
    clf=HistGradientBoostingClassifier(max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=70,l2_regularization=3.,class_weight='balanced',random_state=20260825).fit(Xtr,ytr)
    util=HistGradientBoostingRegressor(loss='absolute_error',max_iter=220,learning_rate=.05,max_leaf_nodes=19,min_samples_leaf=70,l2_regularization=3.,random_state=20260825).fit(Xtr,utr)
    rep={'version':'R3_STABLE_EXPANSION_TEACHER_V1','researchOnly':True,'objective':'stableExpand iff future5s upside_gain > floor_spend; utility=upside_gain-floor_spend, same USDT units','markets':len(uniq),'rows':len(data),'features':FEATURES,'winnerFeature':False,'fixed18Forbidden':True,'splits':{}}
    for name,(X,y,u) in zip(['train','validation','test'],mats):
        d=score_cls(clf,X,y); pred=util.predict(X); d['utility']={'actualMedian':float(np.median(u)),'predMedian':float(np.median(pred)),'mae':float(mean_absolute_error(u,pred)),'positiveActualRate':float(np.mean(u>0)),'positivePredRate':float(np.mean(pred>0))}; rep['splits'][name]=d
    joblib.dump({'model':clf,'utilityModel':util,'features':FEATURES,'version':rep['version']},OUT/'r3_stable_expansion_teacher_v1.joblib');(OUT/'r3_stable_expansion_teacher_v1_report.json').write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
