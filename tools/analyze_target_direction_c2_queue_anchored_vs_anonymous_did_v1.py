from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
L=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
A=L/'C2_FRESH_PARENT_FORWARD_REVERSION_V1.csv'; N=L/'C2_ANONYMOUS_CANCEL_PLACEBO_V1.csv'; O=L/'C2_QUEUE_ANCHORED_VS_ANONYMOUS_DID_V1.json'; SEED=20260907

def prep():
 a=pd.read_csv(A,low_memory=False)[['cohort','market_id','same_side_choice','pl_o_spot_q','pl_o_fut_q']].copy();a.columns=['cohort','market_id','same_side','spot','fut'];a['source']='ANCHORED'
 n=pd.read_csv(N,low_memory=False)[['cohort','market_id','same_side','o_spot_q','o_fut_q']].copy();n.columns=['cohort','market_id','same_side','spot','fut'];n['source']='ANONYMOUS'
 d=pd.concat([a,n],ignore_index=True);d['adverse']=(d.spot<0)&(d.fut<0);return d

def effect(z):
 out={}
 for src in ['ANCHORED','ANONYMOUS']:
  q=z[z.source.eq(src)];a=q[q.adverse];b=q[~q.adverse];out[src]={'rows':len(q),'markets':int(q.market_id.nunique()),'adverseN':len(a),'otherN':len(b),'adverseSameRate':float(a.same_side.mean()),'otherSameRate':float(b.same_side.mean()),'shift':float(a.same_side.mean()-b.same_side.mean())}
 out['DID']=out['ANCHORED']['shift']-out['ANONYMOUS']['shift'];return out

def boot(z,resamples=20000):
 # per-market 8 cells: source x adverse x [n,y]
 mids=np.array(sorted(z.market_id.unique()));rows=[]
 for m in mids:
  q=z[z.market_id.eq(m)];r=[]
  for src in ['ANCHORED','ANONYMOUS']:
   for adv in [1,0]:
    s=q[(q.source.eq(src))&(q.adverse.eq(bool(adv)))];r += [len(s),float(s.same_side.sum())]
  rows.append(r)
 a=np.asarray(rows,float);rng=np.random.default_rng(SEED);vals=[];ach=[];ano=[];chunk=500
 for i in range(0,resamples,chunk):
  k=min(chunk,resamples-i);ix=rng.integers(0,len(a),size=(k,len(a)));s=a[ix].sum(1)
  # positions anchored adv n/y, anchored other n/y, anonymous adv n/y, anonymous other n/y
  good=(s[:,0]>0)&(s[:,2]>0)&(s[:,4]>0)&(s[:,6]>0)
  s=s[good];ea=s[:,1]/s[:,0]-s[:,3]/s[:,2];en=s[:,5]/s[:,4]-s[:,7]/s[:,6];vals.extend((ea-en).tolist());ach.extend(ea.tolist());ano.extend(en.tolist())
 return {'DIDci95':[float(x) for x in np.quantile(vals,[.025,.975])],'anchoredShiftCi95':[float(x) for x in np.quantile(ach,[.025,.975])],'anonymousShiftCi95':[float(x) for x in np.quantile(ano,[.025,.975])],'resamples':len(vals)}
def main():
 d=prep();out={'version':'OUR_C2_QUEUE_ANCHORED_VS_ANONYMOUS_DID_V1','status':'MECHANISM_FALSIFICATION','summary':{}}
 for k,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]:
  out['summary'][k]={**effect(z),**boot(z)}
 out['guards']=['Anchored population = first fresh HQ parent side choice; anonymous population = first non-HQ, non-prechain cancel candidate placebo.','Anonymous ownership is unproven, so DID is a falsification contrast, not causal effect.','Market-cluster bootstrap resamples markets and preserves both populations within sampled market where present.','No runtime authority.']
 O.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
