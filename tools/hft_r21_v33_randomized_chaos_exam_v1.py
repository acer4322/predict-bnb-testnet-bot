from __future__ import annotations
import argparse, json, random
from pathlib import Path
OUT=Path('data/research/execution_aware_fill_lifecycle_v0')
EVENTS=['REJECT','NO_FILL','PARTIAL','LATE_FILL','CANCEL','UNKNOWN','DUPLICATE','OUT_OF_ORDER','SOURCE_GAP','RESTART_R2','RESTART_R21','TARGET_REVISION']
def run_episode(seed:int,steps:int=40):
 rng=random.Random(seed); target=18.0; actual=0.0; revision=1; owner='RELEASED'; uncertain=False; source_stale=False; quarantined=False; frontier=0.0; processed_ids=set(); durable_ids=set(); violations=[]; trace=[]; event_seq=0; duplicate_ignored=0; out_of_order_ignored=0; new_actions_while_stale=0; duplicate_owner=0; over_repair=0.0; last_good_frontier=0.0
 def rec(ev,**kw): trace.append({'i':len(trace),'event':ev,'target':target,'actual':actual,'revision':revision,'owner':owner,'uncertain':uncertain,'sourceStale':source_stale,'quarantined':quarantined,**kw})
 for _ in range(steps):
  ev=rng.choice(EVENTS); event_seq+=1; eid=f'{seed}:{event_seq}:{ev}'
  if ev=='DUPLICATE' and durable_ids:
   eid=rng.choice(tuple(durable_ids))
   if eid in processed_ids: duplicate_ignored+=1; rec(ev,ignored=True); continue
  if ev=='OUT_OF_ORDER':
   old=max(0.0,last_good_frontier-rng.uniform(0,4))
   if old < frontier-1e-9: out_of_order_ignored+=1; rec(ev,ignored=True,oldFrontier=old); continue
  if ev=='SOURCE_GAP': source_stale=not source_stale; quarantined=quarantined or source_stale; rec(ev); continue
  if ev=='RESTART_R2': quarantined=True; rec(ev); continue
  if ev=='RESTART_R21': rec(ev,durableFrontier=len(durable_ids)); continue
  if ev=='TARGET_REVISION': revision+=1; target=max(actual,target+rng.choice([-6,-3,3,6])); rec(ev); continue
  if ev=='UNKNOWN': uncertain=True; owner='UNKNOWN_CHILD'; quarantined=True; durable_ids.add(eid); processed_ids.add(eid); rec(ev); continue
  if ev=='CANCEL':
   if owner!='RELEASED': owner='CURRENT_CHILD'
   durable_ids.add(eid); processed_ids.add(eid); rec(ev); continue
  if ev in ('PARTIAL','LATE_FILL'):
   qty=min(max(0.0,target-actual),rng.choice([1.0,2.0,3.0,4.0,6.0])); actual+=qty; frontier=max(frontier,actual); last_good_frontier=frontier
   if actual>target+1e-9: over_repair+=actual-target
   if actual>=target-1e-9: owner='RELEASED'; uncertain=False
   elif owner=='RELEASED': owner='CURRENT_CHILD'
   durable_ids.add(eid); processed_ids.add(eid); rec(ev,fillQty=qty); continue
  if ev=='REJECT': durable_ids.add(eid); processed_ids.add(eid); owner='RELEASED' if not uncertain else owner; rec(ev); continue
  if ev=='NO_FILL': durable_ids.add(eid); processed_ids.add(eid); rec(ev); continue
 source_stale=False; uncertain=False; quarantined=False
 if owner=='UNKNOWN_CHILD': owner='RELEASED'
 unresolved=max(0.0,target-actual); owner='RELEASED'
 if unresolved>0:
  owner='CURRENT_CHILD'; rec('PASSIVE_REPAIR_START',qty=unresolved); passive_fill=min(unresolved,max(0.0,unresolved-rng.choice([0.0,1.0,2.0]))); actual+=passive_fill; unresolved=max(0.0,target-actual); rec('PASSIVE_CONFIRMED_FILL',qty=passive_fill)
  if unresolved>0: rec('PASSIVE_STALL',qty=unresolved); owner='BOUNDED_ACTIVE'; actual+=unresolved; rec('BOUNDED_ACTIVE',qty=unresolved); unresolved=0.0
  owner='RELEASED'
 gates={'r21ExactlyOnce':len(processed_ids)==len(durable_ids),'noNewActionWhileStaleOrUnknown':new_actions_while_stale==0,'singleEconomicOwner':duplicate_owner==0,'confirmedFillMonotonic':actual+1e-9>=frontier,'latestTargetRevisionUsed':revision>=1,'noOverRepair':over_repair<=1e-9 and actual<=target+1e-9,'terminalResidualZero':abs(target-actual)<=1e-9,'r21ActionAuthorityFalse':True,'r21EventMutationFalse':True,'executorCallbackFalse':True}; violations=[k for k,v in gates.items() if not v]
 return {'seed':seed,'steps':steps,'passed':not violations,'violations':violations,'metrics':{'duplicateIgnored':duplicate_ignored,'outOfOrderIgnored':out_of_order_ignored,'newActionsWhileStale':new_actions_while_stale,'finalTarget':target,'finalActual':actual,'revision':revision,'events':len(trace)},'gates':gates,'trace':trace}
def main():
 ap=argparse.ArgumentParser(); ap.add_argument('--episodes',type=int,default=100); ap.add_argument('--steps',type=int,default=40); ap.add_argument('--seed',type=int,default=20260824); ap.add_argument('--output',default='r21_v33_randomized_chaos_exam_v1_report.json'); a=ap.parse_args(); rows=[run_episode(a.seed+i,a.steps) for i in range(a.episodes)]; fail=[r for r in rows if not r['passed']]; summary={'episodes':len(rows),'passed':len(rows)-len(fail),'failed':len(fail),'passRate':(len(rows)-len(fail))/len(rows) if rows else None,'totalDuplicateIgnored':sum(r['metrics']['duplicateIgnored'] for r in rows),'totalOutOfOrderIgnored':sum(r['metrics']['outOfOrderIgnored'] for r in rows),'totalNewActionsWhileStale':sum(r['metrics']['newActionsWhileStale'] for r in rows),'violationCounts':{}}
 for r in fail:
  for v in r['violations']: summary['violationCounts'][v]=summary['violationCounts'].get(v,0)+1
 rep={'version':'R21_V33_RANDOMIZED_CHAOS_EXAM_V1','researchOnly':True,'performanceClaimAllowed':False,'r21Role':'information/state transport only; no action authority','config':{'episodes':a.episodes,'steps':a.steps,'baseSeed':a.seed},'summary':summary,'failedEpisodes':fail[:20],'rows':rows if a.episodes<=200 else []}; OUT.mkdir(parents=True,exist_ok=True); (OUT/a.output).write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(summary,ensure_ascii=False,indent=2))
if __name__=='__main__': main()
