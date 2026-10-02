from __future__ import annotations
import json,lzma,joblib,sys
from pathlib import Path
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_m0_economic_progress_replication22_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_m0_economic_progress_replication22_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
MODEL=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim

def core(x):
    return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float)
    out={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
    if len(y) and len(np.unique(y))>1:
        out['auc']=float(roc_auc_score(y,p));out['ap']=float(average_precision_score(y,p));out['logLoss']=float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))
    else:out.update({'auc':None,'ap':None,'logLoss':None})
    return out

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8')); ids=[int(x) for x in pre['markets']]
    allrows=[]; exact=0; market_rows=[]
    for mid in ids:
        p=SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz'
        d=json.load(lzma.open(p,'rt',encoding='utf-8'))
        a=base.simulate(d,pre['policy'],collect_shadow=False)
        b=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True)
        ce=(core(a)==core(b)); exact+=int(ce)
        lr=b.get('managementLifecycleRows') or []
        allrows.extend(lr)
        market_rows.append({'marketId':mid,'executionCoreExact':ce,'lifecycleRows':len(lr),'responsibilityRoots':len(set(str(r.get('checkpointResponsibilityId')) for r in lr))})
        print(json.dumps(market_rows[-1],ensure_ascii=False),flush=True)
    # first checkpoint per market/root
    first={}
    for r in allrows:
        k=(int(r['marketId']),str(r['checkpointResponsibilityId']))
        if k not in first or int(r['t'])<int(first[k]['t']):first[k]=r
    rr=sorted(first.values(),key=lambda r:(int(r['marketId']),int(r['t']),str(r['checkpointResponsibilityId'])))
    dup=len(rr)-len({(int(r['marketId']),str(r['checkpointResponsibilityId'])) for r in rr})
    valid_labels={'LIVE_STALLED','COMPLETED','OPEN_UNOWNED','LIVE_PROGRESS','TERMINATED'}
    invalid=sum(1 for r in rr if str(r.get('rootLifecycleOutcome5s')) not in valid_labels)
    stack=joblib.load(MODEL);feats=list(stack['features']['full']);m=stack['M0_model']
    X=np.asarray([[float(r[f]) for f in feats] for r in rr],dtype=float);score=m.predict_proba(X)[:,1]
    labs={
      'FLOOR_IMPROVE_5S':[int(r.get('floorImproved5s') or 0) for r in rr],
      'WEAK_FILL_5S':[int(r.get('futureWeakMakerFill5s') or 0) for r in rr],
      'ABSNET_REDUCE_5S':[int(r.get('absNetReduced5s') or 0) for r in rr],
      'COMPLETED_5S':[int(r.get('rootCompleted5s') or 0) for r in rr],
      'RESPONSIBILITY_PERSISTS_5S':[int(r.get('rootResponsibilityPersists5s') or 0) for r in rr],
    }
    metrics={k:met(v,score) for k,v in labs.items()}
    rule=pre['fixedKeepRule']; prim=metrics['FLOOR_IMPROVE_5S']; sec=[metrics['WEAK_FILL_5S'],metrics['ABSNET_REDUCE_5S']]
    checks={
      'primaryAuc':prim.get('auc') is not None and prim['auc']>=float(rule['primaryAucMinimum']),
      'primaryApAboveRate':prim.get('ap') is not None and prim['ap']>prim['rate'],
      'secondaryEconomic':any(x.get('auc') is not None and x['auc']>=float(rule['atLeastOneSecondaryEconomicAucMinimum']) for x in sec),
      'executionNoRegression':exact==len(ids),
      'duplicateRootCheckpointKeys':dup==0,
      'invalidLifecycleLabels':invalid==0,
    }
    status='REPLICATION_KEEP' if all(checks.values()) else 'REPLICATION_REJECT'
    outcomes={}
    for r in rr: outcomes[str(r['rootLifecycleOutcome5s'])]=outcomes.get(str(r['rootLifecycleOutcome5s']),0)+1
    rep={'version':'R4_P0B_M0_ECONOMIC_PROGRESS_REPLICATION22_V1','status':status,'preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),
      'cohort':{'markets':len(ids),'marketsWithLifecycleRows':len(set(int(r['marketId']) for r in rr)),'firstCheckpointRoots':len(rr),'allLifecycleRows':len(allrows)},
      'integrity':{'executionCoreExact':f'{exact}/{len(ids)}','duplicateRootCheckpointKeys':dup,'invalidLifecycleLabels':invalid},
      'metrics':metrics,'checks':checks,'firstCheckpointOutcomeDistribution':outcomes,'marketRows':market_rows,
      'interpretation':'Independent replication of the development-selected M0 economic-progress semantic. No refit or thresholding; action authority remains false.',
      'guards':pre['guards']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'integrity':rep['integrity'],'metrics':metrics,'checks':checks,'outcomes':outcomes},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
