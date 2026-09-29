"""Research-only funded buy-bundle geometry; no selector, fills, or runtime grants."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path


def rat(value):
    return value if isinstance(value,Fraction) else Fraction(str(value))


def requirements(up, down, paid, legs):
    """All independent partial-fill paths, fixed execution prices, no fees/proceeds.

    Each endpoint is affine in fill quantities. Its minimum on a rectangular
    feasible fill envelope occurs at a corner. UP loses only on DOWN fills;
    DOWN loses only on UP fills. This includes one-sided fills and zero fills,
    never treating a pending opposite leg as already paid protection.
    """
    up,down,paid=map(rat,(up,down,paid))
    assert up>=0 and down>=0 and paid>=0
    initial={'UP':up-paid,'DOWN':down-paid}
    costs={'UP':Fraction(0),'DOWN':Fraction(0)}
    qty={'UP':Fraction(0),'DOWN':Fraction(0)}
    for leg in legs:
        side=leg['side']; p=rat(leg['price']); q=rat(leg['qty'])
        assert side in costs and 0<p<1 and q>=0
        costs[side]+=p*q; qty[side]+=q
    debit=sum(costs.values())
    full={s:initial[s]+qty[s]-debit for s in initial}
    worst={'UP':initial['UP']-costs['DOWN'], 'DOWN':initial['DOWN']-costs['UP']}
    base_loss=max(Fraction(0),-min(initial.values()))
    required_loss=max(Fraction(0),-min(worst.values()))
    return dict(initialEndpoints=initial, fullFillEndpoints=full,
        independentFillWorstEndpoints=worst, additionalCashNeeded=debit,
        minimumInitialCashWithoutProceeds=paid+debit,
        currentSettlementLoss=base_loss, requiredSettlementLossBudget=required_loss,
        addedSettlementLossBudget=required_loss-base_loss)


def plain(x):
    if isinstance(x,Fraction):return float(x)
    if isinstance(x,dict):return {k:plain(v) for k,v in x.items()}
    if isinstance(x,list):return [plain(v) for v in x]
    return x


def main():
    root=Path(__file__).resolve().parents[1]
    path=root/'data/research/lan_worker_returns/hft244-residual-authority-capture-20260910-v1/COMPACT.json'
    data=json.loads(path.read_text()); assert data['referenceParity']
    snap=data['capture']['afterService']; state=snap['state']; qv=snap['quotes']
    assert not state['slot_key'] and not state['activeKeys']
    assert snap['reservedExpand']==0 and snap['reservedRepair']==0
    # 1 is the frozen policy notional primitive, NOT a new strategy threshold.
    minimum=Fraction(1); bundles=[('WAIT',[])]
    for side in ['DOWN','UP']:
        for route,pricekey in [('PASSIVE','bid'),('ACTIVE','ask')]:
            p=rat(qv[side][pricekey])
            bundles.append((route+'_'+side,[dict(side=side,price=p,qty=minimum/p)]))
    upbid=rat(qv['UP']['bid']); downbid=rat(qv['DOWN']['bid'])
    pairqty=max(minimum/upbid,minimum/downbid)
    bundles.append(('EQUAL_Q_PASSIVE_PAIR',[dict(side='UP',price=upbid,qty=pairqty),
                                          dict(side='DOWN',price=downbid,qty=pairqty)]))
    rows=[]
    seed=data['rows'][0]['witness']['state']
    reference_loss=max(0,rat(seed['cost'])-min(rat(seed['inv']['UP']),rat(seed['inv']['DOWN'])))
    for name,legs in bundles:
        assert all(l['qty']<=12 for l in legs)
        geometry=requirements(state['inv']['UP'],state['inv']['DOWN'],state['cost'],legs)
        repair=min(rat(snap['debt']),sum(l['qty'] for l in legs if l['side']=='DOWN'))
        rows.append(dict(bundle=name,legs=legs,**geometry,
            fullFillRepairQty=repair,
            fullFillOverflowQty=sum(l['qty'] for l in legs if l['side']=='DOWN')-repair,
            withinObservedSeedLossReference=geometry['requiredSettlementLossBudget']<=reference_loss,
            currentlyNativeAuthorized=(name=='WAIT'),
            executionStatus='NOT_EXECUTED_FIXED_PRICE_GEOMETRY_ONLY'))
    result=dict(verdict='CASH_RISK_REQUIREMENTS_SEPARATED_NO_EXECUTION',
        sourceSha256=hashlib.sha256(path.read_bytes()).hexdigest(),marketId=2023609,t=snap['t'],
        observedSeedLossReference=reference_loss,seedReferenceIsRiskAuthorization=False,
        rows=rows,newBE=0,modelsTrained=0,runtimeChanges=False,freshUsed=0,
        initialCashBudget='UNSPECIFIED',riskBudget='UNSPECIFIED',fees='EXCLUDED_NOT_ZERO_COST_CLAIM',
        fullNetCost='UNRESOLVED',edge='NOT_IDENTIFIED',
        boundaries=['Fixed-price buy-only geometry; no predicted fills, value or recovery.',
                    'Captured bid/ask implies a possible price point, not a proven reachable native option.',
                    'All unknown independent partial-fill combinations counted, not only joint full fill.',
                    'Loss budget is settlement endpoint loss, not mark-to-market DD or live portfolio VaR.',
                    'Additional cash never obtained by refunding spent service or treating credit as proceeds.',
                    'No new policy grant: same seed loss reference does not imply sufficient funding or value.'])
    print(json.dumps(plain(result),indent=2))


if __name__=='__main__':main()
