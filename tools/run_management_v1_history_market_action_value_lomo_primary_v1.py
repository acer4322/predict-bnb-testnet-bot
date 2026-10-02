from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
from collections import Counter
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error
from scipy.stats import spearmanr

SEED=20260907; EPS=1e-9
ACTION=['actionHistory','actionHold','actionHasSide','actionSideUp','actionRepair','actionExpand','actionPrice','actionQty']
PORT=['marketSideUp','upQty','downQty','cost','absNet','grossQty','currentUpPayoff','currentDownPayoff','currentFloor','currentBest','currentGap','repairDebtUP','repairDebtDOWN','totalDebt','freeSlots','liveSlots','liveCoreCount','liveRepairCount','liveExpandCount','liveCancelPendingCount','liveUpCount','liveDownCount','qLadderPresent','qLadderSideUp','qLadderRepair','pendingActive']
HIST=['historySideUp','historyAgeMs','historyRunLength','recentCleanCount','recentCleanUpRatio','recentRiskUpQty','recentRiskDownQty','recentRiskNet']
ECON=['marketCandidatePrice','marketCandidateQty','historyCandidatePrice','historyCandidateQty','candidateCrossSum','candidatePriceDiffHistoryMinusMarket']
SETS={'ACTION_ONLY':ACTION,'PORTFOLIO_ACTION':ACTION+PORT,'PORTFOLIO_HISTORY_ACTION':ACTION+PORT+HIST,'FULL_STATE_ACTION':ACTION+PORT+HIST+ECON}
TARGETS=['dFloorTerminal','dBestTerminal','dGapTerminal']

def finite(v):
    if v is None:return np.nan
    try:
        x=float(v); return x if math.isfinite(x) else np.nan
    except:return np.nan

def models():
    return {'RIDGE_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('m',Ridge(alpha=1.0))]),'EXTRATREES_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesRegressor(n_estimators=200,max_depth=4,min_samples_leaf=2,max_features='sqrt',random_state=SEED,n_jobs=4))])}

def macro_mae(y,p,g):
    vals=[]; by={}
    for m in sorted(set(g)):
        ix=np.where(g==m)[0]; v=float(mean_absolute_error(y[ix],p[ix])); vals.append(v); by[str(int(m))]=v
    return float(np.mean(vals)),by

def best_action(rows,y,p,g):
    hit=0; total=0; predc=Counter(); truec=Counter()
    for m in sorted(set(g)):
        ix=np.where(g==m)[0]; tv=[('MARKET_DIRECTION',0.0)]+[(rows[i]['action'],float(y[i])) for i in ix]; pv=[('MARKET_DIRECTION',0.0)]+[(rows[i]['action'],float(p[i])) for i in ix]
        mt=max(v for _,v in tv); mp=max(v for _,v in pv); ts={a for a,v in tv if abs(v-mt)<=1e-9}; ps={a for a,v in pv if abs(v-mp)<=1e-9}; hit+=int(bool(ts&ps)); total+=1
        for a in ts:truec[a]+=1
        for a in ps:predc[a]+=1
    return {'accuracy':hit/total if total else None,'hits':hit,'markets':total,'trueBestCounts':dict(truec),'predictedBestCounts':dict(predc)}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8')); rows=d['rows']; groups=np.asarray([int(r['marketId']) for r in rows],dtype=int); markets=sorted(set(groups))
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_ACTION_VALUE_LOMO_PRIMARY_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'nRows':len(rows),'nMarkets':len(markets),'featureSets':SETS,'targets':{},'boundary':['primary terminal vector only: dFloor/dBest/dGap','leave-one-market-out; both actions from held market excluded','MARKET_DIRECTION is known zero-delta reference, not learned','strict-past state/action features only','future exact-fork economics are targets only','fixed Ridge + fixed ExtraTrees(200); no tuning/threshold sweep','no winner/settlement/Target future features','consumed data only; no NEW24-B']}
    for target in TARGETS:
        y=np.asarray([float(r[target]) for r in rows],dtype=float); t={'stats':{'mean':float(np.mean(y)),'std':float(np.std(y)),'pos':int(np.sum(y>EPS)),'neg':int(np.sum(y<-EPS)),'zero':int(np.sum(np.abs(y)<=EPS))},'sets':{}}
        for sname,fs in SETS.items():
            X=np.asarray([[finite(r.get(k)) for k in fs] for r in rows],dtype=float); preds={k:np.zeros(len(rows)) for k in models()}
            for held in markets:
                te=np.where(groups==held)[0]; tr=np.where(groups!=held)[0]
                for name,model in models().items(): model.fit(X[tr],y[tr]); preds[name][te]=model.predict(X[te])
            sm={}
            for name,p in preds.items():
                mae=float(mean_absolute_error(y,p)); macro,by=macro_mae(y,p,groups); rho=spearmanr(y,p).statistic; nz=np.abs(y)>EPS
                sm[name]={'mae':mae,'macroMae':macro,'byMarket':by,'spearman':None if np.isnan(rho) else float(rho),'nonzeroSignAccuracy':float(np.mean(np.sign(p[nz])==np.sign(y[nz]))) if np.any(nz) else None,'bestAction':best_action(rows,y,p,groups)}
            t['sets'][sname]=sm
        comp={}
        for model in ('RIDGE_FIXED','EXTRATREES_FIXED'):
            a0=t['sets']['ACTION_ONLY'][model]; p0=t['sets']['PORTFOLIO_ACTION'][model]; comp[model]={}
            for sname in ('PORTFOLIO_ACTION','PORTFOLIO_HISTORY_ACTION','FULL_STATE_ACTION'):
                z=t['sets'][sname][model]; rec={'maeGainVsActionOnly':a0['mae']-z['mae'],'macroMaeGainVsActionOnly':a0['macroMae']-z['macroMae'],'marketWinsVsActionOnly':sum(z['byMarket'][str(int(m))]<a0['byMarket'][str(int(m))]-1e-12 for m in markets)}
                if sname!='PORTFOLIO_ACTION': rec.update({'maeGainVsPortfolio':p0['mae']-z['mae'],'macroMaeGainVsPortfolio':p0['macroMae']-z['macroMae'],'marketWinsVsPortfolio':sum(z['byMarket'][str(int(m))]<p0['byMarket'][str(int(m))]-1e-12 for m in markets)})
                comp[model][sname]=rec
        t['comparisons']=comp; out['targets'][target]=t
    verdict={}
    for target,t in out['targets'].items():
        portfolio=[]; history=[]; full=[]
        for model,c in t['comparisons'].items():
            z=c['PORTFOLIO_ACTION'];
            if z['maeGainVsActionOnly']>0 and z['macroMaeGainVsActionOnly']>0 and z['marketWinsVsActionOnly']>=17: portfolio.append(model)
            z=c['PORTFOLIO_HISTORY_ACTION'];
            if z['maeGainVsPortfolio']>0 and z['macroMaeGainVsPortfolio']>0 and z['marketWinsVsPortfolio']>=17: history.append(model)
            z=c['FULL_STATE_ACTION'];
            if z['maeGainVsPortfolio']>0 and z['macroMaeGainVsPortfolio']>0 and z['marketWinsVsPortfolio']>=17: full.append(model)
        verdict[target]={'portfolioIncrement':'SUPPORTED_DIAGNOSTIC' if portfolio else 'NO_GROUPED_INCREMENT','historyIncrementBeyondPortfolio':'SUPPORTED_DIAGNOSTIC' if history else 'NO_GROUPED_INCREMENT','fullStateIncrementBeyondPortfolio':'SUPPORTED_DIAGNOSTIC' if full else 'NO_GROUPED_INCREMENT','portfolioModels':portfolio,'historyModels':history,'fullModels':full}
    out['targetVerdicts']=verdict
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'nRows':len(rows),'nMarkets':len(markets),'targetVerdicts':verdict},ensure_ascii=False),flush=True)

if __name__=='__main__':main()
