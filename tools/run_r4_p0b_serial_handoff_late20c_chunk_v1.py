from __future__ import annotations
import argparse,json,lzma,joblib,sys
from pathlib import Path
from collections import defaultdict
import numpy as np
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
from tools.test_r4_p0b_handoff_ordering_semantic_v1 import ordering_labels,core
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_serial_handoff_replication_late20c_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
SPEC=joblib.load(ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_target_handoff_specialist_v1.joblib')
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args()
 pre=json.loads(PRE.read_text(encoding='utf-8'));ids=[int(x) for x in pre['cohort']][a.start:a.start+a.count]
 old=STACK['M1_model'];classes=list(STACK['classes']);full=list(STACK['features']['full']);hi=classes.index('HANDOFF_ALLOW');oi=classes.index('OBSERVE_NO_EVENT');sm=SPEC['model'];sf=list(SPEC['features']);episodes=[];exact=0
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));ba=base.simulate(d,pre['policy'],collect_shadow=False);b=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True);exact+=int(core(ba)==core(b));lr=b.get('managementLifecycleRows') or [];evs=b.get('provenanceJournal') or [];by=defaultdict(list)
  for r in lr:by[str(r['checkpointResponsibilityId'])].append(r)
  ne=0
  for rid,rr in by.items():
   rr=sorted(rr,key=lambda r:int(r['t']));cand=next((r for r in rr if int(r.get('rootResponsibilityPersists5s') or 0)==0),None)
   if cand is None:continue
   t=int(cand['t']);lab=ordering_labels(evs,rid,t,t+5000);xo=np.asarray([[float(cand[f]) for f in full]],float);po=old.predict_proba(xo)[0];den=float(po[hi]+po[oi]);os=float(po[hi]/den) if den>1e-12 else .5;xs=np.asarray([[float(cand[f]) for f in sf]],float);ss=float(sm.predict_proba(xs)[0,list(sm.classes_).index(1)])
   episodes.append({'marketId':mid,'t':t,'responsibilityId':rid,'departureMs':lab['departureMs'],'serialHandoff5s':lab['serial'],'overlapNewRootAck5s':lab['overlap'],'frozenM1ConditionalScore':os,'specialistScore':ss});ne+=1
  print(json.dumps({'marketId':mid,'episodes':ne,'executionExact':core(ba)==core(b)}),flush=True)
 out=ROOT/f'data/research/r4_v0/p0_provenance_v1/r4_p0b_serial_handoff_late20c_chunk_{a.start}_{a.count}.json';out.write_text(json.dumps({'ids':ids,'executionExact':exact,'episodes':episodes},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids),'executionExact':exact,'episodes':len(episodes)}))
if __name__=='__main__':main()
