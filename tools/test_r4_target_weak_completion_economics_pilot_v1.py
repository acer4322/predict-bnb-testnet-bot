from __future__ import annotations
import json
from pathlib import Path
import numpy as np, pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/hourly/r4_target_information_usage_teacher_v1_rows.csv'
OUT=ROOT/'data/research/r4_v0/hourly/r4_target_weak_completion_economics_pilot_v1.json'

def met(y,p):
    return {'n':int(len(y)),'rate':float(np.mean(y)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None,'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,p,labels=[0,1]))}
def fit_eval(tr,te,fs,seed=1):
    m=HistGradientBoostingClassifier(max_depth=3,learning_rate=.05,max_iter=120,l2_regularization=4.,random_state=seed).fit(tr[fs],tr.repair.astype(int))
    return met(te.repair.astype(int),m.predict_proba(te[fs])[:,1])
def main():
    d=pd.read_csv(SRC)
    d=d[np.isfinite(d.price)&np.isfinite(d.predict_up_mid)&np.isfinite(d.predict_down_mid)].copy()
    d['parent_predict_mid']=np.where(d.side.eq('UP'),d.predict_up_mid,d.predict_down_mid)
    d['price_minus_parent_predict']=d.price-d.parent_predict_mid
    d['cheapness_vs_predict']=d.parent_predict_mid-d.price
    d['completion_payoff_per_share']=1.0-d.price
    # small chronological pilot: first 48 markets only; last 12 untouched test within pilot
    order=d.groupby('market_id').first_event_ms.min().sort_values().index.tolist()[:48]
    x=d[d.market_id.isin(order)].copy()
    trm=order[:36]; tem=order[36:48]
    tr=x[x.market_id.isin(trm)].copy(); te=x[x.market_id.isin(tem)].copy()
    base=['seconds_left','pre_abs_payoff_gap','pre_risk_deficit','predict_edge','strike_toward_dominant_bps','predict_supports_dominant','spot_supports_dominant']
    actionprice=['price','parent_predict_mid','cheapness_vs_predict','completion_payoff_per_share']
    res={}
    res['BASE']=fit_eval(tr,te,base,10)
    res['BASE_PLUS_PARENT_PREDICT']=fit_eval(tr,te,base+['parent_predict_mid'],11)
    res['BASE_PLUS_ACTION_PRICE_DIAGNOSTIC']=fit_eval(tr,te,base+actionprice,12)
    b=res['BASE']; a=res['BASE_PLUS_ACTION_PRICE_DIAGNOSTIC']
    res['incrementActionPriceDiagnostic']={'deltaAuc':a['auc']-b['auc'],'deltaAp':a['ap']-b['ap'],'logLossImprovement':b['logLoss']-a['logLoss']}
    # descriptive interaction tables, fixed bins inherited from prior confidence/time work; price bins are quartiles within pilot, outcome-blind
    x['conf_phase']=pd.cut(x.predict_edge,[-1,.10,.30,1],labels=['LOW','MOD','EXTREME'])
    x['time_phase']=pd.cut(x.seconds_left,[-1,60,180,301],labels=['LATE','MID','EARLY'])
    desc=[]
    for (c,t,mode),g in x.groupby(['conf_phase','time_phase','mode'],observed=True):
        desc.append({'conf':str(c),'phase':str(t),'mode':str(mode),'n':int(len(g)),'medianPrice':float(g.price.median()),'medianParentPredict':float(g.parent_predict_mid.median()),'medianCheapnessVsPredict':float(g.cheapness_vs_predict.median()),'medianCompletionPayoff':float(g.completion_payoff_per_share.median()),'medianGap':float(g.pre_abs_payoff_gap.median())})
    # gap quartiles, outcome-blind, compare REPAIR vs ADD economics
    x['gap_q']=pd.qcut(x.pre_abs_payoff_gap.rank(method='first'),4,labels=['Q1','Q2','Q3','Q4'])
    gap=[]
    for (q,mode),g in x.groupby(['gap_q','mode'],observed=True):
        gap.append({'gapQ':str(q),'mode':str(mode),'n':int(len(g)),'medianPrice':float(g.price.median()),'medianCheapnessVsPredict':float(g.cheapness_vs_predict.median()),'medianCompletionPayoff':float(g.completion_payoff_per_share.median())})
    art={'version':'R4_TARGET_WEAK_COMPLETION_ECONOMICS_PILOT_V1','researchOnly':True,'smallPilot':True,'runtimePromotionAllowed':False,'coverage':{'pilotMarkets':48,'trainMarkets':36,'testMarkets':12,'rows':int(len(x)),'trainRows':int(len(tr)),'testRows':int(len(te))},'label':'repair=BUILD_WEAK_SIDE vs add=ALLOW_ASYMMETRY','importantGuard':'Target chosen Maker price is ACTION OUTPUT and is used only as retrospective diagnostic/oracle evidence here; it is NOT runtime-safe input. Positive result only justifies reconstructing strict-past public weak-side book economics in a later test.','featureSets':{'base':base,'actionPriceDiagnostic':actionprice},'results':res,'descriptiveByConfidencePhase':desc,'descriptiveByGap':gap}
    OUT.write_text(json.dumps(art,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'coverage':art['coverage'],'results':res,'sampleDesc':desc[:12]},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
