from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesRegressor
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

TARGETS=['dFloor','dBest','dFavoredPayoff','dWeakPayoff']
MECH={
 'dFloor':'immediateReexpandMinusRepairFloor',
 'dBest':'immediateReexpandMinusRepairBest',
 'dFavoredPayoff':'immediateReexpandMinusRepairFavoredPayoff',
 'dWeakPayoff':'immediateReexpandMinusRepairWeakPayoff',
}
GEOM_FEATURES=[
 'repairPrice','repairQty','expandPrice','expandQty',
 'repairPriceMinusWeakBid','expandPriceMinusExpandBid',
 'repairImmediateDeltaFloor','reexpandImmediateDeltaFloor','immediateReexpandMinusRepairFloor',
 'repairImmediateDeltaBest','reexpandImmediateDeltaBest','immediateReexpandMinusRepairBest',
 'repairImmediateDeltaFavoredPayoff','reexpandImmediateDeltaFavoredPayoff','immediateReexpandMinusRepairFavoredPayoff',
 'repairImmediateDeltaWeakPayoff','reexpandImmediateDeltaWeakPayoff','immediateReexpandMinusRepairWeakPayoff',
]
FOLDS=[(0,40,40,60),(0,60,60,80),(0,80,80,100)]
EPS=1e-9

def mae(a,b):return float(np.mean(np.abs(np.asarray(a)-np.asarray(b))))
def q(a,p):return float(np.quantile(np.asarray(a,float),p))
def quadrant(df,db):
    if abs(df)<=EPS and abs(db)<=EPS:return 'TIE'
    if df>EPS and db>EPS:return 'REEXPAND_DOMINATES'
    if df<-EPS and db<-EPS:return 'REPAIR_DOMINATES'
    return 'TRADEOFF'

def fit_predict(kind,Xtr,ytr,Xte):
    if kind=='EXTRATREES':
        m=ExtraTreesRegressor(n_estimators=300,min_samples_leaf=4,max_features=0.75,random_state=260907,n_jobs=1)
    elif kind=='RIDGE':
        m=make_pipeline(StandardScaler(),Ridge(alpha=10.0))
    else:raise ValueError(kind)
    m.fit(Xtr,ytr);return m.predict(Xte)

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--input',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_ACTION_VALUE_CORPUS_V1_20260907.json');ap.add_argument('--output',default='data/research/r4_v0/p0_provenance_v1/MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_ACTION_VALUE_FORWARD_V1_20260907.json');a=ap.parse_args()
    d=json.loads(Path(a.input).read_text(encoding='utf-8'));rows=sorted(d['rows'],key=lambda r:(int(r['marketId']),int(r['t'])))
    all_features=list(rows[0]['features'])
    for f in GEOM_FEATURES:
        if f not in all_features:raise RuntimeError(f'missing geometry feature {f}')
    out_rows=[];fold_reports=[]
    for fi,(tr0,tr1,te0,te1) in enumerate(FOLDS,1):
        tr=rows[tr0:tr1];te=rows[te0:te1]
        Xfull_tr=np.array([[float(r['features'][f]) for f in all_features] for r in tr],float)
        Xfull_te=np.array([[float(r['features'][f]) for f in all_features] for r in te],float)
        Xgeom_tr=np.array([[float(r['features'][f]) for f in GEOM_FEATURES] for r in tr],float)
        Xgeom_te=np.array([[float(r['features'][f]) for f in GEOM_FEATURES] for r in te],float)
        pred={k:{} for k in TARGETS}
        scales={}
        for target in TARGETS:
            ytr=np.array([float(r['targets'][target]) for r in tr]);yte=np.array([float(r['targets'][target]) for r in te])
            scale=max(float(np.std(ytr)),0.25)
            scales[target]=scale
            pred[target]['true']=yte
            pred[target]['mechanical']=np.array([float(r['features'][MECH[target]]) for r in te])
            for model in ('EXTRATREES','RIDGE'):
                pred[target][f'{model}_GEOM']=fit_predict(model,Xgeom_tr,ytr,Xgeom_te)
                pred[target][f'{model}_FULL']=fit_predict(model,Xfull_tr,ytr,Xfull_te)
        for j,r in enumerate(te):
            rec={'fold':fi,'marketId':int(r['marketId']),'t':int(r['t']),'true':{},'pred':{},'normalizedVectorError':{}}
            for target in TARGETS:
                rec['true'][target]=float(pred[target]['true'][j])
                rec['pred'][target]={k:float(v[j]) for k,v in pred[target].items() if k!='true'}
            for model in ('MECHANICAL','EXTRATREES_GEOM','EXTRATREES_FULL','RIDGE_GEOM','RIDGE_FULL'):
                errs=[]
                for target in TARGETS:
                    key='mechanical' if model=='MECHANICAL' else model
                    errs.append(abs(rec['pred'][target][key]-rec['true'][target])/scales[target])
                rec['normalizedVectorError'][model]=float(np.mean(errs))
            trueq=quadrant(rec['true']['dFloor'],rec['true']['dBest'])
            rec['trueQuadrant']=trueq
            for model in ('EXTRATREES_GEOM','EXTRATREES_FULL','RIDGE_GEOM','RIDGE_FULL'):
                rec[f'{model}Quadrant']=quadrant(rec['pred']['dFloor'][model],rec['pred']['dBest'][model])
            out_rows.append(rec)
        fr={'fold':fi,'trainMarkets':[int(x['marketId']) for x in tr],'testMarkets':[int(x['marketId']) for x in te],'targets':{}}
        for target in TARGETS:
            fr['targets'][target]={}
            for model in ('mechanical','EXTRATREES_GEOM','EXTRATREES_FULL','RIDGE_GEOM','RIDGE_FULL'):
                fr['targets'][target][model]={'mae':mae(pred[target]['true'],pred[target][model])}
        fold_reports.append(fr)
    summary={'marketCount':len(rows),'oosMarkets':len(out_rows),'folds':len(FOLDS),'models':{}}
    true_by={t:np.array([r['true'][t] for r in out_rows]) for t in TARGETS}
    for model in ('MECHANICAL','EXTRATREES_GEOM','EXTRATREES_FULL','RIDGE_GEOM','RIDGE_FULL'):
        ms={'targets':{}}
        for target in TARGETS:
            key='mechanical' if model=='MECHANICAL' else model
            pp=np.array([r['pred'][target][key] for r in out_rows]);yy=true_by[target]
            ms['targets'][target]={'mae':mae(yy,pp),'medianAbsError':float(np.median(np.abs(yy-pp))),'p90AbsError':q(np.abs(yy-pp),.9)}
        ve=np.array([r['normalizedVectorError'][model] for r in out_rows])
        ms['vectorError']={'mean':float(np.mean(ve)),'median':float(np.median(ve)),'p90':q(ve,.9),'max':float(np.max(ve))}
        if model!='MECHANICAL':
            ms['quadrantAccuracy']=sum(r[f'{model}Quadrant']==r['trueQuadrant'] for r in out_rows)/len(out_rows)
            non_tie=[r for r in out_rows if r['trueQuadrant']!='TIE']
            ms['quadrantAccuracyNonTie']=sum(r[f'{model}Quadrant']==r['trueQuadrant'] for r in non_tie)/len(non_tie) if non_tie else None
        summary['models'][model]=ms
    # Primary incremental gate: full ExtraTrees versus same model using candidate geometry only.
    full=np.array([r['normalizedVectorError']['EXTRATREES_FULL'] for r in out_rows]);geom=np.array([r['normalizedVectorError']['EXTRATREES_GEOM'] for r in out_rows])
    delta=geom-full
    improve=int(np.sum(delta>EPS));worse=int(np.sum(delta<-EPS));tie=len(delta)-improve-worse
    improve_rate=improve/len(delta)
    p10_delta=q(delta,.1);mean_delta=float(np.mean(delta))
    primary={
      'comparison':'EXTRATREES_FULL_vs_EXTRATREES_GEOM',
      'improvedMarkets':improve,'worsenedMarkets':worse,'tiedMarkets':tie,'improvedMarketRate':improve_rate,
      'meanNormalizedVectorErrorImprovement':mean_delta,
      'p10PerMarketErrorImprovement':p10_delta,
      'aggregateErrorImproves':bool(mean_delta>0),
      'seventyPercentGate':bool(improve_rate>=0.70),
      # Tail guard for model-development analogue: full model p90 error may not exceed geom p90 by >10%.
      'tailGuard':bool(q(full,.9)<=1.10*q(geom,.9)+EPS),
    }
    primary['developmentGatePass']=bool(primary['seventyPercentGate'] and primary['aggregateErrorImproves'] and primary['tailGuard'])
    # Secondary robustness only; not used to select a model.
    rf=np.array([r['normalizedVectorError']['RIDGE_FULL'] for r in out_rows]);rg=np.array([r['normalizedVectorError']['RIDGE_GEOM'] for r in out_rows]);rd=rg-rf
    secondary={'comparison':'RIDGE_FULL_vs_RIDGE_GEOM','improvedMarketRate':float(np.mean(rd>EPS)),'meanNormalizedVectorErrorImprovement':float(np.mean(rd)),'tailGuard':bool(q(rf,.9)<=1.10*q(rg,.9)+EPS)}
    report={'version':'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_ACTION_VALUE_FORWARD_V1_20260907','researchOnly':True,'runtimeAuthority':False,'input':a.input,'featureCountFull':len(all_features),'featureCountGeometry':len(GEOM_FEATURES),'targets':TARGETS,'foldDefinition':'expanding chronology 40->20, 60->20, 80->20; each OOS market appears once','foldReports':fold_reports,'summary':summary,'primaryGate':primary,'secondaryRobustness':secondary,'oosRows':out_rows,'boundary':['one exact-fork row per market','strict-past features only','winner/settlement/Target future/native action label excluded from features','mechanical candidate geometry is frozen simple baseline','ExtraTrees primary fixed a priori; Ridge secondary robustness only; no hyperparameter sweep','70/30 development analogue preregistered before scoring','runtime authority remains false regardless of model score','whole-market HFT 70/30 promotion gate remains authoritative','NEW24-B untouched','no dream fill/no 8781']}
    Path(a.output).write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'ok':True,'primaryGate':primary,'secondary':secondary,'modelSummary':summary['models']},ensure_ascii=False))
if __name__=='__main__':main()
