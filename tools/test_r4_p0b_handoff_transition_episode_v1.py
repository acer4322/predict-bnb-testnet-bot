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
FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json'
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_handoff_transition_episode_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_handoff_transition_episode_v1.json'
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
    active={}; root_of={}
    # receipt-time order, with original list order as tie breaker
    for i,e in sorted(enumerate(events),key=lambda z:(int(z[1].get('received_at_ms') or 0),i if False else z[0])):
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
    pre=json.loads(PRE.read_text(encoding='utf-8'));fc=json.loads(FIX.read_text(encoding='utf-8'));ids=[int(x) for x in fc['managementHftFresh']['markets']]
    stack=joblib.load(STACK);m1=stack['M1_model'];classes=list(stack['classes']);feats=list(stack['features']['full']); hi=classes.index('HANDOFF_ALLOW');oi=classes.index('OBSERVE_NO_EVENT')
    episodes=[];exact=0;market=[]
    for mid in ids:
        d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'))
        a=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);b=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(a)==core(b);exact+=int(ce)
        lr=b.get('managementLifecycleRows') or [];evs=b.get('provenanceJournal') or []
        by=defaultdict(list)
        for r in lr:by[str(r['checkpointResponsibilityId'])].append(r)
        nm=0
        for rid,rr in by.items():
            rr=sorted(rr,key=lambda r:int(r['t']))
            cand=next((r for r in rr if int(r.get('rootResponsibilityPersists5s') or 0)==0),None)
            if cand is None:continue
            t=int(cand['t']);cut=t+5000; active5=active_roots_at(evs,cut);other=sorted(x for x in active5 if x!=rid)
            new_other=sorted({str(e.get('responsibility_id')) for e in evs if str(e.get('event_type'))=='ACK_NEW' and int(e.get('received_at_ms') or 0)>t and int(e.get('received_at_ms') or 0)<=cut and str(e.get('responsibility_id'))!=rid})
            X=np.asarray([[float(cand[f]) for f in feats]],dtype=float);pp=m1.predict_proba(X)[0];den=float(pp[hi]+pp[oi]);score=float(pp[hi]/den) if den>1e-12 else 0.5
            episodes.append({'marketId':mid,'t':t,'responsibilityId':rid,'outcome5s':str(cand.get('rootLifecycleOutcome5s')),'handoffConditionalScore':score,'otherRootActiveAt5s':int(bool(other)),'newOtherRootAckWithin5s':int(bool(new_other)),'otherActiveRoots':other,'newOtherAckRoots':new_other,'floorDelta5s':float(cand.get('floorDelta5s') or 0.),'absNetDelta5s':float(cand.get('absNetDelta5s') or 0.),'secondsLeft':float(cand.get('seconds_left') or 0.),'absGap':float(cand.get('abs_gap') or 0.),'coverage':float(cand.get('coverage') or 0.)});nm+=1
        market.append({'marketId':mid,'executionCoreExact':ce,'transitionEpisodes':nm});print(json.dumps(market[-1],ensure_ascii=False),flush=True)
    score=[x['handoffConditionalScore'] for x in episodes];metrics={'OTHER_ROOT_ACTIVE_AT_5S':met([x['otherRootActiveAt5s'] for x in episodes],score),'NEW_OTHER_ROOT_ACK_WITHIN_5S':met([x['newOtherRootAckWithin5s'] for x in episodes],score)}
    rep={'version':'R4_P0B_HANDOFF_TRANSITION_EPISODE_V1','status':'DEVELOPMENT_HANDOFF_EPISODE_COMPLETE','preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'cohort':{'markets':len(ids),'marketsWithEpisodes':len(set(x['marketId'] for x in episodes)),'episodes':len(episodes)},'integrity':{'executionCoreExact':f'{exact}/{len(ids)}','duplicateEpisodeRoots':len(episodes)-len({(x['marketId'],x['responsibilityId']) for x in episodes})},'metrics':metrics,'episodes':episodes,'marketRows':market,'interpretation':'Conditional M1 HANDOFF mass evaluated only at durable root transition episodes; same-root reinsert is not handoff. Development only; no action authority.','guards':pre['guards']}
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':rep['status'],'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'integrity':rep['integrity'],'metrics':metrics},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
