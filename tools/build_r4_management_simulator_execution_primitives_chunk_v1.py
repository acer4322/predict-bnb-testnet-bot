from __future__ import annotations
import argparse,json,lzma,sys,psutil
from pathlib import Path
from collections import defaultdict
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_lifecycle_checkpoint_simulator_v1 as sim
from tools import test_r4_rolling_queue_option_lifecycle_shadow_v1 as base
P=ROOT/'data/research/r4_v0/p0_provenance_v1';FIX=ROOT/'data/research/r4_v0/p0_prep_v1/r4_p0_fixed_cohorts_v1.json';SRC=ROOT/'data/hft_forward_paper_v1/markets'
def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--cohort',required=True);ap.add_argument('--start',type=int,required=True);ap.add_argument('--count',type=int,required=True);ap.add_argument('--out',required=True);a=ap.parse_args();fc=json.loads(FIX.read_text(encoding='utf-8'));ids=[int(x) for x in fc[a.cohort]['markets']][a.start:a.start+a.count]
 outrows=[];mex=[]
 for mid in ids:
  vm=psutil.virtual_memory();cpu=psutil.cpu_percent(.05)
  if cpu>=75 or vm.percent>=86:raise SystemExit(f'RESOURCE_GUARD cpu={cpu} ram={vm.percent}')
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b=base.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(b)==core(r);mex.append({'marketId':mid,'executionCoreExact':ce})
  first={}
  for x in r.get('managementLifecycleRows') or []:
   k=str(x['checkpointResponsibilityId'])
   if k not in first or int(x['t'])<int(first[k]['t']):first[k]=x
  journal=r.get('provenanceJournal') or []
  for rid,x in first.items():
   t0=int(x['t']);cut=t0+5000;fut=[e for e in journal if str(e.get('responsibility_id'))==rid and t0<int(e.get('received_at_ms') or 0)<=cut]
   fills=[e for e in fut if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}];qty=sum(float((e.get('extras') or {}).get('fillDeltaQty') or 0) for e in fills);ttf=min([int(e['received_at_ms'])-t0 for e in fills],default=None)
   outrows.append({'marketId':mid,'responsibilityId':rid,'t':t0,'secondsLeft':float(x.get('seconds_left') or 0),'requestedQty':float(x.get('checkpointRequestedQty') or 0),'price':float(x.get('requested_px') or 0),'side':str(x.get('checkpointSide') or ''),'ownerCount':int(x.get('checkpointOwnerCount') or 0),'weakResponsibilityCount':float(x.get('weakResponsibilityCount') or 0),'dominantResponsibilityCount':float(x.get('dominantResponsibilityCount') or 0),'events15s':float(x.get('events_15s') or 0),'transitions15s':float(x.get('transitions_15s') or 0),'oldestOwnerAge':float(x.get('checkpointOldestOwnerAgeS') or 0),'anyFill5s':int(qty>1e-9),'completed5s':int(any(e.get('event_type')=='RESPONSIBILITY_COMPLETED' for e in fut)),'cancelRequest5s':int(any(e.get('event_type')=='CANCEL_REQUESTED' for e in fut)),'fillQty5s':qty,'timeToFirstFillMs':ttf})
  print(json.dumps({'marketId':mid,'exact':ce,'roots':len(first)}),flush=True)
 rep={'cohort':a.cohort,'start':a.start,'count':len(ids),'markets':mex,'rows':outrows};path=ROOT/a.out;path.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps({'artifact':a.out,'rows':len(outrows),'exact':sum(x['executionCoreExact'] for x in mex)},indent=2))
if __name__=='__main__':main()
