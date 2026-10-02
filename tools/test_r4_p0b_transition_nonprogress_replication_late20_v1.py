from __future__ import annotations
import json,lzma,joblib,sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_transition_nonprogress_replication_late20_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_transition_nonprogress_replication_late20_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib'

def core(x):
    return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float);out={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
    if len(y) and len(np.unique(y))>1:
        out['auc']=float(roc_auc_score(y,p));out['ap']=float(average_precision_score(y,p));out['logLoss']=float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))
    else:out.update({'auc':None,'ap':None,'logLoss':None})
    return out

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8'));ids=[int(x) for x in pre['markets']];allrows=[];exact=0;market=[]
    for mid in ids:
        d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        a=base.simulate(d,pre['policy'],collect_shadow=False);b=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True)
        ce=core(a)==core(b);exact+=int(ce);lr=b.get('managementLifecycleRows') or [];allrows.extend(lr);market.append({'marketId':mid,'executionCoreExact':ce,'lifecycleRows':len(lr),'roots':len(set(str(r.get('checkpointResponsibilityId')) for r in lr))});print(json.dumps(market[-1],ensure_ascii=False),flush=True)
    first={}
    for r in allrows:
        k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
        if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
    rr=sorted(first.values(),key=lambda r:(int(r['marketId']),int(r['t']),str(r['checkpointResponsibilityId'])))
    dup=len(rr)-len({(int(r['marketId']),str(r['checkpointResponsibilityId'])) for r in rr})
    art=joblib.load(MODEL);feats=list(art['features']);m=art['model'];X=np.asarray([[float(r[f]) for f in feats] for r in rr],dtype=float);pp=m.predict_proba(X);score=pp[:,1] if pp.shape[1]>1 else pp[:,0]
    labs={
      'NO_ABSNET_REDUCTION_5S':[1-int(r.get('absNetReduced5s') or 0) for r in rr],
      'NO_FLOOR_IMPROVEMENT_5S':[1-int(r.get('floorImproved5s') or 0) for r in rr],
      'LIVE_STALLED_5S':[int(str(r.get('rootLifecycleOutcome5s'))=='LIVE_STALLED') for r in rr],
      'LEAVES_LIVE_STATE_5S':[1-int(r.get('rootResponsibilityPersists5s') or 0) for r in rr],
      'NO_ROOT_PROGRESS_OR_COMPLETE_5S':[1-int(r.get('rootProgressOrComplete5s') or 0) for r in rr]
    }
    metrics={k:met(v,score) for k,v in labs.items()};rule=pre['fixedKeepRule'];p=metrics['NO_ABSNET_REDUCTION_5S'];s=metrics['NO_FLOOR_IMPROVEMENT_5S']
    checks={'primaryAuc':p.get('auc') is not None and p['auc']>=rule['primaryAucMinimum'],'primaryApAboveRate':p.get('ap') is not None and p['ap']>p['rate'],'secondaryAuc':s.get('auc') is not None and s['auc']>=rule['secondaryAucMinimum'],'executionNoRegression':exact==len(ids),'duplicateRootCheckpointKeys':dup==0}
    status='REPLICATION_KEEP' if all(checks.values()) else 'REPLICATION_REJECT';outcomes={}
    for r in rr:outcomes[str(r.get('rootLifecycleOutcome5s'))]=outcomes.get(str(r.get('rootLifecycleOutcome5s')),0)+1
    rep={'version':'R4_P0B_TRANSITION_NONPROGRESS_REPLICATION_LATE20_V1','status':status,'preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'cohort':{'markets':len(ids),'marketsWithRows':len(set(int(r['marketId']) for r in rr)),'roots':len(rr),'allLifecycleRows':len(allrows)},'integrity':{'executionCoreExact':f'{exact}/{len(ids)}','duplicateRootCheckpointKeys':dup},'metrics':metrics,'checks':checks,'outcomes':outcomes,'marketRows':market,'guards':pre['guards']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'integrity':rep['integrity'],'metrics':metrics,'checks':checks,'outcomes':outcomes},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
