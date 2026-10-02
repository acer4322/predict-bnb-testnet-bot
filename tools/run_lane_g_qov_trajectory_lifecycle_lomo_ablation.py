from __future__ import annotations
import argparse, json, math, os
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import mean_absolute_error

SEED=20260907
EPS=1e-9
STATIC=[
'ageAtDecisionMs','price','qty','rank','depth','bestPrice','bestDepth','distanceTicks','pairSum','floor','best','gap','upQty','downQty','cost','scopeDebtQty','reservedRepairQuota','availableExpandRiskCredit','scopeRepairProgressClocks','livePassiveSlots','liveActiveSlots','repairQtyAuthorized','overflowQtyAuthorized']
TARGETS=['dBest5s','dFloor5s','dGap5s','dBest10s','dFloor10s','dGap10s','dActiveQtyTerminal']

def finite(v):
    if v is None:return np.nan
    try:
        x=float(v);return x if math.isfinite(x) else np.nan
    except:return np.nan

def models():
    return {
      'RIDGE_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('m',Ridge(alpha=1.0))]),
      'EXTRATREES_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesRegressor(n_estimators=400,max_depth=4,min_samples_leaf=4,max_features='sqrt',random_state=SEED,n_jobs=4))])}

def macro_mae(y,p,g):
    vals=[];by={}
    for m in sorted(set(g)):
        ix=np.where(g==m)[0];v=float(mean_absolute_error(y[ix],p[ix]));vals.append(v);by[str(int(m))]=v
    return float(np.mean(vals)),by

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8'));rows=d['rows'];groups=np.asarray([int(r['marketId']) for r in rows],dtype=int)
    traj=sorted({k for r in rows for k in r if k.startswith(('w500_','w1000_','w2000_'))})
    life=sorted({k for r in rows for k in r if k.startswith('lc_')})
    extra=sorted(set(['currentBestPrice','currentSecondPrice','currentBestDepth','currentSecondDepth','currentGapTicks','replacementPrice','replacementQty','replacementVsOwnTicks','replacementPairSum','replacementFloorDeltaIfFilled','reservedRepairQuotaWithoutOwn','unreservedDebtIfOwnReleased','ownRepairQuotaRemaining','sameSideLiveCountAtDecision','oppositeUnmatchedAvgAtDecision']))
    sets={'STATIC':STATIC,'STATIC_TRAJECTORY':STATIC+extra+traj,'STATIC_LIFECYCLE':STATIC+life,'FULL':STATIC+extra+traj+life}
    out={'version':'LANE_G_QOV_TRAJECTORY_LIFECYCLE_LOMO_ABLATION_V1','researchOnly':True,'nRows':len(rows),'nMarkets':len(set(groups)),'featureSets':{k:v for k,v in sets.items()},'targets':{},'boundary':['same frozen 67 causal forks','leave-one-market-out only','strict-past decision-time features only','no winner/terminal/Target-future features','no fresh data']}
    for target in TARGETS:
        y=np.asarray([float(r.get(target) or 0.0) for r in rows],dtype=float)
        tout={'targetStats':{'mean':float(np.mean(y)),'pos':int(np.sum(y>EPS)),'neg':int(np.sum(y<-EPS)),'zero':int(np.sum(np.abs(y)<=EPS))},'featureSets':{}}
        for sname,features in sets.items():
            X=np.asarray([[finite(r.get(f)) for f in features] for r in rows],dtype=float)
            pred_mean=np.zeros(len(rows));preds={name:np.zeros(len(rows)) for name in models()};folds=[]
            for held in sorted(set(groups)):
                te=np.where(groups==held)[0];tr=np.where(groups!=held)[0];mu=float(np.mean(y[tr]));pred_mean[te]=mu
                rec={'heldMarket':int(held),'n':int(len(te))}
                for name,model in models().items():
                    model.fit(X[tr],y[tr]);preds[name][te]=model.predict(X[te])
                folds.append(rec)
            bmae=float(mean_absolute_error(y,pred_mean));bmacro,bby=macro_mae(y,pred_mean,groups)
            sm={'TRAIN_MEAN':{'mae':bmae,'marketMacroMae':bmacro,'marketMae':bby}}
            for name,p in preds.items():
                mae=float(mean_absolute_error(y,p));macro,by=macro_mae(y,p,groups);wins=sum(by[str(int(m))]<bby[str(int(m))]-1e-12 for m in sorted(set(groups)))
                sm[name]={'mae':mae,'marketMacroMae':macro,'maeImprovementVsTrainMean':bmae-mae,'marketMacroMaeImprovementVsTrainMean':bmacro-macro,'heldMarketWinsVsTrainMean':int(wins),'marketMae':by}
            tout['featureSets'][sname]=sm
        # compare each enriched set against STATIC same model
        comparisons={}
        for name in ['RIDGE_FIXED','EXTRATREES_FIXED']:
            base=tout['featureSets']['STATIC'][name]
            comparisons[name]={}
            for sname in ['STATIC_TRAJECTORY','STATIC_LIFECYCLE','FULL']:
                z=tout['featureSets'][sname][name]
                comparisons[name][sname]={'rowMaeGainVsStatic':base['mae']-z['mae'],'marketMacroMaeGainVsStatic':base['marketMacroMae']-z['marketMacroMae'],'heldMarketWinsVsStatic':sum(z['marketMae'][str(int(m))]<base['marketMae'][str(int(m))]-1e-12 for m in sorted(set(groups)))}
        tout['comparisonsVsStatic']=comparisons
        out['targets'][target]=tout
    # concise verdict: enriched feature set must improve both row/macro over STATIC and win >=9/18 held markets for same fixed model
    verdict={}
    for target,t in out['targets'].items():
        q=[]
        for model,cmp in t['comparisonsVsStatic'].items():
            for sname,z in cmp.items():
                if z['rowMaeGainVsStatic']>0 and z['marketMacroMaeGainVsStatic']>0 and z['heldMarketWinsVsStatic']>=9:q.append(f'{model}:{sname}')
        verdict[target]={'verdict':'ENRICHED_GROUPED_SIGNAL' if q else 'NO_ENRICHED_GENERALIZATION','qualifying':q}
    out['targetVerdicts']=verdict
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'nRows':len(rows),'nMarkets':len(set(groups)),'targetVerdicts':verdict},ensure_ascii=False),flush=True)
if __name__=='__main__':main()
