from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');V=LANE/'C2_FRESH_PARENT_VALUE_DECOMPOSITION_V1.csv';Q=LANE/'C2_FRESH_PARENT_FORWARD_REVERSION_V1.csv';OUT=LANE/'C2_QUEUE_SETTLEMENT_RESIDUAL_V1.json';CSV=LANE/'C2_QUEUE_SETTLEMENT_RESIDUAL_V1.csv';SEED=20260907

def cluster_mean(z,col,resamples=10000):
 z=z[['market_id',col]].dropna();g=z.groupby('market_id')[col].agg(['sum','count']).to_numpy(float);point=float(g[:,0].sum()/g[:,1].sum());rng=np.random.default_rng(SEED);ix=rng.integers(0,len(g),size=(resamples,len(g)));s=g[ix].sum(1);v=s[:,0]/s[:,1];return {'mean':point,'ci95':[float(x) for x in np.quantile(v,[.025,.975])],'rows':len(z),'markets':int(z.market_id.nunique())}
def cluster_diff(z,col,mask,resamples=10000):
 z=z[['market_id',col]].copy();z['g']=mask.astype(int);z=z.dropna();rows=[]
 for mid,a in z.groupby('market_id'):
  x=a[a.g.eq(1)][col];y=a[a.g.eq(0)][col];rows.append((len(x),float(x.sum()),len(y),float(y.sum())))
 r=np.asarray(rows,float);x=z[z.g.eq(1)][col];y=z[z.g.eq(0)][col];rng=np.random.default_rng(SEED);ix=rng.integers(0,len(r),size=(resamples,len(r)));s=r[ix].sum(1);ok=(s[:,0]>0)&(s[:,2]>0);d=s[ok,1]/s[ok,0]-s[ok,3]/s[ok,2];return {'group1Mean':float(x.mean()),'group0Mean':float(y.mean()),'difference':float(x.mean()-y.mean()),'ci95':[float(v) for v in np.quantile(d,[.025,.975])],'group1N':len(x),'group0N':len(y),'resamples':int(len(d))}
def qtab(z,col):
 q=z[col].dropna();edges=np.unique(np.quantile(q,[0,.25,.5,.75,1]));
 if len(edges)<3:return None
 a=z.copy();a['bin']=pd.cut(a[col],edges,include_lowest=True,duplicates='drop');return [{'bin':str(k),'n':len(g),'markets':int(g.market_id.nunique()),'queueMedian':float(g[col].median()),'residualMean':float(g.directional_residual_vs_mid.mean()),'chosenWinRate':float(g.chosen_side_win.mean()),'meanPublicMid':float(g.chosen_public_mid.mean()),'meanValueCapture':float(g.maker_value_capture_vs_mid.mean())} for k,g in a.groupby('bin',observed=True)]
def main():
 v=pd.read_csv(V,low_memory=False);q=pd.read_csv(Q,low_memory=False);keys=['cohort','market_id','entry_ms'];m=q[keys+['pl_o_spot_q','pl_o_fut_q']].drop_duplicates(keys);d=v.merge(m,on=keys,how='inner',validate='one_to_one');sg=np.where(d.first_parent_same_side.eq(1),1.0,-1.0);d['chosen_o_spot_q']=sg*d.pl_o_spot_q;d['chosen_o_fut_q']=sg*d.pl_o_fut_q;d['chosen_q_mean']=d[['chosen_o_spot_q','chosen_o_fut_q']].mean(axis=1);d['both_chosen_queues_adverse']=(d.chosen_o_spot_q.lt(0)&d.chosen_o_fut_q.lt(0));d.to_csv(CSV,index=False)
 out={}
 for name,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]:
  x={'rows':len(z),'markets':int(z.market_id.nunique()),'residualOverall':cluster_mean(z,'directional_residual_vs_mid'),'bothQueuesAdverseRate':float(z.both_chosen_queues_adverse.mean())}
  x['adverseVsOtherResidual']=cluster_diff(z,'directional_residual_vs_mid',z.both_chosen_queues_adverse)
  x['negativeMeanQueueVsNonnegativeResidual']=cluster_diff(z,'directional_residual_vs_mid',z.chosen_q_mean.lt(0))
  x['queueQuartiles']={'spot':qtab(z,'chosen_o_spot_q'),'futures':qtab(z,'chosen_o_fut_q'),'mean':qtab(z,'chosen_q_mean')}
  # same-side only: directly test the apparent contrarian anchor choice
  s=z[z.first_parent_same_side.eq(1)].copy();x['sameSideOnly']={'rows':len(s),'bothAdverseRate':float(s.both_chosen_queues_adverse.mean()) if len(s) else None,'adverseVsOtherResidual':cluster_diff(s,'directional_residual_vs_mid',s.both_chosen_queues_adverse) if len(s)>10 and s.both_chosen_queues_adverse.nunique()>1 else None,'residual':cluster_mean(s,'directional_residual_vs_mid') if len(s) else None}
  out[name]=x
 report={'version':'OUR_C2_QUEUE_SETTLEMENT_RESIDUAL_V1','status':'POST_HOC_MECHANISM_FALSIFICATION','summary':out,'guards':['Chosen-side queue is derived by re-orienting placement-time anchor-oriented queue to the actual first fresh parent side.','Directional residual = settlement winner indicator minus strict-past public Prediction mid; winner is outcome-only.','Natural zero-sign contrasts only; quartiles are descriptive and not runtime thresholds.','No independent third BTC5M common cohort remains, so this cannot promote authority.']};OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
