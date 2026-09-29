from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
from collections import defaultdict,Counter
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
SETS={
 'ACTION_ONLY':ACTION,
 'PORTFOLIO_ACTION':ACTION+PORT,
 'PORTFOLIO_HISTORY_ACTION':ACTION+PORT+HIST,
 'FULL_STATE_ACTION':ACTION+PORT+HIST+ECON,
}
TARGETS=['dFloor5000ms','dBest5000ms','dGap5000ms','dFloor10000ms','dBest10000ms','dGap10000ms','dFloorTerminal','dBestTerminal','dGapTerminal']

def finite(v):
    if v is None:return np.nan
    try:
        x=float(v); return x if math.isfinite(x) else np.nan
    except:return np.nan

def models():
    return {
      'RIDGE_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('m',Ridge(alpha=1.0))]),
      'EXTRATREES_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesRegressor(n_estimators=400,max_depth=4,min_samples_leaf=2,max_features='sqrt',random_state=SEED,n_jobs=4))])}

def macro_mae(y,p,g):
    vals=[]; by={}
    for m in sorted(set(g)):
        ix=np.where(g==m)[0]; v=float(mean_absolute_error(y[ix],p[ix])); vals.append(v); by[str(int(m))]=v
    return float(np.mean(vals)),by

def best_action_accuracy(rows,y,p,g):
    ok=0; total=0; pred_counts=Counter(); true_counts=Counter(); details=[]
    for m in sorted(set(g)):
        ix=np.where(g==m)[0]
        # MARKET_DIRECTION is the reference action with known delta 0.
        cand_true=[('MARKET_DIRECTION',0.0)]+[(str(rows[i]['action']),float(y[i])) for i in ix]
        cand_pred=[('MARKET_DIRECTION',0.0)]+[(str(rows[i]['action']),float(p[i])) for i in ix]
        mt=max(v for _,v in cand_true); mp=max(v for _,v in cand_pred)
        true_set={a for a,v in cand_true if abs(v-mt)<=1e-9}; pred_set={a for a,v in cand_pred if abs(v-mp)<=1e-9}
        hit=bool(true_set & pred_set); ok+=int(hit); total+=1
        for a in pred_set: pred_counts[a]+=1
        for a in true_set: true_counts[a]+=1
        details.append({'marketId':int(m),'hit':hit,'trueBest':sorted(true_set),'predBest':sorted(pred_set)})
    return {'accuracy':ok/total if total else None,'hits':ok,'markets':total,'predictedBestCounts':dict(pred_counts),'trueBestCounts':dict(true_counts),'details':details}

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--dataset',required=True); ap.add_argument('--output',required=True); a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8')); rows=d['rows']; groups=np.asarray([int(r['marketId']) for r in rows],dtype=int)
    out={'version':'MANAGEMENT_TRAINING_V1_HISTORY_MARKET_ACTION_VALUE_LOMO_V1','date':'2026-09-07','researchOnly':True,'runtimeAuthority':False,'nRows':len(rows),'nMarkets':len(set(groups)),'featureSets':SETS,'targets':{},'boundary':['leave-one-market-out only; both counterfactual actions from held market excluded together','MARKET_DIRECTION is zero-delta reference, not a learned row','strict-past state/action features only','future exact-fork economics are targets only','fixed Ridge and ExtraTrees; no threshold/utility sweep','no winner/settlement/Target-future features','consumed exact-fork data only; no NEW24-B']}
    for target in TARGETS:
        y=np.asarray([float(r.get(target) or 0.0) for r in rows],dtype=float)
        tout={'targetStats':{'mean':float(np.mean(y)),'std':float(np.std(y)),'pos':int(np.sum(y>EPS)),'neg':int(np.sum(y<-EPS)),'zero':int(np.sum(np.abs(y)<=EPS))},'featureSets':{}}
        for sname,features in SETS.items():
            X=np.asarray([[finite(r.get(f)) for f in features] for r in rows],dtype=float)
            preds={name:np.zeros(len(rows)) for name in models()}
            for held in sorted(set(groups)):
                te=np.where(groups==held)[0]; tr=np.where(groups!=held)[0]
                for name,model in models().items():
                    model.fit(X[tr],y[tr]); preds[name][te]=model.predict(X[te])
            sm={}
            for name,p in preds.items():
                mae=float(mean_absolute_error(y,p)); macro,by=macro_mae(y,p,groups); rho=spearmanr(y,p).statistic
                sign_mask=np.abs(y)>EPS
                sign_acc=float(np.mean(np.sign(p[sign_mask])==np.sign(y[sign_mask]))) if np.any(sign_mask) else None
                sm[name]={'mae':mae,'marketMacroMae':macro,'marketMae':by,'spearman':None if np.isnan(rho) else float(rho),'nonzeroSignAccuracy':sign_acc,'bestAction':best_action_accuracy(rows,y,p,groups)}
            tout['featureSets'][sname]=sm
        # Paired comparisons against ACTION_ONLY and PORTFOLIO_ACTION, same fixed model/folds.
        comp={}
        for model in ('RIDGE_FIXED','EXTRATREES_FIXED'):
            comp[model]={}
            b=tout['featureSets']['ACTION_ONLY'][model]; pbase=tout['featureSets']['PORTFOLIO_ACTION'][model]
            for sname in ('PORTFOLIO_ACTION','PORTFOLIO_HISTORY_ACTION','FULL_STATE_ACTION'):
                z=tout['featureSets'][sname][model]
                comp[model][sname]={'rowMaeGainVsActionOnly':b['mae']-z['mae'],'marketMacroMaeGainVsActionOnly':b['marketMacroMae']-z['marketMacroMae'],'heldMarketWinsVsActionOnly':sum(z['marketMae'][str(int(m))]<b['marketMae'][str(int(m))]-1e-12 for m in sorted(set(groups)))}
            for sname in ('PORTFOLIO_HISTORY_ACTION','FULL_STATE_ACTION'):
                z=tout['featureSets'][sname][model]
                comp[model][sname].update({'rowMaeGainVsPortfolio':pbase['mae']-z['mae'],'marketMacroMaeGainVsPortfolio':pbase['marketMacroMae']-z['marketMacroMae'],'heldMarketWinsVsPortfolio':sum(z['marketMae'][str(int(m))]<pbase['marketMae'][str(int(m))]-1e-12 for m in sorted(set(groups)))})
        tout['comparisons']=comp; out['targets'][target]=tout
    # Conservative descriptive verdicts; no action authority.
    verdict={}
    for target,t in out['targets'].items():
        portfolio=[]; history=[]; full=[]
        for model,c in t['comparisons'].items():
            z=c['PORTFOLIO_ACTION']
            if z['rowMaeGainVsActionOnly']>0 and z['marketMacroMaeGainVsActionOnly']>0 and z['heldMarketWinsVsActionOnly']>=17: portfolio.append(model)
            z=c['PORTFOLIO_HISTORY_ACTION']
            if z.get('rowMaeGainVsPortfolio',0)>0 and z.get('marketMacroMaeGainVsPortfolio',0)>0 and z.get('heldMarketWinsVsPortfolio',0)>=17: history.append(model)
            z=c['FULL_STATE_ACTION']
            if z.get('rowMaeGainVsPortfolio',0)>0 and z.get('marketMacroMaeGainVsPortfolio',0)>0 and z.get('heldMarketWinsVsPortfolio',0)>=17: full.append(model)
        verdict[target]={'portfolioIncrement':'SUPPORTED_DIAGNOSTIC' if portfolio else 'NO_GROUPED_INCREMENT','historyIncrementBeyondPortfolio':'SUPPORTED_DIAGNOSTIC' if history else 'NO_GROUPED_INCREMENT','fullStateIncrementBeyondPortfolio':'SUPPORTED_DIAGNOSTIC' if full else 'NO_GROUPED_INCREMENT','qualifyingPortfolio':portfolio,'qualifyingHistory':history,'qualifyingFull':full}
    out['targetVerdicts']=verdict
    op=(Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json') if str(a.output).upper()=='AUTO' else Path(a.output); op.parent.mkdir(parents=True,exist_ok=True); op.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'nRows':len(rows),'nMarkets':len(set(groups)),'targetVerdicts':verdict},ensure_ascii=False),flush=True)

if __name__=='__main__': main()
