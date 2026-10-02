from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np, pandas as pd, joblib
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, log_loss

SRC=Path('data/research/r4_v0/p0_provenance_v1/r4_p0b_target_objective_topology_state_rows_v2.csv')
OUT=Path('data/research/r4_v0/p0_provenance_v1/TARGET_MULTILOOP_CONTRACTION_TEACHER_V1B_MARKETSECOND_20260905.json')
MODEL=Path('data/research/r4_v0/p0_provenance_v1/target_multiloop_contraction_teacher_v1b_marketsecond_20260905.joblib')
FEATURES=['seconds_left','weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','events_5s','events_15s','transitions_15s','mode_age_s','coverage','floor_per_gross','abs_gap']
ABL={'TIME':['seconds_left'],'ROOTS':['weak_active_roots','dominant_active_roots'],'TIME_ROOTS':['seconds_left','weak_active_roots','dominant_active_roots'],'OWNER_PROGRESS':['weak_active_roots','dominant_active_roots','weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio']}

def metrics(y,p):
    if len(set(y))<2:return {'auc':None,'ap':None,'balancedAccuracyAt05':None,'logLoss':None}
    return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'balancedAccuracyAt05':float(balanced_accuracy_score(y,p>=.5)),'logLoss':float(log_loss(y,p,labels=[0,1]))}

def fit_eval(train,test,feats):
    xtr=train[feats].replace([np.inf,-np.inf],np.nan).fillna(0.).to_numpy(float); ytr=train.label.to_numpy(int)
    xte=test[feats].replace([np.inf,-np.inf],np.nan).fillna(0.).to_numpy(float); yte=test.label.to_numpy(int)
    m=HistGradientBoostingClassifier(max_depth=4,max_iter=160,learning_rate=.06,l2_regularization=1.0,random_state=17).fit(xtr,ytr)
    p=m.predict_proba(xte)[:,1]; return m,{'trainRows':len(train),'testRows':len(test),'testMarkets':int(test.market_id.nunique()),'trainRate':float(ytr.mean()),'testRate':float(yte.mean()),**metrics(yte,p)}

def main():
    d=pd.read_csv(SRC)
    d=d[np.isfinite(d.seconds_left)&(d.seconds_left>0)&(d.seconds_left<=300)].copy()
    d['sec_bucket']=np.floor(d['seconds_left']).astype(int)
    # One strict-past state per market-second; use latest event in that second.
    d=d.sort_values(['market_id','t']).groupby(['market_id','sec_bucket'],as_index=False).tail(1).copy()
    d['roots']=d.weak_active_roots.fillna(0)+d.dominant_active_roots.fillna(0)
    # Future <=5s row in same market; label root contraction, not action/PnL/winner.
    rows=[]
    for mid,g in d.groupby('market_id',sort=False):
        g=g.sort_values('t'); ts=g.t.to_numpy(np.int64); roots=g.roots.to_numpy(float)
        for i,r in enumerate(g.itertuples(index=False)):
            j=np.searchsorted(ts,ts[i]+5000,side='left')
            if j>=len(g):continue
            rr=r._asdict(); rr['label']=int(roots[j]<roots[i]-1e-9); rr['futureRoots5s']=float(roots[j]); rows.append(rr)
    x=pd.DataFrame(rows)
    mids=sorted(x.market_id.unique()); n=len(mids)
    folds=[]; last_model=None
    for trf,tef in [(.50,.625),(.625,.75),(.75,.875)]:
        trset=set(mids[:max(1,int(n*trf))]); teset=set(mids[int(n*trf):max(int(n*trf)+1,int(n*tef))])
        tr=x[x.market_id.isin(trset)]; te=x[x.market_id.isin(teset)]
        m,z=fit_eval(tr,te,FEATURES);z['trainFraction']=trf;z['testRange']=[trf,tef];folds.append(z);last_model=m
    cut=int(n*.8); tr=x[x.market_id.isin(set(mids[:cut]))]; te=x[x.market_id.isin(set(mids[cut:]))]
    abl={}
    for k,f in ABL.items():_,abl[k]=fit_eval(tr,te,f)
    final,full=fit_eval(tr,te,FEATURES); joblib.dump({'model':final,'features':FEATURES,'label':'future_5s_live_root_contraction','sampling':'one_latest_state_per_market_second'},MODEL)
    out={'version':'TARGET_MULTILOOP_CONTRACTION_TEACHER_V1B_MARKETSECOND','date':'2026-09-05','researchOnly':True,'actionAuthority':False,'sampling':'one latest Target topology state per market-second to remove event-frequency weighting','rows':len(x),'markets':int(x.market_id.nunique()),'label':'future 5s live-root count is lower than current live-root count','features':FEATURES,'forwardChronologyFolds':folds,'final20pctHoldout':full,'ablation':abl,'decision':'KEEP_MARKETSECOND_CONTRACTION_BELIEF' if min(z['auc'] for z in folds if z['auc'] is not None)>=.75 else 'DO_NOT_PROMOTE_CONTRACTION_BELIEF','boundary':['Target future roots are offline label only','runtime features are strict-past current topology/owner/progress/portfolio state','no winner/PnL/future market price inputs','not action authority; no direct capacity threshold transferred']}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'decision':out['decision'],'rows':len(x),'folds':folds,'final20pct':full,'ablation':abl},ensure_ascii=False))
if __name__=='__main__':main()
