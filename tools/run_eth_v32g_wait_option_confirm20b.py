from __future__ import annotations
import json
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score
from tools import audit_eth_v32f_joint_parent_reachability_v1 as base

ROOT=Path(__file__).resolve().parents[1]
base.SRC=ROOT/'data/research/lan_worker_returns/eth-v24-shadow-quality-confirm20-b/result.json'
base.OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V32G_CONFIRM20B_PARENT_REACHABILITY_RAW.json'
FINAL=ROOT/'data/research/r4_v0/p0_provenance_v1/ETH_REPAIR_V32G_WAIT_OPTION_VALUE_CONFIRM20B_RESULT.json'

def med(rows,k,fail):
    a=[float(r[k]) for r in rows if bool(r['fail'])==fail and r.get(k) is not None]
    return float(np.median(a)) if a else None

def main():
    base.main()
    d=json.loads(base.OUT.read_text(encoding='utf-8'))
    rows=[]
    for p in d['parents']:
        if len(p['rows'])<2: continue
        a,b=p['rows'][0],p['rows'][1]
        gap1=max(0.0,-float(a['gap'])); gap2=max(0.0,-float(b['gap']))
        rows.append({
            'marketId':p['marketId'],'parentSegment':p['parentSegment'],'fail':not bool(p['recovered']),
            'midDelta':float(b['marketMid'])-float(a['marketMid']),
            'gapDelta':gap2-gap1,'gap2':gap2,
            'elapsed':(int(b['placed'])-int(a['placed']))/1000.0,
            'secondsLeft2':float(b['secondsLeft'])
        })
    y=np.asarray([int(r['fail']) for r in rows])
    metrics={}
    for k,sign in [('midDelta',1),('gapDelta',1),('gap2',1),('elapsed',1),('secondsLeft2',-1)]:
        x=np.asarray([float(r[k])*sign for r in rows])
        auc=float(roc_auc_score(y,x)) if len(set(y.tolist()))==2 else None
        metrics[k]={'aucFailure':auc,'recoveredMedian':med(rows,k,False),'failedMedian':med(rows,k,True)}
    m=metrics
    keep=bool(m['midDelta']['aucFailure'] is not None and m['gapDelta']['aucFailure'] is not None and
              m['midDelta']['aucFailure']>=.70 and m['gapDelta']['aucFailure']>=.70 and
              m['midDelta']['failedMedian']>m['midDelta']['recoveredMedian'] and
              m['gapDelta']['failedMedian']>m['gapDelta']['recoveredMedian'])
    out={
      'version':'ETH_REPAIR_V32G_WAIT_OPTION_VALUE_CONFIRM20B_RESULT','researchOnly':True,'actionAuthority':False,
      'preregistered':'ETH_REPAIR_V32G_WAIT_OPTION_VALUE_CONFIRM20B_PREREGISTERED.json',
      'cohort':{'parentsWithSecondCarrier':len(rows),'markets':len(set(r['marketId'] for r in rows)),'failed':int(y.sum()),'recovered':int(len(y)-y.sum())},
      'metrics':metrics,'fixedKeepRulePassed':keep,
      'decision':'KEEP_WAIT_OPTION_GRADIENT_SIGNAL' if keep else 'TESTED_INCONCLUSIVE_OR_REJECT_NO_RESCUE',
      'rows':rows,
      'interpretation':'This is a causal second-carrier checkpoint: both first and second passive opportunities have materialized, no first-carrier responsibility fill has closed the parent, and only strict-past market/book state through the second placement forms the features.',
      'boundary':['future parent fill only scoring label','no winner/PnL','no feature/window/threshold tuning after prereg','no behavior change','continuous readiness signal only even if KEEP']
    }
    FINAL.write_text(json.dumps(out,indent=2),encoding='utf-8')
    print(json.dumps({'ok':True,'cohort':out['cohort'],'metrics':metrics,'passed':keep,'decision':out['decision'],'output':str(FINAL)},ensure_ascii=False))
if __name__=='__main__':main()
