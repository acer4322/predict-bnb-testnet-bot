from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
import test_r4_management_binary_transition_models_v1 as bt
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_observe_specialists_v1.json';F=base.FEATURES;SEED=26082796

def met(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def ebm(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=12,n_jobs=-2,random_state=seed)
def lgb(seed):return LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=25,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=seed,verbosity=-1,n_jobs=-1)
def main():
 base.seed_all(SEED);d=pd.read_csv(SRC);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);ms=base.market_order(d);device='cuda' if torch.cuda.is_available() else 'cpu';blocks=[]
 for bi,trm,tem in base.blocks(ms):
  tr_all=d[d.market_id.isin(trm)].copy();te_all=d[d.market_id.isin(tem)].copy();tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.isin(['HANDOFF_ALLOW','OBSERVE_NO_EVENT'])].copy().sort_values(['market_id','t']);te=te_all[(te_all.build_now==1)&te_all.management_label_5s.isin(['HANDOFF_ALLOW','OBSERVE_NO_EVENT'])].copy().sort_values(['market_id','t']);tr['handoff']=(tr.management_label_5s=='HANDOFF_ALLOW').astype(int);te['handoff']=(te.management_label_5s=='HANDOFF_ALLOW').astype(int)
  em=ebm(SEED+bi).fit(tr[F],tr.handoff);pe=em.predict_proba(te[F])[:,list(em.classes_).index(1)];lm=lgb(SEED+bi).fit(tr[F],tr.handoff);pl=lm.predict_proba(te[F])[:,list(lm.classes_).index(1)]
  mu,sd=base.fit_scaler(tr_all);xtr,_,_,_=base.build_seq(tr_all,tr,mu,sd);xte,_,_,_=base.build_seq(te_all,te,mu,sd);base.seed_all(SEED+bi+29);pt2,eps=bt.train_binary(base.TinyTransformer(len(F),2),xtr,tr.handoff.to_numpy(np.int64),xte,te.handoff.to_numpy(np.int64),device,epochs=30);pt=pt2[:,1]
  avg=(pe+pl+pt)/3;anchor=.5*pe+.5*pl;seq15=.425*pe+.425*pl+.15*pt
  rec={'block':bi,'testMarkets':len(tem),'rows':len(te),'epochs':eps,'EBM':met(te.handoff,pe),'LIGHTGBM':met(te.handoff,pl),'BINARY_TRANSFORMER':met(te.handoff,pt),'AVG3':met(te.handoff,avg),'ANCHOR50':met(te.handoff,anchor),'ANCHOR85_SEQ15':met(te.handoff,seq15)};blocks.append(rec);print(json.dumps(rec),flush=True)
 names=['EBM','LIGHTGBM','BINARY_TRANSFORMER','AVG3','ANCHOR50','ANCHOR85_SEQ15'];summary={}
 for n in names:
  q=[b[n] for b in blocks];summary[n]={'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'stdAuc':float(np.std([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q]))}
 art={'version':'R4_MANAGEMENT_HANDOFF_OBSERVE_SPECIALISTS_V1','researchOnly':True,'actionAuthority':False,'task':'conditional on TRANSITION: HANDOFF_ALLOW vs OBSERVE_NO_EVENT','coverage':{'markets':int(d.market_id.nunique()),'forwardTestMarkets':sum(b['testMarkets'] for b in blocks)},'summary':summary,'blocks':blocks,'guards':['Transition-only training rows.','Same chronological market blocks.','Strict causal sequence for Transformer.','No threshold/weight sweep.','Research only.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary},indent=2))
if __name__=='__main__':main()
