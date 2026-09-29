from __future__ import annotations
import json,joblib
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from sklearn.preprocessing import label_binarize
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_responsibility_progress_full300_v1_rows.csv'
PRE=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
PORT=['seconds_left','abs_gap','risk_deficit','coverage','absnet_ratio','floor_per_gross'];RESP=['weak_active_owners','dominant_active_owners','weak_oldest_age_s','dominant_oldest_age_s'];MEM=['current_mode_age_s','events_5s','events_15s','transitions_15s'];PROG=['weak_unresolved_shares','dominant_unresolved_shares','weak_progress_ratio','dominant_progress_ratio','weak_fill_shares_5s','dominant_fill_shares_5s'];BASE=PORT+RESP+MEM;FULL=BASE+PROG;C=['CONTINUE_WEAK','HANDOFF_ALLOW','OBSERVE_NO_EVENT']
def hgb(seed):return HistGradientBoostingClassifier(learning_rate=.05,max_leaf_nodes=15,max_depth=4,min_samples_leaf=20,l2_regularization=1,max_iter=220,random_state=seed)
def ebm(feats,seed):return ExplainableBoostingClassifier(feature_names=feats,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def bmet(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def align(p,classes):
 co=list(classes);return np.column_stack([p[:,co.index(c)] for c in C])
def mmet(y,p):
 y=np.asarray(y,str);p=np.asarray(p,float);pred=np.asarray(C)[np.argmax(p,1)];Y=label_binarize(y,classes=C);rec=recall_score(y,pred,labels=C,average=None,zero_division=0);return {'n':int(len(y)),'macroAuc':float(roc_auc_score(y,p,labels=C,multi_class='ovr',average='macro')),'macroAp':float(np.mean([average_precision_score(Y[:,j],p[:,j]) for j in range(3)])),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=C)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'perClassRecall':{c:float(v) for c,v in zip(C,rec)}}
def main():
 pre=json.loads(PRE.read_text(encoding='utf-8'));d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan).dropna(subset=FULL).copy();d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].sort_values(['market_id','t']);ms=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();trm=ms[:240];tem=ms[240:];tr=d[d.market_id.isin(trm)];te=d[d.market_id.isin(tem)];tr0=tr[tr.build_now==1].copy();te0=te[te.build_now==1].copy()
 # M0 baseline/progress
 m0b=hgb(26082801).fit(tr0[BASE],tr0.continue_weak_5s.astype(int));m0=hgb(26082802).fit(tr0[FULL],tr0.continue_weak_5s.astype(int));p0b=m0b.predict_proba(te0[BASE])[:,1];p0=m0.predict_proba(te0[FULL])[:,1]
 # M1 baseline/progress EBM
 m1b=ebm(BASE,26082803).fit(tr0[BASE],tr0.management_label_5s);m1=ebm(FULL,26082804).fit(tr0[FULL],tr0.management_label_5s);p1b=align(m1b.predict_proba(te0[BASE]),m1b.classes_);p1=align(m1.predict_proba(te0[FULL]),m1.classes_)
 M0b=bmet(te0.continue_weak_5s,p0b);M0=bmet(te0.continue_weak_5s,p0);M1b=mmet(te0.management_label_5s,p1b);M1=mmet(te0.management_label_5s,p1)
 improve0=((M0['auc']>=M0b['auc'] and M0['logLoss']<=M0b['logLoss']+.005) or (M0['logLoss']<M0b['logLoss'] and M0['auc']>=M0b['auc']-.005));gains=sum([M1['macroAuc']>M1b['macroAuc'],M1['macroAp']>M1b['macroAp'],M1['logLoss']<M1b['logLoss']]);keep=bool(improve0 and gains>=2)
 joblib.dump({'version':'R4_MANAGEMENT_STACK_V4','researchOnly':True,'actionAuthority':False,'features':{'base':BASE,'progress':PROG,'full':FULL},'M0_model':m0,'M1_model':m1,'classes':C,'trainMarkets':trm,'holdoutMarkets':tem},MODEL)
 art={'version':'R4_MANAGEMENT_STACK_V4','status':'FROZEN_RESEARCH_STACK_CANDIDATE' if keep else 'FROZEN_RESEARCH_STACK_NOT_PROMOTED','researchOnly':True,'actionAuthority':False,'preRegistration':str(PRE.relative_to(ROOT)).replace('\\','/'),'coverage':{'markets':len(ms),'trainMarkets':len(trm),'holdoutMarkets':len(tem),'trainRows':int(len(tr0)),'holdoutRows':int(len(te0))},'features':{'base':BASE,'progress':PROG,'full':FULL},'holdout':{'M0_BASE':M0b,'M0_PROGRESS':M0,'M1_BASE_EBM':M1b,'M1_PROGRESS_EBM':M1},'deltas':{'M0_auc':M0['auc']-M0b['auc'],'M0_ap':M0['ap']-M0b['ap'],'M0_ll_improvement':M0b['logLoss']-M0['logLoss'],'M1_auc':M1['macroAuc']-M1b['macroAuc'],'M1_ap':M1['macroAp']-M1b['macroAp'],'M1_ll_improvement':M1b['logLoss']-M1['logLoss']},'fixedRulePassed':keep,'modelArtifact':str(MODEL.relative_to(ROOT)).replace('\\','/'),'guards':pre['guards']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(art,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
