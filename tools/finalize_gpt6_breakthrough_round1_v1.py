"""Compact audit and reusable feature/label-separated candidate capsule."""
import hashlib
import json
from pathlib import Path
import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

root=Path('data/research/r4_v0/p0_provenance_v1')
returns=Path('data/research/lan_worker_returns')
jobs={k:f'gpt6-breakthrough-{k}-20260907-v1' for k in ['geometry-smoke4','geometry-stratified12','intent-smoke4','intent-stratified12']}
data={k:json.loads((returns/v/'result.json').read_text()) for k,v in jobs.items()}
geometry=data['geometry-stratified12'];intent=data['intent-stratified12']
parity=[]
for family in ['geometry','intent']:
    big={r['marketId']:r for r in data[family+'-stratified12']['rows']}
    for r in data[family+'-smoke4']['rows']:
        for b,x in r['branches'].items():
            y=big[r['marketId']]['branches'][b]
            parity.append(x['terminal']==y['terminal'] and x['physicalLabel']==y['physicalLabel'])
baseline_parity=[]
g={r['marketId']:r for r in geometry['rows']}
for r in intent['rows']:
    for b in ['NATIVE','NEXT_REEXPAND','EXPAND_INSIDE']:
        baseline_parity.append(r['branches'][b]['terminal']==g[r['marketId']]['branches'][b]['terminal'])

# Availability-conditioned one-shot readout: unavailable candidates use the actual
# native branch, NOT the unavailable branch's no-op suffix. Not a repeated policy.
vectors={}
for key in ['winnerPnlPosthoc','favoredPayoff','weakPayoff','floor','fills','submits','alternations','buyNotional','gross']:
    control=[];candidate=[]
    for r in intent['rows']:
        native=r['branches']['NATIVE'];trt=r['branches']['EXPAND_INSIDE_INTENT']
        control.append(native['terminal'][key])
        candidate.append((trt if trt['available'] else native)['terminal'][key])
    av=np.array(control);bv=np.array(candidate);dv=bv-av
    vectors[key]={'nativeSum':float(av.sum()),'candidateSum':float(bv.sum()),'deltaSum':float(dv.sum()),
        'improved':int((dv>1e-9).sum()),'worsened':int((dv<-1e-9).sum()),'ties':int((abs(dv)<=1e-9).sum()),
        'worstDelta':float(dv.min()),'deltaQ10':float(np.quantile(dv,.1)),
        'nativeWorst':float(av.min()),'candidateWorst':float(bv.min())}

record=[]
for family,d in [('geometry',geometry),('intent',intent)]:
    for r in d['rows']:
        for b,x in r['branches'].items():
            if not x['available'] or x['preAction'] is None:continue
            feature=x['preAction'];label=x['physicalLabel']
            row={'market_id':r['marketId'],'decision_ms':r['stateSpec']['t'],'experiment':family,'branch':b,
                 'research_stratum':r['stateSpec']['researchStratum'],
                 'candidate_quote_intent':int(b=='EXPAND_INSIDE_INTENT'),
                 'prefix_digest':x['prefixDigest']}
            for k,v in feature.items():
                if isinstance(v,(str,int,float,bool)) or v is None:row['pre_'+k]=v
            for k,v in label.items():
                if k not in feature:row['label_'+k]=v
            for k,v in x['terminal'].items():
                if isinstance(v,(int,float)):row['label_terminal_'+k]=v
            record.append(row)
capsule=root/'GPT6_BREAKTHROUGH_CANDIDATE_CONSEQUENCES_20260907.parquet'
pq.write_table(pa.Table.from_pylist(record),capsule,compression='zstd')
manifest={'rows':len(record),'markets':12,'sourceJobs':jobs,'sha256':hashlib.sha256(capsule.read_bytes()).hexdigest(),
    'featureBoundary':'pre_* and candidate_quote_intent only; pre source/key/t are lineage, not automatic model features. Candidate-side relative debt/progress remain action context.',
    'labelBoundary':'label_* are future physical/economic outcomes only, including winner settlement label',
    'dependence':'Repeated NATIVE and same-class candidates intentionally retained for parity. Deduplicate and split by market before training; never row-random split.',
    'notPresent':['Target teacher input','post-submit self occupancy'],
    'limitations':['Native prefix is preserved by replay, not serialized HFT snapshot restore','No new strict-less-than receipt certification; current receipt semantics inherited','No trained policy or untouched holdout claim']}
capsule.with_suffix('.manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
smoke_mids={r['marketId'] for r in data['intent-smoke4']['rows']}
withheld=[r for r in intent['rows'] if r['marketId'] not in smoke_mids]
held_legal=[r for r in withheld if r['branches']['EXPAND_INSIDE_INTENT']['available']]
out={'allJobCorrectnessPass':all(d['allCorrectnessPass'] for d in data.values()),
     'totalRunsIncludingRepeatedSmoke':sum(d['runs'] for d in data.values()),'uniqueSeams':12,
     'smokeRepeatParityChecks':len(parity),'smokeRepeatParityAll':all(parity),
     'unchangedBranchCrossExperimentParityChecks':len(baseline_parity),'unchangedBranchCrossExperimentParityAll':all(baseline_parity),
     'nativePricePlaceboPass':all(r['checks']['nativePriceIntentPlaceboParity'] and r['checks']['nativePricePlaceboNeverPreserved'] for r in intent['rows']),
     'intentPreservedMarkets':[r['marketId'] for r in intent['rows'] if r['branches']['EXPAND_INSIDE_INTENT']['intentPreserveCount']>0],
     'availabilityConditionedOneShotReadout':vectors,
     'nonSmokeConsumedReplication':{'markets':len(withheld),'legalInsideMarkets':len(held_legal),
         'intentVsInsidePnlDelta':sum(r['branches']['EXPAND_INSIDE_INTENT']['terminal']['winnerPnlPosthoc']-r['branches']['EXPAND_INSIDE']['terminal']['winnerPnlPosthoc'] for r in held_legal)},
     'candidateCapsule':str(capsule),'candidateRows':len(record),
     'verdict':'KEEP quote-intent/lifecycle representation as a measured mechanism; REJECT tested universal price/quantity/WAIT rules as graduation candidates; no 32-64 expansion or closed-loop promotion justified this round',
     'reserveAudit':'Read cohort metadata to resolve two candidate bundle names, including NEW24_20260906_GRADUATION metadata. No tapes, features, HFT, selection or evaluation on that bundle. Only verified consumed 1945xxx and H100 tapes used.',
     'runtimeChanges':False}
(root/'GPT6_BREAKTHROUGH_ROUND1_FINAL_AUDIT_20260907.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
print(json.dumps(out))
