from __future__ import annotations
import json,math
from pathlib import Path
import joblib,numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_state_rows_v2.csv'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_MULTILOOP_CONTRACTION_TEACHER_V1_20260905.json'
MODEL=ROOT/'data/research/r4_v0/p0_provenance_v1/target_multiloop_contraction_teacher_v1_20260905.joblib'
FEATURES=['seconds_left','weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s','current_commitment']
TIME=['seconds_left']
ROOTS=['weak_active_roots','dominant_active_roots']
TIME_ROOTS=['seconds_left','weak_active_roots','dominant_active_roots']

def labels(d:pd.DataFrame)->np.ndarray:
    y=np.full(len(d),np.nan)
    ar=(d.weak_active_roots.fillna(0)+d.dominant_active_roots.fillna(0)).to_numpy(float)
    for _,ix0 in d.groupby('market_id',sort=False).groups.items():
        ix=np.asarray(list(ix0),dtype=int);ts=d.loc[ix,'t'].to_numpy()
        vals=ar[ix]
        for j,gi in enumerate(ix):
            k=np.searchsorted(ts,ts[j]+5000,side='right');f=vals[j+1:k]
            if len(f):y[gi]=1.0 if f.min()<vals[j]-1e-9 else 0.0
    return y

def fit_eval(z,train_markets,test_markets,features):
    tr=z.market_id.isin(train_markets);te=z.market_id.isin(test_markets);y=z.y.astype(int)
    X=z[features].replace([np.inf,-np.inf],np.nan).fillna(0.0)
    m=HistGradientBoostingClassifier(max_iter=160,max_leaf_nodes=15,learning_rate=.06,l2_regularization=1.0,random_state=7).fit(X[tr],y[tr])
    p=m.predict_proba(X[te])[:,1];pred=(p>=.5).astype(int)
    return m,{'trainRows':int(tr.sum()),'testRows':int(te.sum()),'testMarkets':int(z.loc[te,'market_id'].nunique()),'trainRate':float(y[tr].mean()),'testRate':float(y[te].mean()),'auc':float(roc_auc_score(y[te],p)),'ap':float(average_precision_score(y[te],p)),'balancedAccuracyAt05':float(balanced_accuracy_score(y[te],pred)),'logLoss':float(log_loss(y[te],p))}

def main():
    d=pd.read_csv(SRC).sort_values(['market_id','t']).reset_index(drop=True);d['y']=labels(d);z=d[d.y.notna()].copy()
    mts=z.groupby('market_id').t.min().sort_values();ids=list(mts.index);n=len(ids)
    folds=[]
    # expanding chronology: train 50/62.5/75%, test the next 12.5%
    for frac in (.50,.625,.75):
        a=int(n*frac);b=min(n,int(n*(frac+.125)));train=set(ids[:a]);test=set(ids[a:b])
        if not train or not test:continue
        _,met=fit_eval(z,train,test,FEATURES);folds.append({'trainFraction':frac,'testRange':[frac,frac+.125],**met})
    cut=int(n*.8);train=set(ids[:cut]);test=set(ids[cut:])
    blocks={}
    for name,fs in [('TIME',TIME),('ROOTS',ROOTS),('TIME_ROOTS',TIME_ROOTS),('OWNER_PROGRESS',FEATURES)]:
        _,blocks[name]=fit_eval(z,train,test,fs)
    final_model,hold=fit_eval(z,train,test,FEATURES)
    report={'version':'TARGET_MULTILOOP_CONTRACTION_TEACHER_V1','date':'2026-09-05','researchOnly':True,'actionAuthority':False,'source':str(SRC.relative_to(ROOT)),'label':'Among later Target action-state rows in (t,t+5s], does min(live active roots) fall below current live active roots? Future Target rows are offline label only.','features':FEATURES,'coverage':{'rows':len(z),'markets':int(z.market_id.nunique()),'positiveRate':float(z.y.mean())},'forwardChronologyFolds':folds,'final80_20Ablation':blocks,'finalHoldout':hold,'decision':'KEEP_AS_CONTRACTION_BELIEF_TEACHER_NOT_DIRECT_RUNTIME_AUTHORITY' if min(x['auc'] for x in folds)>=.75 and hold['auc']>=.80 else 'DO_NOT_PROMOTE_CONTRACTION_TEACHER','interpretation':['Contraction is a pool-state management problem, not a fixed countdown schedule.','Time-only performance is an explicit falsification baseline.','Current live-root occupancy is a legitimate strict-past state, but high predictiveness does not prove Target has a literal hidden root-count threshold.'],'boundary':['no winner, settlement, PnL or future market features in runtime inputs','future Target root state used only to construct offline label','event-state sampling is action-conditioned; not an unbiased fixed-time snapshot','teacher cannot create new exposure authority','no 8781/live changes']}
    OUT.write_text(json.dumps(report,indent=2),encoding='utf-8');joblib.dump({'version':report['version'],'features':FEATURES,'model':final_model,'report':report},MODEL)
    print(json.dumps({'ok':True,'output':str(OUT),'model':str(MODEL),'decision':report['decision'],'folds':folds,'ablation':blocks},ensure_ascii=False))
if __name__=='__main__':main()
