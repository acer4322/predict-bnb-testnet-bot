from __future__ import annotations
import json, math, warnings
from pathlib import Path
from collections import defaultdict
from typing import Any
import joblib, numpy as np, pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier, ExplainableBoostingRegressor
from sklearn.metrics import roc_auc_score, average_precision_score, mean_absolute_error, mean_squared_error

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
DATA=D/'r2_fill_quality_explicit_v1_dataset.csv'
REPORT=D/'r2_fill_quality_explicit_v1_report.json'
ART=D/'r2_fill_quality_explicit_v1.joblib'
SEED=20260822
GRID=0.01

# Mirrors the mature open-order lifecycle state. All are strict-past at the selected checkpoint.
FEATURES=[
 'side_is_up','order_age_ms','quote_price','status_none','status_new','status_partial',
 'cum_exec_qty','remaining_qty','remaining_ratio','partial_fill_ratio','active_same_count','active_opp_count',
 'quote_offset_ticks','current_bid','current_ask','current_spread_ticks','initial_depth','public_cum_depletion',
 'public_depletion_ratio','public_any_depletion',
 'maker_gross','maker_net','maker_abs_net','maker_imbalance_ratio','maker_paired_coverage',
 'taker_gross','taker_net','taker_abs_net','taker_paired_coverage','combined_gross','combined_net',
 'combined_abs_net','combined_imbalance_ratio','combined_paired_coverage','worst_case_floor','best_case_pnl',
 'abs_payoff_gap','last_maker_age_ms','last_maker_up_age_ms','last_maker_down_age_ms',
 'maker_fills_1s','maker_fills_5s','maker_fills_10s','maker_shares_5s','maker_shares_10s'
]

def finite(v:Any)->float:
 try:
  x=float(v); return x if math.isfinite(x) else math.nan
 except Exception:return math.nan

def load_reports():
 out=[]
 for p in sorted(D.glob('r2_execution_school_market*_mid_risk_v0.json')):
  try:
   r=json.loads(p.read_text(encoding='utf-8'))
   if r.get('version')=='HFTBACKTEST_R2_EXECUTION_SCHOOL_V0': out.append(r)
  except Exception: pass
 return out

def side_mid_from_row(row:dict[str,Any])->float|None:
 b=row.get('currentBid'); a=row.get('currentAsk')
 try:
  b=float(b);a=float(a)
  return (b+a)/2 if math.isfinite(b) and math.isfinite(a) else None
 except Exception:return None

def make_feature(row:dict[str,Any])->dict[str,float]:
 p=row.get('portfolio') if isinstance(row.get('portfolio'),dict) else {}
 cum=finite(row.get('cumExecQty')); rem=finite(row.get('remainingQty')); total=(0 if math.isnan(cum) else cum)+(0 if math.isnan(rem) else rem)
 init=finite(row.get('initialDepth')); dep=finite(row.get('publicCumDepletion'))
 status=str(row.get('hftStatus') or 'NONE')
 vals={
 'side_is_up':float(str(row.get('side')).upper()=='UP'),'order_age_ms':finite(row.get('orderAgeMs')),
 'quote_price':finite(row.get('price')),'status_none':float(status=='NONE'),'status_new':float(status=='NEW'),
 'status_partial':float(status=='PARTIALLY_FILLED'),'cum_exec_qty':cum,'remaining_qty':rem,
 'remaining_ratio':rem/total if total>1e-9 and not math.isnan(rem) else math.nan,
 'partial_fill_ratio':finite(row.get('partialFillRatio')),'active_same_count':finite(row.get('activeSameCount')),
 'active_opp_count':finite(row.get('activeOppCount')),'quote_offset_ticks':finite(row.get('quoteOffsetTicks')),
 'current_bid':finite(row.get('currentBid')),'current_ask':finite(row.get('currentAsk')),
 'current_spread_ticks':finite(row.get('currentSpreadTicks')),'initial_depth':init,'public_cum_depletion':dep,
 'public_depletion_ratio':dep/init if init>1e-9 and not math.isnan(dep) else math.nan,
 'public_any_depletion':float(bool(row.get('publicAnyDepletion'))),
 }
 for k in FEATURES:
  if k not in vals: vals[k]=finite(p.get(k))
 return vals

def build()->pd.DataFrame:
 rows=[]
 for rep in load_reports():
  mid=int(rep['marketId']); states=rep.get('orderStateRows') or []; fills=sorted(rep.get('makerFillEvents') or [],key=lambda x:int(x['atMs'])); takers=sorted(rep.get('takerEvents') or [],key=lambda x:int(x['atMs']))
  byoid=defaultdict(list)
  for s in states: byoid[str(s.get('orderId'))].append(s)
  for z in byoid.values(): z.sort(key=lambda x:int(x['checkpointMs']))
  # Reconstruct maker inventory immediately before/after each actual HFT fill.
  up=down=0.0
  fill_inv={}
  for i,f in enumerate(fills):
   side=str(f['side']).upper(); qty=float(f['deltaShares']); pre_g=up+down; pre_pc=2*min(up,down)/pre_g if pre_g>1e-9 else 1.0
   if side=='UP': up+=qty
   else: down+=qty
   post_g=up+down; post_pc=2*min(up,down)/post_g if post_g>1e-9 else 1.0
   fill_inv[i]=(pre_pc,post_pc,abs(up-down))
  # Build a market-time index of strict-past order-state mid observations by side, used only to create post-fill labels.
  side_states={'UP':[],'DOWN':[]}
  for s in states:
   m=side_mid_from_row(s)
   if m is not None: side_states[str(s.get('side')).upper()].append((int(s['checkpointMs']),m))
  for side in side_states: side_states[side].sort()
  def mid_at(side:str,t:int):
   arr=side_states.get(side) or []
   # first observed state at/after t within 2.5s; label-only future observation
   for ts,m in arr:
    if ts>=t and ts<=t+2500:return m
   return None
  for i,f in enumerate(fills):
   oid=str(f['orderId']); ft=int(f['atMs']); side=str(f['side']).upper(); cand=[s for s in byoid.get(oid,[]) if int(s['checkpointMs'])<=ft]
   if not cand: continue
   s=max(cand,key=lambda x:int(x['checkpointMs']))
   x=make_feature(s); m0=side_mid_from_row(s); m1=mid_at(side,ft+1000); m3=mid_at(side,ft+3000)
   mo1=((m1-m0)/GRID) if m0 is not None and m1 is not None else math.nan
   mo3=((m3-m0)/GRID) if m0 is not None and m3 is not None else math.nan
   pre_pc,post_pc,post_abs=fill_inv[i]
   repair5=int(any(ft < int(t['atMs']) <= ft+5000 for t in takers))
   repair10=int(any(ft < int(t['atMs']) <= ft+10000 for t in takers))
   rows.append({'market_id':mid,'fill_ms':ft,'order_id':oid,'fill_side':side,'fill_shares':float(f['deltaShares']),
                **x,'label_markout1s_ticks':mo1,'label_markout3s_ticks':mo3,
                'label_paired_delta':post_pc-pre_pc,'label_paired_improves':int(post_pc>pre_pc+1e-9),
                'label_paired_worsens':int(post_pc<pre_pc-1e-9),'label_post_abs_net':post_abs,
                'label_repair5s':repair5,'label_repair10s':repair10})
 return pd.DataFrame(rows).sort_values(['fill_ms','market_id','order_id']).reset_index(drop=True)

def cls_metric(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,float)
 return {'n':len(y),'rate':float(y.mean()) if len(y) else None,'auc':float(roc_auc_score(y,p)) if len(set(y.tolist()))>1 else None,'ap':float(average_precision_score(y,p)) if y.sum() else None}
def reg_metric(y,p):
 y=np.asarray(y,float);p=np.asarray(p,float)
 return {'n':len(y),'mae':float(mean_absolute_error(y,p)),'rmse':float(mean_squared_error(y,p)**0.5),'meanTarget':float(np.mean(y)),'meanPred':float(np.mean(p))}

def main():
 warnings.filterwarnings('ignore')
 d=build(); d.to_csv(DATA,index=False)
 mids=(d.groupby('market_id')['fill_ms'].max().sort_values().index.astype(int).tolist()); a=int(len(mids)*.70);b=int(len(mids)*.85); splits={'train':set(mids[:a]),'validation':set(mids[a:b]),'test':set(mids[b:])}
 parts={k:d[d.market_id.astype(int).isin(v)].copy() for k,v in splits.items()}; models={};metrics={};tops={}
 heads=[('markout1s','reg','label_markout1s_ticks'),('markout3s','reg','label_markout3s_ticks'),('paired_worsens','cls','label_paired_worsens'),('repair10s','cls','label_repair10s')]
 for name,kind,label in heads:
  usable={k:x[x[label].notna()].copy() for k,x in parts.items()}; tr=usable['train']; Xtr=tr[FEATURES].apply(pd.to_numeric,errors='coerce')
  if kind=='reg':
   m=ExplainableBoostingRegressor(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=12,n_jobs=-2,random_state=SEED+len(models));m.fit(Xtr,tr[label].astype(float))
  else:
   m=ExplainableBoostingClassifier(feature_names=FEATURES,max_bins=64,max_interaction_bins=16,interactions=6,outer_bags=4,learning_rate=.035,max_rounds=900,early_stopping_rounds=60,min_samples_leaf=12,n_jobs=-2,random_state=SEED+len(models));m.fit(Xtr,tr[label].astype(int))
  models[name]=m; metrics[name]={}
  for part,x in usable.items():
   if not len(x):continue
   X=x[FEATURES].apply(pd.to_numeric,errors='coerce')
   if kind=='reg': metrics[name][part]=reg_metric(x[label].astype(float),m.predict(X))
   else: metrics[name][part]=cls_metric(x[label].astype(int),m.predict_proba(X)[:,1])
  imp=list(m.term_importances()); names=list(m.term_names_);tops[name]=sorted([{'term':str(names[i]),'importance':float(imp[i])} for i in range(len(imp))],key=lambda z:z['importance'],reverse=True)[:12]
 joblib.dump({'version':'R2_FILL_QUALITY_EXPLICIT_V1','models':models,'features':FEATURES,'trainingMarkets':sorted(splits['train']),'runtimeTargetDataAllowed':False,'dreamFillAllowed':False},ART)
 rep={'version':'R2_FILL_QUALITY_EXPLICIT_V1','researchOnly':True,'liveTradingChanges':False,'rows':len(d),'markets':len(mids),'splitMarkets':{k:len(v) for k,v in splits.items()},'metrics':metrics,'topTerms':tops,'artifact':str(ART),'labelSemantics':{
  'markout':'side-token mid move from last strict-past own-order state before actual HFT fill to first observed own-order-state book at/after +1s/+3s; development-grade label, not formal receipt-frontier certification',
  'paired':'actual HFT Maker fill effect on reconstructed Maker paired coverage','repair':'actual HFT Taker fill occurs within 10s after Maker fill'},
 'guardrails':['All Maker/Taker fills are actual HftBacktest Execution Tape V1 events.','No dream fill.','No Target/winner/settlement/PnL runtime feature.','Chronological market split.','No label threshold sweep.','Teacher is development-only until markout labels are rebuilt from receipt-frontier public book.']}
 REPORT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({'ok':True,'report':str(REPORT),'rows':len(d),'markets':len(mids),'splitMarkets':rep['splitMarkets'],'metrics':metrics,'topTerms':{k:v[:5] for k,v in tops.items()}},ensure_ascii=False))
if __name__=='__main__':main()
