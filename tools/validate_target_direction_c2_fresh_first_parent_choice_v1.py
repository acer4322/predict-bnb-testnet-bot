from __future__ import annotations
import json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1';OUT=LANE/'C2_FRESH_FIRST_PARENT_CHOICE_CANONICAL_TO_OLDER173_V1.json'
s=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(s);s.loader.exec_module(v1)
s2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(s2);s2.loader.exec_module(v2)

def pred(z,cur):
 q=z[(z.target_side==cur.target_side)&(z.placement_first_ms<cur.placement_first_ms)&z.post_action.isin(['SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT','REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT'])].copy();q=q[q.post_action_native_price.notna()]
 if not len(q):return None
 e=q[(q.post_action_native_price-cur.native_price).abs()<=1e-6];q=e if len(e) else q[(q.post_action_native_price-cur.native_price).abs()<=0.0100001]
 if not len(q):return None
 q=q[(cur.placement_first_ms-q.placement_first_ms)<=5000];return q.sort_values('placement_first_ms').iloc[-1] if len(q) else None
def traced(z,cur,entry):
 node=cur;seen=set()
 for _ in range(8):
  p=pred(z,node)
  if p is None:return False
  k=str(p.order_hash)
  if k in seen:return False
  seen.add(k)
  if p.placement_first_ms<entry:return True
  node=p
 return False

def build(epfile,parentfile):
 e=pd.read_csv(epfile,low_memory=False);p=pd.read_csv(parentfile,low_memory=False);p=p[(p.placement_coverage>=.85)&(p.fill_allocation_coverage>=.70)&p.placement_first_ms.notna()&p.first_target_ms.notna()&(p.placement_first_ms<=p.first_target_ms)].copy();by={int(m):z.sort_values('placement_first_ms') for m,z in p.groupby('market_id',sort=False)};rows=[]
 for r in e.itertuples():
  z=by.get(int(r.market_id));q=None if z is None else z[(z.placement_first_ms>r.entry_ms)&(z.placement_first_ms<=r.entry_ms+5000)]
  if q is None or len(q)==0:continue
  cur=q.iloc[0];tr=traced(z,cur,int(r.entry_ms));x=r._asdict();x.update({'first_parent_side':str(cur.target_side),'first_parent_same_side':int(str(cur.target_side)==r.anchor),'first_parent_traced_pre':int(tr),'first_parent_price':float(cur.target_price),'first_parent_placement_ms':int(cur.placement_first_ms)});rows.append(x)
 d=pd.DataFrame(rows);return d[d.first_parent_traced_pre.eq(0)].copy()
def feat(d):
 d=d.copy();s=d.anchor.map({'UP':1.,'DOWN':-1.});d['phase']=d.seconds_left/300;d['opp_predict']=-d.predict_support;d['opp_strike']=-d.strike_support;d['o_spot_q']=s*d.spot_queue_imbalance;d['o_fut_q']=s*d.futures_queue_imbalance;d['o_spot_t']=s*d.spot_taker_imbalance_1s;d['o_fut_t']=s*d.futures_taker_imbalance_1s;d['o_spot_r']=s*d.spot_return_1s_bps;d['o_fut_r']=s*d.futures_return_1s_bps;d['log_debt']=np.log1p(d.pre_outstanding_qty.clip(lower=0));d['floor_slog']=np.sign(d.pre_floor)*np.log1p(abs(d.pre_floor));d['best_slog']=np.sign(d.pre_best)*np.log1p(abs(d.pre_best));d['last_age']=np.log1p(d.last_action_age_ms.clip(lower=0));d['prior_ask']=np.where(s==1,d.predict_up_ask,d.predict_down_ask);d['repair_ask']=np.where(s==1,d.predict_down_ask,d.predict_up_ask);d['pair_surplus']=1-d.predict_up_ask-d.predict_down_ask;return d
def fit_transform(tr,te,cols):
 a=np.asarray(tr[cols],float);b=np.asarray(te[cols],float);keep=np.isfinite(a).any(0);a=a[:,keep];b=b[:,keep];am=~np.isfinite(a);bm=~np.isfinite(b);med=np.nanmedian(np.where(am,np.nan,a),axis=0);a=np.where(am,med,a);b=np.where(bm,med,b);mu=a.mean(0);sd=a.std(0);sd[sd<1e-8]=1;m=am.any(0);return np.c_[np.ones(len(a)),np.clip((a-mu)/sd,-8,8),am[:,m]],np.c_[np.ones(len(b)),np.clip((b-mu)/sd,-8,8),bm[:,m]]
def main():
 can=feat(build(LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv',PKG/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'));ext=feat(build(LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv',EXT/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'))
 market=['phase','opp_predict','opp_strike'];port=['log_debt','floor_slog','best_slog','last_age'];price=['prior_ask','repair_ask','pair_surplus'];micro=['o_spot_q','o_fut_q','o_spot_t','o_fut_t','o_spot_r','o_fut_r'];groups={'G0_MARKET':market,'G1_PLUS_PORT_SERVICE':market+port,'G2_PLUS_PRICE':market+price,'G3_PLUS_MICRO':market+micro,'G4_COMPACT':market+port+price};y=ext.first_parent_same_side.to_numpy(int);preds={};metrics={}
 for n,c in groups.items():
  x,z=fit_transform(can,ext,c);b=v1.fit_logit(x,can.first_parent_same_side.to_numpy(int));p=1/(1+np.exp(-np.clip(z@b,-35,35)));preds[n]=p;metrics[n]=v1.metrics(y,p)
 inc={n:v2.market_cluster_delta(ext,preds['G0_MARKET'],preds[n],label='first_parent_same_side',resamples=5000) for n in groups if n!='G0_MARKET'}
 cols=['opp_predict','opp_strike','pre_outstanding_qty','pre_floor','pre_best','last_action_age_ms','prior_ask','repair_ask','pair_surplus','o_spot_q','o_fut_q'];contr={c:{'sameMedian':float(ext[ext.first_parent_same_side.eq(1)][c].median()),'oppositeMedian':float(ext[ext.first_parent_same_side.eq(0)][c].median())} for c in cols}
 out={'version':'OUR_C2_FRESH_FIRST_PARENT_CHOICE_V1','status':'RESEARCH_ONLY','canonicalTrain':{'rows':len(can),'markets':int(can.market_id.nunique()),'sameRate':float(can.first_parent_same_side.mean())},'older173External':{'rows':len(ext),'markets':int(ext.market_id.nunique()),'sameRate':float(ext.first_parent_same_side.mean())},'groups':groups,'externalMetrics':metrics,'incrementsVsMarket':inc,'externalContrasts':contr,'guards':['Condition on at least one HQ parent within 5s, then remove first-parent chains traceable to a pre-conflict same-side-of-that-parent reprice/refill program.','Outcome asks whether first fresh parent is prior-anchor side (Expand-like) or opposite side (Repair-like).','Canonical cohort trains fixed source families; older173 is independent external test.','Accounting role interpretation remains approximate: parent side is an execution admission event, not private intent.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
