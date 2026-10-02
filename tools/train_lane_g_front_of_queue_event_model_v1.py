from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss
STATIC=['sideUp','roleRepair','roleExpand','roleProbe','price','qty','frontCauseInitial']
EXEC=STATIC+['timeToFrontMs','queueInitial','minOwnLevelSinceAccept','samePriceContraTradeQtyBeforeFront','samePriceContraTradeEventsBeforeFront','samePriceDepthEventsBeforeFront','orderCountAtFront','orderCountDeltaToFront','distanceFromSameBestTicks','sameTopQty','oppositeTopQty','sameTop3Qty','oppositeTop3Qty','nativeSpreadTicks','nativeBookImbalance','sameBookLevels','oppositeBookLevels']
def val(x):
    try:
        z=float(x);return z if math.isfinite(z) else np.nan
    except Exception:return np.nan
def enrich(r):
    x=dict(r);role=str(r.get('role') or '')
    x['sideUp']=float(str(r.get('side'))=='UP');x['roleRepair']=float('REPAIR' in role or role=='ECONOMIC_CORE');x['roleExpand']=float('EXPAND' in role);x['roleProbe']=float('PROBE' in role);x['frontCauseInitial']=float(str(r.get('frontStateCause'))=='INITIAL_ZERO');return x
def matrix(rows,feats):return np.asarray([[val(r.get(f)) for f in feats] for r in rows],dtype=float)
def models():
    return {
      'LOGISTIC':Pipeline([('imp',SimpleImputer(strategy='median')),('scale',StandardScaler()),('m',LogisticRegression(C=1.0,max_iter=2000,class_weight=None,random_state=20260907))]),
      'EXTRA_TREES':Pipeline([('imp',SimpleImputer(strategy='median')),('m',ExtraTreesClassifier(n_estimators=300,min_samples_leaf=5,max_features='sqrt',class_weight=None,random_state=20260907,n_jobs=-1))])}
def metrics(y,p,market,prior,other=None):
    p=np.clip(np.asarray(p,float),1e-6,1-1e-6);y=np.asarray(y,int); market=np.asarray(market,int); pp=np.full(len(y),float(prior))
    out={'n':int(len(y)),'positive':int(y.sum()),'rate':float(y.mean()),'auc':float(roc_auc_score(y,p)) if len(set(y))>1 else None,'logloss':float(log_loss(y,p,labels=[0,1])),'brier':float(brier_score_loss(y,p)),'priorLogloss':float(log_loss(y,pp,labels=[0,1])),'priorBrier':float(brier_score_loss(y,pp))}
    out['loglossImprovementVsPrior']=out['priorLogloss']-out['logloss'];out['brierImprovementVsPrior']=out['priorBrier']-out['brier']
    wins=0;staticwins=0;details=[]
    for m in sorted(set(market)):
        ix=np.where(market==m)[0];ll=float(log_loss(y[ix],p[ix],labels=[0,1]));lp=float(log_loss(y[ix],pp[ix],labels=[0,1])); win=ll<lp-1e-12;wins+=int(win);d={'marketId':int(m),'n':int(len(ix)),'logloss':ll,'priorLogloss':lp,'winVsPrior':bool(win)}
        if other is not None:
            op=np.clip(np.asarray(other,float)[ix],1e-6,1-1e-6);lo=float(log_loss(y[ix],op,labels=[0,1]));d['staticLogloss']=lo;d['winVsStatic']=bool(ll<lo-1e-12);staticwins+=int(d['winVsStatic'])
        details.append(d)
    out['marketLoglossWinsVsPrior']=wins;out['testMarkets']=len(details)
    if other is not None:out['marketLoglossWinsVsStatic']=staticwins
    out['marketDetails']=details;return out
def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source',required=True);ap.add_argument('--output',required=True);ap.add_argument('--model-output',default=None);a=ap.parse_args()
    d=json.loads(Path(a.source).read_text(encoding='utf-8'));rows=[enrich(r) for r in d['rows']];mids=sorted({int(r['marketId']) for r in rows});trm=set(mids[:70]);tem=set(mids[70:]);tr=[r for r in rows if int(r['marketId']) in trm];te=[r for r in rows if int(r['marketId']) in tem];ytr=np.asarray([int(r['fillFirst']) for r in tr]);yte=np.asarray([int(r['fillFirst']) for r in te]);mt=np.asarray([int(r['marketId']) for r in te]);prior=float(ytr.mean());pred={};fitted={}
    for fs,feats in [('ORDER_STATIC',STATIC),('FRONT_EXECUTION',EXEC)]:
        pred[fs]={};fitted[fs]={}
        for name,model in models().items():
            model.fit(matrix(tr,feats),ytr);p=model.predict_proba(matrix(te,feats))[:,1];pred[fs][name]=p;fitted[fs][name]=model
    out={'version':'LANE_G_FRONT_OF_QUEUE_EVENT_MODEL_V1_20260907','researchOnly':True,'runtimeAuthority':False,'coverage':{'rows':len(rows),'markets':len(mids),'trainRows':len(tr),'trainMarkets':len(trm),'testRows':len(te),'testMarkets':len(tem),'trainFillRate':prior,'testFillRate':float(yte.mean())},'split':{'trainMarketIds':sorted(trm),'testMarketIds':sorted(tem)},'features':{'ORDER_STATIC':STATIC,'FRONT_EXECUTION':EXEC},'metrics':{},'gates':{}}
    for name in ['LOGISTIC','EXTRA_TREES']:
        sm=metrics(yte,pred['ORDER_STATIC'][name],mt,prior);em=metrics(yte,pred['FRONT_EXECUTION'][name],mt,prior,pred['ORDER_STATIC'][name]);out['metrics'][name]={'ORDER_STATIC':sm,'FRONT_EXECUTION':em}
    passes=[]
    for name,z in out['metrics'].items():
        s=z['ORDER_STATIC'];e=z['FRONT_EXECUTION'];p=bool(e['auc'] is not None and e['auc']>=0.60 and e['logloss']<s['logloss'] and e['brier']<s['brier'] and e['loglossImprovementVsPrior']>0 and e['brierImprovementVsPrior']>0);out['gates'][name]={'frontExecutionPass':p,'auc':e['auc'],'loglossGainVsStatic':s['logloss']-e['logloss'],'brierGainVsStatic':s['brier']-e['brier'],'marketWinsVsStatic':e.get('marketLoglossWinsVsStatic'),'marketWinsVsPrior':e.get('marketLoglossWinsVsPrior')};passes.append(p)
    out['frontExecutionModelPass']=any(passes);Path(a.output).write_text(json.dumps(out,indent=2,ensure_ascii=False),encoding='utf-8')
    if a.model_output:
        import joblib;joblib.dump({'version':out['version'],'features':{'ORDER_STATIC':STATIC,'FRONT_EXECUTION':EXEC},'models':fitted,'trainPrior':prior,'trainMarketIds':sorted(trm)},a.model_output)
    compact={n:{fs:{k:v for k,v in mm.items() if k not in ('marketDetails',)} for fs,mm in z.items()} for n,z in out['metrics'].items()}
    print(json.dumps({'ok':True,'coverage':out['coverage'],'metrics':compact,'gates':out['gates'],'frontExecutionModelPass':out['frontExecutionModelPass']},indent=2,ensure_ascii=False))
if __name__=='__main__':main()
