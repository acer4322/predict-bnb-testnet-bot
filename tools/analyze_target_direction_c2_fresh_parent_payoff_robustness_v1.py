from __future__ import annotations
import json,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1';SRC=LANE/'C2_FRESH_PARENT_SETTLEMENT_VALUE_V1.csv';OUT=LANE/'C2_FRESH_PARENT_PAYOFF_ROBUSTNESS_V1.json';SEED=20260907

def parent_map(path):
 p=pd.read_csv(path,low_memory=False);p=p[(p.placement_coverage>=.85)&(p.fill_allocation_coverage>=.70)&p.placement_first_ms.notna()].copy();return {(int(r.market_id),int(r.placement_first_ms),str(r.target_side)):r for r in p.itertuples()}
def boot_mean(z,col,weight=None,resamples=10000):
 cols=['market_id',col]+([weight] if weight else []);z=z[cols].dropna().copy();rows=[]
 for mid,g in z.groupby('market_id'):
  if weight:
   w=g[weight].clip(lower=0).to_numpy(float);v=g[col].to_numpy(float);rows.append((float((v*w).sum()),float(w.sum())))
  else:rows.append((float(g[col].sum()),float(len(g))))
 a=np.asarray(rows,float);point=float(a[:,0].sum()/a[:,1].sum()) if a[:,1].sum()>0 else None;rng=np.random.default_rng(SEED);ix=rng.integers(0,len(a),size=(resamples,len(a)));s=a[ix].sum(1);q=s[:,0]/s[:,1];return {'mean':point,'ci95':[float(x) for x in np.quantile(q,[.025,.975])],'markets':int(z.market_id.nunique()),'rows':int(len(z)),'resamples':resamples}
def market_equal_mean(z,col,resamples=10000):
 g=z.groupby('market_id')[col].mean().dropna().to_numpy(float);rng=np.random.default_rng(SEED);ix=rng.integers(0,len(g),size=(resamples,len(g)));q=g[ix].mean(1);return {'mean':float(g.mean()),'ci95':[float(x) for x in np.quantile(q,[.025,.975])],'markets':len(g),'resamples':resamples}
def main():
 d=pd.read_csv(SRC,low_memory=False);maps={'CANONICAL120':parent_map(PKG/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv'),'OLDER173':parent_map(EXT/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv')};fills=[];exp=[]
 for r in d.itertuples():
  p=maps[r.cohort].get((int(r.market_id),int(r.first_parent_placement_ms),str(r.first_parent_side)));fills.append(float(p.target_filled_shares) if p is not None and pd.notna(p.target_filled_shares) else np.nan);exp.append(float(p.expected_parent_shares) if p is not None and pd.notna(p.expected_parent_shares) else np.nan)
 d['parent_filled_shares']=fills;d['parent_expected_shares']=exp;out={}
 for cohort,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]:
  q={'rows':len(z),'markets':int(z.market_id.nunique())}
  for side,val in [('SAME',1),('OPPOSITE',0)]:
   g=z[z.first_parent_same_side.eq(val)].copy();q[side]={'episodes':len(g),'unweightedPayoff':boot_mean(g,'per_share_payoff'),'marketEqualPayoff':market_equal_mean(g,'per_share_payoff'),'filledShareWeightedPayoff':boot_mean(g,'per_share_payoff','parent_filled_shares'),'expectedShareWeightedPayoff':boot_mean(g,'per_share_payoff','parent_expected_shares'),'meanFilledShares':float(g.parent_filled_shares.mean()),'medianFilledShares':float(g.parent_filled_shares.median()),'meanPrice':float(g.first_parent_price.mean()),'chosenWinRate':float(g.chosen_side_win.mean())}
  out[cohort]=q
 report={'version':'OUR_C2_FRESH_PARENT_PAYOFF_ROBUSTNESS_V1','status':'POST_HOC_OUTCOME_FALSIFICATION','summary':out,'guards':['Winner/payoff are outcome-only.','Filled-share weighting uses retrospective inferred first fresh parent target_filled_shares and therefore conditions on observable Target parent lifecycle.','Market-cluster bootstrap preserves repeated episode dependence; marketEqual gives each market equal weight.','This is parent-level binary payoff, not full portfolio PnL and not a runtime value estimate.']};OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
