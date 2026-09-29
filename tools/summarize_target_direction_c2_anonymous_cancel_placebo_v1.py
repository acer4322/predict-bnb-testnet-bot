from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
LANE=Path('data/research/r4_v0/our_target_direction_confidence_falsification_v1_20260907')
CSV=LANE/'C2_ANONYMOUS_CANCEL_PLACEBO_V1.csv'; OUT=LANE/'C2_ANONYMOUS_CANCEL_PLACEBO_V1.json'; SEED=20260907

def market_boot_diff(z, mask, resamples=10000):
    x=z.copy();x['g']=mask.astype(int);x['gy']=x.g*x.same_side;x['ngy']=(1-x.g)*x.same_side;x['ng']=1-x.g
    a=x.groupby('market_id').agg(g=('g','sum'),gy=('gy','sum'),ng=('ng','sum'),ngy=('ngy','sum')).to_numpy(float)
    obs=(x.loc[mask,'same_side'].mean()-x.loc[~mask,'same_side'].mean()) if mask.any() and (~mask).any() else np.nan
    rng=np.random.default_rng(SEED);vals=[];chunk=500
    for _ in range(0,resamples,chunk):
        n=min(chunk,resamples-len(vals));ix=rng.integers(0,len(a),size=(n,len(a)));s=a[ix].sum(axis=1)
        good=(s[:,0]>0)&(s[:,2]>0);v=s[good,1]/s[good,0]-s[good,3]/s[good,2];vals.extend(v.tolist())
    return {'group1N':int(mask.sum()),'group0N':int((~mask).sum()),'group1Rate':float(x.loc[mask,'same_side'].mean()),'group0Rate':float(x.loc[~mask,'same_side'].mean()),'difference':float(obs),'ci95':[float(v) for v in np.quantile(vals,[.025,.975])],'resamples':len(vals)}

def auc(y,s):
    y=np.asarray(y,int);s=np.asarray(s,float);pos=y.sum();neg=len(y)-pos
    if not pos or not neg:return None
    r=pd.Series(s).rank(method='average').to_numpy();return float((r[y==1].sum()-pos*(pos+1)/2)/(pos*neg))
def qdiag(z,col):
    q=np.unique(np.nanquantile(z[col],[0,.25,.5,.75,1]));out=[]
    if len(q)<3:return out
    x=z.copy();x['bin']=pd.cut(x[col],q,include_lowest=True,duplicates='drop')
    for k,g in x.groupby('bin',observed=True):out.append({'bin':str(k),'n':len(g),'markets':int(g.market_id.nunique()),'median':float(g[col].median()),'sameRate':float(g.same_side.mean())})
    return out

def one(z):
    both=(z.o_spot_q<0)&(z.o_fut_q<0);meanq=(z.o_spot_q+z.o_fut_q)/2
    return {'rows':len(z),'markets':int(z.market_id.nunique()),'sameRate':float(z.same_side.mean()),
            'sameMedianSpotQueue':float(z.loc[z.same_side==1,'o_spot_q'].median()),'oppMedianSpotQueue':float(z.loc[z.same_side==0,'o_spot_q'].median()),
            'sameMedianFutQueue':float(z.loc[z.same_side==1,'o_fut_q'].median()),'oppMedianFutQueue':float(z.loc[z.same_side==0,'o_fut_q'].median()),
            'bothAdverse':market_boot_diff(z,both),'negativeMeanQueue':market_boot_diff(z,meanq<0),
            'aucNegSpotQueue':auc(z.same_side,-z.o_spot_q),'aucNegFutQueue':auc(z.same_side,-z.o_fut_q),'aucNegMeanQueue':auc(z.same_side,-meanq),
            'spotQuartiles':qdiag(z,'o_spot_q'),'futuresQuartiles':qdiag(z,'o_fut_q'),'postActions':z.post_action.value_counts().to_dict()}
def main():
    d=pd.read_csv(CSV,low_memory=False);out={'version':'OUR_C2_ANONYMOUS_CANCEL_PLACEBO_V1','status':'OWNERSHIP_WEAK_PLACEBO_ONLY','summary':{}}
    for k,z in [('CANONICAL120',d[d.cohort.eq('CANONICAL120')]),('OLDER173',d[d.cohort.eq('OLDER173')]),('ALL',d)]:out['summary'][k]=one(z)
    out['interpretationGuard']=['Anonymous candidates are not Target-owned ground truth.','Pattern replication in anonymous candidates argues for generic book/inference mechanics; non-replication only weakly supports Target specificity due population mismatch.','Candidates close to HQ parent placements and chains traceable to pre-conflict candidates were excluded upstream.','No runtime authority.']
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(out,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
