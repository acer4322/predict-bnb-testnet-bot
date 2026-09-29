from __future__ import annotations
import argparse,json,math,os
from pathlib import Path
import numpy as np
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
SEED=20260907
STATIC=['ageAtDecisionMs','price','qty','rank','depth','bestPrice','bestDepth','distanceTicks','pairSum','floor','best','gap','upQty','downQty','cost','scopeDebtQty','reservedRepairQuota','availableExpandRiskCredit','scopeRepairProgressClocks','livePassiveSlots','liveActiveSlots','repairQtyAuthorized','overflowQtyAuthorized']
EXTRA=['currentBestPrice','currentSecondPrice','currentBestDepth','currentSecondDepth','currentGapTicks','replacementPrice','replacementQty','replacementVsOwnTicks','replacementPairSum','replacementFloorDeltaIfFilled','reservedRepairQuotaWithoutOwn','unreservedDebtIfOwnReleased','ownRepairQuotaRemaining','sameSideLiveCountAtDecision','oppositeUnmatchedAvgAtDecision']

def fv(v):
    if v is None:return np.nan
    try:
        x=float(v);return x if math.isfinite(x) else np.nan
    except:return np.nan

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--dataset',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    d=json.loads(Path(a.dataset).read_text(encoding='utf-8'));rows=d['rows'];g=np.asarray([int(r['marketId']) for r in rows]);y=np.asarray([1 if str(r.get('managedTerminalStatus'))=='FILLED' else 0 for r in rows],dtype=int)
    traj=sorted({k for r in rows for k in r if k.startswith(('w500_','w1000_','w2000_'))});features=STATIC+EXTRA+traj
    X=np.asarray([[fv(r.get(f)) for f in features] for r in rows]);p=np.zeros(len(rows))
    for held in sorted(set(g)):
        te=np.where(g==held)[0];tr=np.where(g!=held)[0]
        m=Pipeline([('imp',SimpleImputer(strategy='median',add_indicator=True)),('m',ExtraTreesClassifier(n_estimators=500,max_depth=4,min_samples_leaf=4,max_features='sqrt',random_state=SEED,n_jobs=4))]);m.fit(X[tr],y[tr]);p[te]=m.predict_proba(X[te])[:,1]
    outrows=[]
    for i,r in enumerate(rows):
        outrows.append({'marketId':int(r['marketId']),'key':r['key'],'cancelT':int(r['cancelT']),'pKeepFillOOF':float(p[i]),'actualKeepFill':int(y[i]),'dBest5s':float(r.get('dBest5s') or 0),'dFloor5s':float(r.get('dFloor5s') or 0),'dGap5s':float(r.get('dGap5s') or 0),'dBest10s':float(r.get('dBest10s') or 0),'dFloor10s':float(r.get('dFloor10s') or 0),'dGap10s':float(r.get('dGap10s') or 0),'dPnlTerminal':float(r.get('dPnlTerminal') or 0),'dActiveQtyTerminal':float(r.get('dActiveQtyTerminal') or 0),'floor':float(r.get('floor') or 0),'best':float(r.get('best') or 0),'gap':float(r.get('gap') or 0),'price':float(r.get('price') or 0),'qty':float(r.get('qty') or 0)})
    out={'version':'LANE_G_QOV_KEEP_FILL_OOF_EXPORT_V1','researchOnly':True,'features':features,'rows':outrows,'boundary':['LOMO ExtraTrees static+trajectory only','probability is execution prediction, not action-value score','no winner/terminal features in model','same frozen 67 causal forks','no fresh data']}
    op=Path(os.environ['BTC5M_LAN_RESULT_DIR'])/'result.json' if a.output.upper()=='AUTO' else Path(a.output);op.parent.mkdir(parents=True,exist_ok=True);op.write_text(json.dumps(out,indent=2),encoding='utf-8');print(json.dumps({'ok':True,'rows':len(outrows),'pMean':float(np.mean(p))}),flush=True)
if __name__=='__main__':main()
