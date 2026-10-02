from __future__ import annotations
import json, random, sys
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss,balanced_accuracy_score,recall_score
from lightgbm import LGBMClassifier
import torch, torch.nn as nn
from torch.utils.data import DataLoader,TensorDataset

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tools'))
import train_r4_management_model_benchmark_v1 as base
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_large300_v1_rows.csv'
PREREG=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_attention_v0_preregistered.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_attention_v0.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_handoff_attention_v0.pt'
F=base.FEATURES; SEQ=16; SEED=26082771

def seed(s):
 random.seed(s);np.random.seed(s);torch.manual_seed(s)
 if torch.cuda.is_available():torch.cuda.manual_seed_all(s)

def label(df):return (df.management_label_5s.astype(str)=='HANDOFF_ALLOW').astype(int)
def elig(df):return df[(df.build_now==1)&df.management_label_5s.isin(['CONTINUE_WEAK','HANDOFF_ALLOW'])].copy()
def met(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);pred=(p>=.5).astype(int);rec=recall_score(y,pred,labels=[0,1],average=None,zero_division=0)
 return {'n':int(len(y)),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1])),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'continueRecall':float(rec[0]),'handoffRecall':float(rec[1])}

class TF(nn.Module):
 def __init__(self,nf):
  super().__init__();d=64;self.inp=nn.Linear(nf,d);self.pos=nn.Parameter(torch.zeros(1,SEQ,d));layer=nn.TransformerEncoderLayer(d_model=d,nhead=4,dim_feedforward=128,dropout=.1,batch_first=True,norm_first=True);self.enc=nn.TransformerEncoder(layer,2);self.norm=nn.LayerNorm(d);self.head=nn.Linear(d,2)
 def forward(self,x):
  z=self.inp(x)+self.pos[:,:x.shape[1]];z=self.enc(z);return self.head(self.norm(z[:,-1]))

def scaler(df):
 x=df[F].to_numpy(np.float32);mu=np.nanmean(x,0);sd=np.nanstd(x,0);sd=np.where(sd<1e-6,1.,sd);return mu.astype(np.float32),sd.astype(np.float32)
def seqs(all_d,eligible,mu,sd):
 xs=[];ys=[];idxset=set(eligible.index.tolist())
 for mid,g in all_d.groupby('market_id',sort=False):
  g=g.sort_values('t');arr=((g[F].to_numpy(np.float32)-mu)/sd);idxs=g.index.to_numpy()
  for j,idx in enumerate(idxs):
   if idx not in idxset:continue
   lo=max(0,j-SEQ+1);s=arr[lo:j+1]
   if len(s)<SEQ:s=np.concatenate([np.repeat(s[:1],SEQ-len(s),0),s],0)
   xs.append(s);ys.append(1 if str(all_d.at[idx,'management_label_5s'])=='HANDOFF_ALLOW' else 0)
 return np.asarray(xs,np.float32),np.asarray(ys,np.int64)
def train_tf(model,xtr,ytr,xva,yva,device):
 model.to(device);cnt=np.bincount(ytr,minlength=2).astype(float);w=cnt.sum()/np.maximum(cnt,1);w=w/w.mean();lossfn=nn.CrossEntropyLoss(weight=torch.tensor(w,dtype=torch.float32,device=device));opt=torch.optim.AdamW(model.parameters(),lr=1.5e-3,weight_decay=1e-4);dl=DataLoader(TensorDataset(torch.from_numpy(xtr),torch.from_numpy(ytr)),batch_size=128,shuffle=True,num_workers=0);best=None;bestll=1e9;bad=0
 for ep in range(28):
  model.train()
  for xb,yb in dl:
   xb=xb.to(device);yb=yb.to(device);opt.zero_grad(set_to_none=True);ls=lossfn(model(xb),yb);ls.backward();nn.utils.clip_grad_norm_(model.parameters(),2.);opt.step()
  model.eval()
  with torch.no_grad():p=torch.softmax(model(torch.from_numpy(xva).to(device)),1)[:,1].cpu().numpy()
  ll=log_loss(yva,np.clip(p,1e-7,1-1e-7),labels=[0,1])
  if ll<bestll-1e-4:bestll=ll;best={k:v.detach().cpu().clone() for k,v in model.state_dict().items()};bad=0
  else:
   bad+=1
   if bad>=6:break
 if best:model.load_state_dict(best)
 return ep+1
def pred_tf(model,x,device):
 model.eval()
 with torch.no_grad():return torch.softmax(model(torch.from_numpy(x).to(device)),1)[:,1].cpu().numpy()

def main():
 pre=json.loads(PREREG.read_text(encoding='utf-8'));seed(SEED);d=pd.read_csv(SRC).replace([np.inf,-np.inf],np.nan);d=d[(d.seconds_left>=60)&(d.seconds_left<=300)].dropna(subset=F).copy().sort_values(['market_id','t']).reset_index(drop=True);ms=base.market_order(d);bs=base.blocks(ms);device='cuda' if torch.cuda.is_available() else 'cpu';blocks=[];last_model=None
 for bi,trm,tem in bs:
  trm=list(trm);cut=max(1,int(len(trm)*.85));fitm=trm[:cut];valm=trm[cut:]
  fitall=d[d.market_id.isin(fitm)].copy();valall=d[d.market_id.isin(valm)].copy();teall=d[d.market_id.isin(tem)].copy();fit=elig(fitall);val=elig(valall);te=elig(teall)
  yte=label(te).to_numpy();yfit=label(fit).to_numpy()
  lm=LGBMClassifier(objective='binary',n_estimators=300,learning_rate=.035,num_leaves=15,max_depth=5,min_child_samples=35,subsample=.9,colsample_bytree=.9,reg_lambda=1.,random_state=SEED+bi,verbosity=-1,n_jobs=-1).fit(fit[F],yfit);pl=lm.predict_proba(te[F])[:,1]
  mu,sd=scaler(fitall);xtr,ytr=seqs(fitall,fit,mu,sd);xva,yva=seqs(valall,val,mu,sd);xte,ytf=seqs(teall,te,mu,sd);seed(SEED+bi+20);tm=TF(len(F));eps=train_tf(tm,xtr,ytr,xva,yva,device);pt=pred_tf(tm,xte,device)
  if len(yte)!=len(pt):raise RuntimeError('alignment mismatch')
  b={'block':bi,'fitMarkets':len(fitm),'valMarkets':len(valm),'testMarkets':len(tem),'fitRows':int(len(fit)),'testRows':int(len(te)),'epochs':eps,'STATE_BASELINE':met(yte,pl),'LIFECYCLE_ATTENTION':met(yte,pt)};blocks.append(b);last_model=(tm,mu,sd);print(json.dumps({'block':bi,'state':b['STATE_BASELINE'],'lifecycle':b['LIFECYCLE_ATTENTION']},ensure_ascii=False),flush=True)
 def summ(n):
  q=[b[n] for b in blocks];return {'meanAuc':float(np.mean([x['auc'] for x in q])),'worstAuc':float(np.min([x['auc'] for x in q])),'meanAp':float(np.mean([x['ap'] for x in q])),'meanLogLoss':float(np.mean([x['logLoss'] for x in q])),'meanBalancedAccuracy':float(np.mean([x['balancedAccuracy'] for x in q])),'meanContinueRecall':float(np.mean([x['continueRecall'] for x in q])),'meanHandoffRecall':float(np.mean([x['handoffRecall'] for x in q])),'allAucAboveHalf':bool(all(x['auc']>.5 for x in q))}
 s={'STATE_BASELINE':summ('STATE_BASELINE'),'LIFECYCLE_ATTENTION':summ('LIFECYCLE_ATTENTION')};a=s['STATE_BASELINE'];t=s['LIFECYCLE_ATTENTION'];keep=(t['allAucAboveHalf'] and t['meanAuc']>=a['meanAuc']-.03 and t['meanAp']>=a['meanAp']-.01 and t['meanHandoffRecall']>=a['meanHandoffRecall']+.10 and t['meanBalancedAccuracy']>=a['meanBalancedAccuracy'])
 art={'version':'R4_MANAGEMENT_HANDOFF_ATTENTION_V0','researchOnly':True,'actionAuthority':False,'preRegistration':str(PREREG.relative_to(ROOT)).replace('\\','/'),'coverage':{'markets':int(d.market_id.nunique()),'outerBlocks':len(blocks),'device':device},'summary':s,'blocks':blocks,'status':'TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED','fixedRulePassed':bool(keep),'role':'Parallel HANDOFF_ATTENTION belief; primary manager probabilities unchanged.','guards':pre['guards']};OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
 if keep and last_model is not None:
  tm,mu,sd=last_model;torch.save({'version':'R4_MANAGEMENT_HANDOFF_ATTENTION_V0','state_dict':tm.state_dict(),'features':F,'seqLen':SEQ,'mu':mu,'sd':sd},MODEL)
 print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':art['status'],'summary':s},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
