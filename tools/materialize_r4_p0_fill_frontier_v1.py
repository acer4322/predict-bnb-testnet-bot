from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_provenance_instrumented_v1.json'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/r4_p0_fill_frontier_v1.json'
HORIZONS=(1000,3000,5000)
EPS=1e-9


def fill_deltas(events: list[dict[str,Any]], rid: str) -> list[dict[str,Any]]:
    xs=[e for e in events if str(e.get('responsibility_id'))==rid]
    xs=sorted(xs,key=lambda e:int(e.get('event_seq') or 0))
    prior=0.0; out=[]
    for e in xs:
        if e.get('event_type') not in {'PARTIAL_FILL','FULL_FILL'}:
            continue
        cum=float(e.get('cum_confirmed_fill_qty') or 0.0)
        d=max(0.0,cum-prior)
        prior=max(prior,cum)
        out.append({'intentId':str(e.get('intent_id')),'eventAtMs':int(e.get('event_at_ms') or 0),'receivedAtMs':int(e.get('received_at_ms') or 0),'shares':d,'executionId':e.get('execution_id')})
    return out


def main() -> None:
    d=json.loads(SRC.read_text(encoding='utf-8'))
    rows=[]; missing=0; monotonic_fail=0; nonzero5=0; positive_any=0
    for r in d.get('rows',[]):
        mid=int(r['marketId'])
        baseline=r.get('keepProvenanceJournal') or []
        stress_events=r.get('provenanceStressEvents') or []
        for se in stress_events:
            rid=str(se.get('responsibilityId')); iid=str(se.get('oldIntentId')); cp=int(se.get('cancelRequestedAtMs') or 0); side=str(se.get('side'))
            carriers=[e for e in baseline if str(e.get('responsibility_id'))==rid and e.get('event_type')=='CARRIER_INTENT_CREATED' and str(e.get('intent_id'))==iid]
            if len(carriers)!=1:
                missing += 1
                rows.append({'marketId':mid,'side':side,'checkpointMs':cp,'responsibilityId':rid,'intentId':iid,'materialized':False,'reason':f'carrier_count_{len(carriers)}'})
                continue
            carrier=carriers[0]
            requested=float(carrier.get('requested_qty') or 0.0)
            deltas=fill_deltas(baseline,rid)
            confirmed_before=sum(x['shares'] for x in deltas if x['intentId']==iid and x['receivedAtMs']<=cp)
            remaining=max(0.0,requested-confirmed_before)
            fut={}
            for h in HORIZONS:
                fut[h]=sum(x['shares'] for x in deltas if x['intentId']==iid and cp < x['receivedAtMs'] <= cp+h)
            mono=(fut[1000] <= fut[3000]+EPS and fut[3000] <= fut[5000]+EPS and fut[5000] <= remaining+EPS)
            if not mono: monotonic_fail += 1
            if fut[5000] > EPS: nonzero5 += 1
            if any(fut[h] > EPS for h in HORIZONS): positive_any += 1
            key=f'{mid}|{rid}|{iid}|{cp}'
            rows.append({
                'marketId':mid,'side':side,'checkpointMs':cp,'responsibilityId':rid,'intentId':iid,
                'immutableFrontierKey':hashlib.sha256(key.encode()).hexdigest()[:24],
                'materialized':True,'requestedQty':requested,'confirmedBeforeCheckpoint':confirmed_before,'remainingQtyAtCheckpoint':remaining,
                'fillShares1s':fut[1000],'fillShares3s':fut[3000],'fillShares5s':fut[5000],
                'fillFraction1s':fut[1000]/remaining if remaining>EPS else 0.0,
                'fillFraction3s':fut[3000]/remaining if remaining>EPS else 0.0,
                'fillFraction5s':fut[5000]/remaining if remaining>EPS else 0.0,
                'monotonicFrontier':mono,
                'futureExecutionIds5s':[x['executionId'] for x in deltas if x['intentId']==iid and cp < x['receivedAtMs'] <= cp+5000],
                'clockSemantics':'receipt-clock confirmed fill: future window uses received_at_ms; exchange event_at_ms retained in journal only'
            })
    n=len(rows); ok=n-missing
    status='MATERIALIZATION_PASS' if n>=20 and missing==0 and monotonic_fail==0 and nonzero5>0 else 'MATERIALIZATION_FAIL'
    rep={
        'version':'R4_P0_FILL_FRONTIER_V1','status':status,
        'source':str(SRC.relative_to(ROOT)).replace('\\','/'),
        'semanticScope':'Pipeline/provenance validation only. Checkpoints are deterministic research lifecycle-exam checkpoints, not Target recovery-candidate labels and not action authority.',
        'summary':{
            'rows':n,'materializedRows':ok,'coverage':ok/max(1,n),'missingRows':missing,
            'monotonicFailureCount':monotonic_fail,'nonZeroFill5sRows':nonzero5,'nonZeroFill5sRate':nonzero5/max(1,ok),
            'anyPositiveFrontierRows':positive_any,'anyPositiveFrontierRate':positive_any/max(1,ok)
        },
        'fixedPipelineGate':{'minimumRows':20,'coverage':1.0,'monotonicFailureCount':0,'requireNonZeroFill5sRows':'>0'},
        'rows':rows,
        'interpretation':'A non-zero deterministic frontier proves the immutable event journal can support offline per-order future-fill labels without replay rediscovery. It does not by itself validate the old continuous queue-option prediction semantic.',
        'guards':{'researchOnly':True,'no8781Change':True,'noLiveR3Change':True,'noEchtgeld':True,'special20260816Sealed':True,'notActionAuthority':True}
    }
    OUT.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':status,'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'summary':rep['summary']},ensure_ascii=False))

if __name__=='__main__': main()
