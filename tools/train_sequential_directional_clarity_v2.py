from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, balanced_accuracy_score, confusion_matrix, log_loss, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

ROOT=Path(__file__).resolve().parents[1]
import sys
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'sequential_arbitration_option_teacher_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
ART=D/'sequential_directional_clarity_v2.joblib'
REPORT=D/'sequential_directional_clarity_v2_report.json'

from tools.train_sequential_arbitration_option_v1 import CURRENT, DELTA, finite


def pipe()->Pipeline:
    return Pipeline([
        ('imputer',SimpleImputer(strategy='median',add_indicator=True)),
        ('scale',StandardScaler()),
        ('logit',LogisticRegression(C=0.35,max_iter=4000,class_weight='balanced',solver='lbfgs')),
    ])


def X(rows:list[dict[str,Any]], feats:list[str])->np.ndarray:
    return np.asarray([[finite((r.get('features') or {}).get(f)) for f in feats] for r in rows],float)


def fit(rows:list[dict[str,Any]], feats:list[str], positive_action:str)->Pipeline:
    y=np.asarray([1 if str(r.get('teacherAction'))==positive_action else 0 for r in rows],int)
    m=pipe(); m.fit(X(rows,feats),y); return m


def fit_wait(rows:list[dict[str,Any]], feats:list[str])->Pipeline:
    z=[r for r in rows if str(r.get('teacherAction')) in {'WAIT_FOR_CLARITY','RETURN_TO_CONTROLLER'}]
    y=np.asarray([1 if str(r.get('teacherAction'))=='WAIT_FOR_CLARITY' else 0 for r in z],int)
    m=pipe(); m.fit(X(z,feats),y); return m


def metric(model:Pipeline, rows:list[dict[str,Any]], feats:list[str], positive_action:str)->dict[str,Any]:
    if not rows:return {'n':0}
    y=np.asarray([1 if str(r.get('teacherAction'))==positive_action else 0 for r in rows],int)
    p=model.predict_proba(X(rows,feats))[:,1]; z=(p>=.5).astype(int); both=len(set(y.tolist()))>1
    return {
        'n':len(rows),'positiveRate':float(y.mean()),'predictedPositiveRate':float(z.mean()),
        'auc':float(roc_auc_score(y,p)) if both else None,
        'ap':float(average_precision_score(y,p)) if y.sum() else None,
        'balancedAccuracy':float(balanced_accuracy_score(y,z)) if both else None,
        'precision':float(precision_score(y,z,zero_division=0)),
        'recall':float(recall_score(y,z,zero_division=0)),
        'logLoss':float(log_loss(y,p,labels=[0,1])),
        'confusionTnFpFnTp':confusion_matrix(y,z,labels=[0,1]).ravel().astype(int).tolist(),
    }


def wait_metric(model:Pipeline, rows:list[dict[str,Any]], feats:list[str])->dict[str,Any]:
    z=[r for r in rows if str(r.get('teacherAction')) in {'WAIT_FOR_CLARITY','RETURN_TO_CONTROLLER'}]
    return metric(model,z,feats,'WAIT_FOR_CLARITY')


def policy_eval(models:dict[str,Pipeline], rows:list[dict[str,Any]], feats:list[str])->dict[str,Any]:
    if not rows:return {'n':0}
    x=X(rows,feats)
    pr=models['replaceClarity'].predict_proba(x)[:,1]
    pk=models['keepClarity'].predict_proba(x)[:,1]
    pw=models['wait'].predict_proba(x)[:,1]
    pred=[]
    for r,k,w in zip(pr,pk,pw):
        rep=bool(r>=.5); keep=bool(k>=.5)
        if rep and not keep: pred.append('REPLACE_ROUTE')
        elif keep and not rep: pred.append('KEEP_EXECUTING')
        else: pred.append('WAIT_FOR_CLARITY' if w>=.5 else 'RETURN_TO_CONTROLLER')
    truth=[str(r.get('teacherAction')) for r in rows]
    n=len(rows)
    premature=sum(t in {'WAIT_FOR_CLARITY','RETURN_TO_CONTROLLER'} and p in {'REPLACE_ROUTE','KEEP_EXECUTING'} for t,p in zip(truth,pred))
    missed=sum(t in {'REPLACE_ROUTE','KEEP_EXECUTING'} and p not in {'REPLACE_ROUTE','KEEP_EXECUTING'} for t,p in zip(truth,pred))
    wrong=sum(t in {'REPLACE_ROUTE','KEEP_EXECUTING'} and p in {'REPLACE_ROUTE','KEEP_EXECUTING'} and t!=p for t,p in zip(truth,pred))
    conflicts=int(sum(bool(a>=.5 and b>=.5) for a,b in zip(pr,pk)))
    neither=int(sum(bool(a<.5 and b<.5) for a,b in zip(pr,pk)))
    return {
        'n':n,'exactAccuracy':sum(a==b for a,b in zip(pred,truth))/n,
        'predictedActions':dict(Counter(pred)),'trueActions':dict(Counter(truth)),
        'prematureActOnWaitOrReturn':premature,'prematureActRate':premature/n,
        'missedAct':missed,'wrongActSide':wrong,'directionalConflictCount':conflicts,'neitherClarityCount':neither,
        'rows':[{'marketId':int(rr['marketId']),'delayMs':int(rr['delayMs']),'truth':t,'pred':p,'pReplaceClarity':float(a),'pKeepClarity':float(b),'pWait':float(w)} for rr,t,p,a,b,w in zip(rows,truth,pred,pr,pk,pw)],
    }


def run_variant(name:str, feats:list[str], parts:dict[str,list[dict[str,Any]]])->tuple[dict[str,Pipeline],dict[str,Any]]:
    tr=parts['train']
    models={
        'replaceClarity':fit(tr,feats,'REPLACE_ROUTE'),
        'keepClarity':fit(tr,feats,'KEEP_EXECUTING'),
        'wait':fit_wait(tr,feats),
    }
    rep={'name':name,'features':feats,'replaceClarity':{},'keepClarity':{},'waitVsReturn':{},'policy':{}}
    for k,rs in parts.items():
        rep['replaceClarity'][k]=metric(models['replaceClarity'],rs,feats,'REPLACE_ROUTE')
        rep['keepClarity'][k]=metric(models['keepClarity'],rs,feats,'KEEP_EXECUTING')
        rep['waitVsReturn'][k]=wait_metric(models['wait'],rs,feats)
        rep['policy'][k]=policy_eval(models,rs,feats)
    return models,rep


def main()->int:
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
    split={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}
    parts={k:[r for r in rows if int(r['marketId']) in ids] for k,ids in split.items()}
    cur_models,cur=run_variant('CURRENT_ONLY_DIRECTIONAL_CLARITY',CURRENT,parts)
    tmp_models,tmp=run_variant('CURRENT_PLUS_TEMPORAL_DIRECTIONAL_CLARITY',CURRENT+DELTA,parts)
    artifact={
        'version':'SEQUENTIAL_DIRECTIONAL_CLARITY_V2','researchOnly':True,'liveTradingChanges':False,
        'thresholds':{'replaceClarity':.5,'keepClarity':.5,'wait':.5},
        'currentOnly':{'models':cur_models,'features':CURRENT},
        'temporal':{'models':tmp_models,'features':CURRENT+DELTA},
        'splitMarkets':{k:sorted(v) for k,v in split.items()},
        'semantics':'Separate one-vs-rest clarity heads. ACT only if exactly one of REPLACE_CLARITY or KEEP_CLARITY is >=0.5. If neither or both, abstain to WAIT/RETURN gate. No threshold sweep.',
    }
    joblib.dump(artifact,ART)
    report={
        'version':'SEQUENTIAL_DIRECTIONAL_CLARITY_V2_REPORT','researchOnly':True,'liveTradingChanges':False,
        'teacher':str(SRC),'split':{k:{'markets':len(v),'rows':len(parts[k])} for k,v in split.items()},
        'currentOnly':cur,'temporal':tmp,
        'guardrails':['Same chronological Frozen100/Frozen26/Forward23 split as V1.','No winner/PnL/Target/future Pareto runtime fields.','Natural 0.5 thresholds only; no sweep.','Directional clarity heads replace the non-convex ACT union; WAIT/RETURN remains a separate subskill.'],
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    compact=lambda v:{'replace':v['replaceClarity'],'keep':v['keepClarity'],'wait':v['waitVsReturn'],'policy':{k:{q:x[q] for q in ['n','exactAccuracy','prematureActRate','missedAct','wrongActSide','directionalConflictCount','predictedActions','trueActions']} for k,x in v['policy'].items()}}
    print(json.dumps({'ok':True,'artifact':str(ART),'report':str(REPORT),'split':report['split'],'currentOnly':compact(cur),'temporal':compact(tmp)},ensure_ascii=False,allow_nan=True))
    return 0

if __name__=='__main__':
    raise SystemExit(main())
