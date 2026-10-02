from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907');SRC=LANE/'C2_FRESH_PARENT_FORWARD_REVERSION_V1.csv';OUT=LANE/'C2_FRESH_PARENT_FORWARD_REVERSION_V1.json'
SEED=20260907

def stat(z,col):
 q=z[col].dropna();return {'n':int(len(q)),'mean':float(q.mean()) if len(q) else None,'median':float(q.median()) if len(q) else None,'positiveRate':float((q>0).mean()) if len(q) else None}
def boot(z,col,resamples=10000):
 z=z[['market_id','same_side_choice',col]].dropna().copy(); rows=[]
 for mid,g in z.groupby('market_id'):
  a=g[g.same_side_choice.eq(1)][col];b=g[g.same_side_choice.eq(0)][col];rows.append((len(a),float(a.sum()),len(b),float(b.sum())))
 g=np.asarray(rows,float);rng=np.random.default_rng(SEED);ix=rng.integers(0,len(g),size=(resamples,len(g)));s=g[ix].sum(1);ok=(s[:,0]>0)&(s[:,2]>0);diff=s[ok,1]/s[ok,0]-s[ok,3]/s[ok,2]
 a=z[z.same_side_choice.eq(1)][col];b=z[z.same_side_choice.eq(0)][col]
 return {'sameMean':float(a.mean()),'oppositeMean':float(b.mean()),'difference':float(a.mean()-b.mean()),'ci95':[float(x) for x in np.quantile(diff,[.025,.975])],'resamples':int(len(diff))}
def main():
 d=pd.read_csv(SRC,low_memory=False);summary={}
 for name,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]:
  q={'rows':len(z),'markets':int(z.market_id.nunique()),'sameRate':float(z.same_side_choice.mean())}
  for h in [1,3,5]:
   q[f'{h}s']={'same':{'spot':stat(z[z.same_side_choice.eq(1)],f'spot_ret_{h}s'),'fut':stat(z[z.same_side_choice.eq(1)],f'fut_ret_{h}s')},'opposite':{'spot':stat(z[z.same_side_choice.eq(0)],f'spot_ret_{h}s'),'fut':stat(z[z.same_side_choice.eq(0)],f'fut_ret_{h}s')},'spotMeanDiffBootstrap':boot(z,f'spot_ret_{h}s'),'futMeanDiffBootstrap':boot(z,f'fut_ret_{h}s')}
  summary[name]=q
 # stronger adverse queue subset: both placement queues <0, still descriptive
 sub=d[(d.pl_o_spot_q<0)&(d.pl_o_fut_q<0)].copy();subsum={}
 for name,z in [('CANONICAL120',sub[sub.cohort.eq('CANONICAL120')]),('OLDER173',sub[sub.cohort.eq('OLDER173')])]:
  subsum[name]={'rows':len(z),'sameRate':float(z.same_side_choice.mean()) if len(z) else None,'spot3sDiff':boot(z,'spot_ret_3s') if len(z)>10 else None,'fut3sDiff':boot(z,'fut_ret_3s') if len(z)>10 else None}
 out={'version':'OUR_C2_FRESH_PARENT_FORWARD_REVERSION_V1','status':'POST_HOC_ECONOMIC_FALSIFICATION','summary':summary,'bothQueuesAgainstAnchorSubset':subsum,'guards':['Future 1/3/5s spot/futures path is outcome-only and never a runtime feature.','Return is oriented to prior clean anchor; positive means market subsequently moves back toward prior side.','Base snapshot is strict-past <=2s before fresh parent placement; future snapshot is first at/after horizon and no more than 1s late.','Analysis asks economic plausibility of contrarian fresh-parent choice, not profitability of the Prediction order itself.']};OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
