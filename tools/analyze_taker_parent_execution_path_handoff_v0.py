from __future__ import annotations

import json, math, sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, log_loss

import train_target_maker_taker_coordination_big_v1 as coord

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'data'/'research'/'target_maker_taker_coordination_big_v1'
HAND=OUT/'post_taker_handoff_states_v1.csv'
TRANS=OUT/'post_taker_handoff_transition_v0.csv'
TARGET=ROOT/'data'/'target_wallet_official_v1.db'
REPORT=OUT/'taker_parent_execution_path_handoff_v0_report.json'
AUG=OUT/'post_taker_handoff_execution_path_v0.csv'

EXEC=[
 'exec_parent_duration_ms','exec_fill_legs','exec_price_first','exec_price_last','exec_price_range','exec_price_last_minus_first',
 'exec_leg_shares_mean','exec_leg_shares_std','exec_leg_shares_max','exec_largest_leg_fraction','exec_interleg_gap_mean_ms','exec_interleg_gap_max_ms',
 'exec_same_timestamp_fraction','exec_same_bid_distance','exec_same_ask_distance','exec_opp_bid_pair_edge','exec_opp_ask_pair_edge',
 'exec_effect_absnet_delta','exec_effect_reduced_absnet','exec_effect_increased_absnet','exec_effect_crossed_zero',
]

RUNTIME_TRANS=[
 'trans_duration_ms','trans_combined_net_delta','trans_combined_abs_net_delta','trans_combined_paired_coverage_delta','trans_floor_delta','trans_best_case_delta',
 'trans_maker_net_delta','trans_maker_abs_net_delta','trans_maker_paired_coverage_delta','trans_pair_bid_edge_delta','trans_pair_ask_edge_delta',
 'trans_same_bid_delta','trans_opp_bid_delta','trans_same_top3_depth_delta','trans_opp_top3_depth_delta','trans_maker_fills_during_count','trans_maker_fills_during_shares',
 'trans_maker_same_fills_during_count','trans_maker_opp_fills_during_count','trans_maker_same_shares_during','trans_maker_opp_shares_during','trans_maker_fill_side_balance'
]

def ro(p):
 c=sqlite3.connect(f"file:{Path(p).resolve().as_posix()}?mode=ro",uri=True,timeout=30); c.row_factory=sqlite3.Row; c.execute('pragma query_only=on'); return c

def f(v):
 try:x=float(v); return x if math.isfinite(x) else math.nan
 except:return math.nan

def metrics(y,pred,prob,classes):
 cm=confusion_matrix(y,pred,labels=classes); return {'n':len(y),'accuracy':float(accuracy_score(y,pred)),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'macroF1':float(f1_score(y,pred,labels=classes,average='macro',zero_division=0)),'logLoss':float(log_loss(y,prob,labels=classes)),'truthDistribution':{c:int((y==c).sum()) for c in classes},'predictedDistribution':{c:int(np.sum(pred==c)) for c in classes},'perClassRecall':{c:(float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None) for i,c in enumerate(classes)},'confusionMatrix':{'labels':classes,'matrix':cm.tolist()}}

def train(df,features,name):
 sp=coord.split_markets(df); parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}; m=coord.ebm(features); m.fit(coord.numeric(parts['train'],features),parts['train'].label_handoff.astype(str).tolist()); classes=[str(x) for x in m.classes_]; rep={'features':features,'splitMarkets':{k:len(v) for k,v in sp.items()}}
 for k in ('train','validation','test'):
  x=coord.numeric(parts[k],features); y=parts[k].label_handoff.astype(str); rep[k]=metrics(y,m.predict(x),m.predict_proba(x),classes)
 rep['topTerms']=coord.top_terms(m,25); art=OUT/f'handoff_{name}.joblib'; joblib.dump({'features':features,'classes':classes,'model':m},art); rep['artifact']=str(art); return rep

def main():
 base=pd.read_csv(HAND); trans=pd.read_csv(TRANS) if TRANS.exists() else None
 con=ro(TARGET)
 try:
  parents={str(r['parent_id']):dict(r) for r in con.execute("select parent_id,market_id,side,order_hash,first_event_ms,last_event_ms,average_price,shares,fill_legs from target_parent_orders where asset='BTC' and role='TAKER' and quote_type='BID'")}
  legs=defaultdict(list)
  for r in con.execute("select order_hash,event_ms,side,price,shares from wallet_shadow_target_events where asset='BTC' and role='TAKER' and quote_type='BID' order by event_ms,id"):
   legs[str(r['order_hash'])].append(dict(r))
 finally: con.close()
 rows=[]; missing=0
 for _,r in base.iterrows():
  p=parents.get(str(r.parent_id));
  if p is None: missing+=1; continue
  ls=legs.get(str(p['order_hash']),[]); ls=sorted(ls,key=lambda x:int(x['event_ms']))
  prices=[float(x['price']) for x in ls]; shares=[float(x['shares']) for x in ls]; times=[int(x['event_ms']) for x in ls]
  n=max(1,len(ls)); total=sum(shares); gaps=[b-a for a,b in zip(times,times[1:])]
  side=str(r.intervention_side); same_bid=f(r.up_bid if side=='UP' else r.down_bid); same_ask=f(r.up_ask if side=='UP' else r.down_ask); opp_bid=f(r.down_bid if side=='UP' else r.up_bid); opp_ask=f(r.down_ask if side=='UP' else r.up_ask); avgpx=float(p['average_price'])
  pre_abs=abs(float(r.post_taker_combined_abs_net) - 0.0) # replaced below using deterministic inversion only when possible
  # exact pre combined net is recovered from post net and intervention signed shares using current combined_net field (already post at checkpoint) minus parent action.
  post_net=float(r.combined_net); signed=float(p['shares']) if side=='UP' else -float(p['shares']); pre_net=post_net-signed
  d=r.to_dict(); d.update({
   'exec_parent_duration_ms':float(int(p['last_event_ms'])-int(p['first_event_ms'])),'exec_fill_legs':float(p['fill_legs']),
   'exec_price_first':prices[0] if prices else avgpx,'exec_price_last':prices[-1] if prices else avgpx,'exec_price_range':(max(prices)-min(prices)) if prices else 0.0,'exec_price_last_minus_first':(prices[-1]-prices[0]) if len(prices)>=2 else 0.0,
   'exec_leg_shares_mean':float(np.mean(shares)) if shares else float(p['shares']),'exec_leg_shares_std':float(np.std(shares)) if shares else 0.0,'exec_leg_shares_max':max(shares) if shares else float(p['shares']),'exec_largest_leg_fraction':(max(shares)/total) if shares and total>0 else 1.0,
   'exec_interleg_gap_mean_ms':float(np.mean(gaps)) if gaps else 0.0,'exec_interleg_gap_max_ms':float(max(gaps)) if gaps else 0.0,'exec_same_timestamp_fraction':float(sum(1 for t in times if t==times[0])/n) if times else 1.0,
   'exec_same_bid_distance':avgpx-same_bid,'exec_same_ask_distance':avgpx-same_ask,'exec_opp_bid_pair_edge':1.0-avgpx-opp_bid,'exec_opp_ask_pair_edge':1.0-avgpx-opp_ask,
   'exec_effect_absnet_delta':abs(post_net)-abs(pre_net),'exec_effect_reduced_absnet':float(abs(post_net)<abs(pre_net)-1e-9),'exec_effect_increased_absnet':float(abs(post_net)>abs(pre_net)+1e-9),'exec_effect_crossed_zero':float(pre_net*post_net<0),
  }); rows.append(d)
 aug=pd.DataFrame(rows); aug.to_csv(AUG,index=False)
 full=coord.HANDOFF_FEATURE_SETS['FULL']; exec_rep=train(aug,full+EXEC,'full_plus_taker_execution_path_v0')
 combo_rep=None
 if trans is not None:
  merged=trans.drop(columns=[c for c in trans.columns if c in EXEC],errors='ignore').merge(aug[['parent_id']+EXEC],on='parent_id',how='inner'); combo_rep=train(merged,full+RUNTIME_TRANS+EXEC,'full_plus_transition_execution_path_v0')
 baseline=json.loads((OUT/'report_handoff_full.json').read_text(encoding='utf-8'))
 report={'reportVersion':'TAKER_PARENT_EXECUTION_PATH_HANDOFF_V0','researchOnly':True,'runtimeDeployable':True,'question':'Does parent-specific Taker execution path known by completion explain subsequent Maker handoff?','coverage':{'rows':len(aug),'markets':int(aug.market_id.nunique()),'missingParents':missing},'executionFeatures':EXEC,'executionOnly':exec_rep,'transitionPlusExecution':combo_rep,'comparison':{}}
 for split in ('validation','test'):
  bb=float(baseline[split]['balancedAccuracy']); report['comparison'][split]={'baselineBalanced':bb,'executionBalanced':float(exec_rep[split]['balancedAccuracy']),'executionLift':float(exec_rep[split]['balancedAccuracy'])-bb,'comboBalanced':float(combo_rep[split]['balancedAccuracy']) if combo_rep else None,'comboLift':float(combo_rep[split]['balancedAccuracy'])-bb if combo_rep else None,'baselineMacroF1':float(baseline[split]['macroF1']),'executionMacroF1':float(exec_rep[split]['macroF1']),'comboMacroF1':float(combo_rep[split]['macroF1']) if combo_rep else None}
 REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(report,ensure_ascii=False,indent=2)); return 0
if __name__=='__main__': raise SystemExit(main())
