from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.metrics import roc_auc_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_management_transition_incremental_hft_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_management_transition_incremental_hft_v1_bootstrap.json';SEED=26082807;B=500
TARGETS=['weakFillFailure5s','floorFailure5s','absNetFailure5s']
def main():
 d=pd.read_csv(SRC).reset_index(drop=True);mids=np.array(sorted(d.marketId.unique()));groups=[np.flatnonzero(d.marketId.to_numpy()==m) for m in mids];rng=np.random.default_rng(SEED)
 ys={t:d[t].to_numpy(float) for t in TARGETS};pm=d.multi_risk_anchor.to_numpy(float);pf=d.fused_anchor_50.to_numpy(float);boot={t:[] for t in TARGETS}
 for _ in range(B):
  picks=rng.integers(0,len(groups),size=len(groups));idx=np.concatenate([groups[j] for j in picks])
  for t in TARGETS:
   valid=~np.isnan(ys[t][idx]);y=ys[t][idx][valid].astype(int)
   if len(np.unique(y))<2:continue
   boot[t].append(float(roc_auc_score(y,pf[idx][valid])-roc_auc_score(y,pm[idx][valid])))
 market={}
 for t in TARGETS:
  wins=ties=losses=0;deltas=[]
  for idx in groups:
   valid=~np.isnan(ys[t][idx]);y=ys[t][idx][valid].astype(int)
   if len(y)==0:continue
   a=log_loss(y,pm[idx][valid],labels=[0,1]);b=log_loss(y,pf[idx][valid],labels=[0,1]);delta=float(a-b);deltas.append(delta)
   if delta>1e-12:wins+=1
   elif delta<-1e-12:losses+=1
   else:ties+=1
  market[t]={'markets':len(deltas),'fusedLogLossWins':wins,'losses':losses,'ties':ties,'winRate':wins/max(1,wins+losses),'meanLogLossImprovement':float(np.mean(deltas)),'medianLogLossImprovement':float(np.median(deltas))}
 res={}
 for t,v in boot.items():
  x=np.asarray(v);res[t]={'bootstrapN':len(x),'meanAucDelta':float(x.mean()),'medianAucDelta':float(np.median(x)),'ci95':[float(np.quantile(x,.025)),float(np.quantile(x,.975))],'probabilityPositive':float((x>0).mean())}
 n=min(map(len,boot.values()));joint=np.column_stack([np.asarray(boot[t][:n]) for t in TARGETS]);md=joint.mean(1)
 overall={'meanAcrossOutcomes':{'meanDelta':float(md.mean()),'ci95':[float(np.quantile(md,.025)),float(np.quantile(md,.975))],'probabilityPositive':float((md>0).mean())},'allThreePositiveProbability':float((joint>0).all(1).mean())}
 decision='KEEP_SIGNAL_NOT_RUNTIME' if overall['meanAcrossOutcomes']['probabilityPositive']>=.75 else 'WEAK_SIGNAL_ONLY'
 if all(res[t]['ci95'][0]>0 for t in TARGETS):decision='ROBUST_RUNTIME_CANDIDATE'
 art={'version':'R4_MANAGEMENT_TRANSITION_INCREMENTAL_HFT_V1_MARKET_BOOTSTRAP','researchOnly':True,'actionAuthority':False,'bootstrap':{'unit':'marketId cluster','draws':B,'markets':len(mids),'seed':SEED},'aucDeltaFusedMinusMulticlass':res,'overall':overall,'marketLogLoss':market,'decision':decision,'guards':['Market-cluster bootstrap preserves within-market row dependence.','No model refit or threshold tuning in bootstrap.','Research only.']};OUT.write_text(json.dumps(art,indent=2),encoding='utf-8');print(json.dumps(art,indent=2))
if __name__=='__main__':main()
