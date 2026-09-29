from pathlib import Path
import json
p=Path('data/research/r4_v0/p0_provenance_v1/r4_management_testbed_status_current_v1.json')
d=json.loads(p.read_text(encoding='utf-8'))
d['date']='2026-08-30'
steps=d.setdefault('latestVerifiedSteps',[])
add=[
"Behavior Ledger V1/V2 now tracks probability movement, wrong->right/right->wrong flips, phase, floor, and ownership context across chronological fresh streams. Cross-stream recurrence found 6 robust improving contexts, 7 robust worsening contexts, and 17 direction reversals.",
"Cross-stream behavior recurrence identifies the strongest stable capability tradeoff: REPAIR-related Purpose contexts repeatedly improve while ADD / State-Shaping contexts repeatedly worsen across both guard and final slices. Formation ADD, Management ADD, and Protection ADD all showed robust worsening at phase level.",
"Role Maker/Taker behavior is not a stable global bias: 17 context-level stream-direction reversals show Maker/Taker movement changes sign across chronology/regime, so global Role pushing is rejected as an adaptive strategy.",
"V6 class-balanced episodic replay did not solve global Purpose interference on consumed fresh80: 3 Purpose / 0 Role / 1 Hazard updates accepted; final Purpose AUC changed +0.000241 but BA fell about -0.01242. Balanced sampling alone is insufficient.",
"User research objective clarified: build our own Target-inspired adaptive cycle system rather than a full Target clone. Target is now auxiliary post-episode teacher/reference; Champion promotion is governed by our capability balance, safety, lifecycle/execution invariants, behavior ledger, and fresh realistic-HFT evidence.",
"R4 Adaptive Cycle System V1 contract frozen. Supervisor V1 consumes cross-stream behavior recurrence and currently marks STATE_SHAPING_OPPORTUNITY as CAPABILITY_DEFICIT, ROLE_MAKER_TAKER as REGIME_OR_CONTEXT_DEPENDENT, REPAIR_DEMAND as REGIME_OR_CONTEXT_DEPENDENT, and TIMING_HAZARD as INSUFFICIENT_CROSS_STREAM_EVIDENCE. Priority recommendation is to decompose binary Purpose into independent Repair Demand and State-Shaping Opportunity specialists."
]
for s in add:
    if s not in steps: steps.append(s)
d['currentConclusion']=(
"The primary research goal is now an autonomous adaptive cycle for our own Target-inspired system, not full Target imitation. "
"Target remains useful as post-episode teacher/reference, but aggregate similarity is not a promotion objective. Two chronological behavior ledgers expose a structural interference problem: global Purpose learning repeatedly strengthens REPAIR while suppressing ADD/State-Shaping, while Role direction reverses across regimes. V6 class-balanced replay did not remove this interference. The next architecture should separate capabilities and let Objective Ledger/lifecycle arbitrate them rather than forcing global binary competition."
)
d['nextHighestInformationStep']=(
"Preregister and test Dual Purpose Specialists V1 inside the adaptive cycle: independent Repair Demand and State-Shaping Opportunity evidence, no direct action authority, strict-past context, Objective Ledger/lifecycle arbitration, behavior ledger after every accepted update, and fresh chronology validation. Build the adaptive supervisor so accepted/rollback/specialize decisions are generated from capability-level recurrence rather than aggregate Target imitation score."
)
d['adaptiveCycle']={
  'contract':'r4_adaptive_cycle_system_v1_contract.json',
  'supervisorState':'r4_adaptive_cycle_supervisor_state_v1.json',
  'objective':'OUR_OWN_TARGET_INSPIRED_ADAPTIVE_SYSTEM',
  'targetRole':'AUXILIARY_POST_EPISODE_TEACHER_REFERENCE',
  'behaviorLedgerRequired':True,
  'actionAuthority':False
}
d['latestResearchUpdate']={
  'id':'R4_ADAPTIVE_CYCLE_SYSTEM_V1_20260830',
  'result':'PRIMARY_DIRECTION_FROZEN_ADAPTIVE_NOT_CLONE',
  'binaryPurposeTradeoffDetected':True,
  'robustImprovingContexts':6,
  'robustWorseningContexts':7,
  'directionReversalContexts':17,
  'stateShapingStatus':'CAPABILITY_DEFICIT',
  'roleStatus':'REGIME_OR_CONTEXT_DEPENDENT',
  'repairStatus':'REGIME_OR_CONTEXT_DEPENDENT',
  'timingStatus':'INSUFFICIENT_CROSS_STREAM_EVIDENCE',
  'actionAuthority':False,
  'contractArtifact':'r4_adaptive_cycle_system_v1_contract.json',
  'supervisorArtifact':'r4_adaptive_cycle_supervisor_state_v1.json',
  'next':'dual-purpose specialists + capability-level adaptive supervisor'
}
p.write_text(json.dumps(d,indent=2,ensure_ascii=False),encoding='utf-8')
print(p)
