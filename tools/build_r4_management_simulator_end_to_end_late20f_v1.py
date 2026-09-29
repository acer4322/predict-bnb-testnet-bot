from __future__ import annotations
import json,lzma,sys,time,psutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from tools import test_r4_p0b_pending_submit_reservation_simulator_v1 as sim
P=ROOT/'data/research/r4_v0/p0_provenance_v1';PRE=P/'r4_management_simulator_end_to_end_trajectory_v1_preregistered.json';SRC=ROOT/'data/hft_forward_paper_v1/markets'
OUT=Path(__import__('os').environ.get('BTC5M_LAN_RESULT_DIR',str(P)))/'late20f_end_to_end_observed_v1.json'
def core(x):return {k:v for k,v in x.items() if k not in {'shadowRows','managementShadowRows','managementLifecycleRows','provenanceJournal','provenanceResponsibilityState','provenanceSummary'}}
def main():
 ids=json.loads(PRE.read_text(encoding='utf-8'))['validationCohort']['marketIds'];rep={'version':'R4_MANAGEMENT_SIMULATOR_END_TO_END_LATE20F_OBSERVED_V1','markets':[],'lifecycleRows':[],'executionRows':[],'duplicateExecutionCredit':0,'pendingSubmitReservationBlocks':0}
 for mid in ids:
  vm=psutil.virtual_memory();cpu=psutil.cpu_percent(.3)
  if cpu>=75 or vm.percent>=86:time.sleep(2)
  d=json.load(lzma.open(SRC/f'{mid}_r2_hft_closed_loop_v1.json.xz','rt',encoding='utf-8'));b=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=False,collect_provenance=False);r=sim.simulate(d,'ROLL_KEEP_GAP_OWNER',collect_shadow=True,collect_provenance=True);ce=core(b)==core(r);lr=r.get('managementLifecycleRows') or [];journal=r.get('provenanceJournal') or [];first={}
  for x in lr:
   rid=str(x['checkpointResponsibilityId']);
   if rid not in first or int(x['t'])<int(first[rid]['t']):first[rid]=x
  er=[]
  for rid,x in first.items():
   t0=int(x['t']);fut=[e for e in journal if str(e.get('responsibility_id'))==rid and t0<int(e.get('received_at_ms') or 0)<=t0+30000];fills=[e for e in fut if e.get('event_type') in {'PARTIAL_FILL','FULL_FILL'}]
   er.append({'marketId':mid,'responsibilityId':rid,'t':t0,'requestedQty':float(x.get('checkpointRequestedQty') or 0),'unresolvedQty':float(x.get('checkpointUnresolvedQty') or 0),'confirmedQty':float(x.get('checkpointConfirmedQty') or 0),'price':float(x.get('requested_px') or 0),'side':str(x.get('checkpointSide') or ''),'secondsLeft':float(x.get('seconds_left') or 0),'ownerCount':int(x.get('checkpointOwnerCount') or 0),'weakResponsibilityCount':float(x.get('weakResponsibilityCount') or 0),'dominantResponsibilityCount':float(x.get('dominantResponsibilityCount') or 0),'events15s':float(x.get('events_15s') or 0),'transitions15s':float(x.get('transitions_15s') or 0),'oldestOwnerAge':float(x.get('checkpointOldestOwnerAgeS') or 0),'fills30s':[{'dtMs':int(e['received_at_ms'])-t0,'qty':float((e.get('extras') or {}).get('fillDeltaQty') or 0),'eventType':e.get('event_type'),'intentId':e.get('intent_id')} for e in fills],'completedDtMs':min([int(e['received_at_ms'])-t0 for e in fut if e.get('event_type')=='RESPONSIBILITY_COMPLETED'],default=None),'cancelDtMs':min([int(e['received_at_ms'])-t0 for e in fut if e.get('event_type')=='CANCEL_REQUESTED'],default=None)})
  rep['markets'].append({'marketId':mid,'executionCoreExact':ce,'lifecycleRows':len(lr),'roots':len(er)});rep['lifecycleRows'].extend(lr);rep['executionRows'].extend(er);rep['duplicateExecutionCredit']+=int((r.get('provenanceSummary') or {}).get('duplicateExecutionApplicationCount') or 0);rep['pendingSubmitReservationBlocks']+=int((r.get('counts') or {}).get('pendingSubmitReservationBlocks') or 0);OUT.parent.mkdir(parents=True,exist_ok=True);OUT.write_text(json.dumps(rep),encoding='utf-8');print(json.dumps(rep['markets'][-1]),flush=True)
 print(json.dumps({'status':'COMPLETE','markets':len(rep['markets']),'lifecycleRows':len(rep['lifecycleRows']),'roots':len(rep['executionRows']),'exact':sum(x['executionCoreExact'] for x in rep['markets']),'duplicateExecutionCredit':rep['duplicateExecutionCredit'],'pendingBlocks':rep['pendingSubmitReservationBlocks']}))
if __name__=='__main__':main()
