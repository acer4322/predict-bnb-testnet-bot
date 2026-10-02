from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_transition_incremental_hft_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_repair_stall_matched_upgrade_v1.json';SEED=26082811;B=500
FAIL=['weakFillFailure5s','floorFailure5s','absNetFailure5s']
def score(y,p):
 y=np.asarray(y,int);p=np.clip(np.asarray(p,float),1e-7,1-1e-7);return {'n':len(y),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def bootstrap(g,target,base,new):
 mids=np.array(sorted(g.marketId.unique()));groups=[np.flatnonzero(g.marketId.to_numpy()==m) for m in mids];rng=np.random.default_rng(SEED);y0=g[target].to_numpy(int);pb=g[base].to_numpy(float);pn=g[new].to_numpy(float);ds=[]
 for _ in range(B):
  idx=np.concatenate([groups[j] for j in rng.integers(0,len(groups),size=len(groups))]);y=y0[idx]
  if len(np.unique(y))<2:continue
  ds.append(float(roc_auc_score(y,pn[idx])-roc_auc_score(y,pb[idx])))
 x=np.asarray(ds);return {'draws':len(x),'meanAucDelta':float(x.mean()),'ci95':[float(np.quantile(x,.025)),float(np.quantile(x,.975))],'probabilityPositive':float((x>0).mean())}
def cohort(g,native_col):
 g=g.dropna(subset=[native_col,'multi_risk_anchor','binary_risk_anchor','fused_anchor_50']+FAIL).copy().reset_index(drop=True);g['stall']=(g[FAIL].sum(1)==3).astype(int);g['bad2of3']=(g[FAIL].sum(1)>=2).astype(int);g['native_risk']=1-g[native_col].astype(float)
 out={'rows':len(g),'markets':int(g.marketId.nunique()),'nativeColumn':native_col,'targets':{}}
 for t in ['stall','bad2of3']:
  y=g[t].to_numpy();out['targets'][t]={}
  for s in ['native_risk','multi_risk_anchor','binary_risk_anchor','fused_anchor_50']:out['targets'][t][s]=score(y,g[s])
  out['targets'][t]['bootstrapBinaryVsNative']=bootstrap(g,t,'native_risk','binary_risk_anchor');out['targets'][t]['bootstrapMultiVsNative']=bootstrap(g,t,'native_risk','multi_risk_anchor')
 return out
def main():
 d=pd.read_csv(SRC)
 m0=d[d.p_m0_full.notna()].copy();phase=d[d.p_phase_routed.notna()].copy()
 out={'version':'R4_MANAGEMENT_REPAIR_STALL_MATCHED_UPGRADE_V1','researchOnly':True,'actionAuthority':False,'m0FullMatched':cohort(m0,'p_m0_full'),'phaseRoutedMatched':cohort(phase,'p_phase_routed'),'guards':['Every comparison uses identical HFT rows for native and new Target-trained management risk.','Future repair outcomes are scoring only.','Market-cluster bootstrap; no threshold or weight tuning.','Research only.']}
 OUT.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,indent=2))
if __name__=='__main__':main()
