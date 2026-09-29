from __future__ import annotations
import json, math, joblib
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score, average_precision_score, log_loss

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_lifecycle_checkpoint_dataset_v1.json'
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_frozen_m0_semantic_identification_preregistered_v1.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_frozen_m0_semantic_identification_v1.json'
EPS=1e-12

def metric(y,p):
    y=np.asarray(y,dtype=int); p=np.asarray(p,dtype=float)
    if len(y)==0: return {'n':0}
    out={'n':int(len(y)),'rate':float(y.mean())}
    if len(np.unique(y))>1:
        out['auc']=float(roc_auc_score(y,p))
        out['ap']=float(average_precision_score(y,p)) if y.sum()>0 else None
        out['logLoss']=float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))
    else:
        out.update({'auc':None,'ap':None,'logLoss':None})
    return out

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8'))
    src=json.loads(SRC.read_text(encoding='utf-8'))
    rows=src['rows']
    # deterministic first checkpoint per durable root
    first={}
    for r in rows:
        k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
        if k not in first or int(r['t'])<int(first[k]['t']): first[k]=r
    rr=sorted(first.values(), key=lambda r:(int(r['marketId']),int(r['t']),str(r['checkpointResponsibilityId'])))
    stack=joblib.load(MODEL); feats=list(stack['features']['full']); m=stack['M0_model']
    X=np.asarray([[float(r[f]) for f in feats] for r in rr],dtype=float)
    p=m.predict_proba(X)[:,1]
    labels={
        'COMPLETED_5S': np.asarray([int(r.get('rootCompleted5s') or 0) for r in rr]),
        'PROGRESS_OR_COMPLETE_5S': np.asarray([int(r.get('rootProgressOrComplete5s') or 0) for r in rr]),
        'RESPONSIBILITY_PERSISTS_5S': np.asarray([int(r.get('rootResponsibilityPersists5s') or 0) for r in rr]),
        'LIVE_OWNER_AT_5S': np.asarray([int(r.get('rootLiveOwnerAt5s') or 0) for r in rr]),
        'NOT_STALLED_OR_UNOWNED_5S': np.asarray([int(str(r.get('rootLifecycleOutcome5s')) in {'COMPLETED','LIVE_PROGRESS'}) for r in rr]),
        'WEAK_FILL_5S': np.asarray([int(r.get('futureWeakMakerFill5s') or 0) for r in rr]),
        'FLOOR_IMPROVE_5S': np.asarray([int(r.get('floorImproved5s') or 0) for r in rr]),
        'ABSNET_REDUCE_5S': np.asarray([int(r.get('absNetReduced5s') or 0) for r in rr]),
    }
    metrics={k:metric(y,p) for k,y in labels.items()}
    ranked=[]
    for k,v in metrics.items():
        if v.get('auc') is not None:
            ranked.append({'semantic':k,**v,'aucDistanceFromChance':abs(float(v['auc'])-.5),'direction':'POSITIVE' if float(v['auc'])>=.5 else 'INVERSE'})
    ranked=sorted(ranked,key=lambda z:(-z['aucDistanceFromChance'],-(z.get('ap') or 0)))
    # Correlation with continuous realized path diagnostics, diagnostic only.
    cont={}
    for name,key in [('ROOT_FILL_SHARES_5S','rootFillShares5s'),('FLOOR_DELTA_5S','floorDelta5s'),('ABSNET_DELTA_5S','absNetDelta5s')]:
        y=np.asarray([float(r.get(key) or 0.) for r in rr],dtype=float)
        if np.std(y)>EPS and np.std(p)>EPS:
            cont[name]={'pearson':float(np.corrcoef(p,y)[0,1]),'n':len(rr)}
        else: cont[name]={'pearson':None,'n':len(rr)}
    per_rows=[]
    for i,r in enumerate(rr):
        per_rows.append({'marketId':int(r['marketId']),'t':int(r['t']),'responsibilityId':str(r['checkpointResponsibilityId']),'m0Score':float(p[i]),'outcome5s':str(r.get('rootLifecycleOutcome5s')),'rootFillShares5s':float(r.get('rootFillShares5s') or 0.),'floorDelta5s':float(r.get('floorDelta5s') or 0.),'absNetDelta5s':float(r.get('absNetDelta5s') or 0.)})
    rep={
        'version':'R4_P0B_FROZEN_M0_SEMANTIC_IDENTIFICATION_V1',
        'status':'DEVELOPMENT_SEMANTIC_IDENTIFICATION_COMPLETE',
        'preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),
        'cohort':{'markets':len(set(int(r['marketId']) for r in rr)),'responsibilityRoots':len(rr),'allLifecycleRowsExcludedFromPrimary':len(rows)-len(rr)},
        'model':{'artifact':str(MODEL.relative_to(ROOT)).replace('\\','/'),'features':feats,'refit':False,'thresholded':False},
        'metrics':metrics,
        'rankedByAbsoluteAucDistanceFromChance':ranked,
        'continuousDiagnostics':cont,
        'referenceOldEventMappingAuc':pre['referenceOldEventMapping']['auc'],
        'rows':per_rows,
        'interpretation':'Development-only semantic identification. A high/inverse association does not authorize action. The exact semantic selected for follow-up must be frozen before using the untouched 22-market replication cohort.',
        'guards':pre['guards']
    }
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':rep['status'],'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'metrics':metrics,'ranked':ranked,'continuousDiagnostics':cont},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
