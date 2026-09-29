from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error

SEED=20260907; EPS=1e-9
ACTION=['actionHistory','actionHold','actionHasSide','actionSideUp','actionRepair','actionExpand','actionPrice','actionQty']
PORT=['marketSideUp','upQty','downQty','cost','absNet','grossQty','currentUpPayoff','currentDownPayoff','currentFloor','currentBest','currentGap','repairDebtUP','repairDebtDOWN','totalDebt','freeSlots','liveSlots','liveCoreCount','liveRepairCount','liveExpandCount','liveCancelPendingCount','liveUpCount','liveDownCount','qLadderPresent','qLadderSideUp','qLadderRepair','pendingActive']
HIST=['historySideUp','historyAgeMs','historyRunLength','recentCleanCount','recentCleanUpRatio','recentRiskUpQty','recentRiskDownQty','recentRiskNet']
ECON=['marketCandidatePrice','marketCandidateQty','historyCandidatePrice','historyCandidateQty','candidateCrossSum','candidatePriceDiffHistoryMinusMarket']
SETS={'ACTION_ONLY':ACTION,'PORTFOLIO_ACTION':ACTION+PORT,'PORTFOLIO_HISTORY_ACTION':ACTION+PORT+HIST,'FULL_STATE_ACTION':ACTION+PORT+HIST+ECON}
TARGETS=['dFavoredTerminal','dOppositeTerminal']

def finite(v):
    if v is None:return np.nan
    try:
        x=float(v);return x if math.isfinite(x) else np.nan
    except:return np.nan

def models():
    return {'RIDGE_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('sc',StandardScaler()),('m',Ridge(alpha=1.0))]),'EXTRATREES_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesRegressor(n_estimators=150,max_depth=4,min_samples_leaf=2,max_features='sqrt',random_state=SEED,n_jobs=4))])}

def macro(y,p,g):
    by={};vals=[]
    for m in sorted(set(g)):
        ix=np.where(g==m)[0];v=float(mean_absolute_error(y[ix],p[ix]));by[str(int(m))]=v;vals.append(v)
    return float(np.mean(vals)),by

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8'));rows=d['rows'];g=np.asarray([int(r['marketId']) for r in rows],dtype=int);markets=sorted(set(g))
    out={'version':'MANAGEMENT_TRAINING_V1_FIXED_FAVORED_ACTION_VALUE_LOMO_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'nRows':len(rows),'nMarkets':len(markets),'targets':{},'boundary':['favored side fixed pre-branch as baseline current-market proposal','leave-one-market-out; both counterfactual actions from held market excluded','strict-past state/action features only','future exact-fork payoff deltas are targets only','fixed Ridge + fixed ExtraTrees(150); no tuning','no winner/settlement/Target future features','consumed data only; no NEW24-B']}
    for target in TARGETS:
        y=np.asarray([float(r[target]) for r in rows],dtype=float);t={'stats':{'mean':float(np.mean(y)),'std':float(np.std(y)),'pos':int(np.sum(y>EPS)),'neg':int(np.sum(y<-EPS)),'zero':int(np.sum(np.abs(y)<=EPS))},'sets':{}}
        for sname,fs in SETS.items():
            X=np.asarray([[finite(r.get(k)) for k in fs] for r in rows],dtype=float);preds={k:np.zeros(len(rows)) for k in models()}
            for held in markets:
                te=np.where(g==held)[0];tr=np.where(g!=held)[0]
                for name,model in models().items():model.fit(X[tr],y[tr]);preds[name][te]=model.predict(X[te])
            t['sets'][sname]={}
            for name,p in preds.items():
                mae=float(mean_absolute_error(y,p));mm,by=macro(y,p,g);nz=np.abs(y)>EPS
                t['sets'][sname][name]={'mae':mae,'macroMae':mm,'byMarket':by,'nonzeroSignAccuracy':float(np.mean(np.sign(p[nz])==np.sign(y[nz]))) if np.any(nz) else None}
        comp={}
        for model in ('RIDGE_FIXED','EXTRATREES_FIXED'):
            a0=t['sets']['ACTION_ONLY'][model];p0=t['sets']['PORTFOLIO_ACTION'][model];comp[model]={}
            for sname in ('PORTFOLIO_ACTION','PORTFOLIO_HISTORY_ACTION','FULL_STATE_ACTION'):
                z=t['sets'][sname][model];rec={'maeGainVsActionOnly':a0['mae']-z['mae'],'macroMaeGainVsActionOnly':a0['macroMae']-z['macroMae'],'winsVsActionOnly':sum(z['byMarket'][str(int(m))]<a0['byMarket'][str(int(m))]-1e-12 for m in markets)}
                if sname!='PORTFOLIO_ACTION':rec.update({'maeGainVsPortfolio':p0['mae']-z['mae'],'macroMaeGainVsPortfolio':p0['macroMae']-z['macroMae'],'winsVsPortfolio':sum(z['byMarket'][str(int(m))]<p0['byMarket'][str(int(m))]-1e-12 for m in markets)})
                comp[model][sname]=rec
        t['comparisons']=comp;out['targets'][target]=t
    verdict={}
    for target,t in out['targets'].items():
        pv=[];hv=[];fv=[]
        for model,c in t['comparisons'].items():
            p=c['PORTFOLIO_ACTION']
            if p['maeGainVsActionOnly']>0 and p['macroMaeGainVsActionOnly']>0 and p['winsVsActionOnly']>=17:pv.append(model)
            h=c['PORTFOLIO_HISTORY_ACTION']
            if h['maeGainVsActionOnly']>0 and h['macroMaeGainVsActionOnly']>0 and h['winsVsActionOnly']>=17 and h['maeGainVsPortfolio']>0 and h['macroMaeGainVsPortfolio']>0 and h['winsVsPortfolio']>=17:hv.append(model)
            f=c['FULL_STATE_ACTION']
            if f['maeGainVsActionOnly']>0 and f['macroMaeGainVsActionOnly']>0 and f['winsVsActionOnly']>=17 and f['maeGainVsPortfolio']>0 and f['macroMaeGainVsPortfolio']>0 and f['winsVsPortfolio']>=17:fv.append(model)
        verdict[target]={'portfolioIncrement':'SUPPORTED_DIAGNOSTIC' if pv else 'NO_GROUPED_INCREMENT','historyIndependentIncrement':'SUPPORTED_DIAGNOSTIC' if hv else 'NO_GROUPED_INCREMENT','fullStateIncrement':'SUPPORTED_DIAGNOSTIC' if fv else 'NO_GROUPED_INCREMENT','portfolioModels':pv,'historyModels':hv,'fullModels':fv}
    out['targetVerdicts']=verdict
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'targetVerdicts':verdict},ensure_ascii=False))
if __name__=='__main__':main()
