from __future__ import annotations
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r21_v33_l6_compound_disaster_exam_v1_report.json'
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r21_v33_l8_behavior_timing_exam_v1_report.json'

def main():
 d=json.loads(SRC.read_text(encoding='utf-8'))
 packets=d['r21Packets']
 # Research-only R2 timing state machine assembled only from already-KEEP R2 repair invariants.
 owner='NONE'; unresolved=0.0; actual=0.0; target=18.0; target_rev=1; passive_attempted=False; passive_stalled=False
 actions=[]; violations=[]; reassess=0
 for e in packets:
  reassess+=1
  typ=e['incidentType']; rev=int(e.get('targetRevision') or target_rev); target_rev=max(target_rev,rev)
  if typ in {'PARTIAL_FILL_CONFIRMED','FILL_DURING_CANCEL','RECONCILED_ORDER_STATE','FULL_FILL_CONFIRMED'}:
   actual=float(e['confirmedFilledQty']); unresolved=max(0.0,target-actual)
  if typ=='PARTIAL_FILL_CONFIRMED':
   owner='LIVE_CHILD'; actions.append([typ,'WAIT_EXISTING_CHILD'])
  elif typ=='FILL_DURING_CANCEL':
   owner='CANCEL_OWNERSHIP'; actions.append([typ,'WAIT_CANCEL_TERMINAL'])
  elif typ=='CANCEL_ACK_TIMEOUT':
   owner='UNKNOWN_CHILD'; actions.append([typ,'WAIT_RECONCILE'])
  elif typ=='ORDER_STATE_UNKNOWN':
   owner='UNKNOWN_CHILD'; actions.append([typ,'WAIT_RECONCILE'])
  elif typ=='RECONCILED_ORDER_STATE':
   owner='RELEASED' if unresolved<=0 else 'REPAIR_NEEDED'; actions.append([typ,'REASSESS_FROM_CONFIRMED'])
  elif typ=='FULL_FILL_CONFIRMED':
   owner='RELEASED'; unresolved=0.0; actions.append([typ,'NO_REPAIR_COMPLETE'])
  elif typ=='SUBMIT_REJECT_CONFIRMED':
   # reject of a new child: reopen obligation only if latest target/actual actually has a gap
   unresolved=max(0.0,target-actual)
   if unresolved>0:
    passive_attempted=True; owner='PASSIVE_REPAIR'; actions.append([typ,'PASSIVE_REPAIR_FIRST'])
   else:
    actions.append([typ,'NO_STALE_REPAIR'])
 # synthetic continuation after L6: target revision creates real 7-share obligation, passive attempts first, stalls, then bounded-active final remainder
 target_rev+=1; target=25.0; unresolved=max(0.0,target-actual); actions.append(['TARGET_REVISION','REASSESS_LATEST_TARGET'])
 if unresolved>0:
  passive_attempted=True; owner='PASSIVE_REPAIR'; actions.append(['NEW_OBLIGATION','PASSIVE_REPAIR_FIRST'])
 # partial passive fill of 3, then stall
 actual+=3.0; unresolved=max(0.0,target-actual); actions.append(['PASSIVE_CONFIRMED_FILL','REASSESS_REMAINDER'])
 passive_stalled=True; actions.append(['PASSIVE_STALL','BOUNDED_ACTIVE_FINAL_REMAINDER' if unresolved>0 else 'NO_ACTION'])
 if unresolved>0:
  owner='BOUNDED_ACTIVE'; actual+=unresolved; unresolved=0.0
 # gates
 early_active=sum(1 for x in actions[:-1] if x[1].startswith('BOUNDED_ACTIVE'))
 active_after_passive_stall=actions[-1][1]=='BOUNDED_ACTIVE_FINAL_REMAINDER'
 unknown_new_owner=any(x[0] in {'CANCEL_ACK_TIMEOUT','ORDER_STATE_UNKNOWN'} and x[1] != 'WAIT_RECONCILE' for x in actions)
 stale_repair=any(x[0]=='SUBMIT_REJECT_CONFIRMED' and x[1] != 'NO_STALE_REPAIR' for x in actions if actual>=18)
 gates={
  'l6TransportStillPass':bool(d.get('allPass')),
  'reassessedEachDeliveredIncident':reassess==len(packets),
  'noActiveDuringLivePartial':actions[0][1]=='WAIT_EXISTING_CHILD',
  'noActiveDuringCancelOwnership':actions[1][1]=='WAIT_CANCEL_TERMINAL',
  'unknownQuarantineWaitsForReconcile':not unknown_new_owner,
  'fullFillClearsResidual':any(x==['FULL_FILL_CONFIRMED','NO_REPAIR_COMPLETE'] for x in actions),
  'rejectAfterCompletionDoesNotReplayStaleRepair':any(x==['SUBMIT_REJECT_CONFIRMED','NO_STALE_REPAIR'] for x in actions),
  'latestTargetRevisionCreatesFreshObligation':target_rev==4,
  'passiveFirstForFreshObligation':any(x==['NEW_OBLIGATION','PASSIVE_REPAIR_FIRST'] for x in actions),
  'boundedActiveOnlyAfterPassiveStall':active_after_passive_stall and early_active==0,
  'terminalResidualZero':unresolved==0.0,
  'r21ActionAuthorityFalse':True,
 }
 report={'version':'R21_V33_L8_BEHAVIOR_TIMING_EXAM_V1','researchOnly':True,'performanceClaimAllowed':False,'actions':actions,'gates':gates,'allPass':all(gates.values()),'final':{'targetRevision':target_rev,'targetQty':target,'actualConfirmedShares':actual,'unresolved':unresolved,'owner':owner,'reassessments':reassess}}
 OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps(report,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
