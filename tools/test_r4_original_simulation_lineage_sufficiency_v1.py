from __future__ import annotations
import json, math
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'data/research/execution_aware_fill_lifecycle_v0/r2_queue_option_counterfactual_v1.json'
OUT=ROOT/'data/research/r4_v0/hourly/r4_original_simulation_lineage_sufficiency_v1.json'
TEST_ID='R4_ORIGINAL_SIMULATION_LINEAGE_SUFFICIENCY_V1_20260827_1939'
TZ=ZoneInfo('Asia/Taipei')

STABLE_ID_KEYS={
    'clientIntentId','client_intent_id','economicIntentId','economic_intent_id',
    'responsibilityId','responsibility_id','clientOrderId','client_order_id',
    'intentId','intent_id','lifecycleId','lifecycle_id'
}
FILL_CONTAINER_KEYS={'fillEvents','fill_events','fills','executions','executionEvents','execution_events','orderEvents','order_events'}
TIME_KEYS={'t','ts','timestamp','timestampMs','timestamp_ms','atMs','at_ms','eventAtMs','event_at_ms','filledAtMs','filled_at_ms'}
QTY_KEYS={'qty','shares','size','fillQty','fill_qty','filledQty','filled_qty','execQty','exec_qty','cumExecQty','cum_exec_qty'}


def finite(x):
    try:
        return math.isfinite(float(x))
    except Exception:
        return False


def walk(obj, path=''):
    if isinstance(obj, dict):
        for k,v in obj.items():
            p=f'{path}.{k}' if path else k
            yield p,k,v
            yield from walk(v,p)
    elif isinstance(obj, list):
        for i,v in enumerate(obj):
            yield from walk(v,f'{path}[{i}]')


def stable_ids(row):
    out=[]
    for p,k,v in walk(row):
        if k in STABLE_ID_KEYS and v not in (None,''):
            out.append({'path':p,'value':str(v)})
    return out


def direct_fill_timelines(row):
    found=[]
    for p,k,v in walk(row):
        if k not in FILL_CONTAINER_KEYS or not isinstance(v,list) or not v:
            continue
        valid=0
        for e in v:
            if not isinstance(e,dict):
                continue
            has_t=any(kk in e and finite(e.get(kk)) for kk in TIME_KEYS)
            has_q=any(kk in e and finite(e.get(kk)) for kk in QTY_KEYS)
            if has_t and has_q:
                valid+=1
        if valid:
            found.append({'path':p,'events':len(v),'eventsWithTimeAndQty':valid})
    return found


def economic_complete(r):
    q=r.get('queueFeatures') or {}
    return bool(
        r.get('candidateAtMs') is not None and str(r.get('side') or '').upper() in {'UP','DOWN'}
        and finite(q.get('orderAgeMs')) and finite(q.get('orderPrice'))
        and finite(q.get('remainingQty')) and finite(q.get('cumExecQty'))
    )


def main():
    src=json.loads(SRC.read_text(encoding='utf-8'))
    eligible=[r for r in src.get('rows',[]) if r.get('eligible') and r.get('queueFeatures')]
    rows=[]
    observed_top=set(); observed_nested=set()
    for r in eligible:
        observed_top.update(r.keys())
        for _,k,_ in walk(r): observed_nested.add(k)
        ids=stable_ids(r); fills=direct_fill_timelines(r); econ=economic_complete(r)
        rows.append({
            'marketId':int(r['marketId']),
            'economicCheckpointComplete':econ,
            'stableIdentityEvidence':ids,
            'hasExplicitStableIdentity':bool(ids),
            'directFutureFillTimelineEvidence':fills,
            'hasDirectPerOrderFillTimeline':bool(fills),
            'fullImmutableLineage':bool(econ and ids and fills),
            'transientOrderNumPresent':bool((r.get('reinsertEvent') or {}).get('oldOrderNum') is not None),
            'terminalCumOnlyPresent':bool((r.get('reinsertEvent') or {}).get('oldCumFinal') is not None),
        })
    n=len(rows)
    econ=sum(x['economicCheckpointComplete'] for x in rows)
    sid=sum(x['hasExplicitStableIdentity'] for x in rows)
    ftl=sum(x['hasDirectPerOrderFillTimeline'] for x in rows)
    full=sum(x['fullImmutableLineage'] for x in rows)
    rates={
        'economicCheckpointCompletenessRate':econ/max(1,n),
        'explicitStableIdentityRate':sid/max(1,n),
        'directPerOrderFillTimelineRate':ftl/max(1,n),
        'fullImmutableLineageRate':full/max(1,n),
    }
    support=n>=20 and rates['economicCheckpointCompletenessRate']>=.90
    keep=support and rates['explicitStableIdentityRate']>=.90 and rates['directPerOrderFillTimelineRate']>=.90 and rates['fullImmutableLineageRate']>=.80
    reject=support and rates['fullImmutableLineageRate']<.50
    status='TESTED_KEEP_SIGNAL' if keep else 'TESTED_REJECTED' if reject else 'TESTED_INCONCLUSIVE'
    rep={
        'version':'R4_ORIGINAL_SIMULATION_LINEAGE_SUFFICIENCY_V1',
        'testId':TEST_ID,
        'createdAt':datetime.now(TZ).isoformat(),
        'status':status,
        'semanticNovelty':'Source-only audit of whether the original simulation artifact itself preserves immutable candidate/responsibility lineage plus direct future confirmed-fill evidence. No current replay is used, unlike prior provenance tests.',
        'layerAssignment':{
            'sourceOrderLifecycle':'EXECUTION_INFORMATION',
            'immutableCandidateResponsibilityLineage':'EXECUTION_DATA_PROVENANCE',
            'futureConfirmedFillEvidence':'OFFLINE_LABEL_PROVENANCE',
            'authority':'NOT_ACTION_AUTHORITY'
        },
        'cohort':{'eligibleRows':n,'source':str(SRC.relative_to(ROOT)).replace('\\','/'),'special20260816Sealed':True,'echtgeldTraining':False,'dreamFill':False},
        'primaryResult':{
            'economicCheckpointCompleteRows':econ,
            'explicitStableIdentityRows':sid,
            'directPerOrderFillTimelineRows':ftl,
            'fullImmutableLineageRows':full,
            **rates,
            'transientOrderNumRows':sum(x['transientOrderNumPresent'] for x in rows),
            'terminalCumOnlyRows':sum(x['terminalCumOnlyPresent'] for x in rows),
        },
        'observedSchema':{'topLevelKeys':sorted(observed_top),'allNestedKeyNames':sorted(observed_nested)},
        'decisionRule':{
            'minimumEligibleRows':20,'economicCheckpointCompletenessRequired':.90,
            'stableIdentityRateForKeep':.90,'futureFillTimelineRateForKeep':.90,
            'fullImmutableLineageRateForKeep':.80,'rejectIfSupportAdequateAndFullImmutableLineageBelow':.50
        },
        'decision':status,
        'rows':rows,
        'guards':{'noCurrentReplay':True,'noModelFit':True,'noThresholdSweep':True,'noActionAuthority':True,'no8781Change':True,'noLiveR3Change':True,'noNewEchtgeld':True},
        'nextDistinct':'Instrument future non-live receipt-clock simulations at creation time with an immutable responsibility/client-intent id plus append-only per-order fill event timeline. Then validate that newly produced artifacts satisfy the same provenance contract before reopening any continuous remaining-option belief.'
    }
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8')
    print(json.dumps({'artifact':str(OUT.relative_to(ROOT)).replace('\\','/'),'status':status,'cohort':rep['cohort'],'primaryResult':rep['primaryResult']},ensure_ascii=False))

if __name__=='__main__': main()
