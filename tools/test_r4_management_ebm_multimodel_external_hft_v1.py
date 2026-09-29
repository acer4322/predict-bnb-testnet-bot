from __future__ import annotations
import json, sys, random
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch, torch.nn as nn
from torch.utils.data import DataLoader,TensorDataset
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
HFT=[ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_fresh24_v1_rows.csv',ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv',ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_replication3_v1_rows.csv',ROOT/'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_v1_rows.csv']
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_ebm_multimodel_external_hft_v1.json'
F=base.FEATURES; C=base.CLASSES; SEED=26082741; SEQ=base.SEQ_LEN

def ebm(): return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=SEED)
def score(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}
def seq_arrays(df,mu,sd):
 xs=[]; idx=[]
 groupcols=['source','marketId'] if 'source' in df else ['market_id']
 for _,g in df.groupby(groupcols,sort=False):
  g=g.sort_values('t'); arr=((g[F].to_numpy(np.float32)-mu)/sd); inds=g.index.to_numpy()
  for j,i in enumerate(inds):
   lo=max(0,j-SEQ+1); s=arr[lo:j+1]
   if len(s)<SEQ:s=np.concatenate([np.repeat(s[:1],SEQ-len(s),axis=0),s],axis=0)
   xs.append(s);idx.append(i)
 return np.asarray(xs,np.float32),idx
def train_tf_fixed(x,y,epochs=10):
 device='cuda' if torch.cuda.is_available() else 'cpu'; base.seed_all(SEED); m=base.TinyTransformer(len(F),len(C)).to(device)
 cnt=np.bincount(y,minlength=3).astype(np.float32); w=cnt.sum()/np.maximum(cnt,1);w=w/w.mean(); lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device));opt=torch.optim.AdamW(m.parameters(),lr=1.5e-3,weight_decay=1e-4)
 dl=DataLoader(TensorDataset(torch.from_numpy(x),torch.from_numpy(y)),batch_size=128,shuffle=True,num_workers=0)
 for _ in range(epochs):
  m.train()
  for xb,yb in dl:
   xb=xb.to(device);yb=yb.to(device);opt.zero_grad(set_to_none=True);loss=lossfn(m(xb),yb);loss.backward();nn.utils.clip_grad_norm_(m.parameters(),2.0);opt.step()
 return m,device
def main():
 td=pd.read_csv(TARGET);td=td[(td.seconds_left>=60)&(td.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True); tr=td[(td.build_now==1)&td.management_label_5s.notna()&(td.management_label_5s!='')].copy()
 em=ebm().fit(tr[F],tr.management_label_5s)
 lm=LGBMClassifier(objective='multiclass',num_class=3,n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.0,random_state=SEED,verbosity=-1,n_jobs=-1).fit(tr[F],tr.management_label_5s)
 mu,sd=base.fit_scaler(td); xtr,ytr,_,_=base.build_seq(td,tr,mu,sd); tm,device=train_tf_fixed(xtr,ytr,10)
 rows=[]
 for p in HFT:
  if p.exists():
   z=pd.read_csv(p);z['source']=p.stem;rows.append(z)
 hd=pd.concat(rows,ignore_index=True); keys=['marketId','t','side','kind'];hd=hd.sort_values(keys).drop_duplicates(keys,keep='last').copy();hd=hd[(hd.seconds_left>=60)&(hd.seconds_left<=300)&(hd.build_now==1)].dropna(subset=F).copy().reset_index(drop=True)
 pe0=em.predict_proba(hd[F]); eo=list(em.classes_); pe=np.column_stack([pe0[:,eo.index(c)] for c in C]); pl0=lm.predict_proba(hd[F]);lo=list(lm.classes_);pl=np.column_stack([pl0[:,lo.index(c)] for c in C])
 xh,idx=seq_arrays(hd,mu,sd);tm.eval();
 with torch.no_grad(): pt=torch.softmax(tm(torch.from_numpy(xh).to(device)),1).cpu().numpy()
 # seq order is group order; place back on original row indices
 ptmap=np.zeros_like(pt)
 for j,i in enumerate(idx):ptmap[i]=pt[j]
 ci=C.index('CONTINUE_WEAK'); cand={'EBM':pe[:,ci],'LIGHTGBM':pl[:,ci],'TINY_TRANSFORMER':ptmap[:,ci],'AVG3':((pe+pl+ptmap)/3)[:,ci],'TRI_BLEND_25_45_30':(.25*pe+.45*pl+.30*ptmap)[:,ci]}
 targets=['futureWeakMakerFill5s','floorImproved5s','absNetReduced5s'];out={'version':'R4_MANAGEMENT_EBM_MULTIMODEL_EXTERNAL_HFT_V1','researchOnly':True,'actionAuthority':False,'training':{'targetMarkets':int(td.market_id.nunique()),'eligibleRows':int(len(tr)),'transformerEpochsFixed':10},'coverage':{'hftRows':int(len(hd)),'hftMarkets':int(hd.marketId.nunique()),'sources':sorted(hd.source.unique().tolist())},'targets':{},'device':device}
 for target in targets:
  q=hd[target].notna();y=hd.loc[q,target].astype(int).to_numpy();out['targets'][target]={k:score(y,v[q.to_numpy()]) for k,v in cand.items()}
  baseS=out['targets'][target]['EBM'];
  for k in ['AVG3','TRI_BLEND_25_45_30']:
   m=out['targets'][target][k];m['vsEBM']={'aucDelta':m['auc']-baseS['auc'],'apDelta':m['ap']-baseS['ap'],'logLossImprovement':baseS['logLoss']-m['logLoss']}
 out['guards']=['Target-trained management experts only; HFT outcomes external scoring only.','No weight sweep; blends frozen before scoring.','No runtime/action modification.','HFT sequences current-and-past market-local only.']
 OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
