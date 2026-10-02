from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');PKG=Path('data/research/r4_v0/gpt6_target_direction_confidence_sources_v1_20260907');EXT=LANE/'unseen_older173_v1';SRC=LANE/'C2_FRESH_PARENT_SETTLEMENT_VALUE_V1.csv';OUT=LANE/'C2_FRESH_PARENT_VALUE_DECOMPOSITION_V1.json';CSV=LANE/'C2_FRESH_PARENT_VALUE_DECOMPOSITION_V1.csv';SEED=20260907

def attach(d,cpfile,cohort):
 cp=pd.read_csv(cpfile,low_memory=False);by={int(m):z.sort_values('checkpoint_ms') for m,z in cp.groupby('market_id',sort=False)};rows=[]
 for r in d[d.cohort.eq(cohort)].itertuples():
  z=by.get(int(r.market_id));
  if z is None:continue
  q=z[z.checkpoint_ms<int(r.first_parent_placement_ms)].tail(1)
  if len(q)==0:continue
  s=q.iloc[0];age=int(r.first_parent_placement_ms)-int(s.checkpoint_ms)
  if age<=0 or age>2000:continue
  chosen=str(r.first_parent_side);mid=float(s.predict_up_mid if chosen=='UP' else s.predict_down_mid);ask=float(s.predict_up_ask if chosen=='UP' else s.predict_down_ask);bid=float(s.predict_up_bid if chosen=='UP' else s.predict_down_bid)
  if not np.isfinite(mid):continue
  win=float(r.chosen_side_win);price=float(r.first_parent_price);x=r._asdict();x.update({'placement_snapshot_age_ms':age,'chosen_public_mid':mid,'chosen_public_bid':bid,'chosen_public_ask':ask,'directional_residual_vs_mid':win-mid,'maker_value_capture_vs_mid':mid-price,'maker_saving_vs_ask':ask-price,'recomposed_payoff':(win-mid)+(mid-price)});rows.append(x)
 return rows

def cluster_mean(z,col,resamples=10000):
 z=z[['market_id',col]].dropna();g=z.groupby('market_id')[col].agg(['sum','count']).to_numpy(float);point=float(g[:,0].sum()/g[:,1].sum());rng=np.random.default_rng(SEED);ix=rng.integers(0,len(g),size=(resamples,len(g)));s=g[ix].sum(1);v=s[:,0]/s[:,1];return {'mean':point,'ci95':[float(x) for x in np.quantile(v,[.025,.975])],'rows':len(z),'markets':int(z.market_id.nunique())}
def cluster_diff(z,col,resamples=10000):
 z=z[['market_id','first_parent_same_side',col]].dropna();rows=[]
 for mid,g in z.groupby('market_id'):
  a=g[g.first_parent_same_side.eq(1)][col];b=g[g.first_parent_same_side.eq(0)][col];rows.append((len(a),float(a.sum()),len(b),float(b.sum())))
 arr=np.asarray(rows,float);a=z[z.first_parent_same_side.eq(1)][col];b=z[z.first_parent_same_side.eq(0)][col];rng=np.random.default_rng(SEED);ix=rng.integers(0,len(arr),size=(resamples,len(arr)));s=arr[ix].sum(1);ok=(s[:,0]>0)&(s[:,2]>0);v=s[ok,1]/s[ok,0]-s[ok,3]/s[ok,2];return {'sameMean':float(a.mean()),'oppositeMean':float(b.mean()),'difference':float(a.mean()-b.mean()),'ci95':[float(x) for x in np.quantile(v,[.025,.975])],'resamples':int(len(v))}
def summarize(z):
 out={'rows':len(z),'markets':int(z.market_id.nunique()),'maxRecomposeError':float((z.recomposed_payoff-z.per_share_payoff).abs().max())}
 for label,val in [('SAME',1),('OPPOSITE',0)]:
  g=z[z.first_parent_same_side.eq(val)];out[label]={'episodes':len(g),'meanPrice':float(g.first_parent_price.mean()),'meanPublicMid':float(g.chosen_public_mid.mean()),'chosenWinRate':float(g.chosen_side_win.mean()),'directionalResidual':cluster_mean(g,'directional_residual_vs_mid'),'makerValueCapture':cluster_mean(g,'maker_value_capture_vs_mid'),'savingVsAsk':cluster_mean(g,'maker_saving_vs_ask'),'realizedPayoff':cluster_mean(g,'per_share_payoff')}
 out['differencesSameMinusOpposite']={c:cluster_diff(z,c) for c in ['directional_residual_vs_mid','maker_value_capture_vs_mid','maker_saving_vs_ask','per_share_payoff']};return out

def main():
 d=pd.read_csv(SRC,low_memory=False);rows=attach(d,PKG/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv','CANONICAL120')+attach(d,EXT/'23_TARGET_BTC_DIRECTION_CONFIDENCE_CHECKPOINT_PANEL_V1.csv','OLDER173');x=pd.DataFrame(rows);x.to_csv(CSV,index=False);summary={n:summarize(z) for n,z in [('CANONICAL120',x[x.cohort.eq('CANONICAL120')]),('OLDER173',x[x.cohort.eq('OLDER173')]),('ALL',x)]};out={'version':'OUR_C2_FRESH_PARENT_VALUE_DECOMPOSITION_V1','status':'POST_HOC_OUTCOME_FALSIFICATION','identity':'realized payoff = (winner - strict-past public mid) + (strict-past public mid - parent price)','summary':summary,'guards':['Winner term is future outcome-only; public mid is strict-past <=2s before parent placement.','Directional residual is realized calibration residual vs public Prediction mid, not a forecast model score.','Maker value capture is public mid minus inferred Target parent price; positive means Target buys below contemporaneous public mid.','This decomposition is parent-level and retrospective, not full portfolio PnL or runtime authority.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
