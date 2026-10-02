from __future__ import annotations
import json,sys
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
from lightgbm import LGBMClassifier
from interpret.glassbox import ExplainableBoostingClassifier
import torch,torch.nn as nn
from torch.utils.data import DataLoader,TensorDataset
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
TARGET=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
HFT=[ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_fresh24_v1_rows.csv',ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_unseen24_v1_rows.csv',ROOT/'data/research/r4_v0/hourly/r4_management_hft_shadow_replication3_v1_rows.csv',ROOT/'data/research/r4_v0/hourly/r4_management_m0_phase_routing_replication4_v1_rows.csv']
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_specialist_risk_transfer_hft_v1.json'
ROWS=ROOT/'data/research/r4_v0/hourly/r4_management_specialist_risk_transfer_hft_v1_rows.csv'
F=base.FEATURES;SEQ=base.SEQ_LEN;SEED=26082797

def ebm(seed):return ExplainableBoostingClassifier(feature_names=F,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=16,n_jobs=-2,random_state=seed)
def seq_arrays(df,eligible,mu,sd,target):
 xs=[];ys=[];mids=[];times=[];idxset=set(eligible.index.tolist())
 group='market_id' if 'market_id' in df.columns else 'marketId'
 for mid,g in df.groupby(group,sort=False):
  g=g.sort_values('t');arr=((g[F].to_numpy(np.float32)-mu)/sd);inds=g.index.to_numpy()
  for j,i in enumerate(inds):
   if i not in idxset:continue
   s=arr[max(0,j-SEQ+1):j+1]
   if len(s)<SEQ:s=np.concatenate([np.repeat(s[:1],SEQ-len(s),axis=0),s],axis=0)
   xs.append(s);ys.append(int(df.at[i,target]) if target in df.columns else 0);mids.append(int(mid));times.append(int(df.at[i,'t']))
 return np.asarray(xs,np.float32),np.asarray(ys,np.int64),mids,times
def seq_score_all(df,mu,sd):
 xs=[];idx=[]
 for _,g in df.groupby(['source','marketId'],sort=False):
  g=g.sort_values('t');arr=((g[F].to_numpy(np.float32)-mu)/sd);inds=g.index.to_numpy()
  for j,i in enumerate(inds):
   s=arr[max(0,j-SEQ+1):j+1]
   if len(s)<SEQ:s=np.concatenate([np.repeat(s[:1],SEQ-len(s),axis=0),s],axis=0)
   xs.append(s);idx.append(i)
 return np.asarray(xs,np.float32),idx
def train_tf(x,y,seed,device,epochs=12):
 base.seed_all(seed);m=base.TinyTransformer(len(F),2).to(device);cnt=np.bincount(y,minlength=2).astype(np.float32);w=cnt.sum()/np.maximum(cnt,1);w=w/w.mean();lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device));opt=torch.optim.AdamW(m.parameters(),lr=1.5e-3,weight_decay=1e-4);dl=DataLoader(TensorDataset(torch.from_numpy(x),torch.from_numpy(y)),batch_size=128,shuffle=True,num_workers=0)
 for _ in range(epochs):
  m.train()
  for xb,yb in dl:
   xb=xb.to(device);yb=yb.to(device);opt.zero_grad(set_to_none=True);loss=lossfn(m(xb),yb);loss.backward();nn.utils.clip_grad_norm_(m.parameters(),2.0);opt.step()
 return m
def score(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7)
 return {'n':int(len(y)),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}
def qlift(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);q1=np.quantile(p,.25);q3=np.quantile(p,.75);lo=y[p<=q1];hi=y[p>=q3]
 return {'bottomQuartileN':int(len(lo)),'bottomQuartileRate':float(lo.mean()) if len(lo) else None,'topQuartileN':int(len(hi)),'topQuartileRate':float(hi.mean()) if len(hi) else None,'topMinusBottom':float(hi.mean()-lo.mean()) if len(lo) and len(hi) else None}
def main():
 device='cuda' if torch.cuda.is_available() else 'cpu';base.seed_all(SEED)
 td=pd.read_csv(TARGET);td=td[(td.seconds_left>=60)&(td.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);td['transition']=(td.management_label_5s.notna()&(td.management_label_5s!='')&(td.management_label_5s!='CONTINUE_WEAK')).astype(int);td['handoff']=(td.management_label_5s=='HANDOFF_ALLOW').astype(int);tr=td[(td.build_now==1)&td.management_label_5s.notna()&(td.management_label_5s!='')].copy();tr2=tr[tr.transition==1].copy()
 # transition specialists
 et=ebm(SEED).fit(tr[F],tr.transition);lt=LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=SEED,verbosity=-1,n_jobs=-1).fit(tr[F],tr.transition)
 # conditional handoff specialists
 eh=ebm(SEED+1).fit(tr2[F],tr2.handoff);lh=LGBMClassifier(n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=28,subsample=.9,colsample_bytree=.9,reg_lambda=1,random_state=SEED+1,verbosity=-1,n_jobs=-1).fit(tr2[F],tr2.handoff)
 mu,sd=base.fit_scaler(td);xt,yt,_,_=seq_arrays(td,tr,mu,sd,'transition');xh,yh,_,_=seq_arrays(td,tr2,mu,sd,'handoff');tt=train_tf(xt,yt,SEED+31,device);th=train_tf(xh,yh,SEED+61,device)
 rows=[]
 for p in HFT:
  if p.exists():z=pd.read_csv(p);z['source']=p.stem;rows.append(z)
 hd=pd.concat(rows,ignore_index=True);keys=[c for c in ['marketId','t','side','kind'] if c in hd.columns];hd=hd.sort_values(keys).drop_duplicates(keys,keep='last');hd=hd[(hd.seconds_left>=60)&(hd.seconds_left<=300)&(hd.build_now==1)].dropna(subset=F).copy().reset_index(drop=True)
 peT=et.predict_proba(hd[F])[:,list(et.classes_).index(1)];plT=lt.predict_proba(hd[F])[:,list(lt.classes_).index(1)];peH=eh.predict_proba(hd[F])[:,list(eh.classes_).index(1)];plH=lh.predict_proba(hd[F])[:,list(lh.classes_).index(1)]
 xs,idx=seq_score_all(hd,mu,sd);tt.eval();th.eval()
 with torch.no_grad():pt_raw=torch.softmax(tt(torch.from_numpy(xs).to(device)),1).cpu().numpy()[:,1];ph_raw=torch.softmax(th(torch.from_numpy(xs).to(device)),1).cpu().numpy()[:,1]
 pt=np.zeros(len(hd));ph=np.zeros(len(hd))
 for j,i in enumerate(idx):pt[i]=pt_raw[j];ph[i]=ph_raw[j]
 hd['p_transition_ebm']=peT;hd['p_transition_lgb']=plT;hd['p_transition_tf']=pt;hd['p_transition_avg3']=(peT+plT+pt)/3;hd['p_handoff_ebm']=peH;hd['p_handoff_lgb']=plH;hd['p_handoff_tf']=ph;hd['p_handoff_avg3']=(peH+plH+ph)/3;hd['risk_observe_joint']=hd.p_transition_avg3*(1-hd.p_handoff_avg3);hd['risk_handoff_joint']=hd.p_transition_avg3*hd.p_handoff_avg3
 # Risk labels are inverses of economically desirable next-5s outcomes.
 pos={'weakFillFailure5s':'futureWeakMakerFill5s','floorFailure5s':'floorImproved5s','absNetFailure5s':'absNetReduced5s'}
 for new,src in pos.items():hd[new]=np.where(hd[src].notna(),1-hd[src].astype(float),np.nan)
 hd.to_csv(ROWS,index=False)
 signals=['p_transition_ebm','p_transition_lgb','p_transition_tf','p_transition_avg3','risk_observe_joint','risk_handoff_joint']
 out={'version':'R4_MANAGEMENT_SPECIALIST_RISK_TRANSFER_HFT_V1','researchOnly':True,'actionAuthority':False,'training':{'targetMarkets':int(td.market_id.nunique()),'eligibleM1Rows':int(len(tr)),'transitionRows':int(len(tr2)),'transformerEpochsFixed':12},'coverage':{'hftRows':int(len(hd)),'hftMarkets':int(hd.marketId.nunique()),'sources':sorted(hd.source.unique().tolist())},'targets':{},'perSource':{},'device':device}
 for target in pos:
  q=hd[target].notna();y=hd.loc[q,target].astype(int).to_numpy();out['targets'][target]={}
  for s in signals:
   p=hd.loc[q,s].to_numpy(float);out['targets'][target][s]={**score(y,p),'quartileLift':qlift(y,p)}
 for source,g in hd.groupby('source'):
  so={'rows':int(len(g)),'markets':int(g.marketId.nunique()),'targets':{}}
  for target in pos:
   q=g[target].notna();y=g.loc[q,target].astype(int).to_numpy();so['targets'][target]={}
   if len(y)<20 or len(np.unique(y))<2:continue
   for s in ['p_transition_avg3','risk_observe_joint','risk_handoff_joint']:so['targets'][target][s]=score(y,g.loc[q,s].to_numpy(float))
  out['perSource'][source]=so
 out['guards']=['Specialists trained only on Target management labels.','HFT future 5s outcomes are external scoring labels only and never training/runtime inputs.','No threshold/weight sweep.','Sequence inference uses current-and-past same-market HFT rows only.','Research only; no action authority.'];out['rowsArtifact']=str(ROWS.relative_to(ROOT)).replace('\\','/');OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
