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
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_serial_handoff_replication_late20c_preregistered_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_serial_handoff_replication_late20c_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
SPEC=joblib.load(ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_handoff_specialist_v1.joblib')

def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def met(y,p):
 y=np.asarray(y,dtype=int);p=np.asarray(p,dtype=float);o={'n':int(len(y)),'rate':float(y.mean()) if len(y) else None}
 if len(y) and len(np.unique(y))>1:o.update({'auc':float(roc_auc_score(y,p)),'ap':float(average_precision_score(y,p)),'logLoss':float(log_loss(y,np.clip(p,1e-6,1-1e-6),labels=[0,1]))})
 else:o.update({'auc':None,'ap':None,'logLoss':None})
 return o

def ordering_labels(events,rid,t,cut):
 # build intent/root maps and active state through t
 evs=sorted(list(enumerate(events)),key=lambda z:(int(z[1].get('received_at_ms') or 0),z[0]))
 active={};root_of={};i=0
 def apply(e):
  et=str(e.get('event_type') or '');iid=e.get('intent_id');rr=str(e.get('responsibility_id'))
  if iid:root_of[str(iid)]=rr
  if et=='ACK_NEW' and iid:active[str(iid)]=True
  elif et in {'FULL_FILL','ACK_CANCELED','SUBMIT_REJECTED','IOC_TERMINAL'} and iid:active[str(iid)]=False
  elif et in {'RESPONSIBILITY_COMPLETED','RESPONSIBILITY_TERMINATED'}:
   for x,r in list(root_of.items()):
    if r==rr:active[x]=False
 for _,e in evs:
  if int(e.get('received_at_ms') or 0)>t:break
  apply(e)
 def root_count():return sum(1 for iid,v in active.items() if v and root_of.get(iid)==rid)
 prior=root_count();departure=None;other_acks=[]
 # group future events by receipt timestamp so same-ms same-root replacement does not create a fake departure
 bytime=defaultdict(list)
 for idx,e in evs:
  tm=int(e.get('received_at_ms') or 0)
  if t<tm<=cut:bytime[tm].append((idx,e))
 for tm in sorted(bytime):
  for _,e in sorted(bytime[tm],key=lambda z:z[0]):
   if str(e.get('event_type'))=='ACK_NEW' and str(e.get('responsibility_id'))!=rid:other_acks.append((tm,str(e.get('responsibility_id'))))
   apply(e)
  now=root_count()
  if departure is None and prior>0 and now==0:departure=tm
  prior=now
 serial=int(departure is not None and any(tm>departure for tm,_ in other_acks))
 overlap=int(departure is not None and any(t<tm<departure for tm,_ in other_acks))
 return {'departureMs':departure,'serial':serial,'overlap':overlap,'otherAckTimes':other_acks}

def main():
 pre=json.loads(PRE.read_text(encoding='utf-8'));ids=[int(x) for x in pre['cohort']];old=STACK['M1_model'];classes=list(STACK['classes']);full=list(STACK['features']['full']);hi=classes.index('HANDOFF_ALLOW');oi=classes.index('OBSERVE_NO_EVENT');sm=SPEC['model'];sf=list(SPEC['features']);episodes=[];exact=0
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));a=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);b=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);exact+=int(core(a)==core(b));lr=b.get('managementLifecycleRows') or [];evs=b.get('provenanceJournal') or [];by=defaultdict(list)
  for r in lr:by[str(r['checkpointResponsibilityId'])].append(r)
  for rid,rr in by.items():
   rr=sorted(rr,key=lambda r:int(r['t']));cand=next((r for r in rr if int(r.get('rootResponsibilityPersists5s') or 0)==0),None)
   if cand is None:continue
   t=int(cand['t']);lab=ordering_labels(evs,rid,t,t+5000);xo=np.asarray([[float(cand[f]) for f in full]],float);po=old.predict_proba(xo)[0];den=float(po[hi]+po[oi]);oldscore=float(po[hi]/den) if den>1e-12 else .5;xs=np.asarray([[float(cand[f]) for f in sf]],float);ss=float(sm.predict_proba(xs)[0,list(sm.classes_).index(1)])
   episodes.append({'marketId':mid,'t':t,'responsibilityId':rid,'departureMs':lab['departureMs'],'serialHandoff5s':lab['serial'],'overlapNewRootAck5s':lab['overlap'],'frozenM1ConditionalScore':oldscore,'specialistScore':ss,'otherAckTimes':lab['otherAckTimes']})
 print(json.dumps({'episodes':len(episodes),'marketsWithEpisodes':len(set(x['marketId'] for x in episodes)),'executionExact':f'{exact}/{len(ids)}'},ensure_ascii=False),flush=True)
 metrics={}
 for label,key in [('SERIAL_NEW_ROOT_HANDOFF_5S','serialHandoff5s'),('OVERLAP_NEW_ROOT_ACK_5S','overlapNewRootAck5s')]:
  y=[x[key] for x in episodes];metrics[label]={'FROZEN_M1_CONDITIONAL':met(y,[x['frozenM1ConditionalScore'] for x in episodes]),'TARGET_SPECIALIST':met(y,[x['specialistScore'] for x in episodes])}
 rep={'version':'R4_P0B_SERIAL_HANDOFF_REPLICATION_LATE20C_V1','status':'REPLICATION_KEEP' if (len(episodes)>=int(pre['keepRule']['minimumEpisodes']) and metrics['SERIAL_NEW_ROOT_HANDOFF_5S']['FROZEN_M1_CONDITIONAL']['auc'] is not None and metrics['SERIAL_NEW_ROOT_HANDOFF_5S']['FROZEN_M1_CONDITIONAL']['auc']>=float(pre['keepRule']['primaryAucAtLeast']) and metrics['SERIAL_NEW_ROOT_HANDOFF_5S']['FROZEN_M1_CONDITIONAL']['ap']>metrics['SERIAL_NEW_ROOT_HANDOFF_5S']['FROZEN_M1_CONDITIONAL']['rate'] and exact==len(ids)) else 'REPLICATION_REJECT','preRegistered':str(PRE.relative_to(ROOT)).replace('\\','/'),'cohort':{'markets':len(ids),'marketsWithEpisodes':len(set(x['marketId'] for x in episodes)),'episodes':len(episodes)},'integrity':{'executionCoreExact':f'{exact}/{len(ids)}','missingDeparture':sum(x['departureMs'] is None for x in episodes)},'metrics':metrics,'episodes':episodes}
 OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':rep['status'],'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'cohort':rep['cohort'],'integrity':rep['integrity'],'metrics':metrics},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
