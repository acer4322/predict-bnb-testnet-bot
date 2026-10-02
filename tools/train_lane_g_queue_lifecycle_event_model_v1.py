from __future__ import annotations
import argparse,json,math
from pathlib import Path
import numpy as np
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler
from sklearn.linear_model import LogisticRegression
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.metrics import roc_auc_score,log_loss,brier_score_loss,balanced_accuracy_score

QUEUE_CORE=[
'ownPrice','orderQty','cumQtyAtDecision','remainingQtyAtDecision','ageAtDecisionMs',
'currentBestPrice','currentSecondPrice','currentBestDepth','currentSecondDepth','currentGapTicks',
'replacementPrice','replacementQty','replacementVsOwnTicks','replacementPairSum','replacementFloorDeltaIfFilled',
'physicalFloorAtDecision','physicalBestAtDecision','physicalGapAtDecision',
'scopeGenerationAtDecision','scopeDebtQtyAtDecision','reservedRepairQuotaAtDecision','ownRepairQuotaRemaining',
'reservedRepairQuotaWithoutOwn','unreservedDebtIfOwnReleased','availableExpandRiskCreditAtDecision',
'scopeRepairProgressClocksAtDecision','livePassiveSlotsAtDecision','liveActiveSlotsAtDecision',
'sameSideLiveCountAtDecision','oppositeUnmatchedAvgAtDecision','sideUp','scopeSideUp','sideMatchesScope'
]
EXCLUDE_LC=set()

def safe(v):
    if v is None:return np.nan
    if isinstance(v,bool):return float(v)
    try:
        x=float(v);return x if math.isfinite(x) else np.nan
    except Exception:return np.nan

def enrich_feature_row(r):
    z=dict(r);side=str(r.get('side') or '');scope=str(r.get('scopeSideAtDecision') or '')
    z['sideUp']=1.0 if side=='UP' else 0.0
    z['scopeSideUp']=1.0 if scope=='UP' else 0.0
    z['sideMatchesScope']=1.0 if side and scope and side==scope else 0.0
    return z

def first_local_outcome(fr):
    t=int(fr['baselineCancelT']); ev=fr.get('events') or []
    pl=next((e for e in ev if e.get('event')=='EXACT_T_PRIORITY_LOSS_CANCEL'),None)
    te=next((e for e in ev if e.get('event')=='EXACT_T_MANAGED_TERMINAL'),None)
    if pl and (not te or int(pl.get('t') or 0)<=int(te.get('t') or 0)):
        return {'outcome':'PRIORITY_LOSS_CANCEL','priorityLoss':1,'survive':0,'filledAmongSurvive':None,'lagMs':int(pl['t'])-t,'fillQty':0.0}
    if te:
        q=float(te.get('cum') or 0.0);filled=1 if q>1e-12 else 0
        return {'outcome':'FILLED_TERMINAL' if filled else 'ZERO_FILL_TERMINAL','priorityLoss':0,'survive':1,'filledAmongSurvive':filled,'lagMs':int(te['t'])-t,'fillQty':q,'terminalStatus':str(te.get('status') or '')}
    return {'outcome':'UNOBSERVED','priorityLoss':None,'survive':None,'filledAmongSurvive':None,'lagMs':None,'fillQty':None}

def model(kind):
    if kind=='LOGISTIC':
        return Pipeline([('imp',SimpleImputer(strategy='median')),('sc',StandardScaler()),('m',LogisticRegression(C=1.0,class_weight='balanced',max_iter=2000,random_state=1709))])
    return Pipeline([('imp',SimpleImputer(strategy='median')),('m',ExtraTreesClassifier(n_estimators=300,min_samples_leaf=3,max_features='sqrt',class_weight='balanced',random_state=1709,n_jobs=1))])

def evaluate(rows,features,label,kind):
    markets=sorted({int(r['marketId']) for r in rows});pred=[];truth=[];prior=[];mids=[]
    for m in markets:
        tr=[r for r in rows if int(r['marketId'])!=m and r[label] is not None]
        va=[r for r in rows if int(r['marketId'])==m and r[label] is not None]
        if not va or not tr:continue
        ytr=np.asarray([int(r[label]) for r in tr],int); yv=np.asarray([int(r[label]) for r in va],int)
        p0=float(np.mean(ytr));p0=min(max(p0,1e-6),1-1e-6)
        if len(np.unique(ytr))<2:p=np.repeat(p0,len(va))
        else:
            Xtr=np.asarray([[safe(r.get(f)) for f in features] for r in tr],float);Xv=np.asarray([[safe(r.get(f)) for f in features] for r in va],float)
            md=model(kind);md.fit(Xtr,ytr);p=md.predict_proba(Xv)[:,1]
        pred.extend([float(x) for x in p]);truth.extend([int(x) for x in yv]);prior.extend([p0]*len(va));mids.extend([m]*len(va))
    y=np.asarray(truth,int);p=np.asarray(pred,float);p0=np.asarray(prior,float)
    auc=float(roc_auc_score(y,p)) if len(np.unique(y))>1 else None
    ll=float(log_loss(y,p,labels=[0,1]));ll0=float(log_loss(y,p0,labels=[0,1]));br=float(brier_score_loss(y,p));br0=float(brier_score_loss(y,p0))
    ba=float(balanced_accuracy_score(y,(p>=.5).astype(int))) if len(np.unique(y))>1 else None
    bym=[]
    for m in sorted(set(mids)):
        ix=[i for i,x in enumerate(mids) if x==m]
        bym.append({'marketId':m,'n':len(ix),'logloss':float(log_loss(y[ix],p[ix],labels=[0,1])),'priorLogloss':float(log_loss(y[ix],p0[ix],labels=[0,1]))})
    return {'n':len(y),'positive':int(y.sum()),'auc':auc,'logloss':ll,'priorLogloss':ll0,'loglossImprovementVsPrior':ll0-ll,'brier':br,'priorBrier':br0,'brierImprovementVsPrior':br0-br,'balancedAccuracyAt05':ba,'marketLoglossWinsVsPrior':sum(x['logloss']<x['priorLogloss'] for x in bym),'markets':len(bym),'byMarket':bym,'oof':[{'marketId':mids[i],'y':int(y[i]),'p':float(p[i]),'prior':float(p0[i])} for i in range(len(y))]}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--forks',required=True);ap.add_argument('--features',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    fd=json.loads(Path(a.forks).read_text(encoding='utf-8'));xd=json.loads(Path(a.features).read_text(encoding='utf-8'))
    fx={(int(r['marketId']),str(r['key']),int(r['t'])):enrich_feature_row(r) for r in xd['rows']}
    rows=[];missing=[]
    for fr in fd['rows']:
        k=(int(fr['marketId']),str(fr['targetKey']),int(fr['baselineCancelT']));x=fx.get(k)
        if x is None:missing.append(k);continue
        o=first_local_outcome(fr);z=dict(x);z.update({'marketId':k[0],'key':k[1],'t':k[2],**o,'terminalPnlDelta':float((fr.get('terminalDelta') or {}).get('pnl') or 0.0)});rows.append(z)
    lc=sorted([k for k in rows[0].keys() if k.startswith('lc_')]) if rows else []
    sets={'QUEUE_CORE':QUEUE_CORE,'QUEUE_PLUS_LIFECYCLE':QUEUE_CORE+lc}
    tasks={'priorityLossVsSurvive':([r for r in rows if r['priorityLoss'] is not None],'priorityLoss'), 'fillVsZeroAmongSurvive':([r for r in rows if r['filledAmongSurvive'] is not None],'filledAmongSurvive')}
    out={'version':'LANE_G_QUEUE_LIFECYCLE_EVENT_MODEL_V1_20260907','researchOnly':True,'runtimeAuthority':False,'coverage':{'forkRows':len(fd['rows']),'matchedRows':len(rows),'missing':missing,'markets':len({r['marketId'] for r in rows})},'outcomeCounts':{},'featureSets':sets,'tasks':{},'corpus':rows,'boundary':['exact (market,key,t) clean forks only','strict-past decision features only','no w500/w1000/w2000 fixed-window trajectory features','continuous age/counter state only; no hard thresholds','leave-one-market-out OOF','no terminal PnL in model features','no runtime authority']}
    from collections import Counter
    out['outcomeCounts']=dict(Counter(r['outcome'] for r in rows))
    for tn,(rr,lab) in tasks.items():
        out['tasks'][tn]={}
        for sn,fs in sets.items():
            out['tasks'][tn][sn]={kind:evaluate(rr,fs,lab,kind) for kind in ('LOGISTIC','EXTRA_TREES')}
    gates={}
    for tn in out['tasks']:
        core=out['tasks'][tn]['QUEUE_CORE'];plus=out['tasks'][tn]['QUEUE_PLUS_LIFECYCLE']
        gates[tn]={
          'queueCoreInformative':any((v['auc'] or 0)>=.60 and v['loglossImprovementVsPrior']>0 for v in core.values()),
          'lifecycleIncrement':any((plus[k]['auc'] or 0)>(core[k]['auc'] or 0) and plus[k]['logloss']<core[k]['logloss'] for k in core),
          'details':{k:{'coreAuc':core[k]['auc'],'plusAuc':plus[k]['auc'],'coreLogloss':core[k]['logloss'],'plusLogloss':plus[k]['logloss']} for k in core}
        }
    out['gates']=gates
    op=Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'coverage':out['coverage'],'outcomeCounts':out['outcomeCounts'],'gates':gates,'metrics':{tn:{sn:{k:{kk:v[kk] for kk in ('n','positive','auc','logloss','priorLogloss','loglossImprovementVsPrior','brierImprovementVsPrior','marketLoglossWinsVsPrior')} for k,v in mm.items()} for sn,mm in tt.items()} for tn,tt in out['tasks'].items()}},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
