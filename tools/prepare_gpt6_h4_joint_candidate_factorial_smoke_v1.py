"""Prepare outcome-blind consumed H100 2x2 seam cohort for H4 joint-candidate factorial smoke.

Selection is research-only and never becomes runtime authority.
- Excludes all Round-1 breakthrough seams.
- Requires >=2 free structural slots, no pending Active, and both frozen Repair/Expand candidates.
- Inventory direction = pre-branch dominant/expandSide.
- Market pricing alignment = strict-past midpoint of expandSide > 0.50 (binary complement makes this equivalent to market pricing that side above the opposite side).
- Responsibility service stratum uses repairProgressFrac median only as an outcome-blind sampling proxy; it is NOT an exact pending-reservation coverage measure and NOT a trading threshold.
"""
from __future__ import annotations
import json, statistics
from pathlib import Path

ROOT=Path('data/research/r4_v0/p0_provenance_v1')
SRC=ROOT/'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_FIRST_ELIGIBLE_H100_V1_20260907.json'
ROUND1=ROOT/'GPT6_BREAKTHROUGH_GEOMETRY_STRATIFIED_PREREG_20260907.json'
OUT=ROOT/'GPT6_H4_JOINT_CANDIDATE_FACTORIAL_SMOKE4_PREREG_20260907.json'

states=json.loads(SRC.read_text(encoding='utf-8'))['states']
round1_ids={int(x['marketId']) for x in json.loads(ROUND1.read_text(encoding='utf-8'))['states']}
eligible=[]
for s in states:
    if int(s['marketId']) in round1_ids: continue
    if int(s.get('freeSlots') or 0)<2: continue
    if bool(s.get('qPendingActive')): continue
    if not s.get('repairCandidate') or not s.get('expandCandidate'): continue
    if not (float(s['repairCandidate']['price'])>0 and float(s['expandCandidate']['price'])>0): continue
    eligible.append(s)
if not eligible: raise RuntimeError('no eligible states')
med=float(statistics.median(float(s['repairProgressFrac']) for s in eligible))
by={}
for s in sorted(eligible,key=lambda x:(int(x['marketId']),int(x['t']))):
    em=(float(s['book']['expandBid'])+float(s['book']['expandAsk']))/2.0
    aligned=em>0.5
    service_high=float(s['repairProgressFrac'])>=med
    key=('ALIGNED' if aligned else 'MISALIGNED')+'_'+('HIGH_SERVICE_PROGRESS' if service_high else 'LOW_SERVICE_PROGRESS')
    row={**s,
         'h4ResearchStratum':key,
         'inventoryDirection':str(s['expandSide']),
         'marketPricingDirection':str(s['expandSide'] if aligned else s['weakSide']),
         'expandSideMid':em,
         'inventoryMarketAligned':bool(aligned),
         'serviceProgressHigh':bool(service_high),
         'serviceProgressMedianEligible':med,
         'selectionCoverageProxy':'repairProgressFrac only; exact pending reservation coverage is not asserted'}
    by.setdefault(key,[]).append(row)
required=['ALIGNED_LOW_SERVICE_PROGRESS','ALIGNED_HIGH_SERVICE_PROGRESS','MISALIGNED_LOW_SERVICE_PROGRESS','MISALIGNED_HIGH_SERVICE_PROGRESS']
missing=[k for k in required if not by.get(k)]
if missing: raise RuntimeError(f'missing required strata: {missing}')
chosen=[by[k][0] for k in required]
chosen.sort(key=lambda x:(int(x['marketId']),int(x['t'])))
out={
 'version':'GPT6_H4_JOINT_CANDIDATE_FACTORIAL_SMOKE4_PREREG_V1_20260907',
 'researchOnly':True,'runtimeAuthority':False,
 'hypothesis':'H4: the relevant decision unit may be a resource-coordinated candidate set; joint Repair+Expand value may be non-additive relative to single actions.',
 'states':chosen,
 'selection':{
   'source':str(SRC),'round1ExcludedMarkets':sorted(round1_ids),'eligiblePool':len(eligible),
   'required':'>=2 free slots, no pending Active, frozen Repair and Expand candidates present, consumed H100 only',
   'outcomeBlind':True,'winnerUsed':False,'futureFillUsed':False,'futurePnlUsed':False,
   'inventoryMarketAlignment':'expandSide strict-past midpoint > 0.50 => aligned; otherwise misaligned',
   'serviceProgressSampling':'within eligible pool repairProgressFrac median split; sampling proxy only, not exact pending-reservation coverage and not runtime rule',
   'serviceProgressMedian':med,
   'strataCounts':{k:len(v) for k,v in sorted(by.items())},
 },
 'branches':['NATIVE','ZERO','A_REPAIR','B_EXPAND','AB_REPAIR_THEN_EXPAND','BA_EXPAND_THEN_REPAIR','AA_REPAIR_FANOUT','BB_EXPAND_FANOUT'],
 'candidateContract':{
   'A':'frozen strict-past current-V3B legal Repair candidate at prefix',
   'B':'frozen strict-past current-V3B legal Expand candidate at prefix',
   'AB_BA':'same frozen candidates, same decision timestamp; order is explicitly represented because runtime has no proven atomic dual-submit primitive',
   'AA_BB':'two same-side venue-minimum passive carriers; second carrier uses nearest less-aggressive distinct Pair-legal price when available; two slots and approximately two 1-USDT notionals match joint branch resource class',
 },
 'primaryEstimand':'J(Y)=Y(A+B)-Y(A)-Y(B)+Y(ZERO), computed separately for AB and BA. Vector Y includes fixed UP/DOWN payoff, midpoint, inventory-oriented skew, Floor/Best, fills, notional, gross and unmatched exposure.',
 'pathDecomposition':'post-prefix confirmed fills classified as intervention keys, prefix-existing keys, or later suffix keys; each fill contributes exact UP/DOWN payoff vector from side/qty/execution price. This is accounting decomposition, not a complete causal mediation proof.',
 'gates':['all branch prefix digests identical','native physical parity','all frozen A/B submissions legal/exercised where required','AB and BA both submit two candidates','AA/BB resource controls submit two legal distinct same-side carriers','exact-FIFO ledger clean','maxSlots<=4','no dream fill','consumed only','no 8781'],
 'interpretation':['Smoke tests existence and mechanism of non-additivity, not 70% graduation breadth.','Do not promote from 4 seams.','If H4 is near-additive/resource-explained, downgrade H4 and return to H1/H2; if interaction exists but suffix reverses it, elevate H3 continuation-response research.']
}
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'output':str(OUT),'markets':[x['marketId'] for x in chosen],'strata':[x['h4ResearchStratum'] for x in chosen],'median':med},ensure_ascii=False))
