from __future__ import annotations
import glob,json,statistics,sys
from collections import Counter
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
T=P/'r4_p0b_objective_counterfactual_audit_candidate_table_v2.json'
EPS=1e-9

def med(xs):
 xs=sorted(float(x) for x in xs);return statistics.median(xs) if xs else None

def compact(r):
 maps=r['creditAudit']['mappings']
 return {
  'marketId':r['marketId'],'candidateKey':r['candidateKey'],'knownRole':r['knownRole'],'candidateSide':r['candidateSide'],'parentRoot':r['parentRoot'],
  'candidateDeficit':r['candidateDeficit'],'reservedQty':r['candidateStrictPast']['reservedQty'],'ackedCommitment':r['candidateStrictPast']['ackedCommitment'],'pendingSubmitCommitment':r['candidateStrictPast']['pendingSubmitCommitment'],'cancelPendingCommitment':r['candidateStrictPast']['cancelPendingCommitment'],'currentResidualAfterReservation':r['candidateStrictPast']['currentResidualAfterReservation'],
  'futureSameObjectiveNewDemandQty':r['reservationObjectiveGroundTruth']['futureSameObjectiveNewDemandQty'],'futureOppositeSideNewDemandQty':r['reservationObjectiveGroundTruth']['futureOppositeSideNewDemandQty'],'successorRealizedQty':r['successorRealizedQty'],'mechanicallySuppressedFutureQty':r['creditAudit'].get('mechanicallySuppressedFutureQty',0.0),'economicallyValidatedCreditQty':r['creditAudit']['creditableFutureQty'],'nonCreditableParallelQty':r['creditAudit']['nonCreditableParallelQty'],
  'firstObjectiveBoundaryT':r['reservationObjectiveGroundTruth']['firstObjectiveBoundaryT'],'successorFirstFillMs':r['successorFirstFillMs'],'routing':r['causalDivergence']['additiveVsReservationRouting'],'creditMappings':maps,'finalBranches':r['finalBranches'],'explanation':r['finalRoleExplanation']
 }

def main():
 d=json.loads(T.read_text(encoding='utf-8'));rows=d['rows']
 amb=[]
 for f in sorted(P.glob('r4_p0b_objective_counterfactual_audit_ambiguous_chunk_*_v1.json')):amb.extend(json.loads(f.read_text(encoding='utf-8')).get('rows',[]))
 role_stats={}
 for role in ('PREPOSITION_REPAIR_SUBSTITUTE','PARALLEL_STATE_SHAPING','REJECT_NO_ACTION'):
  rr=[r for r in rows if r['knownRole']==role];maps=[m for r in rr for m in r['creditAudit']['mappings']]
  role_stats[role]={
   'n':len(rr),'candidateGapValues':[r['candidateDeficit'] for r in rr],'medianCandidateDeficit':med([r['candidateDeficit'] for r in rr]),'medianReservedQty':med([r['candidateStrictPast']['reservedQty'] for r in rr]),'medianResidualAfterReservation':med([r['candidateStrictPast']['currentResidualAfterReservation'] for r in rr]),
   'ackedAtCandidateCount':sum(r['candidateStrictPast']['ackedCommitment']>EPS for r in rr),'pendingSubmitAtCandidateCount':sum(r['candidateStrictPast']['pendingSubmitCommitment']>EPS for r in rr),'cancelPendingAtCandidateCount':sum(r['candidateStrictPast']['cancelPendingCommitment']>EPS for r in rr),
   'mechanicalSuppressionPositiveCount':sum(float(r['creditAudit'].get('mechanicallySuppressedFutureQty',0))>EPS for r in rr),'mechanicalSuppressionFullSuccessorCount':sum(float(r['creditAudit'].get('mechanicallySuppressedFutureQty',0))>=float(r['successorRealizedQty'])-EPS for r in rr),'validatedCreditFullSuccessorCount':sum(float(r['creditAudit']['creditableFutureQty'])>=float(r['successorRealizedQty'])-EPS for r in rr),
   'currentPlusFutureSameObjectiveDemandCoversCurrentGapCount':sum(float(r['candidateStrictPast']['reservedQty'])+float(r['reservationObjectiveGroundTruth']['futureSameObjectiveNewDemandQty'])>=float(r['candidateDeficit'])-EPS for r in rr),
   'routingChangedCount':sum(bool(r['causalDivergence']['additiveVsReservationRouting']['routingChanged']) for r in rr),'reservationObjectiveBoundaryBeforeSuccessorFillCount':sum(bool(r['reservationObjectiveGroundTruth']['boundaryBeforeSuccessorFill']) for r in rr),
   'creditTargetLifecycleClasses':dict(Counter(str(m.get('targetLifecycleClass')) for m in maps)),'creditTargetWeakImmediatelyBeforeAdditiveFillCount':sum((m.get('additiveStateImmediatelyBeforeTargetFill') or {}).get('weakSide')==r['candidateSide'] for r in rr for m in r['creditAudit']['mappings']),'creditTargetMappingCount':len(maps)
  }
 reps={
  'PREPOSITION_REPAIR_SUBSTITUTE':[compact(next(r for r in rows if r['marketId']==mid)) for mid in (1690492,1704187,1654455)],
  'PARALLEL_STATE_SHAPING':[compact(next(r for r in rows if r['marketId']==mid)) for mid in (1711210,1676462,1741448)],
  'REJECT_NO_ACTION':[compact(next(r for r in rows if r['marketId']==mid)) for mid in (1695801,1706952,1699904)]
 }
 ambiguous={'n':len(amb),'mechanicalSuppressionPositiveCount':sum(float(r['creditAudit'].get('mechanicallySuppressedFutureQty',0))>EPS for r in amb),'routingChangedCount':sum(bool(r['causalDivergence']['additiveVsReservationRouting']['routingChanged']) for r in amb),'residualAfterReservationValues':[r['candidateStrictPast']['currentResidualAfterReservation'] for r in amb]}
 ledger_fields={
  'identity':['objective_id','objective_episode_id','objective_group_id_or_deficit_group_id','objective_type','objective_side','objective_generation','parent_objective_id','supersedes_objective_id','parallel_to_objective_ids','obligation_slot_id'],
  'economics':['objective_target_qty','gross_deficit_at_objective_open','current_portfolio_gap','portfolio_floor','portfolio_upside','portfolio_abs_net','portfolio_pair_balance','objective_economic_value_or_priority','phase_or_need_at_ms'],
  'completionAccounting':['confirmed_completion_qty','credited_completion_qty','acked_reserved_qty','pending_submit_reserved_qty','cancel_pending_reserved_qty','unresolved_objective_qty','residual_objective_deficit'],
  'creditLineage':['credit_lot_id','credit_source_objective_id','credit_source_responsibility_id','credit_source_intent_id','credit_source_execution_id','credit_target_objective_id','credit_target_obligation_slot_id','credit_target_responsibility_id','credit_target_carrier_generation','credit_qty','credit_remaining_qty','credit_semantic_SUBSTITUTE_or_ADDITIVE'],
  'executionLineage':['responsibility_ids','intent_ids','carrier_kind_OPTION_MAIN_REPRICE','carrier_generation','client_order_id','venue_order_id','lifecycle_state','event_journal_cursor'],
  'topology':['objective_boundary_seq','weak_side_at_last_update','objective_topology_state','bilateral_peer_objective_ids','handoff_from_objective_id','handoff_to_objective_id','opened_at_ms','last_updated_at_ms','terminal_reason']
 }
 report={
  'version':'R4_P0B_OBJECTIVE_COUNTERFACTUAL_AUDIT_SYNTHESIS_V1','researchOnly':True,'candidateCount':len(rows),'ambiguousDiagnosticCount':len(amb),'maxCandidateGapReconstructionError':d.get('maxCandidateGapReconstructionError'),'roleStats':role_stats,'ambiguousDiagnostics':ambiguous,
  'findings':[
   'Mechanical same-side credit is not objective identity: it suppresses future carrier quantity in all 5 PREPOSITION cases, all 13 STATE_SHAPING cases, and 11/13 REJECT cases.',
   'PREPOSITION ground truth is carrier-level substitution, not merely root-level substitution. Across the 5 PREPOSITION cases, credit targets span future roots and already-open roots; one canonical example (1690492) credits a future MAIN continuation after a pre-positioned OPTION was canceled.',
   'STATE_SHAPING is genuinely additive relative to the frozen role ground truth. In 11/13 STATE mappings the candidate side is weak immediately before the generic-credit target would later fill in ADDITIVE, showing that deleting that future carrier frequently removes work that is still economically required after intervening topology changes.',
   'REJECT cannot be reduced to lack of a credit target: 11/13 REJECT cases mechanically suppress future carrier quantity, yet Reservation remains the unique Pareto winner. Early realization itself can damage floor/routing or duplicate a pipeline even when a future carrier can be mechanically removed.',
   'Quantity-only residual arithmetic is therefore necessary but not sufficient. The runtime ledger needs immutable objective/episode/obligation-slot identity plus execution reservation and economic/topology state.'
  ],
  'formulaAssessment':{
   'keep':'Per-objective residual accounting: objective_target_qty - confirmed_completion_qty - reserved_same_objective_qty - durable_valid_credit_qty.',
   'rejectAsComplete':'Using current portfolio gap or same-side future FIFO demand as the objective deficit is insufficient; future pipeline is causal-audit ground truth and cannot be treated as strict-past runtime knowledge.',
   'strictPastRuntimeRule':'A credit lot may reduce an obligation only when source and target share an explicit compatible objective/episode/obligation identity and the target obligation is still unresolved. Same side alone is never sufficient.'
  },
  'objectiveLedgerV1RequiredFields':ledger_fields,'representativeCases':reps,
  'decision':'KEEP','decisionMeaning':'KEEP objective-level ledger/accounting as required Management state representation; REJECT generic same-side/FIFO credit and REJECT a scalar quantity-only formula as complete role semantics. This lane does not promote a role classifier or action threshold.'
 }
 jp=P/'r4_p0b_objective_counterfactual_audit_synthesis_v1.json';jp.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
 # Human-readable representative audit
 lines=['# R4 P0-B Objective Counterfactual Audit — Representative Causal Cases','',f"Decision: **{report['decision']}**",'', '## Core result','', 'Mechanical same-side credit is common in all roles; it is not objective identity. PREPOSITION is a validated substitution of a concrete future obligation/carrier. STATE_SHAPING keeps the successor additive because the future carrier remains a separate needed obligation or belongs to changed topology. REJECT may have a mechanically removable future carrier but preposition itself is economically harmful or duplicates an already-funded pipeline.','']
 for role,cases in reps.items():
  lines += [f'## {role}','']
  for c in cases:
   lines += [f"### Market {c['marketId']} — `{c['candidateKey']}`",f"- Candidate: side={c['candidateSide']}, gap={c['candidateDeficit']:.2f}, reserved={c['reservedQty']:.2f}, residual-after-reservation={c['currentResidualAfterReservation']:.2f}.",f"- Future Reservation same-objective new demand={c['futureSameObjectiveNewDemandQty']:.2f}; successor realized={c['successorRealizedQty']:.2f}; mechanical suppression={c['mechanicallySuppressedFutureQty']:.2f}; economically validated credit={c['economicallyValidatedCreditQty']:.2f}.",f"- Routing changed by ADDITIVE={c['routing']['routingChanged']}; new={c['routing']['newLogicals']}; missing={c['routing']['missingLogicals']}; changed={c['routing']['changedLogicals']}."]
   for m in c['creditMappings']:
    st=m.get('additiveStateImmediatelyBeforeTargetFill') or {}
    lines += [f"- Credit target logical={m.get('targetLogical')}, lifecycle={m.get('targetLifecycleClass')}, future carrier kinds={m.get('additiveFutureCarrierKindsAfterCredit')}, suppressed={float(m.get('mechanicallySuppressedFutureCarrierQty') or 0):.2f}, weak-before-target-fill={st.get('weakSide')}, gap-before-target-fill={st.get('gap')}." ]
   lines += [f"- Frozen finals: {json.dumps(c['finalBranches'],ensure_ascii=False)}",'']
 mp=P/'r4_p0b_objective_counterfactual_audit_representative_cases_v1.md';mp.write_text('\n'.join(lines),encoding='utf-8')
 print(json.dumps({'synthesis':str(jp.relative_to(ROOT)),'cases':str(mp.relative_to(ROOT)),'candidateCount':len(rows),'ambiguous':len(amb),'roleStats':role_stats},ensure_ascii=False))
if __name__=='__main__':main()
