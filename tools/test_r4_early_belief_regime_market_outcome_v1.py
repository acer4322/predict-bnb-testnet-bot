from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

ROOT=Path(__file__).resolve().parents[1]
FILES={
 'FRESH24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_fresh24_v1_rows.csv',
 'UNSEEN24':ROOT/'data/research/r4_v0/hourly/r4_parallel_belief_hft_shadow_unseen24_v1_rows.csv',
}
OUT=ROOT/'data/research/r4_v0/hourly/r4_early_belief_regime_market_outcome_v1.json'

# Outcome-blind belief regimes inherited from prior map: per-cohort tertiles of p_build and p_prepare.
def add_regimes(df):
    # cuts are based on the whole cohort score distribution only, never on outcomes.
    for col,new in [('p_build','buildRegime'),('p_prepare_role_routed','prepareRegime')]:
        qs=df[col].quantile([1/3,2/3]).to_numpy()
        df[new]=np.where(df[col] <= qs[0],'LOW',np.where(df[col] <= qs[1],'MID','HIGH'))
    return df

def market_rows(df):
    early=df[(df.seconds_left>=180)&(df.seconds_left<=300)].copy()
    # good regime from prior cross-cohort shadow map: BUILD=MID and PREPARE in LOW or MID.
    early['good']=((early.buildRegime=='MID') & (early.prepareRegime.isin(['LOW','MID']))).astype(int)
    out=[]
    for mid,z in early.groupby('marketId'):
        if len(z)<20: continue
        # durable/final are constant market-level fields in shadow rows.
        out.append({
            'marketId':int(mid),'ticks':int(len(z)),'goodFrac':float(z.good.mean()),
            'goodTicks':int(z.good.sum()),
            'durable':int(bool(z.durableBaseMarket.iloc[0])),
            'finalFloor':float(z.finalFloorMarket.iloc[0]),
            'finalAbsNet':float(z.finalAbsNetMarket.iloc[0]),
            'meanFloorDelta5s':float(z.floorDelta5s.mean()),
            'meanAbsNetDelta5s':float(z.absNetDelta5s.mean()),
        })
    return pd.DataFrame(out)

def corr(x,y):
    if len(x)<3 or np.std(x)==0 or np.std(y)==0:return None
    return float(np.corrcoef(x,y)[0,1])

def summarize(m):
    d={
      'markets':int(len(m)),
      'durableRate':float(m.durable.mean()) if len(m) else None,
      'meanGoodFrac':float(m.goodFrac.mean()) if len(m) else None,
      'corrGoodFracFinalFloor':corr(m.goodFrac,m.finalFloor),
      'corrGoodFracFinalAbsNet':corr(m.goodFrac,m.finalAbsNet),
      'corrGoodFracMeanFloorDelta5s':corr(m.goodFrac,m.meanFloorDelta5s),
      'corrGoodFracMeanAbsNetDelta5s':corr(m.goodFrac,m.meanAbsNetDelta5s),
    }
    if len(m) and m.durable.nunique()>1:
        d['aucGoodFracDurable']=float(roc_auc_score(m.durable,m.goodFrac))
    else:d['aucGoodFracDurable']=None
    # Outcome-blind quartiles by goodFrac rank; report only, no thresholds promoted.
    if len(m)>=8:
        q=pd.qcut(m.goodFrac.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4'])
        d['quartiles']=[]
        for lab in ['Q1','Q2','Q3','Q4']:
            z=m[q==lab]
            d['quartiles'].append({'q':lab,'n':int(len(z)),'meanGoodFrac':float(z.goodFrac.mean()),'durableRate':float(z.durable.mean()),'meanFinalFloor':float(z.finalFloor.mean()),'meanFinalAbsNet':float(z.finalAbsNet.mean())})
    return d

def main():
    rep={'version':'R4_EARLY_BELIEF_REGIME_MARKET_OUTCOME_V1','researchOnly':True,'runtimePromotionAllowed':False,'definition':'Early phase 180-300s. Good belief regime fixed from prior cross-cohort map: BUILD=MID and PREPARE in LOW/MID. Regime boundaries are outcome-blind score tertiles within each cohort; no result-based threshold search.' ,'cohorts':{},'crossCohortDirection':{}}
    allm=[]
    for name,p in FILES.items():
        df=pd.read_csv(p)
        df=add_regimes(df)
        m=market_rows(df);m['cohort']=name;allm.append(m)
        rep['cohorts'][name]={'summary':summarize(m),'markets':m.to_dict(orient='records')}
    # Direction replication check only, not pooled tuning.
    metrics=['aucGoodFracDurable','corrGoodFracFinalFloor','corrGoodFracFinalAbsNet','corrGoodFracMeanFloorDelta5s','corrGoodFracMeanAbsNetDelta5s']
    for k in metrics:
        vals={n:rep['cohorts'][n]['summary'].get(k) for n in FILES}
        # favorable sign: durable/floor/floorDelta positive, absNet/future absNetDelta negative
        fav_positive=k in {'aucGoodFracDurable','corrGoodFracFinalFloor','corrGoodFracMeanFloorDelta5s'}
        # AUC favorable is >.5 rather than >0
        if k=='aucGoodFracDurable': same=all(v is not None and v>.5 for v in vals.values())
        elif fav_positive:same=all(v is not None and v>0 for v in vals.values())
        else:same=all(v is not None and v<0 for v in vals.values())
        rep['crossCohortDirection'][k]={'values':vals,'favorableBoth':bool(same)}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohorts':{k:v['summary'] for k,v in rep['cohorts'].items()},'crossCohortDirection':rep['crossCohortDirection']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
