from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1];SRC=ROOT/'data/research/r4_v0/hourly/r4_anticipated_responsibility_teacher_v1_rows.csv';OUT=ROOT/'data/research/r4_v0/hourly/r4_anticipated_responsibility_group_ablation_v1.json'
G={'GEOMETRY':['combined_abs_net','maker_abs_net','combined_paired_coverage','maker_paired_coverage','worst_case_floor','best_case_pnl','abs_payoff_gap'],'RECENT_FLOW':['maker_fills_1s','maker_fills_5s','maker_shares_5s','maker_shares_10s','taker_fills_1s','taker_fills_5s','taker_shares_5s','taker_shares_10s'],'CONTROLLER':['p_maker_side','p_maker_opp','p_taker_1s','p_taker_3s','p_residual_wake'],'EXECUTION_STATE':['active_maker_orders','book_age_ms'],'PREDICT_NATIVE':['predict_toward_side'],'EXTERNAL':['spot_toward_side','chainlink_toward_side']}
BASE=['GEOMETRY','RECENT_FLOW','CONTROLLER','EXECUTION_STATE','PREDICT_NATIVE']
def mdl():return Pipeline([('i',SimpleImputer(strategy='median')),('m',HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=140,l2_regularization=3.0,min_samples_leaf=30,random_state=42,class_weight='balanced'))])
def met(y,p):return {'n':len(y),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-7,1-1e-7),labels=[0,1]))}
def main():
 d=pd.read_csv(SRC).sort_values(['t','market_id']);mids=d.groupby('market_id').t.min().sort_values().index.astype(int).tolist();a=int(len(mids)*.70);b=int(len(mids)*.85);parts={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 configs={'LOCAL_FULL':BASE,'LOCAL_PLUS_EXTERNAL':BASE+['EXTERNAL']}
 for g in BASE:configs['DROP_'+g]=[x for x in BASE if x!=g]
 configs.update({g+'_ONLY':[g,'EXECUTION_STATE'] if g!='EXECUTION_STATE' else [g] for g in ['GEOMETRY','RECENT_FLOW','CONTROLLER','PREDICT_NATIVE','EXTERNAL']})
 res={}
 for name,gs in configs.items():
  feats=[]
  for g in gs:
   for f in G[g]:
    if f not in feats:feats.append(f)
  tr=d[d.market_id.isin(parts['train'])];m=mdl().fit(tr[feats],tr.y.astype(int));res[name]={'groups':gs,'features':feats}
  for sk,ids in parts.items():
   x=d[d.market_id.isin(ids)];res[name][sk]=met(x.y.astype(int),m.predict_proba(x[feats])[:,1])
 full=res['LOCAL_FULL'];delta={}
 for name,z in res.items():
  if name in ('LOCAL_FULL','LOCAL_PLUS_EXTERNAL'):continue
  delta[name]={sk:{'dAucVsFull':z[sk]['auc']-full[sk]['auc'],'dAPVsFull':z[sk]['ap']-full[sk]['ap'],'dLogLossVsFull':z[sk]['logLoss']-full[sk]['logLoss']} for sk in ('validation','test')}
 rep={'version':'R4_ANTICIPATED_RESPONSIBILITY_GROUP_ABLATION_V1','researchOnly':True,'groups':G,'results':res,'deltaVsLocalFull':delta,'guards':['Same fixed chronology split as teacher rows','Research teacher only; no action threshold','Future responsibility remains label only']};OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'full':{k:full[k] for k in ('validation','test')},'plusExternal':{k:res['LOCAL_PLUS_EXTERNAL'][k] for k in ('validation','test')},'delta':delta},ensure_ascii=False))
if __name__=='__main__':main()
