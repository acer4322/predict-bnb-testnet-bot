from __future__ import annotations
import json, math
from pathlib import Path
from typing import Any
import joblib, numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score, average_precision_score, balanced_accuracy_score, precision_score, recall_score, log_loss, confusion_matrix
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler
ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools.train_sequential_arbitration_option_v1 import CURRENT, DELTA

D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'theory_execution_handoff_curriculum_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
ART=D/'theory_execution_handoff_v1.joblib'
REPORT=D/'theory_execution_handoff_v1_report.json'

TRAP=[
 'pastDuplicateCount','pastDuplicate1s','pastDuplicate3s','pastDuplicate5s','pastDuplicate10s',
 'lastDuplicateAgeMs','lastDuplicateExistingChildAgeMs','lastDuplicateRemainingQty','lastDuplicateQuoteOffsetTicks','maxPastDuplicateExistingChildAgeMs',
 'pastDuplicateMakerHazard','pastDuplicateMakerBurst','pastDuplicatePassiveRepair','pastDuplicateUnresolvedRepair',
 'pastStaleCount','pastStale1s','pastStale3s','pastStale5s','lastStaleEventAgeMs','lastStaleChildAgeMs','lastStaleRemainingQty','lastStaleQuoteOffsetTicks',
 'pastPartialCount','pastPartial5s','lastPartialEventAgeMs','lastPartialCumExecQty','lastPartialRemainingQty','lastPartialQuoteOffsetTicks',
 'pastTakerNotCompletedCount','lastTakerNotCompletedAgeMs','hasPastDuplicate','hasPastStale','hasPastPartial','hasPastTakerNotCompleted'
]
TRAP_DELTA=['delta_'+x for x in TRAP if x.startswith('past') or x.startswith('hasPast')]

def finite(v:Any)->float:
    try:
        x=float(v); return x if math.isfinite(x) else math.nan
    except Exception:return math.nan

def getv(r,f):
    if f in (r.get('features') or {}): return (r.get('features') or {}).get(f)
    if f in (r.get('theoryTrapHistoryFeatures') or {}): return (r.get('theoryTrapHistoryFeatures') or {}).get(f)
    return (r.get('theoryTrapHistoryDeltaFeatures') or {}).get(f)

def X(rows,feats): return np.asarray([[finite(getv(r,f)) for f in feats] for r in rows],float)
def pipe(): return Pipeline([('imputer',SimpleImputer(strategy='median',add_indicator=True)),('scale',StandardScaler()),('logit',LogisticRegression(C=.35,max_iter=4000,class_weight='balanced',solver='lbfgs'))])
def fit(rows,feats,label):
    m=pipe();m.fit(X(rows,feats),np.asarray([int(label(r)) for r in rows],int));return m

def metric(m,rows,feats,label):
    y=np.asarray([int(label(r)) for r in rows],int);p=m.predict_proba(X(rows,feats))[:,1];z=(p>=.5).astype(int);both=len(set(y.tolist()))>1
    return {'n':len(rows),'positiveRate':float(y.mean()),'predictedPositiveRate':float(z.mean()),'auc':float(roc_auc_score(y,p)) if both else None,'ap':float(average_precision_score(y,p)) if y.sum() else None,'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None,'precision':float(precision_score(y,z,zero_division=0)),'recall':float(recall_score(y,z,zero_division=0)),'logLoss':float(log_loss(y,p,labels=[0,1])),'confusionTnFpFnTp':confusion_matrix(y,z,labels=[0,1]).ravel().astype(int).tolist()}

def policy(models,rows,feats):
    xa=X(rows,feats);pa=models['act'].predict_proba(xa)[:,1];pw=models['wait'].predict_proba(xa)[:,1];pr=models['replace'].predict_proba(xa)[:,1]
    pred=[]
    for a,w,r in zip(pa,pw,pr): pred.append(('REPLACE_ROUTE' if r>=.5 else 'KEEP_EXECUTING') if a>=.5 else ('WAIT_FOR_CLARITY' if w>=.5 else 'RETURN_TO_CONTROLLER'))
    truth=[str(r['teacherAction']) for r in rows]
    actset={'REPLACE_ROUTE','KEEP_EXECUTING'}
    from collections import Counter
    return {'n':len(rows),'exactAccuracy':sum(a==b for a,b in zip(pred,truth))/len(rows),'prematureActRate':sum(t not in actset and p in actset for t,p in zip(truth,pred))/len(rows),'missedAct':sum(t in actset and p not in actset for t,p in zip(truth,pred)),'wrongActSide':sum(t in actset and p in actset and t!=p for t,p in zip(truth,pred)),'waitInsteadOfReturn':sum(t=='RETURN_TO_CONTROLLER' and p=='WAIT_FOR_CLARITY' for t,p in zip(truth,pred)),'returnInsteadOfWait':sum(t=='WAIT_FOR_CLARITY' and p=='RETURN_TO_CONTROLLER' for t,p in zip(truth,pred)),'predictedActions':dict(Counter(pred)),'trueActions':dict(Counter(truth))}

def variant(name,feats,parts):
    tr=parts['train'];act=fit(tr,feats,lambda r:r['teacherActNow']); no=[r for r in tr if int(r['teacherActNow'])==0];wait=fit(no,feats,lambda r:r['teacherWait']); ac=[r for r in tr if int(r['teacherActNow'])==1];rep=fit(ac,feats,lambda r:r['teacherReplaceIfAct']);models={'act':act,'wait':wait,'replace':rep}
    out={'name':name,'features':feats,'actNow':{},'waitVsReturn':{},'replaceVsKeep':{},'policy':{}}
    for k,rs in parts.items():
        out['actNow'][k]=metric(act,rs,feats,lambda r:r['teacherActNow'])
        nr=[r for r in rs if int(r['teacherActNow'])==0]; ar=[r for r in rs if int(r['teacherActNow'])==1]
        out['waitVsReturn'][k]=metric(wait,nr,feats,lambda r:r['teacherWait']);out['replaceVsKeep'][k]=metric(rep,ar,feats,lambda r:r['teacherReplaceIfAct']);out['policy'][k]=policy(models,rs,feats)
    return models,out

def main():
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
    sp={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])};parts={k:[r for r in rows if int(r['marketId']) in v] for k,v in sp.items()}
    specs={'BASE_TEMPORAL':CURRENT+DELTA,'PLUS_TRAP_HISTORY':CURRENT+DELTA+TRAP,'PLUS_TRAP_HISTORY_DELTAS':CURRENT+DELTA+TRAP+TRAP_DELTA}
    arts={};reps={}
    for n,f in specs.items(): arts[n],reps[n]=variant(n,f,parts)
    joblib.dump({'version':'THEORY_EXECUTION_HANDOFF_V1','models':arts,'features':specs,'splitMarkets':{k:sorted(v) for k,v in sp.items()},'threshold':.5},ART)
    report={'version':'THEORY_EXECUTION_HANDOFF_V1_REPORT','researchOnly':True,'liveTradingChanges':False,'source':str(SRC),'split':{k:{'markets':len(v),'rows':len(parts[k])} for k,v in sp.items()},'variants':reps,'guardrails':['Strict-past trap events only.','Full-market trap categories/severity excluded from runtime features.','Same 100/26/23 chronological market split.','Threshold fixed at 0.5; no sweep.','Winner/PnL/Target/future event labels excluded.']}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    compact={}
    for n,v in reps.items(): compact[n]={'act':{k:v['actNow'][k] for k in ['validation','forwardOos']},'wait':{k:v['waitVsReturn'][k] for k in ['validation','forwardOos']},'replace':{k:v['replaceVsKeep'][k] for k in ['validation','forwardOos']},'policy':{k:v['policy'][k] for k in ['validation','forwardOos']}}
    print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'split':report['split'],'results':compact},ensure_ascii=False,allow_nan=True))
if __name__=='__main__':main()
