from __future__ import annotations
import json
from pathlib import Path

ROOT=Path('.')
contract_p=ROOT/'data/research/r4_v0/r4_hourly_research_cycle_contract_v1.json'
registry_p=ROOT/'data/research/r4_v0/r4_hourly_experiment_registry_v1.json'
handoff_p=ROOT/'data/research/r4_v0/r4_hourly_research_handoff_v1.md'
guard_p=ROOT/'data/research/RESEARCH_EXECUTION_PIPELINE_GUARD_V3.md'

contract=json.loads(contract_p.read_text(encoding='utf-8'))
contract['status']='ENABLED_RESEARCH_ONLY_CONTINUOUS_POST_REPAIR'
contract['objective']='Preserve the now-demonstrated persistent Repair obligation capability while researching Target-like economic payoff amplification: selectively schedule Repair execution, allow beneficial asymmetry, build/preserve an economic safe base, and expand favorable surplus without forgetting or duplicating Repair responsibility.'
contract['currentMainline']={
  'date':'2026-09-01',
  'baseline':'V9 conjunctive parallel router is the frozen near-break-even economic baseline.',
  'repairCapabilityProof':'V10 continuous Repair authority proves required Repair parent birth/proposal can operate independently of the legacy global action gate while preserving carrier/role/ownership invariants.',
  'v9NewMarketEvidence':{
    'markets':172,'marketIdRange':[1823553,1840312],'wins':50,'losses':25,'flats':97,
    'activeWinRate':0.6666666666666666,'totalPnl':0.2784828259045802,'profitFactor':1.006149111247504,
    'maxWin':3.4,'maxLoss':-3.3499999999999996,'maxDrawdown':14.225730310821607,
    'repairToExpandDrift':0,'authorizedTruthMismatch':0,'overOwnedRepair':0,'unresolvedQtyEnd':0.0,
    'expandUnderRepairParent':321,'repairResumeAfterExpand':320
  },
  'v10CapabilityEvidence':{
    'developmentMarkets':24,'fiveDisturbancePass':True,'repairParentBirthsBelowOldActionGatePerScenario':14,
    'repairSubmitsBelowOldActionGatePerScenario':35,'controlMeanAbsNetV9':0.8923656767422514,
    'controlMeanAbsNetV10':0.09921839626707984,'controlAbsNet5MarketsV9':4,'controlAbsNet5MarketsV10':0,
    'economicPromotion':False,
    'reason':'Always-on Repair improves responsibility completion/balance but overpays repair and erases favorable asymmetry; capability and economic scheduling must remain separate.'
  },
  'nextObjective':'POST_REPAIR_ECONOMIC_AMPLIFICATION'
}
new_axes=[
 'POST_REPAIR_ECONOMIC_AMPLIFICATION','SELECTIVE_REPAIR_EXECUTION','ALLOW_ASYMMETRY_WITH_PERSISTENT_REPAIR_OBLIGATION',
 'FORMATION_BUILD_WAIT_CROSS_ROUTING','SURPLUS_EXPANSION_WITH_RESERVE','MARGINAL_PAIR_QUALITY','SAFE_BASE_PRESERVATION',
 'DIRECTIONAL_SURPLUS_RETENTION','REPAIR_PRICE_AND_TIMING_QUALITY'
]
contract['priorityAxes']=new_axes
extra_steps=[
 'Keep Repair parent/ownership truth persistent regardless of the economic execution decision. Never solve PnL by making Repair responsibility disappear.',
 'Research the execution child separately: WAIT, REPRICE, BUILD_WEAK_SIDE, CROSS_PROTECTION, or EXPAND_SURPLUS may be concurrent children under the same parent.',
 'Reuse validated R2 execution lifecycle, R3 ALLOW_ASYMMETRY/BUILD_WEAK_SIDE/CROSSING_PROTECTION semantics, and R4 floor/reserve/MPQ/deliberate-side findings before inventing new primitives.',
 'Use large ETH Target lifecycle data for policy values and BTC only for architecture portability/falsification; do not direct-transfer BTC thresholds or labels.',
 'After a candidate is rejected, record it and continue to the next semantically distinct hypothesis. The research loop must not stop by itself while the main objective remains unresolved.',
 'On 502/transport failure: preserve running-job state, split reads/collects/submissions into bounded batches, retry the failed transport step, and continue the cycle. Never treat a 502 as research termination.'
]
for s in extra_steps:
    if s not in contract['cycleSteps']: contract['cycleSteps'].append(s)
for r in [
 'V9 near-break-even baseline is frozen as the economic reference; do not mutate it when researching amplification.',
 'V10 is a Repair-capability proof, not the economic champion; do not promote always-on Repair as a profitability policy.',
 'Repair obligation identity/ownership and Repair execution timing/value are separate layers.',
 'The hourly research loop may not self-terminate merely because a candidate fails or a transport call returns 502; continue with the next valid step/hypothesis.',
 '502 handling is batch-first: small artifact, bounded read, staged submit, status, collect; no blind rerun of unknown jobs.'
]:
    if r not in contract['hardRules']: contract['hardRules'].append(r)
contract_p.write_text(json.dumps(contract,indent=2,ensure_ascii=False),encoding='utf-8')

registry=json.loads(registry_p.read_text(encoding='utf-8'))
ids={e.get('experimentId') or e.get('cycleId') for e in registry.get('experiments',[])}
entries=[
 {
  'experimentId':'ETH_PASSIVE_REPAIR_V9_NEAR_BREAK_EVEN_BASELINE_20260901','testedAt':'2026-09-01','type':'PERSISTENT_REPAIR_NEAR_BREAK_EVEN_FROZEN_BASELINE','status':'KEEP_SIGNAL_FROZEN_BASELINE',
  'semanticKeys':['persistent Repair parent','lane-scoped ownership','Repair/Expand natural interleave','near break-even realistic HFT','new-market chronology'],
  'candidate':'V9 conjunctive Repair/Expand primitive authority over V8 dual proposals + V7 lane ownership + V4 persistent carrier ledger.',
  'primaryResult':{'newMarkets':172,'firstMarketId':1823553,'lastMarketId':1840312,'wins':50,'losses':25,'flats':97,'activeWinRate':0.6666666666666666,'totalPnl':0.2784828259045802,'profitFactor':1.006149111247504,'maxWin':3.4,'maxLoss':-3.3499999999999996,'maxDrawdown':14.225730310821607,'repairToExpandDrift':0,'authorizedTruthMismatch':0,'overOwnedRepair':0,'unresolvedQtyEnd':0.0,'expandUnderRepairParent':321,'repairResumeAfterExpand':320},
  'conclusion':'Frozen near-break-even economic baseline. Repair ownership/interleave generalizes to new chronology; economics are approximately break-even, not stable-profit graduation.',
  'artifacts':['data/research/r4_v0/p0_provenance_v1/ETH_PASSIVE_REPAIR_V9_FROZEN_CANDIDATE_MANIFEST_20260901.json','data/research/lan_worker_returns/eth-repair-v9-frozen-holdout100-control/result.json','data/research/lan_worker_returns/eth-repair-v9-frozen-confirmatory72-control/result.json'],
  'retestAllowed':False,'nextDistinct':'Separate persistent Repair obligation from economic execution scheduling, then study Target-like favorable asymmetry/surplus amplification.'
 },
 {
  'experimentId':'ETH_PASSIVE_REPAIR_V10_CONTINUOUS_AUTHORITY_PROOF_20260901','testedAt':'2026-09-01','type':'CONTINUOUS_REPAIR_AUTHORITY_CAPABILITY_PROOF','status':'KEEP_SIGNAL_CAPABILITY_ONLY_NOT_ECONOMIC_CHAMPION',
  'semanticKeys':['Repair obligation independent of global action gate','continuous Repair supervision','persistent Repair parent','economic scheduling separation'],
  'candidate':'Move required Repair parent birth/supervision/proposal outside the legacy global action-intent gate; keep optional Expand behind that gate.',
  'primaryResult':{'fiveDisturbanceAllPass':True,'repairParentBirthsBelowOldGatePerScenario':14,'repairSubmitsBelowOldGatePerScenario':35,'controlMeanPairCoverageV9':0.4102647061744317,'controlMeanPairCoverageV10':0.5726496274373821,'controlMeanAbsNetV9':0.8923656767422514,'controlMeanAbsNetV10':0.09921839626707984,'controlAbsNet5MarketsV9':4,'controlAbsNet5MarketsV10':0,'controlPnlV9':-2.8786966406225845,'controlPnlV10':-4.567108071315693},
  'conclusion':'Repair capability is real: previously suppressed parent births/submits are recovered and naked abs-net tails disappear without reintroducing ownership drift. Always-on execution is economically wrong because it over-repairs/overpays and erases profitable asymmetry. Preserve the capability primitive; do not promote the always-on policy.',
  'artifact':'data/research/lan_worker_returns/eth-repair-v10-continuous-repair-full5/result.json','retestAllowed':False,
  'nextDistinct':'Post-Repair economic amplification: persistent obligation + learned/structured WAIT/REPRICE/BUILD/CROSS/EXPAND child scheduling using Target lifecycle, R3 formation semantics and R4 floor/reserve economics.'
 }
]
for e in entries:
    if e['experimentId'] not in ids: registry['experiments'].append(e)
registry['champion']='ETH_PASSIVE_REPAIR_V9_NEAR_BREAK_EVEN_FROZEN_BASELINE_RESEARCH_ONLY'
registry.setdefault('rules',{})['continueAfterRejectedCandidate']=True
registry['rules']['transport502BatchFallback']=True
registry['rules']['repairObligationSeparateFromEconomicExecution']=True
registry_p.write_text(json.dumps(registry,indent=2,ensure_ascii=False),encoding='utf-8')

append='''\n\n## 2026-09-01 — Persistent Repair capability confirmed; next mainline = economic amplification\n\n- **Frozen economic baseline:** V9 conjunctive parallel router. New chronology `marketId 1823553..1840312`, 172 markets: 50 wins / 25 losses / 97 flats, active win rate 66.67%, total PnL +0.2785, PF 1.0061, max win +3.40, max loss -3.35, max drawdown 14.2257. Safety: 0 Repair→Expand first-fill drift, 0 authorized/truth mismatch, 0 over-owned Repair, 0 unresolved qty; 321 natural Expand-under-Repair-parent and 320 Repair resumes. Treat as **near break-even**, not stable-profit graduation.\n- **Repair capability proof:** V10 moved required Repair supervision/parent birth/proposal outside the legacy global action gate. Five disturbance scenarios all pass safety. Each 24-market scenario recovered 14 Repair-parent births and 35 Repair submits that occurred while the old action gate was closed. CONTROL mean abs-net improved 0.892→0.099 and absNet=5 tail markets 4→0.\n- **Do not promote V10 economically:** CONTROL PnL worsened -2.879→-4.567 because always-on Repair overpays and removes beneficial asymmetry (e.g. a V9 +2.60 naked-direction win became -0.35 after forced balancing). This separates **Repair obligation** from **Repair execution economics**.\n- **Current conclusion:** The system has learned/implemented persistent Repair responsibility strongly enough to move on. The next unsolved problem is not whether Repair exists, but how to schedule Repair children economically while preserving the parent.\n- **Next mainline:** `POST_REPAIR_ECONOMIC_AMPLIFICATION`. Reuse R2 execution lifecycle; R3 `ALLOW_ASYMMETRY ↔ BUILD_WEAK_SIDE → CROSSING_PROTECTION`; R4 floor/reserve, MPQ, deliberate-side, durable-base and upside-retention research. Study concurrent child routing: WAIT / REPRICE / BUILD / CROSS / EXPAND under one persistent Repair parent. ETH Target data supplies policy values; BTC supplies architecture-only cross-checks.\n- **Anti-passivity/economic rule:** do not raise win rate by simply reducing participation; track participation, repair completion, payoff asymmetry, loss-tail magnitude, PnL/PF and max drawdown together.\n- **Execution continuity hard rule:** the research cycle must not stop by itself while the main objective remains unresolved. A rejected candidate advances to the next semantically distinct candidate. On 502/transport errors, keep job identity, split submit/status/tail/collect/read into bounded batches, retry the failed transport step, and continue. Never interpret a 502 as research termination.\n'''
text=handoff_p.read_text(encoding='utf-8')
if '## 2026-09-01 — Persistent Repair capability confirmed; next mainline = economic amplification' not in text:
    handoff_p.write_text(text+append,encoding='utf-8')

if guard_p.exists():
    g=guard_p.read_text(encoding='utf-8')
    sec='''\n\n## 2026-09-01 Continuous-cycle / 502 batch fallback amendment\n\n- A research mainline with an unresolved objective must not self-terminate after a rejected/inconclusive experiment. Record it, select the next semantically distinct preregistered hypothesis, and continue.\n- HTTP/MCP/tunnel 502 is a transport failure, not a research result and not a stop condition. Do not blind-rerun an unknown job. First recover job state; if RUNNING, continue status/tail monitoring; if TERMINAL, collect immediately.\n- After a 502, switch to bounded operations: small artifact writes, staged submit, short status/tail, bounded `read_file_chunk`, and separate collect/read. Split large jobs/artifacts into batches when practical.\n- PREPARED/COMPILED must proceed to stage+submit; TERMINAL must proceed to collect+read+report. Do not leave a workflow idle between these states.\n'''
    if '## 2026-09-01 Continuous-cycle / 502 batch fallback amendment' not in g:
        guard_p.write_text(g+sec,encoding='utf-8')
print(json.dumps({'ok':True,'contract':str(contract_p),'registry':str(registry_p),'handoff':str(handoff_p),'guard':str(guard_p)},ensure_ascii=False,indent=2))
