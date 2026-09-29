from __future__ import annotations
import json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1';OUT=LANE/'C2_FIRST_FRESH_PARENT_PLACEMENT_QUEUE_V1.json'
s=importlib.util.spec_from_file_location('base','tools/validate_target_direction_c2_fresh_first_parent_choice_v1.py');base=importlib.util.module_from_spec(s);s.loader.exec_module(base)
s1=importlib.util.spec_from_file_location('v1','tools/run_target_direction_confidence_c2_aligned_new_risk_v1.py');v1=importlib.util.module_from_spec(s1);s1.loader.exec_module(v1)
s2=importlib.util.spec_from_file_location('v2','tools/run_target_direction_confidence_c2_aligned_new_risk_v2.py');v2=importlib.util.module_from_spec(s2);s2.loader.exec_module(v2)

def attach(d,cpfile):
 cp=pd.read_csv(cpfile,low_memory=False);by={int(m):z.sort_values('checkpoint_ms') for m,z in cp.groupby('market_id',sort=False)};rows=[]
 for r in d.itertuples():
  z=by[int(r.market_id)];q=z[z.checkpoint_ms<r.first_parent_placement_ms].tail(1)
  if len(q)==0:continue
  s=q.iloc[0];age=float(r.first_parent_placement_ms-s.checkpoint_ms)
  if not (age>0 and age<=2000):continue
  x=r._asdict();anchor=1. if r.anchor=='UP' else -1.;chosen=1. if r.first_parent_side=='UP' else -1.
  x['placement_snapshot_age_ms']=age;x['pl_o_spot_q']=anchor*float(s.spot_queue_imbalance) if pd.notna(s.spot_queue_imbalance) else np.nan;x['pl_o_fut_q']=anchor*float(s.futures_queue_imbalance) if pd.notna(s.futures_queue_imbalance) else np.nan;x['pl_o_spot_t']=anchor*float(s.spot_taker_imbalance_1s) if pd.notna(s.spot_taker_imbalance_1s) else np.nan;x['pl_o_fut_t']=anchor*float(s.futures_taker_imbalance_1s) if pd.notna(s.futures_taker_imbalance_1s) else np.nan
  upa=float(s.predict_up_ask);dna=float(s.predict_down_ask);upb=float(s.predict_up_bid);dnb=float(s.predict_down_bid);x['pl_prior_ask']=upa if anchor==1 else dna;x['pl_repair_ask']=dna if anchor==1 else upa;x['pl_pair_surplus']=1-upa-dna
  cbid=upb if chosen==1 else dnb;cask=upa if chosen==1 else dna;oppask=dna if chosen==1 else upa;price=float(r.first_parent_price);x['chosen_saving_vs_ask']=cask-price;x['chosen_pair_surplus']=1-price-oppask;x['parent_at_or_below_bid']=int(price<=cbid+1e-9);x['chosen_oriented_spot_q']=chosen*float(s.spot_queue_imbalance) if pd.notna(s.spot_queue_imbalance) else np.nan;x['chosen_oriented_fut_q']=chosen*float(s.futures_queue_imbalance) if pd.notna(s.futures_queue_imbalance) else np.nan;rows.append(x)
 return pd.DataFrame(rows)
def fitt(tr,te,cols,label):
 x,z=base.fit_transform(tr,te,cols);b=v1.fit_logit(x,tr[label].to_numpy(int));p=1/(1+np.exp(-np.clip(z@b,-35,35)));return p
def main():
 can=base.feat(base.build(LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv',PKG/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'));ext=base.feat(base.build(LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv',EXT/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'));can=attach(can,PKG/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv');ext=attach(ext,EXT/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv')
 side_groups={'S0_ENTRY_MARKET':['phase','opp_predict','opp_strike'],'S1_ENTRY_QUEUE':['phase','opp_predict','opp_strike','o_spot_q','o_fut_q'],'S2_PLACEMENT_QUEUE':['phase','opp_predict','opp_strike','pl_o_spot_q','pl_o_fut_q'],'S3_ENTRY_PLUS_PLACEMENT_QUEUE':['phase','opp_predict','opp_strike','o_spot_q','o_fut_q','pl_o_spot_q','pl_o_fut_q'],'S4_PLACEMENT_MARKET':['phase','opp_predict','opp_strike','pl_o_spot_q','pl_o_fut_q','pl_prior_ask','pl_repair_ask','pl_pair_surplus']};yp=ext.first_parent_same_side.to_numpy(int);pm={};pr={}
 for n,c in side_groups.items():pr[n]=fitt(can,ext,c,'first_parent_same_side');pm[n]=v1.metrics(yp,pr[n])
 inc={n:v2.market_cluster_delta(ext,pr['S0_ENTRY_MARKET'],pr[n],label='first_parent_same_side',resamples=5000) for n in side_groups if n!='S0_ENTRY_MARKET'}
 # execution-style model after chosen side is known
 exec_groups={'E0_PRICE':['chosen_saving_vs_ask','chosen_pair_surplus'],'E1_PLUS_CHOSEN_QUEUE':['chosen_saving_vs_ask','chosen_pair_surplus','chosen_oriented_spot_q','chosen_oriented_fut_q']};ye=ext.parent_at_or_below_bid.to_numpy(int);em={};er={}
 for n,c in exec_groups.items():er[n]=fitt(can,ext,c,'parent_at_or_below_bid');em[n]=v1.metrics(ye,er[n])
 einc=v2.market_cluster_delta(ext,er['E0_PRICE'],er['E1_PLUS_CHOSEN_QUEUE'],label='parent_at_or_below_bid',resamples=5000)
 contrasts={'sideChoice':{c:{'sameMedian':float(ext.loc[ext.first_parent_same_side.eq(1),c].median()),'oppositeMedian':float(ext.loc[ext.first_parent_same_side.eq(0),c].median())} for c in ['pl_o_spot_q','pl_o_fut_q','pl_prior_ask','pl_repair_ask','pl_pair_surplus']},'execution':{c:{'passiveMedian':float(ext.loc[ext.parent_at_or_below_bid.eq(1),c].median()),'otherMedian':float(ext.loc[ext.parent_at_or_below_bid.eq(0),c].median())} for c in ['chosen_oriented_spot_q','chosen_oriented_fut_q','chosen_saving_vs_ask','chosen_pair_surplus']}}
 out={'version':'OUR_C2_FIRST_FRESH_PARENT_PLACEMENT_QUEUE_V1','status':'RESEARCH_ONLY','canonicalRows':len(can),'externalRows':len(ext),'externalSameSideRate':float(ext.first_parent_same_side.mean()),'externalPassiveBidOrLowerRate':float(ext.parent_at_or_below_bid.mean()),'sideChoiceGroups':side_groups,'sideChoiceMetrics':pm,'sideChoiceIncrementsVsEntryMarket':inc,'executionGroups':exec_groups,'executionMetrics':em,'executionQueueIncrement':einc,'contrasts':contrasts,'guards':['Placement snapshot is latest strict-past checkpoint < parent placement and <=2s old.','Placement-time public state is causally available but temporally closer to action; no future fill used.','Side-choice models use only anchor-oriented public/price state, not chosen-side-derived price features.','Execution-style model is conditioned on chosen side and may use chosen-side price/queue orientation.','No runtime authority or fixed threshold.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
