from __future__ import annotations
import json, math, sqlite3
from pathlib import Path
from collections import deque
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, accuracy_score, balanced_accuracy_score
import joblib

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data'/'target_wallet_official_v1.db'
OUTDIR=ROOT/'data'/'research'/'r3_v0'
OUTDIR.mkdir(parents=True,exist_ok=True)
DATA_OUT=OUTDIR/'r3_surplus_gate_teacher_v0.json'
MODEL_OUT=OUTDIR/'r3_surplus_gate_hgb_v0.joblib'
REPORT_OUT=OUTDIR/'r3_surplus_gate_hgb_v0_report.json'

FEATURES=[
 'floor','upside','upside_gap','surplus_shares','base_pair_shares','surplus_ratio',
 'cost_per_gross_share','last_price','last_shares','last_role_taker','last_side_is_surplus',
 'age_since_last_ms','events_5s','events_15s','maker_events_15s','taker_events_15s',
 'same_side_events_15s','opp_side_events_15s','same_side_shares_15s','opp_side_shares_15s',
 'surplus_change_5s','floor_change_5s','upside_change_5s','event_index_norm'
]

def fee(sh,price,role):
    # target accounting uses 2% taker fee; maker assumed no fee here
    return float(sh)*float(price)*0.02 if str(role).upper()=='TAKER' else 0.0

def rows_for_market(c,mid):
    return c.execute('''select parent_id,role,side,first_event_ms,average_price,shares
                        from target_parent_orders where asset='BTC' and market_id=?
                        order by first_event_ms,parent_id''',(mid,)).fetchall()

def build_market(c,mid):
    rr=rows_for_market(c,mid)
    if len(rr)<3:return []
    up=down=cost=fees=0.0
    hist=deque()
    prev_t=None
    points=[]
    snapshots=[]
    for i,r in enumerate(rr):
        pid,role,side,t,price,shares=r
        t=int(t); price=float(price); shares=float(shares)
        if side=='UP':up+=shares
        else:down+=shares
        cost+=price*shares; fees+=fee(shares,price,role)
        pnl_up=up-cost-fees; pnl_down=down-cost-fees
        floor=min(pnl_up,pnl_down); upside=max(pnl_up,pnl_down)
        if up>down: surplus='UP'
        elif down>up: surplus='DOWN'
        else: surplus='FLAT'
        surplus_sh=abs(up-down); base=min(up,down); gross=up+down
        hist.append((t,role,side,shares,surplus_sh,floor,upside))
        while hist and t-hist[0][0]>15000:hist.popleft()
        def recent(ms): return [x for x in hist if t-x[0]<=ms]
        r5=recent(5000); r15=recent(15000)
        old5=r5[0] if r5 else hist[0]
        feat={
          'floor':floor,'upside':upside,'upside_gap':upside-floor,'surplus_shares':surplus_sh,
          'base_pair_shares':base,'surplus_ratio':surplus_sh/gross if gross>0 else 0.0,
          'cost_per_gross_share':(cost+fees)/gross if gross>0 else 0.0,
          'last_price':price,'last_shares':shares,'last_role_taker':1.0 if role=='TAKER' else 0.0,
          'last_side_is_surplus':1.0 if surplus!='FLAT' and side==surplus else 0.0,
          'age_since_last_ms':0.0 if prev_t is None else float(t-prev_t),
          'events_5s':float(len(r5)),'events_15s':float(len(r15)),
          'maker_events_15s':float(sum(x[1]=='MAKER' for x in r15)),'taker_events_15s':float(sum(x[1]=='TAKER' for x in r15)),
          'same_side_events_15s':float(sum(surplus!='FLAT' and x[2]==surplus for x in r15)),
          'opp_side_events_15s':float(sum(surplus!='FLAT' and x[2]!=surplus for x in r15)),
          'same_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]==surplus)),
          'opp_side_shares_15s':float(sum(x[3] for x in r15 if surplus!='FLAT' and x[2]!=surplus)),
          'surplus_change_5s':float(surplus_sh-old5[4]),'floor_change_5s':float(floor-old5[5]),'upside_change_5s':float(upside-old5[6]),
          'event_index_norm':i/max(1,len(rr)-1)
        }
        snapshots.append((i,t,surplus,feat,pnl_up,pnl_down))
        prev_t=t
    # label current checkpoint using next parent: same current surplus side = EXPAND, opposite = REPAIR.
    # Skip flat and tiny surplus to keep teacher clean; label only if next parent exists.
    out=[]
    for j in range(len(snapshots)-1):
        i,t,surplus,feat,pu,pd=snapshots[j]
        if surplus=='FLAT' or feat['surplus_shares']<5: continue
        nr=rr[j+1]; nrole,nside=str(nr[1]),str(nr[2])
        label=1 if nside==surplus else 0
        out.append({'marketId':mid,'t':t,'label':label,'labelName':'ALLOW_EXPAND' if label else 'REPAIR','nextRole':nrole,'nextSide':nside,'surplusSide':surplus,'features':feat})
    return out

def main():
    c=sqlite3.connect(DB)
    mids=[r[0] for r in c.execute("select distinct market_id from target_parent_orders where asset='BTC' order by market_id")]
    data=[]
    for mid in mids:data.extend(build_market(c,mid))
    c.close()
    # chronological market split 60/20/20
    uniq=sorted(set(d['marketId'] for d in data)); n=len(uniq); a=int(.6*n); b=int(.8*n)
    train_m=set(uniq[:a]); val_m=set(uniq[a:b]); test_m=set(uniq[b:])
    def mat(ms):
        dd=[d for d in data if d['marketId'] in ms]
        X=np.asarray([[float(d['features'][f]) for f in FEATURES] for d in dd],dtype=float)
        y=np.asarray([d['label'] for d in dd],dtype=int)
        return dd,X,y
    tr,Xtr,ytr=mat(train_m); va,Xv,yv=mat(val_m); te,Xt,yt=mat(test_m)
    model=HistGradientBoostingClassifier(max_iter=180,learning_rate=.06,max_leaf_nodes=15,min_samples_leaf=50,l2_regularization=2.0,random_state=20260824)
    model.fit(Xtr,ytr)
    def score(X,y):
        p=model.predict_proba(X)[:,1]; pred=(p>=.5).astype(int)
        return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}
    rep={'version':'R3_SURPLUS_GATE_HGB_V0','teacherDefinition':'At each non-flat Target checkpoint, next parent on current surplus side => ALLOW_EXPAND=1; next parent on opposite side => REPAIR=0. Winner excluded. This is a first behavioral teacher, not proof of intentionality.','features':FEATURES,'markets':len(uniq),'rows':len(data),'splits':{'trainMarkets':len(train_m),'valMarkets':len(val_m),'testMarkets':len(test_m)},'train':score(Xtr,ytr),'validation':score(Xv,yv),'test':score(Xt,yt)}
    # lightweight feature permutation importance on test
    base=rep['test']['auc']; rng=np.random.default_rng(7); imps=[]
    if base is not None:
      for k,f in enumerate(FEATURES):
        Xp=Xt.copy(); rng.shuffle(Xp[:,k]); pp=model.predict_proba(Xp)[:,1]; auc=roc_auc_score(yt,pp); imps.append((f,float(base-auc)))
      imps.sort(key=lambda z:z[1],reverse=True)
    rep['permutationImportanceTest']=imps[:15]
    DATA_OUT.write_text(json.dumps({'version':'R3_SURPLUS_GATE_TEACHER_V0','rows':len(data),'sample':data[:100]},ensure_ascii=False,indent=2),encoding='utf-8')
    joblib.dump({'model':model,'features':FEATURES,'teacherVersion':'R3_SURPLUS_GATE_TEACHER_V0'},MODEL_OUT)
    REPORT_OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'report':str(REPORT_OUT),'model':str(MODEL_OUT),'summary':rep},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
