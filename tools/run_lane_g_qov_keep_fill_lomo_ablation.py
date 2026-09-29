from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import brier_score_loss,balanced_accuracy_score,roc_auc_score,log_loss
SEED=20260907
STATIC=['ageAtDecisionMs','price','qty','rank','depth','bestPrice','bestDepth','distanceTicks','pairSum','floor','best','gap','upQty','downQty','cost','scopeDebtQty','reservedRepairQuota','availableExpandRiskCredit','scopeRepairProgressClocks','livePassiveSlots','liveActiveSlots','repairQtyAuthorized','overflowQtyAuthorized']

def fv(v):
    if v is None:return np.nan
    try:
        x=float(v);return x if math.isfinite(x) else np.nan
    except:return np.nan

def models():
    return {
      'LOGISTIC_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('sc',StandardScaler()),('m',LogisticRegression(C=1.0,max_iter=3000,random_state=SEED))]),
      'EXTRATREES_FIXED':Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesClassifier(n_estimators=500,max_depth=4,min_samples_leaf=4,max_features='sqrt',random_state=SEED,n_jobs=4))])}

def macro_brier(y,p,g):
    vals=[];by={}
    for m in sorted(set(g)):
        ix=np.where(g==m)[0];v=float(np.mean((p[ix]-y[ix])**2));vals.append(v);by[str(int(m))]=v
    return float(np.mean(vals)),by

def metrics(y,p,g):
    p=np.clip(p,1e-6,1-1e-6);pred=(p>=.5).astype(int);mb,by=macro_brier(y,p,g)
    return {'brier':float(brier_score_loss(y,p)),'logLoss':float(log_loss(y,np.c_[1-p,p],labels=[0,1])),'balancedAccuracy':float(balanced_accuracy_score(y,pred)),'auc':float(roc_auc_score(y,p)) if len(np.unique(y))==2 else None,'marketMacroBrier':mb,'marketBrier':by}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8'));rows=d['rows'];g=np.asarray([int(r['marketId']) for r in rows]);y=np.asarray([1 if str(r.get('managedTerminalStatus'))=='FILLED' else 0 for r in rows],dtype=int)
    traj=sorted({k for r in rows for k in r if k.startswith(('w500_','w1000_','w2000_'))})
    life=sorted({k for r in rows for k in r if k.startswith('lc_')})
    extra=['currentBestPrice','currentSecondPrice','currentBestDepth','currentSecondDepth','currentGapTicks','replacementPrice','replacementQty','replacementVsOwnTicks','replacementPairSum','replacementFloorDeltaIfFilled','reservedRepairQuotaWithoutOwn','unreservedDebtIfOwnReleased','ownRepairQuotaRemaining','sameSideLiveCountAtDecision','oppositeUnmatchedAvgAtDecision']
    sets={'STATIC':STATIC,'STATIC_TRAJECTORY':STATIC+extra+traj,'STATIC_LIFECYCLE':STATIC+life,'FULL':STATIC+extra+traj+life}
    out={'version':'LANE_G_QOV_KEEP_FILL_LOMO_ABLATION_V1','researchOnly':True,'nRows':len(rows),'nMarkets':len(set(g)),'positiveFill':int(y.sum()),'negativeCancel':int((1-y).sum()),'featureSets':{},'boundary':['same frozen 67 causal KEEP forks','label is actual KEEP-branch managed order FILLED vs CANCELED','leave-one-market-out only','strict-past decision-time features only','no winner/terminal pnl/Target future input','no fresh data']}
    for sname,features in sets.items():
        X=np.asarray([[fv(r.get(f)) for f in features] for r in rows])
        pp={'TRAIN_PREVALENCE':np.zeros(len(rows))};mods=models()
        for held in sorted(set(g)):
            te=np.where(g==held)[0];tr=np.where(g!=held)[0];prev=float(np.mean(y[tr]));pp['TRAIN_PREVALENCE'][te]=prev
            for name,m in mods.items():
                m.fit(X[tr],y[tr]);
                if name not in pp:pp[name]=np.zeros(len(rows))
                pp[name][te]=m.predict_proba(X[te])[:,1]
        sm={name:metrics(y,p,g) for name,p in pp.items()};base=sm['TRAIN_PREVALENCE']
        for name,z in sm.items():
            z['brierGainVsPrevalence']=base['brier']-z['brier'];z['marketMacroBrierGainVsPrevalence']=base['marketMacroBrier']-z['marketMacroBrier']
            if name!='TRAIN_PREVALENCE':
                z['heldMarketBrierWinsVsPrevalence']=sum(z['marketBrier'][str(int(m))]<base['marketBrier'][str(int(m))]-1e-12 for m in sorted(set(g)))
        out['featureSets'][sname]={'features':features,'models':sm}
    # compare enriched vs STATIC same model
    cmp={}
    for model in ['LOGISTIC_FIXED','EXTRATREES_FIXED']:
        b=out['featureSets']['STATIC']['models'][model];cmp[model]={}
        for sname in ['STATIC_TRAJECTORY','STATIC_LIFECYCLE','FULL']:
            z=out['featureSets'][sname]['models'][model];cmp[model][sname]={'brierGainVsStatic':b['brier']-z['brier'],'marketMacroBrierGainVsStatic':b['marketMacroBrier']-z['marketMacroBrier'],'heldMarketWinsVsStatic':sum(z['marketBrier'][str(int(m))]<b['marketBrier'][str(int(m))]-1e-12 for m in sorted(set(g)))}
    out['comparisonsVsStatic']=cmp
    qualifiers=[]
    for model,ss in cmp.items():
        for sname,z in ss.items():
            zz=out['featureSets'][sname]['models'][model]
            if z['brierGainVsStatic']>0 and z['marketMacroBrierGainVsStatic']>0 and z['heldMarketWinsVsStatic']>=9 and zz['brierGainVsPrevalence']>0 and zz['marketMacroBrierGainVsPrevalence']>0:qualifiers.append(f'{model}:{sname}')
    out['verdict']={'status':'MECHANISTIC_KEEP_FILL_SIGNAL' if qualifiers else 'NO_ROBUST_KEEP_FILL_SIGNAL','qualifying':qualifiers}
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'verdict':out['verdict'],'positiveFill':int(y.sum()),'negativeCancel':int((1-y).sum())}),flush=True)
if __name__=='__main__':main()
