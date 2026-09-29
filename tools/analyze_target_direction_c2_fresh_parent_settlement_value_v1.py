from __future__ import annotations
import json,sqlite3,importlib.util
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1';DB=Path('data/target_wallet_official_v1.db');OUT=LANE/'C2_FRESH_PARENT_SETTLEMENT_VALUE_V1.json';CSV=LANE/'C2_FRESH_PARENT_SETTLEMENT_VALUE_V1.csv'
s=importlib.util.spec_from_file_location('base','tools/validate_target_direction_c2_fresh_first_parent_choice_v1.py');base=importlib.util.module_from_spec(s);s.loader.exec_module(base)
SEED=20260907

def attach(d,cohort,winners):
 rows=[]
 for r in d.itertuples():
  win=winners.get(int(r.market_id))
  if win not in ('UP','DOWN'):continue
  chosen=str(r.first_parent_side);anchor=str(r.anchor);price=float(r.first_parent_price);cw=int(chosen==win);aw=int(anchor==win);pay=(1-price) if cw else -price
  x=r._asdict();x.update({'cohort':cohort,'winner':win,'chosen_side_win':cw,'anchor_win':aw,'per_share_payoff':pay});rows.append(x)
 return rows

def cluster_diff(z,col,resamples=10000):
 z=z[['market_id','first_parent_same_side',col]].dropna().copy();rows=[]
 for mid,g in z.groupby('market_id'):
  a=g[g.first_parent_same_side.eq(1)][col];b=g[g.first_parent_same_side.eq(0)][col];rows.append((len(a),float(a.sum()),len(b),float(b.sum())))
 g=np.asarray(rows,float);a=z[z.first_parent_same_side.eq(1)][col];b=z[z.first_parent_same_side.eq(0)][col]
 rng=np.random.default_rng(SEED);ix=rng.integers(0,len(g),size=(resamples,len(g)));s=g[ix].sum(1);ok=(s[:,0]>0)&(s[:,2]>0);dif=s[ok,1]/s[ok,0]-s[ok,3]/s[ok,2]
 return {'sameMean':float(a.mean()),'oppositeMean':float(b.mean()),'difference':float(a.mean()-b.mean()),'ci95':[float(x) for x in np.quantile(dif,[.025,.975])],'resamples':int(len(dif))}
def summ(z):
 a=z[z.first_parent_same_side.eq(1)];b=z[z.first_parent_same_side.eq(0)];return {'rows':len(z),'markets':int(z.market_id.nunique()),'sameRate':float(z.first_parent_same_side.mean()),'sameChoice':{'n':len(a),'anchorWinRate':float(a.anchor_win.mean()),'chosenWinRate':float(a.chosen_side_win.mean()),'meanParentPrice':float(a.first_parent_price.mean()),'medianParentPrice':float(a.first_parent_price.median()),'meanPerSharePayoff':float(a.per_share_payoff.mean()),'positivePayoffRate':float((a.per_share_payoff>0).mean())},'oppositeChoice':{'n':len(b),'anchorWinRate':float(b.anchor_win.mean()),'chosenWinRate':float(b.chosen_side_win.mean()),'meanParentPrice':float(b.first_parent_price.mean()),'medianParentPrice':float(b.first_parent_price.median()),'meanPerSharePayoff':float(b.per_share_payoff.mean()),'positivePayoffRate':float((b.per_share_payoff>0).mean())},'anchorWinDiff':cluster_diff(z,'anchor_win'),'chosenWinDiff':cluster_diff(z,'chosen_side_win'),'payoffDiff':cluster_diff(z,'per_share_payoff')}
def main():
 can=base.build(LANE/'C2_FRESH_PROGRAM_SOURCE_FEATURES_V1.csv',PKG/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv');ext=base.build(LANE/'UNSEEN_OLDER173_SERVICE_REPLICATION_V1.csv',EXT/'26_TARGET_BTC_MAKER_PARENT_LIFECYCLES_RETROSPECTIVE_V1.csv');c=sqlite3.connect(f'file:{DB.resolve().as_posix()}?mode=ro',uri=True);w={int(m):str(x) for m,x in c.execute("select market_id,winner from target_market_results where asset='BTC' and winner in ('UP','DOWN')")};c.close();d=pd.DataFrame(attach(can,'CANONICAL120',w)+attach(ext,'OLDER173',w));d.to_csv(CSV,index=False);summary={name:summ(z) for name,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]}
 out={'version':'OUR_C2_FRESH_PARENT_SETTLEMENT_VALUE_V1','status':'POST_HOC_OUTCOME_FALSIFICATION','summary':summary,'guards':['Winner and per-share payoff are future outcome-only; never runtime features.','Per-share payoff uses inferred first fresh parent target price and binary settlement winner; it is not full portfolio PnL and ignores role/hedge interaction.','Same-side choice means first fresh parent is prior-anchor side; opposite means Repair-like side.','Multiple episodes per market are market-cluster bootstrapped.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
