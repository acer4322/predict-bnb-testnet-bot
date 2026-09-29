from __future__ import annotations
import argparse, importlib.util, json, math, sys
from pathlib import Path
import joblib, numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, brier_score_loss, log_loss

ROOT=Path.cwd().resolve() if (Path.cwd()/'tools').exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
HERE=Path(__file__).resolve().parent
sp=importlib.util.spec_from_file_location('basehaz',HERE/'train_target_eth_repair_taker_escalation_hazard_v1.py')
base=importlib.util.module_from_spec(sp); sp.loader.exec_module(base)
EPS=1e-9

BOOK=list(base.core.BOOK)
PORTABLE_FEATURES=[
 'seconds_left_norm',
 'maker_imbalance_ratio','maker_paired_coverage',
 'combined_imbalance_ratio','combined_paired_coverage',
 'floor_per_gross','best_per_gross','gap_per_gross',
 *BOOK,
 'last_maker_age_log','last_maker_up_age_log','last_maker_down_age_log',
 'maker_fill_frac_1s','maker_fill_frac_3s','maker_fill_frac_5s',
 'maker_share_frac_3s','maker_share_frac_5s','maker_share_frac_10s',
 'maker_absnet_change_10s_per_gross','combined_absnet_change_10s_per_gross',
 'repair_parent_frac_1s','repair_parent_frac_3s','repair_parent_frac_5s','repair_parent_frac_10s',
 'repair_share_frac_1s','repair_share_frac_3s','repair_share_frac_5s','repair_share_frac_10s',
 'latest_expansion_age_log','latest_expansion_repaid_frac'
]

def age_log(x):
 try:
  x=float(x)
  if not math.isfinite(x): return math.nan
  return math.log1p(max(0.0,x)/1000.0)
 except Exception:return math.nan

def portable_values(r):
 mg=max(0.0,float(r.get('maker_gross') or 0.0)); cg=max(0.0,float(r.get('combined_gross') or 0.0)); gd=cg+1.0; md=mg+1.0
 m10=max(0.0,float(r.get('maker_fills_10s') or 0.0)); denom_events=m10+1.0
 out={
  'seconds_left_norm':float(r.get('seconds_left') or 0.0)/300.0,
  'maker_imbalance_ratio':float(r.get('maker_imbalance_ratio') or 0.0),
  'maker_paired_coverage':float(r.get('maker_paired_coverage') or 0.0),
  'combined_imbalance_ratio':float(r.get('combined_imbalance_ratio') or 0.0),
  'combined_paired_coverage':float(r.get('combined_paired_coverage') or 0.0),
  'floor_per_gross':float(r.get('worst_case_floor') or 0.0)/gd,
  'best_per_gross':float(r.get('best_case_pnl') or 0.0)/gd,
  'gap_per_gross':float(r.get('abs_payoff_gap') or 0.0)/gd,
  'last_maker_age_log':age_log(r.get('last_maker_age_ms')),
  'last_maker_up_age_log':age_log(r.get('last_maker_up_age_ms')),
  'last_maker_down_age_log':age_log(r.get('last_maker_down_age_ms')),
  'maker_fill_frac_1s':float(r.get('maker_fills_1s') or 0.0)/denom_events,
  'maker_fill_frac_3s':float(r.get('maker_fills_3s') or 0.0)/denom_events,
  'maker_fill_frac_5s':float(r.get('maker_fills_5s') or 0.0)/denom_events,
  'maker_share_frac_3s':float(r.get('maker_shares_3s') or 0.0)/md,
  'maker_share_frac_5s':float(r.get('maker_shares_5s') or 0.0)/md,
  'maker_share_frac_10s':float(r.get('maker_shares_10s') or 0.0)/md,
  'maker_absnet_change_10s_per_gross':float(r.get('maker_absnet_change_10s') or 0.0)/md,
  'combined_absnet_change_10s_per_gross':float(r.get('combined_absnet_change_10s') or 0.0)/gd,
  'repair_parent_frac_1s':float(r.get('maker_repair_parents_1s') or 0.0)/denom_events,
  'repair_parent_frac_3s':float(r.get('maker_repair_parents_3s') or 0.0)/denom_events,
  'repair_parent_frac_5s':float(r.get('maker_repair_parents_5s') or 0.0)/denom_events,
  'repair_parent_frac_10s':float(r.get('maker_repair_parents_10s') or 0.0)/denom_events,
  'repair_share_frac_1s':float(r.get('maker_repair_shares_1s') or 0.0)/md,
  'repair_share_frac_3s':float(r.get('maker_repair_shares_3s') or 0.0)/md,
  'repair_share_frac_5s':float(r.get('maker_repair_shares_5s') or 0.0)/md,
  'repair_share_frac_10s':float(r.get('maker_repair_shares_10s') or 0.0)/md,
  'latest_expansion_age_log':age_log(r.get('latest_maker_expansion_age_ms')),
  'latest_expansion_repaid_frac':float(r.get('latest_maker_expansion_repaid_frac')) if r.get('latest_maker_expansion_repaid_frac') is not None else math.nan,
 }
 for k in BOOK:
  v=r.get(k); out[k]=math.nan if v is None else float(v)
 return out

def matrix(rows):
 X=[]; y=[]
 for r in rows:
  z=portable_values(r); X.append([z[k] for k in PORTABLE_FEATURES]); y.append(int(r['label_repair_taker_1s']))
 return np.asarray(X,np.float32),np.asarray(y,int)

def metr(y,p):
 y=np.asarray(y,int);p=np.asarray(p,float);pred=(p>=.5).astype(int);both=len(set(y.tolist()))>1
 return {'n':int(len(y)),'positives':int(y.sum()),'positiveRate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum()>0 else None,'brier':float(brier_score_loss(y,p)) if len(y) else None,'logLoss':float(log_loss(y,p,labels=[0,1])) if len(y) else None,'predictedPositiveRateAt05':float(pred.mean()) if len(y) else None,'recallAt05':float(((pred==1)&(y==1)).sum()/y.sum()) if y.sum()>0 else None}

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--db',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model',required=True);a=ap.parse_args()
 rows=base.build(a.db); parts,split=base.split(rows)
 Xtr,ytr=matrix(parts['train']);Xv,yv=matrix(parts['validation']);Xt,yt=matrix(parts['test'])
 model=HistGradientBoostingClassifier(max_iter=300,learning_rate=.05,max_leaf_nodes=31,min_samples_leaf=30,l2_regularization=2.0,class_weight='balanced',random_state=9201).fit(Xtr,ytr)
 ptr=model.predict_proba(Xtr)[:,1];pv=model.predict_proba(Xv)[:,1];pt=model.predict_proba(Xt)[:,1]
 art={'version':'TARGET_ETH_REPAIR_TAKER_PORTABLE_NORMALIZED_V57','task':'repair_taker_1s','features':PORTABLE_FEATURES,'model':model,'transform':'portable_values_v57','sourceCohort':[1845039,1862886],'guards':['No raw gross/net/share-count scale as feature.','No separate Taker-history action-density features.','Public-book features retained.','No threshold sweep.','No winner/PnL.']}
 Path(a.model).parent.mkdir(parents=True,exist_ok=True);joblib.dump(art,a.model)
 out={'version':'TARGET_ETH_REPAIR_TAKER_PORTABLE_NORMALIZED_V57','researchOnly':True,'behaviorChange':False,'coverage':{'rows':len(rows),'markets':len({int(r['market_id']) for r in rows})},'split':split,'features':PORTABLE_FEATURES,'train':metr(ytr,ptr),'validation':metr(yv,pv),'test':metr(yt,pt),'model':a.model,'referenceFrozenHazardTestAuc':0.8254855553133402,'boundary':['Exact fresh150 chronology 1845039-1862886.','Fixed single portability transform; no feature sweep.','Same HGB training recipe/random_state as frozen 1s hazard where applicable.','No runtime action authority.']}
 Path(a.output).parent.mkdir(parents=True,exist_ok=True);Path(a.output).write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2),flush=True)
if __name__=='__main__':main()
