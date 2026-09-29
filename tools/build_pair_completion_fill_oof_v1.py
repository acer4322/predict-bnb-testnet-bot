from __future__ import annotations

import json, math, sys, warnings
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
FILL_DATA=D/'open_order_fill_lifecycle_v0_dataset.csv'
PAIR=D/'pair_completion_tradeoff_curriculum_canonical_v4.jsonl'
CHILD=D/'pair_completion_child_fill_labels_canonical_v1.jsonl'
OUT=D/'pair_completion_fill_expert_oof_v1.csv'
REPORT=D/'pair_completion_fill_expert_oof_v1_report.json'
MODEL_DIR=D/'pair_fill_oof_models_v1'
SEED=20260821

# Only features that can be reconstructed faithfully at a Pair Completion checkpoint.
FEATURES=[
 'side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial',
 'cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count',
 'quote_offset_ticks','current_bid','current_ask','current_spread_ticks',
 'maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
 'taker_gross','taker_net','taker_abs_net','taker_paired_coverage',
 'combined_gross','combined_net','combined_abs_net','combined_imbalance_ratio','combined_paired_coverage',
 'last_maker_age_ms','maker_shares_5s','maker_shares_10s',
]

def finite(v:Any)->float:
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except Exception:return math.nan

def pc(a:float,b:float)->float:
 g=a+b; return 2*min(a,b)/g if g>1e-9 else 1.0

def pair_frame(r:dict[str,Any])->pd.DataFrame:
 f=r.get('features') or {}
 side_up=float(f.get('recoverySideIsUp') or 0.0)
 age=finite(f.get('workingRecoveryAgeMs')); qpx=finite(f.get('workingRecoveryPrice'))
 status=str(f.get('workingRecoveryStatus') or 'NONE')
 cum=finite(f.get('workingRecoveryCumExecQty')); cum=0.0 if not math.isfinite(cum) else cum
 rem=finite(f.get('workingRecoveryRemainingQty')); rem=0.0 if not math.isfinite(rem) else rem
 tot=cum+rem
 mup=finite(f.get('actualMakerUp')); mdn=finite(f.get('actualMakerDown'))
 if not math.isfinite(mup) or not math.isfinite(mdn):
  mg=finite(f.get('actualMakerGross')); mn=finite(f.get('actualMakerNet'))
  if math.isfinite(mg) and math.isfinite(mn): mup=(mg+mn)/2; mdn=(mg-mn)/2
  else: mup=mdn=math.nan
 mg=(mup+mdn) if math.isfinite(mup) and math.isfinite(mdn) else finite(f.get('actualMakerGross'))
 mn=(mup-mdn) if math.isfinite(mup) and math.isfinite(mdn) else finite(f.get('actualMakerNet'))
 cg=finite(f.get('actualCombinedGross')); cn=finite(f.get('actualNet'))
 tg=cg-mg if math.isfinite(cg) and math.isfinite(mg) else math.nan
 tn=cn-mn if math.isfinite(cn) and math.isfinite(mn) else math.nan
 tup=(tg+tn)/2 if math.isfinite(tg) and math.isfinite(tn) else math.nan
 tdn=(tg-tn)/2 if math.isfinite(tg) and math.isfinite(tn) else math.nan
 vals={
  'side_is_up':side_up,'order_age_ms':age,'quote_price':qpx,
  'status_none':float(status=='NONE'),'status_new':float(status=='NEW'),'status_partial':float(status=='PARTIALLY_FILLED'),
  'cum_exec_qty':cum,'remaining_qty':rem,'remaining_ratio':rem/tot if tot>1e-9 else math.nan,
  'partial_fill_ratio':cum/tot if tot>1e-9 else 0.0,'active_same_count':float(f.get('workingRecoveryExists') or 0.0),
  'quote_offset_ticks':finite(f.get('workingRecoveryOffsetTicks')),'current_bid':finite(f.get('recoveryBid')),
  'current_ask':finite(f.get('recoveryAsk')),'current_spread_ticks':finite(f.get('recoverySpreadTicks')),
  'maker_gross':mg,'maker_net':mn,'maker_abs_net':abs(mn) if math.isfinite(mn) else math.nan,
  'maker_imbalance_ratio':abs(mn)/mg if math.isfinite(mn) and math.isfinite(mg) and mg>1e-9 else 0.0,
  'maker_paired_coverage':pc(mup,mdn) if math.isfinite(mup) and math.isfinite(mdn) else math.nan,
  'taker_gross':tg,'taker_net':tn,'taker_abs_net':abs(tn) if math.isfinite(tn) else math.nan,
  'taker_paired_coverage':pc(max(0,tup),max(0,tdn)) if math.isfinite(tup) and math.isfinite(tdn) else math.nan,
  'combined_gross':cg,'combined_net':cn,'combined_abs_net':abs(cn) if math.isfinite(cn) else math.nan,
  'combined_imbalance_ratio':abs(cn)/cg if math.isfinite(cn) and math.isfinite(cg) and cg>1e-9 else 0.0,
  'combined_paired_coverage':1-abs(cn)/cg if math.isfinite(cn) and math.isfinite(cg) and cg>1e-9 else 1.0,
  'last_maker_age_ms':finite(f.get('lastMakerFillAgeMs')),
  'maker_shares_5s':finite(f.get('makerFillShares5s')),'maker_shares_10s':finite(f.get('makerFillShares10s')),
 }
 return pd.DataFrame([{k:vals.get(k,math.nan) for k in FEATURES}],columns=FEATURES)

def make_model(h:int)->ExplainableBoostingClassifier:
 return ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=4,outer_bags=4,learning_rate=.035,max_rounds=850,early_stopping_rounds=50,min_samples_leaf=16,n_jobs=-2,random_state=SEED+h)

def fit_models(d:pd.DataFrame, cutoff:int, tag:str)->dict[str,Any]:
 MODEL_DIR.mkdir(parents=True,exist_ok=True); path=MODEL_DIR/f'{tag}.joblib'
 if path.exists(): return joblib.load(path)
 hist=d[d.checkpoint_ms < int(cutoff)].copy()
 models={}; meta={'tag':tag,'cutoffMs':int(cutoff),'trainRows':int(len(hist)),'trainMarkets':int(hist.market_id.nunique()),'trainMaxCheckpointMs':int(hist.checkpoint_ms.max()) if len(hist) else None}
 if hist.market_id.nunique()<12: raise RuntimeError(f'not enough historical fill markets for {tag}: {hist.market_id.nunique()}')
 for h in (1,3,5):
  x=hist[hist[f'censored_{h}s']==0].copy(); y=x[f'label_fill_{h}s'].astype(int)
  m=make_model(h); m.fit(x[FEATURES].apply(pd.to_numeric,errors='coerce'),y); models[f'fill_{h}s']=m; meta[f'positiveRate{h}s']=float(y.mean()); meta[f'rows{h}s']=int(len(x))
 bundle={'version':'PAIR_FILL_OOF_EXPERT_V1','features':FEATURES,'models':models,'meta':meta}; joblib.dump(bundle,path); return bundle

def eval_metric(rows:list[dict[str,Any]], h:int)->dict[str,Any]:
 z=[r for r in rows if r.get(f'yFill{h}s') is not None and r.get(f'pFill{h}s') is not None]
 if not z:return {'n':0}
 y=np.asarray([int(r[f'yFill{h}s']) for r in z],int); p=np.asarray([float(r[f'pFill{h}s']) for r in z],float); p=np.clip(p,1e-7,1-1e-7)
 return {'n':len(z),'positives':int(y.sum()),'rate':float(y.mean()),'predMean':float(p.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'logLoss':float(log_loss(y,p,labels=[0,1]))}

def main():
 warnings.filterwarnings('ignore',message='X does not have valid feature names')
 d=pd.read_csv(FILL_DATA,low_memory=False)
 pair=[json.loads(x) for x in PAIR.read_text(encoding='utf-8').splitlines() if x.strip()]; pair.sort(key=lambda r:int(r['checkpointMs']))
 child={int(r['marketId']):r for r in [json.loads(x) for x in CHILD.read_text(encoding='utf-8').splitlines() if x.strip()]}
 if len(pair)!=149: raise RuntimeError(len(pair))
 # Fixed expanding-window schedule. First 25 Pair rows intentionally get no expert prediction.
 folds=[('oof25_49',25,50,int(pair[25]['checkpointMs'])),('oof50_74',50,75,int(pair[50]['checkpointMs'])),('oof75_99',75,100,int(pair[75]['checkpointMs'])),('frozen_val',100,126,int(pair[100]['checkpointMs'])),('forward',126,149,int(pair[126]['checkpointMs']))]
 out=[]; fold_meta=[]
 for tag,a,b,cut in folds:
  expert=fit_models(d,cut,tag); fold_meta.append(expert['meta'])
  for r in pair[a:b]:
   lab=(child.get(int(r['marketId'])) or {}).get('childKeepLabels')
   rec={'marketId':int(r['marketId']),'checkpointMs':int(r['checkpointMs']),'fold':tag,'hasChild':bool(lab),'expertTrainingMaxCheckpointMs':expert['meta']['trainMaxCheckpointMs'],'expertTrainMarkets':expert['meta']['trainMarkets']}
   if lab:
    x=pair_frame(r)
    for h in (1,3,5): rec[f'yFill{h}s']=int(lab[f'anyFill{h}s']); rec[f'pFill{h}s']=float(expert['models'][f'fill_{h}s'].predict_proba(x)[0,1])
   else:
    for h in (1,3,5): rec[f'yFill{h}s']=None; rec[f'pFill{h}s']=None
   out.append(rec)
 # Explicit missing expert rows for earliest curriculum.
 for r in pair[:25]:
  lab=(child.get(int(r['marketId'])) or {}).get('childKeepLabels'); rec={'marketId':int(r['marketId']),'checkpointMs':int(r['checkpointMs']),'fold':'NO_PRIOR_EXPERT','hasChild':bool(lab),'expertTrainingMaxCheckpointMs':None,'expertTrainMarkets':0}
  for h in (1,3,5): rec[f'yFill{h}s']=int(lab[f'anyFill{h}s']) if lab else None; rec[f'pFill{h}s']=None
  out.append(rec)
 out.sort(key=lambda r:int(r['checkpointMs'])); pd.DataFrame(out).to_csv(OUT,index=False)
 groups={'pairTrainOOF':[r for r in out if r['fold'].startswith('oof')],'frozenValidation':[r for r in out if r['fold']=='frozen_val'],'forwardOos':[r for r in out if r['fold']=='forward']}
 report={'version':'PAIR_COMPLETION_FILL_EXPERT_OOF_V1','researchOnly':True,'features':FEATURES,'folds':fold_meta,'earliest25Policy':'No pFill expert feature; insufficient earlier fill curriculum. Missingness is preserved, never backfilled from future data.','metrics':{g:{f'fill{h}s':eval_metric(rs,h) for h in (1,3,5)} for g,rs in groups.items()},'guardrails':['Every OOF Pair prediction uses a fill expert with trainingMaxCheckpointMs < Pair checkpointMs.','No Pair future-fill label enters runtime features.','Frozen validation expert is fit only before validation start.','Forward expert is fit only before Forward start.','No probability threshold sweep.']}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'ok':True,'output':str(OUT),'report':str(REPORT),'folds':fold_meta,'metrics':report['metrics']},ensure_ascii=False))
if __name__=='__main__':main()
