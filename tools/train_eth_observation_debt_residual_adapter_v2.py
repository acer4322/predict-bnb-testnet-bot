from __future__ import annotations
import argparse,json,os
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
import numpy as np
import torch, torch.nn as nn
from sklearn.metrics import roc_auc_score,average_precision_score,balanced_accuracy_score
import tools.train_eth_observation_debt_lifecycle_memory_v1 as v1
SEED=v1.SEED; TASKS=v1.TASKS; MAX_LAG=v1.MAX_LAG

class ResidualAdapter(nn.Module):
 def __init__(self,use_debt:bool):
  super().__init__();self.use_debt=use_debt
  d=len(v1.CUR_FEATURES)+1+(len(v1.DEBT_FEATURES) if use_debt else 1)
  self.h=nn.ModuleDict({t:nn.Sequential(nn.Linear(d,48),nn.ReLU(),nn.LayerNorm(48),nn.Linear(48,24),nn.ReLU(),nn.Linear(24,1)) for t in TASKS})
 def forward(self,cur,base_logit,debt,lag,task):
  x=torch.cat([cur,base_logit[:,None],debt if self.use_debt else lag[:,None]],1)
  return base_logit + lag*self.h[task](x).squeeze(-1)

def freeze(m):
 for p in m.parameters():p.requires_grad=False
 m.eval();return m

def pool(rows,task):
 z,X,S,M,D,y=v1.arrays(rows,task,None);lag=np.asarray([r['lag']/MAX_LAG for r in z],np.float32);return z,X,S,M,D,y,lag

def train_adapter(adapter,base,rows,dev,use_debt):
 adapter.to(dev);base=freeze(base.to(dev));opt=torch.optim.AdamW(adapter.parameters(),lr=1.2e-3,weight_decay=2e-4);rng=np.random.default_rng(SEED);src=[r for r in rows if r['lag']>0];pools={t:pool(src,t) for t in TASKS}
 for ep in range(12):
  for _ in range(150):
   opt.zero_grad();L=0.
   for t in TASKS:
    z,X,S,M,D,y,lag=pools[t];idx=rng.integers(0,len(y),size=min(384,len(y)));xt=torch.from_numpy(X[idx]).to(dev);st=torch.from_numpy(S[idx]).to(dev);mt=torch.from_numpy(M[idx]).to(dev);dt=torch.from_numpy(D[idx]).to(dev);lt=torch.from_numpy(lag[idx]).to(dev);yt=torch.from_numpy(y[idx]).to(dev)
    with torch.no_grad():bl=base(xt,st,mt,t)
    log=adapter(xt,bl,dt,lt,t);pos=max(float(yt.mean()),1e-4);L+=nn.functional.binary_cross_entropy_with_logits(log,yt,pos_weight=torch.tensor((1-pos)/pos,device=dev).clamp(.25,4))
   L.backward();torch.nn.utils.clip_grad_norm_(adapter.parameters(),5);opt.step()
 return adapter

def met(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);return {'n':len(y),'positiveRate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'averagePrecision':float(average_precision_score(y,p)),'balancedAccuracy':float(balanced_accuracy_score(y,pred))}

def predict(base,adapter,rows,task,lag_value,dev):
 z,X,S,M,D,y=v1.arrays(rows,task,lag_value);lag=np.asarray([r['lag']/MAX_LAG for r in z],np.float32);ps=[];base.eval();adapter.eval()
 with torch.no_grad():
  for i in range(0,len(y),4096):
   xt=torch.from_numpy(X[i:i+4096]).to(dev);st=torch.from_numpy(S[i:i+4096]).to(dev);mt=torch.from_numpy(M[i:i+4096]).to(dev);dt=torch.from_numpy(D[i:i+4096]).to(dev);lt=torch.from_numpy(lag[i:i+4096]).to(dev);bl=base(xt,st,mt,task);log=adapter(xt,bl,dt,lt,task);ps.append(torch.sigmoid(log).cpu().numpy())
 return z,y,np.concatenate(ps)

def evaluate(base,adapter,rows,dev):
 out={};preds={}
 for lag in range(MAX_LAG+1):
  out[str(lag)]={}
  for t in TASKS:
   z,y,p=predict(base,adapter,rows,t,lag,dev);out[str(lag)][t]=met(y,p);preds[(lag,t)]={r['row_id']:float(q) for r,q in zip(z,p)}
 drift={}
 for t in TASKS:
  a=preds[(0,t)];b=preds[(MAX_LAG,t)];ids=sorted(set(a)&set(b));drift[t]=float(np.mean([abs(a[i]-b[i]) for i in ids]))
 return out,drift,preds

def eval_base_clean(base,rows,dev):
 out={};preds={};base.eval()
 with torch.no_grad():
  for t in TASKS:
   z,X,S,M,D,y=v1.arrays(rows,t,0);ps=[]
   for i in range(0,len(y),4096):ps.append(torch.sigmoid(base(torch.from_numpy(X[i:i+4096]).to(dev),torch.from_numpy(S[i:i+4096]).to(dev),torch.from_numpy(M[i:i+4096]).to(dev),t)).cpu().numpy())
   p=np.concatenate(ps);out[t]=met(y,p);preds[t]={r['row_id']:float(q) for r,q in zip(z,p)}
 return out,preds

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-out',required=True);a=ap.parse_args();rows,cut,nwin,base_n=v1.build(a.db);tr=[r for r in rows if r['end']<cut];te=[r for r in rows if r['end']>=cut];stats=v1.standardize(tr,te);dev='cuda' if torch.cuda.is_available() else 'cpu'
 torch.manual_seed(SEED);base=v1.train_model(v1.Memory(),tr,dev,False,True);freeze(base)
 torch.manual_seed(SEED);nod=train_adapter(ResidualAdapter(False),base,tr,dev,False);torch.manual_seed(SEED);deb=train_adapter(ResidualAdapter(True),base,tr,dev,True)
 mb,base_preds=eval_base_clean(base,te,dev);mn,dn,pn=evaluate(base,nod,te,dev);md,dd,pd=evaluate(base,deb,te,dev)
 lag3={t:md[str(MAX_LAG)][t]['auc']-mn[str(MAX_LAG)][t]['auc'] for t in TASKS};drift_gain={t:dn[t]-dd[t] for t in TASKS}
 clean_maxdiff={}
 for t in TASKS:
  ids=sorted(set(base_preds[t])&set(pd[(0,t)]));clean_maxdiff[t]=float(max(abs(base_preds[t][i]-pd[(0,t)][i]) for i in ids)) if ids else None
 passed=sum(v>=.015 for v in lag3.values())>=2 and min(lag3.values())>=-.01 and sum(v>0 for v in drift_gain.values())>=2 and all((v is not None and v<1e-7) for v in clean_maxdiff.values())
 out={'version':'ETH_OBSERVATION_DEBT_RESIDUAL_ADAPTER_V2','researchOnly':True,'device':dev,'sourceDb':os.path.abspath(a.db),'chronologyCutoff':cut,'windows':nwin,'baseDecisionRows':base_n,'expandedRows':len(rows),'trainRows':len(tr),'testRows':len(te),'models':{'FROZEN_CLEAN_GRU_LAG0':mb,'LAG_RESIDUAL_NO_DEBT':mn,'DEBT_RESIDUAL':md},'predictionDriftCleanVsLag3':{'noDebtResidual':dn,'debtResidual':dd},'lag3AucDeltaDebtMinusNoDebt':lag3,'driftReductionDebtVsNoDebt':drift_gain,'lag0MaxProbabilityDiffDebtVsFrozenBase':clean_maxdiff,'robustnessPass':bool(passed),'passRule':'lag3 debt residual >=+0.015 AUC on >=2/3 vs no-debt residual, none <-0.01; drift improves >=2/3; lag0 exactly frozen-base','boundary':['ETH-only labels/gradients','BTC/ETH cross-analysis selected architecture only','Residual correction multiplied by debt-presence lag factor and is exactly zero at lag0','No winner/future PnL','No TARGET_UNIT=18 or expected_parent_shares','Must pass realistic-HFT Repair Functional Exam before promotion']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');mu,sd,tmu,tsd,dmu,dsd=stats;torch.save({'version':out['version'],'currentFeatures':v1.CUR_FEATURES,'tokenFeatures':v1.TOK_FEATURES,'debtFeatures':v1.DEBT_FEATURES,'mu':mu,'sd':sd,'tmu':tmu,'tsd':tsd,'dmu':dmu,'dsd':dsd,'base_state_dict':base.state_dict(),'debt_residual_state_dict':deb.state_dict(),'pass':bool(passed)},a.model_out);print(json.dumps({'ok':True,'pass':passed,'lag3':lag3,'driftGain':drift_gain,'cleanMaxDiff':clean_maxdiff},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
