"""Price-domain diagnostic on existing Target coordinates; not a strategy replay.

Ignores Target quantities, directions, timestamps and outcomes. All authorization
is an explicitly synthetic fixture. No actual order, fill or economic gain exists.
"""
from collections import Counter, defaultdict
from dataclasses import asdict, replace
from pathlib import Path
import hashlib
import json
import time

from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant
from tools.pair_core_objective_quantity_planner_v1 import ExecutionLimits, prepare

BASE=Path('data/research/r4_v0/p0_provenance_v1')
SOURCE=BASE/'PAIR_CORE_TARGET_POST180_EVENTS_COMPACT_V1_20260910.jsonl'
EXPECTED='a105a6fa22274f24132c7fa2ef8552fdd035694d0ce7b6cedafea0e61d2d4090'
LIMITS=ExecutionLimits(.01,.01,.01,0.,12.)


def price_case(raw_price):
    # Normalize binary serialization noise only; meaningful non-tick prices are
    # unsupported under the disclosed diagnostic .01 tick, never repriced.
    p=round(raw_price,2)
    if not 0<p<1 or abs(p-raw_price)>1e-9:
        return dict(status='UNSUPPORTED_BY_DECLARED_PRICE_GRID',rawPrice=raw_price,
                    oldAdmissible=False,newAdmissible=False)
    x=EconomicGrantLedger(1.)
    x.issue(Grant(1,'fixture-v1','UP',12.,0.,1.,'SYNTHETIC_PRICE_DOMAIN_ONLY'))
    kwargs=dict(key='conditional-child',quote_reference='fixture-coordinate',
                now_ms=0,market_end_ms=300000)
    answer=prepare(x,1,'PASSIVE',p,limits=LIMITS,**kwargs)
    strict_min=prepare(x,1,'PASSIVE',p,limits=replace(LIMITS,min_notional=1.),**kwargs)
    old_qty=1./p;old=old_qty<=12.+1e-9;plan=answer.plan
    if plan:
        assert 0<plan.quantity<=12. and plan.reserved_child_cost<=1.+1e-9
        if old:
            assert -1e-9<=old_qty-plan.quantity<.01000001, 'legacy quantity changed beyond downward lot rounding'
    assert not x.carriers and x.account(1)['repair_paid']==0.
    return dict(status=answer.reason,price=p,oldAdmissible=old,
                oldFixedSpendQty=old_qty,newAdmissible=plan is not None,
                newQuantity=plan.quantity if plan else None,
                newMaxCash=plan.reserved_child_cost if plan else None,
                minNotional1Sensitivity=strict_min.reason,
                note='Conditional reservation only; synthetic grant, not actual trading')


def main():
    start=time.monotonic()
    out=BASE/'PAIR_CORE_OBJECTIVE_QUANTITY_DOMAIN_RESULT_V1_20260910.json'
    if out.exists():raise FileExistsError(str(out))
    if SOURCE.stat().st_size>20*1024**2:raise ValueError('source too large')
    digest=hashlib.sha256();seen=set();by_asset=defaultdict(list);cache={}
    with SOURCE.open('rb') as f:
        for raw in f:
            digest.update(raw);r=json.loads(raw)
            if r['leg'] in seen:raise ValueError('duplicate coordinate')
            seen.add(r['leg']);a=r['asset'];p=float(r['p'])
            if a not in ('BTC','ETH'):raise ValueError('unexpected asset')
            if p not in cache:cache[p]=price_case(p)
            # Deliberately not using r[q], r[side], r[t], role, or any winner.
            by_asset[a].append((int(r['marketId']),p))
    if digest.hexdigest()!=EXPECTED:raise ValueError('source changed')
    summary={}
    for asset,coordinates in by_asset.items():
        new_domain=[(m,p) for m,p in coordinates if not cache[p]['oldAdmissible'] and cache[p]['newAdmissible']]
        legacy_valid=[(m,p) for m,p in coordinates if cache[p]['oldAdmissible']]
        summary[asset]=dict(markets=len({m for m,p in coordinates}),priceCoordinates=len(coordinates),
          distinctRawPrices=len({p for m,p in coordinates}),legacySupportedCoordinates=len(legacy_valid),
          plannedCoordinatesUnderFixture=sum(cache[p]['newAdmissible'] for m,p in coordinates),
          gainedPriceCoordinates=len(new_domain),marketsWithGainedCoordinates=len({m for m,p in new_domain}),
          zeroActualNewOrders=len([]),unsupportedByDeclaredGrid=sum(cache[p]['status']=='UNSUPPORTED_BY_DECLARED_PRICE_GRID' for m,p in coordinates),
          rejectionReasons=dict(Counter(cache[p]['status'] for m,p in coordinates if not cache[p]['newAdmissible'])),
          minNotional1Sensitivity=dict(Counter(cache[p]['minNotional1Sensitivity'] for m,p in coordinates if cache[p]['status']!='UNSUPPORTED_BY_DECLARED_PRICE_GRID')))
    if len(seen)!=34212 or summary['BTC']['markets']!=50 or summary['ETH']['markets']!=100:
        raise ValueError('cohort changed')
    sources=[]
    for name in ['tools/pair_core_objective_quantity_planner_v1.py','tools/pair_core_economic_grant_ledger_v1.py',
                 'tools/allocation_ledger_v2.py','tools/hft244_pair_route_legality_v1.py',
                 'tools/run_eth_role_separated_minimal_pair_safety_smoke.py',
                 'tools/run_eth_target_grounded_distinct_multislot_v2_smoke.py']:
        p=Path(name);b=p.read_bytes();sources.append(dict(path=name,bytes=len(b),sha256=hashlib.sha256(b).hexdigest()))
    result=dict(version='PAIR_CORE_OBJECTIVE_QUANTITY_DOMAIN_V1',
       verdict='SYNTHETIC_AUTHORITY_PRICE_DOMAIN_CHECK_NOT_POLICY_EVIDENCE',
       source=dict(path=SOURCE.as_posix(),bytes=SOURCE.stat().st_size,sha256=digest.hexdigest()),
       syntheticGrant=dict(side='UP_FOR_COORDINATE_TEST_ONLY',repairQty=12.,addQty=0.,cash=1.,capital=1.),
       fixtureLimits=asdict(LIMITS),oldRule='q=1/p; reject q>12',
       newRule='q=floor_to_step(min(unreserved authorized qty,child cap,available cash/price))',
       summaries=summary,priceCases=[cache[p] for p in sorted(cache)],sourceManifest=sources,
       newHFT=0,newTraining=0,newOrders=0,realizedEconomicDelta=None,nativeIntegration=False,
       economicGrantGenerator=False,elapsedSeconds=time.monotonic()-start,
       limitations=['Target coordinates are existing observed prices, not our decisions or training labels.',
        'Cash/minimum order/lot rules are fixtures until verified against actual execution contract.',
        'A real min-notional constraint can still exclude low-price children; do not bypass it.',
        'Old admitted quantities are retained only within declared downward lot rounding.',
        '150 markets here are source coverage, not 150 strategy evaluations or generalization.',
        'BTC/ETH are different historical calendar strata; no causal asset-effect claim.',
        'Economic intent is supplied, not learned; no result on Target win/loss matching.'])
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({k:result[k] for k in ('verdict','summaries','newHFT','newTraining','newOrders','elapsedSeconds')},ensure_ascii=False))
    print(json.dumps({'output':out.as_posix(),'bytes':out.stat().st_size,
                      'sha256':hashlib.sha256(out.read_bytes()).hexdigest()},ensure_ascii=False))


if __name__=='__main__':main()
