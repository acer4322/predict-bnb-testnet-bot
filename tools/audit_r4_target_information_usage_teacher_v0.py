from __future__ import annotations

import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CSV = ROOT / 'data/research/r4_v0/hourly/target_strike_distance_formation_mode_rebuild_v2_rows.csv'
MODEL = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v0.joblib'
OUT = ROOT / 'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v0_counterfactual.json'


def qbin(s):
    return pd.qcut(s.rank(method='first'), 4, labels=['Q1','Q2','Q3','Q4'])


def main():
    pack = joblib.load(MODEL)
    feats = pack['features']['LOGIC_PLUS_PREDICT_STRIKE']
    model = pack['models']['LOGIC_PLUS_PREDICT_STRIKE']
    df = pd.read_csv(CSV).replace([np.inf,-np.inf],np.nan).dropna(subset=feats+['market_id','first_event_ms','is_add']).copy()
    mt = df.groupby('market_id',as_index=False)['first_event_ms'].min().sort_values('first_event_ms')
    markets = mt.market_id.astype(int).tolist(); n=len(markets); a=max(1,int(n*.70)); b=min(n,max(a+1,int(n*.85)))
    test=set(markets[b:]); d=df[df.market_id.astype(int).isin(test)].copy()
    d['repair']=1-d.is_add.astype(int)
    d['gap_bin']=qbin(d.pre_abs_payoff_gap)
    d['conf_group']=pd.cut(d.predict_edge,[-1,.10,.30,1.0],labels=['LOW_LT_.10','MOD_.10_.30','EXT_GE_.30'])
    d['time_group']=pd.cut(d.seconds_left,[-1,60,180,301],labels=['LATE_0_60','MID_60_180','EARLY_180_300'])

    # Controlled paired diagnostic: preserve magnitude, flip only sign relative to dominant.
    mag=d.strike_toward_dominant_bps.abs().clip(lower=1e-6)
    pos=d.copy(); neg=d.copy()
    pos['strike_toward_dominant_bps']=mag; pos['spot_supports_dominant']=1
    neg['strike_toward_dominant_bps']=-mag; neg['spot_supports_dominant']=0
    ppos=model.predict_proba(pos[feats])[:,1]
    pneg=model.predict_proba(neg[feats])[:,1]
    d['pRepair_if_support']=ppos; d['pRepair_if_oppose']=pneg; d['delta_support_minus_oppose']=ppos-pneg

    def group(cols):
        out=[]
        for key,g in d.groupby(cols,observed=True):
            if not isinstance(key,tuple): key=(key,)
            out.append({**{str(c):str(v) for c,v in zip(cols,key)},'n':int(len(g)),'observedRepairRate':float(g.repair.mean()),'meanPredRepairSupport':float(g.pRepair_if_support.mean()),'meanPredRepairOppose':float(g.pRepair_if_oppose.mean()),'meanDeltaSupportMinusOppose':float(g.delta_support_minus_oppose.mean()),'medianDelta':float(g.delta_support_minus_oppose.median())})
        return out

    rep={'version':'R4_TARGET_INFORMATION_USAGE_TEACHER_V0_COUNTERFACTUAL','researchOnly':True,'runtimePromotionAllowed':False,'testMarkets':sorted(test),'rows':int(len(d)),'diagnostic':'For each untouched chronological-test row, hold portfolio/time/Predict fixed and flip only signed spot-vs-strike support around zero at the same absolute magnitude. Delta is teacher-implied change in P(REPAIR/BUILD_WEAK_SIDE); this is model interpretation, not causal proof or action authority.','overall':{'meanDeltaSupportMinusOppose':float(d.delta_support_minus_oppose.mean()),'medianDelta':float(d.delta_support_minus_oppose.median()),'positiveDeltaRate':float((d.delta_support_minus_oppose>0).mean())},'byConfidence':group(['conf_group']),'byConfidenceGap':group(['conf_group','gap_bin']),'byConfidenceTime':group(['conf_group','time_group'])}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'overall':rep['overall'],'byConfidence':rep['byConfidence'],'byConfidenceGap':[x for x in rep['byConfidenceGap'] if x['n']>=80],'byConfidenceTime':[x for x in rep['byConfidenceTime'] if x['n']>=80]},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
