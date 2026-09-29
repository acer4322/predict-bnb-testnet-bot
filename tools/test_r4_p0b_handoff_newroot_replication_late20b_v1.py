from __future__ import annotations
import json,lzma,joblib,sys
from pathlib import Path
from collections import defaultdict
import numpy as np
from sklearn.metrics import roc_auc_score,average_precision_score,log_loss
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_handoff_newroot_replication_late20b_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_handoff_newroot_replication_late20b_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
STACK=ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib'

def core(x):
    return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}

def met(y,p):
    y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float);out={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
    if len(y) and len(np.unique(y))>1:
        out['auc']=float(roc_auc_score(y,p));out['ap']=float(average_precision_score(y,p));out['logLoss']=float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))
    else:out.update({'auc':None,'ap':None,'logLoss':None})
    return out

def active_roots_at(events,cutoff):
    active={};root_of={}
    for idx,e in sorted(enumerate(events),key=lambda z:(int(z[1].get('received_at_ms') or 0),z[0])):
        if int(e.get('received_at_ms') or 0)>int(cutoff):break
        et=str(e.get('event_type') or '');iid=e.get('intent_id');rid=str(e.get('responsibility_id'))
        if iid:root_of[str(iid)]=rid
        if et=='ACK_NEW' and iid:active[str(iid)]=True
        elif et in {'FULL_FILL','ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:active[str(iid)]=False
        elif et in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}:
            for x,r in list(root_of.items()):
                if r==rid:active[x]=False
    return {root_of[i] for i,v in active.items() if v and i in root_of}

def main():
    pre=json.loads(PRE.read_text(encoding='utf-8'));ids=[int(x) for x in pre['markets']]
    stack=joblib.load(STACK);m1=stack['M1_model'];classes=list(stack['classes']);feats=list(stack['features']['full']);hi=classes.index('HANDOFF_ALLOW');oi=classes.index('OBSERVE_NO_EVENT')
    episodes=[];exact=0;market=[]
    for mid in ids:
        d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        a=base.simulate(d,pre['policy'],collect_shadow=False);b=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True);ce=core(a)==core(b);exact+=int(ce)
        lr=b.get('managementLifecycleRows') or [];evs=b.get('provenanceJournal') or [];by=defaultdict(list)
        for r in lr:by[str(r['checkpointResponsibilityId'])].append(r)
        nm=0
        for rid,rr in by.items():
            rr=sorted(rr,key=lambda r:int(r['t']));cand=next((r for r in rr if int(r.get('rootResponsibilityPersists5s') or 0)==0),None)
            if cand is None:continue
            t=int(cand['t']);cut=t+5000;other=sorted(x for x in active_roots_at(evs,cut) if x!=rid);new_other=sorted({str(e.get('responsibility_id')) for e in evs if str(e.get('event_type'))=='ACK_NEW' and t<int(e.get('received_at_ms') or 0)<=cut and str(e.get('responsibility_id'))!=rid})
            X=np.asarray([[float(cand[f]) for f in feats]],dtype=float);pp=m1.predict_proba(X)[0];den=float(pp[hi]+pp[oi]);score=float(pp[hi]/den) if den>1e-12 else 0.5
            episodes.append({'marketId':mid,'t':t,'responsibilityId':rid,'handoffConditionalScore':score,'otherRootActiveAt5s':int(bool(other)),'newOtherRootAckWithin5s':int(bool(new_other)),'outcome5s':str(cand.get('rootLifecycleOutcome5s')),'otherActiveRoots':other,'newOtherAckRoots':new_other,'secondsLeft':float(cand.get('seconds_left') or 0.),'absGap':float(cand.get('abs_gap') or 0.),'coverage':float(cand.get('coverage') or 0.)});nm+=1
        market.append({'marketId':mid,'executionCoreExact':ce,'transitionEpisodes':nm});print(json.dumps(market[-1],ensure_ascii=False),flush=True)
    score=[x['handoffConditionalScore'] for x in episodes];metrics={'NEW_OTHER_ROOT_ACK_WITHIN_5S':met([x['newOtherRootAckWithin5s'] for x in episodes],score),'OTHER_ROOT_ACTIVE_AT_5S':met([x['otherRootActiveAt5s'] for x in episodes],score)}
    rule=pre['fixedKeepRule'];p=metrics['NEW_OTHER_ROOT_ACK_WITHIN_5S'];dup=len(episodes)-len({(x['marketId'],x['responsibilityId']) for x in episodes});checks={'minimumEpisodes':len(episodes)>=rule['minimumEpisodes'],'primaryAuc':p.get('auc') is not None and p['auc']>=rule['primaryAucMinimum'],'primaryApAboveRate':p.get('ap') is not None and p['ap']>p['rate'],'executionNoRegression':exact==len(ids),'duplicateEpisodeRoots':dup==0};status='REPLICATION_KEEP' if all(checks.values()) else 'REPLICATION_REJECT'
    rep={'version':'R4_P0B_HANDOFF_NEWROOT_REPLICATION_LATE20B_V1','status':status,'preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'cohort':{'markets':len(ids),'marketsWithEpisodes':len(set(x['marketId'] for x in episodes)),'episodes':len(episodes)},'integrity':{'executionCoreExact':f'{exact}/{len(ids)}','duplicateEpisodeRoots':dup},'metrics':metrics,'checks':checks,'episodes':episodes,'marketRows':market,'guards':pre['guards']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'integrity':rep['integrity'],'metrics':metrics,'checks':checks},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
