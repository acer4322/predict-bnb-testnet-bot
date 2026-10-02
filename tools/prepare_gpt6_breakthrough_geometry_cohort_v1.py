"""Outcome-blind stratification and clean current-baseline case contrast."""
import collections
import json
from pathlib import Path
import numpy as np
import pyarrow.parquet as pq

ROOT=Path('data/research/r4_v0/p0_provenance_v1')
source=ROOT/'MANAGEMENT_MAINLINE_V3B_ROLE_SWITCH_FIRST_ELIGIBLE_H100_V1_20260907.json'
states=json.loads(source.read_text())['states']
groups=collections.defaultdict(list)
progress_medians={c:float(np.median([s['repairProgressFrac'] for s in states if s['nativeClass']==c])) for c in ('REPAIR','EXPAND')}
for s in sorted(states,key=lambda x:(x['marketId'],x['t'])):
    # Research sampling strata only. No state threshold enters the controller.
    category=s['nativeClass']+'_'+('HIGH_PROGRESS' if s['repairProgressFrac']>progress_medians[s['nativeClass']] else 'LOW_PROGRESS')
    groups[category].append(s)
chosen=[]
for category,rows in sorted(groups.items()):
    for s in rows[:3]:
        chosen.append({**s,'researchStratum':category})
chosen.sort(key=lambda x:(x['marketId'],x['t']))
smoke=[]
for cat in sorted(groups):
    smoke.append(next(s for s in chosen if s['researchStratum']==cat))
for label,ss in [('SMOKE',smoke),('STRATIFIED',chosen)]:
    out={'version':'GPT6_BREAKTHROUGH_GEOMETRY_PREREG_V1','states':ss,
         'selection':'First chronological 3 per native role x within-role median pre-action paid-progress stratum; one state per market; consumed H100 only; outcomes unused',
         'samplingProgressMedians':progress_medians,
         'source':str(source),'hypotheses':{
             'H1':'Role-first economics insufficient; joint candidate value is needed',
             'H2':'Venue-legal quantity and payment granularity determine upside cost',
             'H3':'Same-role price geometry and execution path dominate naive role label',
             'H4':'Existing responsibility and concurrent-carrier path alters marginal value',
             'H5_OUTSIDE_FRAMING':'Historical version/cohort mismatch explains A-E patterns; simple features are nontransportable'},
         'primaryContrast':'REPAIR_PAIR_ONLY minus NEXT_REPAIR; same-role venue-minimum notional, native managed versus inherited Pair quote',
         'secondaryContrasts':['REPAIR_DOUBLE minus NEXT_REPAIR','EXPAND_DOUBLE minus NEXT_REEXPAND','EXPAND_INSIDE minus NEXT_REEXPAND'],
         'evaluation':'Candidate availability first; same-prefix/native parity/exact FIFO before effects. Report 5s physical labels, terminal payoff vector, notional, fills, alternations. WAIT reference only. No winner-selected candidate.',
         'expansion':'Smoke correctness then stratified. Only a stable, non-inactivity explanation may expand to 32-64; no promotion from seam oracle.'}
    (ROOT/f'GPT6_BREAKTHROUGH_GEOMETRY_{label}_PREREG_20260907.json').write_text(json.dumps(out,indent=2),encoding='utf-8')

folder=Path('data/research/lan_worker_returns/gpt6-breakthrough-clean-case5-20260907-v1')
cases=[json.loads(line) for line in (folder/'preaction_rows.jsonl').read_text().splitlines()]
report=json.loads((folder/'result.json').read_text())
capsule=pq.read_table('data/research/management_training_v1/our_v3b_preaction_h100_v1/our_decision_features_v1.parquet').to_pandas()
agg=[]
for mid in sorted({r['marketId'] for r in cases}):
    rr=[r for r in cases if r['marketId']==mid]
    repairs=[r for r in rr if r['isRepairRole']]
    expand=[r for r in rr if r['isExpandRole']]
    agg.append({'marketId':mid,'actions':len(rr),'roleCounts':dict(collections.Counter(r['role'] for r in rr)),
        'routeCounts':dict(collections.Counter(r['route'] for r in rr)),
        'medianPreState':{k:float(np.median([r[k] for r in rr])) for k in ['absNet','floor','best','totalDebt','liveSlots','oldestRepairProgress','oldestRepairAgeMs']},
        'repairMeanFill5s':float(np.mean([r['fillQty5s'] for r in repairs])) if repairs else None,
        'expandMeanFill5s':float(np.mean([r['fillQty5s'] for r in expand])) if expand else None,
        'passiveMinNotionalActions':sum(abs(r['price']*r['qty']-1)<1e-8 and r['route']=='PASSIVE' for r in rr),
        'cleanPreactionAll':all(r['candidateAlreadyInState']==0 for r in rr)})
out={'version':'GPT6_BREAKTHROUGH_CLEAN_CASE_CONTRAST_V1','currentV3BCaseRows':agg,
     'baselineParity':report['allBaselinePhysicalParity'],'ledgerPass':report['allLedgerInvariantsPass'],
     'capsuleRows':len(capsule),'capsuleMarkets':int(capsule.market_id.nunique()),
     'caseOverlapWithH100':sorted(set(capsule.market_id)&{r['marketId'] for r in cases}),
     'limitation':'Historical A-E R247/R264 outcome classes are not labels for current V3B states. Small case aggregate is descriptive, not a causal explanation or role-value classifier.',
     'samplerStrataAvailable':{k:len(v) for k,v in groups.items()},'smokeSeams':len(smoke),'stratifiedSeams':len(chosen)}
(ROOT/'GPT6_BREAKTHROUGH_CLEAN_CASE_CONTRAST_20260907.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out))
