from __future__ import annotations
import json, sqlite3
from pathlib import Path
from collections import deque
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, balanced_accuracy_score, accuracy_score
import joblib
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'; OUT=ROOT/'data'/'research'/'r3_v0'; OUT.mkdir(parents=True,exist_ok=True)
MODEL=OUT/'r3_floor_protection_hgb_v0.joblib'; REPORT=OUT/'r3_floor_protection_hgb_v0_report.json'
FEATURES=['floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio','cost_per_gross_share','last_price','last_shares','last_role_taker','age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s','same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s','surplus_change_5s','floor_change_5s','upside_change_5s','floor_to_upside_ratio','floor_per_base_share','upside_per_surplus_share','event_index_norm']
def fee(sh,p,r): return float(sh)*float(p)*0.02 if str(r).upper()=='TAKER' else 0.0
def build(c,mid):
    rr=c.execute("select role,side,first_event_ms,average_price,shares from target_parent_orders where asset='BTC' and market_id=? order by first_event_ms,parent_id",(mid,)).fetchall()
    if len(rr)<4:return []
    up=down=cost=fees=0.; hist=deque(); prev=None; snaps=[]
    for i,(role,side,t,p,sh) in enumerate(rr):
        t=int(t); p=float(p); sh=float(sh); up+=sh if side=='UP' else 0.; down+=sh if side=='DOWN' else 0.; cost+=p*sh; fees+=fee(sh,p,role)
        pu=up-cost-fees; pd=down-cost-fees; fl=min(pu,pd); us=max(pu,pd); sur='UP' if up>down else 'DOWN' if down>up else 'FLAT'; ss=abs(up-down); base=min(up,down); gross=up+down
        hist.append((t,role,side,sh,ss,fl,us));
        while hist and t-hist[0][0]>15000: hist.popleft()
        r5=[x for x in hist if t-x[0]<=5000]; r15=list(hist); old=r5[0] if r5 else hist[0]
        feat={'floor':fl,'upside':us,'upside_gap':us-fl,'surplus_shares':ss,'base_pair_shares':base,'surplus_ratio':ss/gross if gross else 0.,'cost_per_gross_share':(cost+fees)/gross if gross else 0.,'last_price':p,'last_shares':sh,'last_role_taker':1. if role=='TAKER' else 0.,'age_since_last_ms':0. if prev is None else float(t-prev),'events_5s':float(len(r5)),'events_15s':float(len(r15)),'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),'same_side_events_15s':float(sum(sur!='FLAT' and x[2]==sur for x in r15)),'opp_side_events_15s':float(sum(sur!='FLAT' and x[2]!=sur for x in r15)),'same_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]==sur)),'opp_side_shares_15s':float(sum(x[3] for x in r15 if sur!='FLAT' and x[2]!=sur)),'surplus_change_5s':ss-old[4],'floor_change_5s':fl-old[5],'upside_change_5s':us-old[6],'floor_to_upside_ratio':fl/us if abs(us)>1e-9 else 0.,'floor_per_base_share':fl/base if base>1e-9 else 0.,'upside_per_surplus_share':(us-fl)/ss if ss>1e-9 else 0.,'event_index_norm':i/max(1,len(rr)-1)}
        snaps.append((t,sur,ss,fl,feat)); prev=t
    out=[]
    for j,(t,sur,ss,fl,feat) in enumerate(snaps[:-1]):
        if sur=='FLAT' or ss<5: continue
        fut=[]
        for k in range(j+1,len(snaps)):
            if snaps[k][0]-t>5000: break
            fut.append(snaps[k])
        if not fut: continue
        fss=fut[-1][2]; ffl=fut[-1][3]; protect=(ss-fss>=max(5.,.10*ss) and ffl>=fl-2.)
        out.append({'marketId':mid,'label':int(protect),'features':feat})
    return out
def main():
    c=sqlite3.connect(DB); mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]; data=[]
    for m in mids:data.extend(build(c,m))
    c.close(); uniq=sorted({x['marketId'] for x in data}); a=int(.6*len(uniq)); b=int(.8*len(uniq)); sets=[set(uniq[:a]),set(uniq[a:b]),set(uniq[b:])]
    def mat(ms):
        d=[x for x in data if x['marketId'] in ms]; return np.array([[float(x['features'][f]) for f in FEATURES] for x in d]),np.array([x['label'] for x in d])
    Xtr,ytr=mat(sets[0]); Xv,yv=mat(sets[1]); Xt,yt=mat(sets[2]); model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=2.,random_state=20260824); model.fit(Xtr,ytr)
    def sc(X,y):
        p=model.predict_proba(X)[:,1]; q=(p>=.5).astype(int); return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,q)),'accuracy':float(accuracy_score(y,q))}
    rep={'version':'R3_FLOOR_PROTECTION_HGB_V0','teacher':'5s STOP_PROTECT when surplus shrinks >=max(5sh,10%) while floor worsens no more than $2. Winner excluded; no last-side shortcut.','markets':len(uniq),'rows':len(data),'features':FEATURES,'train':sc(Xtr,ytr),'validation':sc(Xv,yv),'test':sc(Xt,yt)}; joblib.dump({'model':model,'features':FEATURES},MODEL); REPORT.write_text(json.dumps(rep,indent=2)); print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
