from __future__ import annotations
import argparse,json,lzma,joblib,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
PRE=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0b_progress_head_redundancy_late20d_preregistered_v1.json'
SRC=ROOT/'data/hft_forward_paper_v1/markets'
STACK=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_stack_v4.joblib')
TRANS=joblib.load(ROOT/'data/research/r4_v0/hourly/r4_management_transition_belief_v1.joblib')
def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);a=ap.parse_args();pre=json.loads(PRE.read_text(encoding='utf-8'));ids=[int(x) for x in pre['cohort']][a.start:a.start+a.count]
 F=list(STACK['features']['full']);tf=list(TRANS['features']);m0=STACK['M0_model'];tm=TRANS['model'];roots=[];exact=0
 for mid in ids:
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b0=base.simulate(d,pre['policy'],collect_shadow=False);r=sim.simulate(d,pre['policy'],collect_shadow=True,collect_provenance=True);ce=core(b0)==core(r);exact+=int(ce);lr=r.get('managementLifecycleRows') or [];first={}
  for x in lr:
   rid=str(x['checkpointResponsibilityId']);
   if rid not in first or int(x['t'])<int(first[rid]['t']):first[rid]=x
  for rid,x in first.items():
   import numpy as np
   pm=m0.predict_proba(np.asarray([[float(x[f]) for f in F]],float))[0];m0p=float(pm[list(m0.classes_).index(1)]);pt=tm.predict_proba(np.asarray([[float(x[f]) for f in tf]],float))[0];tp=float(pt[list(tm.classes_).index(1)]);roots.append({'marketId':mid,'t':int(x['t']),'responsibilityId':rid,'m0Progress':m0p,'transitionRisk':tp,'invTransition':1-tp,'fixedAvg':.5*m0p+.5*(1-tp),'floorImproved5s':int(x.get('floorImproved5s') or 0),'absNetReduced5s':int(x.get('absNetReduced5s') or 0),'rootCompleted5s':int(x.get('rootCompleted5s') or 0),'rootLifecycleOutcome5s':str(x.get('rootLifecycleOutcome5s'))})
  print(json.dumps({'marketId':mid,'executionExact':ce,'roots':len(first)}),flush=True)
 out=ROOT/f'data/research/r4_v0/p0_provenance_v1/r4_p0b_progress_redundancy_late20d_chunk_{a.start}_{a.count}.json';out.write_text(json.dumps({'ids':ids,'executionExact':exact,'roots':roots},ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'artifact':str(out.relative_to(ROOT)),'markets':len(ids),'executionExact':exact,'roots':len(roots)}))
if __name__=='__main__':main()
