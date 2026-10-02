from __future__ import annotations
import json,joblib,sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_lifecycle_dataset_fresh21_v1.json'
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_frozen_transition_semantic_identification_preregistered_v1.json'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_frozen_transition_semantic_identification_v1.json'

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float)
    out={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
    if len(y) and len(np.unique(y))>1:
        out['auc']=float(roc_auc_score(y,p));out['ap']=float(average_precision_score(y,p));out['logLoss']=float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))
    else:out.update({'auc':None,'ap':None,'logLoss':None})
    return out

def main():
    src=json.loads(SRC.read_text(encoding='utf-8'));pre=json.loads(PRE.read_text(encoding='utf-8'))
    first={}
    for r in src['rows']:
        k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
        if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
    rr=sorted(first.values(),key=lambda r:(int(r['marketId']),int(r['t']),str(r['checkpointResponsibilityId'])))
    art=joblib.load(MODEL);feats=list(art['features']);m=art['model']
    X=np.asarray([[float(r[f]) for f in feats] for r in rr],dtype=float); proba=m.predict_proba(X); p=proba[:,1] if proba.shape[1]>1 else proba[:,0]
    labels={
      'LEAVES_LIVE_STATE_5S':[1-int(r.get('rootResponsibilityPersists5s') or 0) for r in rr],
      'LIVE_STALLED_5S':[int(str(r.get('rootLifecycleOutcome5s'))=='LIVE_STALLED') for r in rr],
      'NO_ROOT_PROGRESS_OR_COMPLETE_5S':[1-int(r.get('rootProgressOrComplete5s') or 0) for r in rr],
      'NO_FLOOR_IMPROVEMENT_5S':[1-int(r.get('floorImproved5s') or 0) for r in rr],
      'NO_ABSNET_REDUCTION_5S':[1-int(r.get('absNetReduced5s') or 0) for r in rr],
      'COMPLETED_5S':[int(r.get('rootCompleted5s') or 0) for r in rr],
    }
    metrics={k:met(v,p) for k,v in labels.items()}
    ranked=[]
    for k,v in metrics.items():
        if v.get('auc') is not None: ranked.append({'semantic':k,**v,'aucDistanceFromChance':abs(v['auc']-.5),'direction':'POSITIVE' if v['auc']>=.5 else 'INVERSE'})
    ranked.sort(key=lambda z:(-z['aucDistanceFromChance'],-(z.get('ap') or 0)))
    rep={'version':'R4_P0B_FROZEN_TRANSITION_SEMANTIC_IDENTIFICATION_V1','status':'DEVELOPMENT_SEMANTIC_IDENTIFICATION_COMPLETE','preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'cohort':{'markets':len(set(int(r['marketId']) for r in rr)),'roots':len(rr)},'model':{'artifact':str(MODEL.relative_to(ROOT)).replace('\\','/'),'features':feats,'refit':False,'thresholded':False},'metrics':metrics,'rankedByAbsoluteAucDistanceFromChance':ranked,'rows':[{'marketId':int(r['marketId']),'t':int(r['t']),'responsibilityId':str(r['checkpointResponsibilityId']),'transitionScore':float(p[i]),'outcome5s':str(r.get('rootLifecycleOutcome5s')),'floorDelta5s':float(r.get('floorDelta5s') or 0.),'absNetDelta5s':float(r.get('absNetDelta5s') or 0.)} for i,r in enumerate(rr)],'guards':pre['guards']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':rep['status'],'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'metrics':metrics,'ranked':ranked},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
