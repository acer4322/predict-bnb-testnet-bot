from __future__ import annotations
import json, math
from pathlib import Path
import numpy as np
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, r2_score

ROOT=Path(__file__).resolve().parents[1]
NPZ=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_target_favorable_pair_safe_surplus_v1_full.npz'
META=ROOT/'data/research/r4_v0/p0_provenance_v1/eth_target_favorable_pair_safe_surplus_v1_full.meta.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_ETH_QUANTITY_OBJECTIVE_DECOMPOSITION_V1.json'

def med_abs_share(y,p):
    return float(np.median(np.abs(np.expm1(y)-np.expm1(p))))

def score(y,p):
    return {'n':int(len(y)),'maeLogQty':float(mean_absolute_error(y,p)),'r2LogQty':float(r2_score(y,p)),'medianAbsShareError':med_abs_share(y,p)}

def main():
    z=np.load(NPZ); meta=json.loads(META.read_text(encoding='utf-8')); F=meta['features']; ix={f:i for i,f in enumerate(F)}
    X=z['X'].astype(np.float64); mids=z['market_id']; rel=z['relation']; y=X[:,ix['candidate_qty_log']].copy()
    weak=rel==1
    mids_unique=np.unique(mids[weak]); n=len(mids_unique); a=int(n*.60); b=int(n*.80)
    train_m=set(int(x) for x in mids_unique[:a]); val_m=set(int(x) for x in mids_unique[a:b]); test_m=set(int(x) for x in mids_unique[b:])
    tr=weak & np.isin(mids,list(train_m)); va=weak & np.isin(mids,list(val_m)); te=weak & np.isin(mids,list(test_m))
    bal_names=['seconds_left','pair_coverage','absnet_ratio','gross_log']
    econ_add=['floor_ratio','best_pnl_ratio','avg_cost_up','avg_cost_down','avg_cost_gap','candidate_side_up','candidate_price','opp_unmatched_ratio','pair_reserve_ratio','pair_debt_ratio','net_pair_reserve_ratio','last_maker_age_log','recent_repair_frac','recent_expand_frac','recent_maker_log']
    bal=[ix[f] for f in bal_names]; econ=[ix[f] for f in bal_names+econ_add]
    params=dict(max_iter=200,learning_rate=.05,max_leaf_nodes=31,l2_regularization=1.0,random_state=7)
    mb=HistGradientBoostingRegressor(**params).fit(X[tr][:,bal],y[tr])
    me=HistGradientBoostingRegressor(**params).fit(X[tr][:,econ],y[tr])
    out={'version':'TARGET_ETH_QUANTITY_OBJECTIVE_DECOMPOSITION_V1','researchOnly':True,'actionAuthority':False,
         'coverage':{'weakRows':int(weak.sum()),'trainMarkets':len(train_m),'validMarkets':len(val_m),'testMarkets':len(test_m),'trainRows':int(tr.sum()),'validRows':int(va.sum()),'testRows':int(te.sum())},
         'features':{'balance':bal_names,'economicAdded':econ_add},'splits':{}}
    for nm,mask in [('VALID20',va),('TEST20',te)]:
        pb=mb.predict(X[mask][:,bal]); pe=me.predict(X[mask][:,econ]); yy=y[mask]
        gross=np.expm1(X[mask][:,ix['gross_log']]); gap=gross*X[mask][:,ix['absnet_ratio']]; pg=np.log1p(np.maximum(gap,0))
        sb=score(yy,pb); se=score(yy,pe); sg=score(yy,pg)
        out['splits'][nm]={'directGapHeuristic':sg,'balanceModel':sb,'economicModel':se,
                           'economicLiftVsBalance':{'maeReductionFraction':(sb['maeLogQty']-se['maeLogQty'])/sb['maeLogQty'],'medianShareErrorReductionFraction':(sb['medianAbsShareError']-se['medianAbsShareError'])/sb['medianAbsShareError']},
                           'economicLiftVsGap':{'maeReductionFraction':(sg['maeLogQty']-se['maeLogQty'])/sg['maeLogQty'],'medianShareErrorReductionFraction':(sg['medianAbsShareError']-se['medianAbsShareError'])/sg['medianAbsShareError']}}
    out['interpretation']={'passEconomicLiftBothSplits':all(out['splits'][s]['economicLiftVsBalance']['maeReductionFraction']>0 for s in ('VALID20','TEST20')),
                           'gapHeuristicIsCompetitiveOnTest':out['splits']['TEST20']['directGapHeuristic']['maeLogQty']<=out['splits']['TEST20']['economicModel']['maeLogQty']}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8'); print(json.dumps(out,indent=2))
if __name__=='__main__':main()
