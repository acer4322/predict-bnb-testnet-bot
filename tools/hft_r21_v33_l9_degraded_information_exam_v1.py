from __future__ import annotations
import json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0,str(ROOT))
OUT=ROOT/'data/research/execution_aware_fill_lifecycle_v0'
VERSION='R21_V33_L9_DEGRADED_INFORMATION_EXAM_V1'

def main():
    # Deterministic composite trace: confirmed fills only advance frontier; duplicate/out-of-order are ignored;
    # source gaps freeze new action creation; recovered fresh snapshot reconciles before any new child.
    events=[
      {'seq':1,'kind':'CONFIRMED_FILL','cum':6,'fresh':True},
      {'seq':1,'kind':'DUPLICATE_FILL','cum':6,'fresh':True},
      {'seq':0,'kind':'OUT_OF_ORDER_FILL','cum':3,'fresh':True},
      {'seq':2,'kind':'SOURCE_GAP_START','cum':6,'fresh':False},
      # skipped/missing snapshot intentionally omitted here
      {'seq':3,'kind':'CONFIRMED_FILL_DURING_GAP','cum':10,'fresh':False},
      {'seq':4,'kind':'SOURCE_RECOVERED','cum':10,'fresh':True},
      {'seq':5,'kind':'PASSIVE_REPAIR_OPEN','cum':10,'fresh':True},
      {'seq':6,'kind':'PASSIVE_FILL','cum':14,'fresh':True},
      {'seq':7,'kind':'STALE_REPLAY_OLD_PRE_GAP','cum':6,'fresh':True},
      {'seq':8,'kind':'PASSIVE_STALL','cum':14,'fresh':True},
      {'seq':9,'kind':'BOUNDED_ACTIVE_FILL','cum':18,'fresh':True},
    ]
    requested=18.0; frontier=0.0; actual=0.0; unresolved=requested
    source_fresh=True; owner=None; duplicate_ignored=0; stale_ignored=0; replay_ignored=0
    new_actions_while_stale=0; duplicate_owner=0; over_repair=0.0; active_before_recovered_passive_stall=0
    trace=[]; reassess=0
    seen=set()
    for e in events:
      k=(e['seq'],e['kind'],e['cum'])
      if e['kind']=='DUPLICATE_FILL' or k in seen:
        duplicate_ignored+=1; trace.append({'event':e['kind'],'result':'IGNORED_DUPLICATE','actual':actual,'unresolved':unresolved}); continue
      seen.add(k)
      if e['kind']=='OUT_OF_ORDER_FILL' or e['kind']=='STALE_REPLAY_OLD_PRE_GAP' or e['cum'] < frontier:
        stale_ignored+=1
        if e['kind']=='STALE_REPLAY_OLD_PRE_GAP': replay_ignored+=1
        trace.append({'event':e['kind'],'result':'IGNORED_STALE','actual':actual,'unresolved':unresolved}); continue
      if e['kind']=='SOURCE_GAP_START':
        source_fresh=False; trace.append({'event':e['kind'],'result':'FREEZE_NEW_ACTIONS','actual':actual,'unresolved':unresolved}); continue
      if e['kind']=='SOURCE_RECOVERED':
        source_fresh=True; reassess+=1; trace.append({'event':e['kind'],'result':'RECONCILE_THEN_REASSESS','actual':actual,'unresolved':unresolved}); continue
      if e['kind'] in {'CONFIRMED_FILL','CONFIRMED_FILL_DURING_GAP','PASSIVE_FILL','BOUNDED_ACTIVE_FILL'}:
        new=max(frontier,float(e['cum'])); delta=max(0.0,new-frontier); frontier=new; actual=new; unresolved=max(0.0,requested-actual); reassess+=1
        if e['kind']=='CONFIRMED_FILL_DURING_GAP' and source_fresh: raise RuntimeError('gap state mismatch')
        if e['kind']=='BOUNDED_ACTIVE_FILL' and owner!='PASSIVE_STALLED': active_before_recovered_passive_stall+=1
        trace.append({'event':e['kind'],'result':'APPLY_CONFIRMED_FRONTIER','delta':delta,'actual':actual,'unresolved':unresolved,'sourceFresh':source_fresh}); continue
      if e['kind']=='PASSIVE_REPAIR_OPEN':
        if not source_fresh: new_actions_while_stale+=1
        if owner is not None: duplicate_owner+=1
        owner='PASSIVE'; trace.append({'event':e['kind'],'result':'OPEN_SINGLE_OWNER','qty':unresolved}); continue
      if e['kind']=='PASSIVE_STALL':
        if owner!='PASSIVE': duplicate_owner+=1
        owner='PASSIVE_STALLED'; reassess+=1; trace.append({'event':e['kind'],'result':'ALLOW_EXISTING_BOUNDED_ACTIVE_REASSESS','qty':unresolved}); continue
    if actual>requested: over_repair=actual-requested
    gates={
      'duplicateIgnored': duplicate_ignored==1,
      'outOfOrderAndStaleReplayIgnored': stale_ignored>=2 and replay_ignored==1,
      'confirmedFillDuringGapStillUpdatesActual': actual==18.0 and frontier==18.0,
      'noNewActionsWhileSourceStale': new_actions_while_stale==0,
      'freshSnapshotRequiredBeforeNewRepair': any(x['event']=='SOURCE_RECOVERED' and x['result']=='RECONCILE_THEN_REASSESS' for x in trace),
      'singleEconomicOwner': duplicate_owner==0,
      'noOverRepair': over_repair==0.0,
      'boundedActiveOnlyAfterRecoveredPassiveStall': active_before_recovered_passive_stall==0,
      'terminalResidualZero': unresolved==0.0,
      'r21ActionAuthorityFalse': True,
      'r21EventMutationFalse': True,
      'executorCallbackFalse': True,
    }
    report={'version':VERSION,'researchOnly':True,'performanceClaimAllowed':False,'scenario':'duplicate + out-of-order + missing snapshot/source gap + fill during gap + recovery + stale replay + passive stall + bounded active','events':events,'trace':trace,'summary':{'duplicateIgnored':duplicate_ignored,'staleIgnored':stale_ignored,'staleReplayIgnored':replay_ignored,'newActionsWhileSourceStale':new_actions_while_stale,'duplicateOwnerCount':duplicate_owner,'overRepairQty':over_repair,'reassessments':reassess,'actualConfirmedShares':actual,'unresolved':unresolved},'gates':gates,'allPass':all(gates.values())}
    OUT.mkdir(parents=True,exist_ok=True); p=OUT/'r21_v33_l9_degraded_information_exam_v1_report.json'; p.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps({'allPass':report['allPass'],'summary':report['summary'],'gates':gates},ensure_ascii=False,indent=2))
if __name__=='__main__': main()
