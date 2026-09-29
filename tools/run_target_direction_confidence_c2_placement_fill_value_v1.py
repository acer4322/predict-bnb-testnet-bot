from __future__ import annotations
import json
from pathlib import Path
import numpy as np,pandas as pd
P=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907/C2_PLACEMENT_TIME_PASSIVE_VALUE_V1.csv')
E=Path('data/research/r4_v0/OUR_C2_ALIGNED_RENEWED_RISK_EPISODES_V2_20260907.csv')
p=pd.read_csv(P,low_memory=False);e=pd.read_csv(E,low_memory=False)[['market_id','entry_ms','renewed_clean_lb_positive','renewed_clean_birth_lower_5s']]
d=p.merge(e,on=['market_id','entry_ms'],how='left',validate='one_to_one')
d['pair_cost_le1']=d.pair_cost_parent_plus_oppask.le(1+1e-12);d['at_or_below_bid']=d.location.eq('AT_OR_BELOW_BID');d['placement_delay_ms']=d.placement_first_ms-d.entry_ms
def groupstats(z,key):
 out=[]
 for k,g in z.groupby(key,dropna=False):out.append({'group':str(k),'n':len(g),'markets':g.market_id.nunique(),'renewalRate':float(g.renewed_clean_lb_positive.mean()),'renewedQty':float(g.renewed_clean_birth_lower_5s.sum()),'medianSavingVsAsk':float(g.saving_vs_ask.median()),'medianPairSurplus':float(g.pair_surplus.median()),'medianPlacementDelayMs':float(g.placement_delay_ms.median())})
 return out
summ={'ALL':{'location':groupstats(d,'location'),'pairCostLe1':groupstats(d,'pair_cost_le1')}}
for sp in ['TRAIN','VALIDATION','TEST']:
 z=d[d.split.eq(sp)];summ[sp]={'location':groupstats(z,'location'),'pairCostLe1':groupstats(z,'pair_cost_le1')}
# bootstrap RD at/below bid vs others, whole-market cluster within each split
def boot(z,B=5000):
 x=z.copy();mids=x.market_id.unique();rows=[]
 for mid in mids:
  m=x[x.market_id.eq(mid)];a=m[m.at_or_below_bid];b=m[~m.at_or_below_bid];rows.append([len(a),a.renewed_clean_lb_positive.sum(),len(b),b.renewed_clean_lb_positive.sum()])
 g=np.asarray(rows,float);rng=np.random.default_rng(20260907);ix=rng.integers(0,len(g),size=(B,len(g)));s=g[ix].sum(1);ra=np.divide(s[:,1],s[:,0],out=np.full(B,np.nan),where=s[:,0]>0);rb=np.divide(s[:,3],s[:,2],out=np.full(B,np.nan),where=s[:,2]>0);vals=ra-rb
 a=x[x.at_or_below_bid];b=x[~x.at_or_below_bid];return {'belowBidN':len(a),'otherN':len(b),'belowBidRate':float(a.renewed_clean_lb_positive.mean()),'otherRate':float(b.renewed_clean_lb_positive.mean()),'riskDifference':float(a.renewed_clean_lb_positive.mean()-b.renewed_clean_lb_positive.mean()),'bootstrap95CI':[float(v) for v in np.nanquantile(vals,[.025,.975])],'markets':len(mids),'resamples':B}
rd={sp:boot(d[d.split.eq(sp)]) for sp in ['VALIDATION','TEST']}
out={'version':'OUR_C2_PLACEMENT_FILL_VALUE_V1','status':'RESEARCH_ONLY','rows':len(d),'markets':d.market_id.nunique(),'renewedPositive':int(d.renewed_clean_lb_positive.sum()),'summary':summ,'belowBidVsOther':rd,'guards':['Conditioned on a new HQ same-side Maker parent being placed within 5s; this is execution/fill-hazard analysis, not admission-source causality.','Location and pair cost use strict-past public book at placement plus inferred parent price.','Outcome is conservative renewed clean lower-bound within same 5s episode.']}
Path('data/research/r4_v0/OUR_C2_PLACEMENT_FILL_VALUE_V1_20260907.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
