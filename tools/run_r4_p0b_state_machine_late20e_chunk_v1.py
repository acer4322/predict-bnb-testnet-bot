from __future__ import annotations
import argparse,json,lzma,joblib,math,sys
from pathlib import Path
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_state_machine_late20e_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
PROT=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_protection_floor_reserve_stack_v0.joblib')
def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def prefix_active(events,cursor):
 active={};root={};pending_cancel=set()
 for e in events[:int(cursor)]:
  et=str(e.get('event_type') or '');iid=e.get('intent_id');rid=str(e.get('responsibility_id'))
  if iid:root[str(iid)]=rid
  if et=='ACK_NEW' and iid:active[str(iid)]=True
  elif et in {'FULL_FILL','ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:active[str(iid)]=False
  if et=='CANCEL_REQUESTED' and iid:pending_cancel.add(str(iid))
  elif et in {'ACK_CANCELED','FULL_FILL','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:pending_cancel.discard(str(iid))
  if et in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}:
   for x,r in list(root.items()):
    if r==rid:active[x]=False;pending_cancel.discard(x)
 return active,root,pending_cancel
def prob1(model,x):
 p=model.predict_proba(np.asarray([x],float))[0];return float(p[list(model.classes_).index(1)])
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();pre=json.loads(PRE.read_text(encoding='utf-8'));ids=[int(x) for x in pre['cohort']][a.start:a.start+a.count]
 F=list(STACK['features']['full']);tf=list(TRANS['features']);m0=STACK['M0_model'];m1=STACK['M1_model'];classes=list(STACK['classes']);hi=classes.index('HANDOFF_ALLOW');oi=classes.index('OBSERVE_NO_EVENT');tm=TRANS['model'];traces=[];markets=[];exact=0
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b0=base.simulate(d,pre['policy'],collect_shadow=False);r=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True);ce=core(b0)==core(r);exact+=int(ce);evs=r.get('provenanceJournal') or [];lr=r.get('managementLifecycleRows') or [];mf=0
  for x in lr:
   sl=float(x['seconds_left']);phase='FORMATION_CONTEXT_ONLY' if 180<=sl<=300 else 'RESPONSIBILITY_MANAGEMENT' if 60<=sl<180 else 'PROTECTION_PRECEDENCE' if 0<=sl<60 else 'OUTSIDE_ROUTED_WINDOW';rid=str(x['checkpointResponsibilityId']);cursor=int(x['checkpointJournalCursor']);active,root,pending=prefix_active(evs,cursor);root_active=sorted(i for i,v in active.items() if v and root.get(i)==rid);root_pending=sorted(i for i in pending if root.get(i)==rid);state='CANCEL_PENDING' if root_pending else 'LIVE_ACKED' if root_active else 'OPEN_UNOWNED';ep=en=hs=ps=None;pm=None
   if phase=='RESPONSIBILITY_MANAGEMENT':
    vec=[float(x[f]) for f in F];ep=prob1(m0,vec);en=prob1(tm,[float(x[f]) for f in tf]);pp=m1.predict_proba(np.asarray([vec],float))[0];den=float(pp[hi]+pp[oi]);hs=float(pp[hi]/den) if den>1e-12 else .5
   elif phase=='PROTECTION_PRECEDENCE':
    floor=float(x['floor']);pm='RELAPSE_RISK' if floor>=0 else 'CROSS_READINESS';model=PROT['models'][pm];ps=prob1(model,[floor])
   row={'marketId':mid,'checkpointMs':int(x['t']),'responsibilityId':rid,'phase':phase,'executionState':state,'activeIntentIds':root_active,'pendingCancelIntentIds':root_pending,'economicProgressScore':ep,'economicNonprogressRiskScore':en,'serialHandoffScore':hs,'protectionMode':pm,'protectionScore':ps,'rootLinked':rid in set(map(str,x.get('weakResponsibilityIds') or [])),'activeIntentExact':root_active==sorted(map(str,x.get('checkpointActiveIntentIds') or [])),'pendingCancelExact':root_pending==sorted(i for i in map(str,x.get('pendingCancelIntentIds') or []) if root.get(i)==rid),'scoreFinite':all(v is None or math.isfinite(v) for v in [ep,en,hs,ps]),'futureFloorImproved5s':int(x.get('floorImproved5s') or 0),'futureAbsNetReduced5s':int(x.get('absNetReduced5s') or 0),'futureRootOutcome5s':str(x.get('rootLifecycleOutcome5s'))};traces.append(row);mf+=1
  markets.append({'marketId':mid,'executionCoreExact':ce,'traceRows':mf});print(json.dumps(markets[-1]),flush=True)
 out=ROOT/f'data/research/r4_v0/p0_provenance_v1/r4_p0b_state_machine_late20e_chunk_{a.start}_{a.count}.json';out.write_text(json.dumps({'ids':ids,'executionExact':exact,'markets':markets,'traces':traces},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids),'executionExact':exact,'traceRows':len(traces)}))
if __name__=='__main__':main()
