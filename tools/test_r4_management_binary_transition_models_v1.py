from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch
import torch.nn as nn
from torch.utils.data import DataLoader,TensorDataset
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_binary_transition_models_v1.json';F=base.FEATURES;SEED=26082774

def bmet(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def ebm(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def train_binary(model,xtr,ytr,xva,yva,device,epochs=30):
 model.to(device);counts=np.bincount(ytr,minlength=2).astype(np.float32);w=counts.sum()/np.maximum(counts,1);w=w/w.mean();lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device));opt=torch.optim.AdamW(model.parameters(),lr=1.5e-3,weight_decay=1e-4);dl=DataLoader(TensorDataset(torch.from_numpy(xtr),torch.from_numpy(ytr)),batch_size=128,shuffle=True,num_workers=0);best=None;best_loss=1e9;bad=0
 for ep in range(epochs):
  model.train()
  for xb,yb in dl:
   xb=xb.to(device);yb=yb.to(device);opt.zero_grad(set_to_none=True);loss=lossfn(model(xb),yb);loss.backward();nn.utils.clip_grad_norm_(model.parameters(),2.0);opt.step()
  model.eval()
  with torch.no_grad():pv=torch.softmax(model(torch.from_numpy(xva).to(device)),1).cpu().numpy()
  vl=log_loss(yva,np.clip(pv,1e-7,1-1e-7),labels=[0,1])
  if vl<best_loss-1e-4:best_loss=vl;best={k:v.detach().cpu().clone() for k,v in model.state_dict().items()};bad=0
  else:
   bad+=1
   if bad>=6:break
 if best is not None:model.load_state_dict(best)
 model.eval()
 with torch.no_grad():p=torch.softmax(model(torch.from_numpy(xva).to(device)),1).cpu().numpy()
 return p,ep+1
def main():
 base.seed_all(SEED);d=pd.read_csv(SRC);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);d['transition']=(d.management_label_5s.notna()&(d.management_label_5s!='')&(d.management_label_5s!='CONTINUE_WEAK')).astype(int)
 ms=base.market_order(d);device='cuda' if torch.cuda.is_available() else 'cpu';blocks=[]
 for bi,trm,tem in base.blocks(ms):
  tr_all=d[d.market_id.isin(trm)].copy();te_all=d[d.market_id.isin(tem)].copy();tr=tr_all[(tr_all.build_now==1)&tr_all.management_label_5s.notna()&(tr_all.management_label_5s!='')].copy();te=te_all[(te_all.build_now==1)&te_all.management_label_5s.notna()&(te_all.management_label_5s!='')].copy()
  tr_sorted=tr.sort_values(['market_id','t']);te_sorted=te.sort_values(['market_id','t'])
  em=ebm(SEED+bi).fit(tr[F],tr.transition);pe=em.predict_proba(te[F])[:,list(em.classes_).index(1)]
  lm=LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=SEED+bi,verbosity=-1,n_jobs=-1).fit(tr[F],tr.transition);pl=lm.predict_proba(te[F])[:,list(lm.classes_).index(1)]
  mu,sd=base.fit_scaler(tr_all);xtr,_,_,_=base.build_seq(tr_all,tr,mu,sd);xte,_,_,_=base.build_seq(te_all,te,mu,sd);ytr=tr_sorted.transition.to_numpy(np.int64);yte=te_sorted.transition.to_numpy(np.int64)
  base.seed_all(SEED+bi+29);pt2,eps=train_binary(base.TinyTransformer(len(F),2),xtr,ytr,xte,yte,device,epochs=30);pt=pt2[:,1]
  avg=(pe+pl+pt)/3;anchor=.5*pe+.5*pl;seq15=.425*pe+.425*pl+.15*pt
  rec={'block':bi,'testMarkets':len(tem),'testRows':len(te),'epochs':eps,'EBM':bmet(yte,pe),'LIGHTGBM':bmet(yte,pl),'BINARY_TRANSFORMER':bmet(yte,pt),'AVG3':bmet(yte,avg),'ANCHOR50':bmet(yte,anchor),'ANCHOR85_SEQ15':bmet(yte,seq15)};blocks.append(rec);print(json.dumps(rec),flush=True)
 names=['EBM','LIGHTGBM','BINARY_TRANSFORMER','AVG3','ANCHOR50','ANCHOR85_SEQ15'];summary={}
 for n in names:
  q=[b[n] for b in blocks];summary[n]={'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'stdAuc':float(np.std([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q]))}
 art={'version':'R4_MANAGEMENT_BINARY_TRANSITION_MODELS_V1','researchOnly':True,'actionAuthority':False,'task':'current BUILD: CONTINUE_WEAK vs TRANSITION(HANDOFF or OBSERVE)','coverage':{'markets':int(d.market_id.nunique()),'forwardTestMarkets':sum(b['testMarkets'] for b in blocks)},'summary':summary,'blocks':blocks,'guards':['Same chronological blocks/features as management benchmark.','Binary Transformer trained directly on transition target; not derived from multiclass output.','Strict same-market causal sequence.','No threshold/weight sweep.','Research only.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':summary},indent=2))
if __name__=='__main__':main()
