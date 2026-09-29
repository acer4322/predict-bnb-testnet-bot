from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
HFT=ROOT/'data/research/r4_v0/hourly/r4_management_specialist_risk_transfer_hft_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_transition_incremental_hft_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_transition_incremental_hft_v1_rows.csv'
F=base.FEATURES;C=base.CLASSES;SEED=26082801

def ebm(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def score(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);return {'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'n':int(len(y)),'rate':float(y.mean())}
def qlift(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);a=np.quantile(p,.25);b=np.quantile(p,.75);lo=y[p<=a];hi=y[p>=b];return {'bottomRate':float(lo.mean()),'topRate':float(hi.mean()),'topMinusBottom':float(hi.mean()-lo.mean())}
def main():
 td=pd.read_csv(TARGET);td=td[(td.seconds_left>=60)&(td.seconds_left<=300)].dropna(subset=F).copy();tr=td[(td.build_now==1)&td.management_label_5s.notna()&(td.management_label_5s!='')].copy()
 em=ebm(SEED).fit(tr[F],tr.management_label_5s);lm=LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=SEED,verbosity=-1,n_jobs=-1).fit(tr[F],tr.management_label_5s)
 h=pd.read_csv(HFT).dropna(subset=F).copy().reset_index(drop=True);ci=C.index('CONTINUE_WEAK')
 pe0=em.predict_proba(h[F]);eo=list(em.classes_);pe=np.column_stack([pe0[:,eo.index(c)] for c in C]);pl0=lm.predict_proba(h[F]);lo=list(lm.classes_);pl=np.column_stack([pl0[:,lo.index(c)] for c in C])
 h['multi_risk_ebm']=1-pe[:,ci];h['multi_risk_lgb']=1-pl[:,ci];h['multi_risk_anchor']=(h.multi_risk_ebm+h.multi_risk_lgb)/2
 h['binary_risk_anchor']=(h.p_transition_ebm+h.p_transition_lgb)/2
 h['fused_anchor_50']=.5*h.multi_risk_anchor+.5*h.binary_risk_anchor
 h['fused_ebm_50']=.5*h.multi_risk_ebm+.5*h.p_transition_ebm
 h['fused_lgb_50']=.5*h.multi_risk_lgb+.5*h.p_transition_lgb
 targets=['weakFillFailure5s','floorFailure5s','absNetFailure5s'];signals=['multi_risk_ebm','p_transition_ebm','fused_ebm_50','multi_risk_lgb','p_transition_lgb','fused_lgb_50','multi_risk_anchor','binary_risk_anchor','fused_anchor_50']
 out={'version':'R4_MANAGEMENT_TRANSITION_INCREMENTAL_HFT_V1','researchOnly':True,'actionAuthority':False,'coverage':{'rows':int(len(h)),'markets':int(h.marketId.nunique())},'targets':{},'perSource':{}}
 for t in targets:
  q=h[t].notna();y=h.loc[q,t].astype(int).to_numpy();out['targets'][t]={}
  for s in signals:out['targets'][t][s]={**score(y,h.loc[q,s]),'quartileLift':qlift(y,h.loc[q,s])}
 for src,g in h.groupby('source'):
  so={'rows':int(len(g)),'markets':int(g.marketId.nunique()),'targets':{}}
  for t in targets:
   q=g[t].notna();y=g.loc[q,t].astype(int).to_numpy();so['targets'][t]={}
   if len(np.unique(y))<2:continue
   for s in ['multi_risk_anchor','binary_risk_anchor','fused_anchor_50']:so['targets'][t][s]=score(y,g.loc[q,s])
  out['perSource'][src]=so
 # Fixed no-sweep pass: fused anchor must improve anchor mean AUC over all three outcomes and not worsen any outcome AUC.
 ds=[]
 for t in targets:
  b=out['targets'][t]['multi_risk_anchor'];f=out['targets'][t]['fused_anchor_50'];ds.append(f['auc']-b['auc'])
 out['fusedAnchorVsMulticlass']={'aucDeltas':dict(zip(targets,ds)),'meanAucDelta':float(np.mean(ds)),'allNonNegative':bool(all(x>=0 for x in ds))}
 out['decision']='KEEP_BINARY_TRANSITION_INCREMENTAL_HFT' if out['fusedAnchorVsMulticlass']['meanAucDelta']>0 and out['fusedAnchorVsMulticlass']['allNonNegative'] else 'NO_INCREMENTAL_PROMOTION'
 h.to_csv(ROWS,index=False)
 out['rowsArtifact']=str(ROWS.relative_to(ROOT)).replace('\\','/')
 out['guards']=['Both multiclass and binary transition models trained only on Target labels.','Same HFT rows and failure outcomes; future HFT labels used only for scoring.','50/50 fusion fixed a priori; no weight sweep.','Research only; no runtime modification.'];OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
