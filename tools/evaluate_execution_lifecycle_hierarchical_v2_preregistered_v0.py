from __future__ import annotations

import json, math
from collections import Counter
from pathlib import Path
from typing import Any

import joblib
import numpy as np

ROOT=Path(__file__).resolve().parents[1]
D=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
SRC=D/'sequential_arbitration_option_teacher_v1.jsonl'
COHORT=D/'pair_completion_canonical_cohort_v1.json'
ART=D/'sequential_arbitration_option_v1.joblib'
OUT=D/'hierarchical_execution_lifecycle_v2_preregistered_offline_v0.json'


def finite(v:Any)->float:
    try:
        x=float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def X(rows, feats):
    return np.asarray([[finite((r.get('features') or {}).get(f)) for f in feats] for r in rows],float)


def eval_split(rows, variant):
    feats=list(variant['features']); models=variant['models']; x=X(rows,feats)
    pw=models['wait'].predict_proba(x)[:,1]
    pr=models['replace'].predict_proba(x)[:,1]
    pred=[]; branches=[]
    for r,w,q in zip(rows,pw,pr):
        f=r.get('features') or {}
        # Pre-registered V2 topology: lifecycle state, not monolithic ACT probability,
        # chooses which already-trained binary head has authority.
        working=finite(f.get('workingRecoveryExists')) >= 0.5
        if working:
            p='REPLACE_ROUTE' if q>=0.5 else 'KEEP_EXECUTING'; b='WORKING_CHILD_REPLACE_KEEP'
        else:
            p='WAIT_FOR_CLARITY' if w>=0.5 else 'RETURN_TO_CONTROLLER'; b='NO_WORKING_CHILD_WAIT_RETURN'
        pred.append(p);branches.append(b)
    truth=[str(r['teacherAction']) for r in rows]
    exact=sum(a==b for a,b in zip(pred,truth))
    premature=sum(t in {'WAIT_FOR_CLARITY','RETURN_TO_CONTROLLER'} and p in {'REPLACE_ROUTE','KEEP_EXECUTING'} for t,p in zip(truth,pred))
    missed=sum(t in {'REPLACE_ROUTE','KEEP_EXECUTING'} and p not in {'REPLACE_ROUTE','KEEP_EXECUTING'} for t,p in zip(truth,pred))
    wrong=sum(t in {'REPLACE_ROUTE','KEEP_EXECUTING'} and p in {'REPLACE_ROUTE','KEEP_EXECUTING'} and t!=p for t,p in zip(truth,pred))
    wait_on_return=sum(t=='RETURN_TO_CONTROLLER' and p=='WAIT_FOR_CLARITY' for t,p in zip(truth,pred))
    return_on_wait=sum(t=='WAIT_FOR_CLARITY' and p=='RETURN_TO_CONTROLLER' for t,p in zip(truth,pred))
    by_branch={}
    for b in sorted(set(branches)):
        idx=[i for i,z in enumerate(branches) if z==b]
        by_branch[b]={'n':len(idx),'exactAccuracy':sum(pred[i]==truth[i] for i in idx)/len(idx) if idx else None,'predictedActions':dict(Counter(pred[i] for i in idx)),'trueActions':dict(Counter(truth[i] for i in idx))}
    return {'n':len(rows),'exactAccuracy':exact/len(rows) if rows else None,'predictedActions':dict(Counter(pred)),'trueActions':dict(Counter(truth)),'prematureActOnWaitOrReturn':premature,'prematureActRate':premature/len(rows) if rows else None,'missedAct':missed,'missedActRate':missed/len(rows) if rows else None,'wrongActSide':wrong,'waitInsteadOfReturn':wait_on_return,'returnInsteadOfWait':return_on_wait,'branchCounts':dict(Counter(branches)),'byBranch':by_branch}


def compact_monolithic(report, split):
    p=report['currentOnly']['policy'][split]
    return {k:p[k] for k in ['n','exactAccuracy','predictedActions','trueActions','prematureActOnWaitOrReturn','prematureActRate','missedAct','wrongActSide','waitInsteadOfReturn','returnInsteadOfWait']}


def main():
    rows=[json.loads(x) for x in SRC.read_text(encoding='utf-8').splitlines() if x.strip()]
    mids=[int(x) for x in json.loads(COHORT.read_text(encoding='utf-8'))['marketIds']]
    split={'train':set(mids[:100]),'validation':set(mids[100:126]),'forwardOos':set(mids[126:])}
    parts={k:[r for r in rows if int(r['marketId']) in ids] for k,ids in split.items()}
    art=joblib.load(ART); variant=art['currentOnly']
    old=json.loads((D/'sequential_arbitration_option_v1_report.json').read_text(encoding='utf-8'))
    out={'version':'HIERARCHICAL_EXECUTION_LIFECYCLE_V2_PREREGISTERED_OFFLINE_V0','researchOnly':True,'graduationEligible':False,'liveTradingChanges':False,'candidateFrozen':False,'hypothesis':'Remove unstable monolithic ACT_NOW gate. Let current execution lifecycle topology choose authority: working Maker child -> frozen REPLACE-vs-KEEP head; no working child -> frozen WAIT-vs-RETURN head. Natural 0.5 only.','runtimeFeatures':'strict-past public + OUR execution state only; no winner/PnL/Target/future labels','modelReuse':'Existing SEQUENTIAL_ARBITRATION_OPTION_V1/currentOnly binary heads, no retraining','splits':{},'guardrails':['Opened canonical 149-market execution curriculum only.','No Candidate V1 formal exam markets used for training/selection.','No threshold sweep; natural 0.5 only.','This is teacher-space architecture screening, not closed-loop performance evidence.']}
    for name,rs in parts.items():
        new=eval_split(rs,variant); base=compact_monolithic(old,name)
        out['splits'][name]={'monolithicV1':base,'hierarchicalV2':new,'deltaExactAccuracy':new['exactAccuracy']-base['exactAccuracy'],'deltaPrematureActRate':new['prematureActRate']-base['prematureActRate'],'deltaMissedAct':new['missedAct']-base['missedAct']}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'report':str(OUT),'splits':{k:{'v1Exact':v['monolithicV1']['exactAccuracy'],'v2Exact':v['hierarchicalV2']['exactAccuracy'],'deltaExact':v['deltaExactAccuracy'],'v1Premature':v['monolithicV1']['prematureActRate'],'v2Premature':v['hierarchicalV2']['prematureActRate'],'v1Missed':v['monolithicV1']['missedAct'],'v2Missed':v['hierarchicalV2']['missedAct'],'v2Actions':v['hierarchicalV2']['predictedActions'],'branches':v['hierarchicalV2']['branchCounts']} for k,v in out['splits'].items()}},ensure_ascii=False))

if __name__=='__main__': main()
