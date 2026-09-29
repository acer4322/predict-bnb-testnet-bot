"""Observation-only geometry; prices are not economic beliefs or action authority."""
from collections import deque


def quote_frontier(inv, un, qv, side, role):
    opp='DOWN' if side=='UP' else 'UP'
    lots=list(un[opp]);held=sum(q for q,p in lots)
    avg=sum(q*p for q,p in lots)/held if held>1e-9 else None
    quote=qv.get(side) or {};bid=quote.get('bid');ask=quote.get('ask')
    eligible=(ask is not None and (avg is None or avg+ask<=1.0000001))
    return dict(side=side,role=role,oppositeUnmatchedQty=held,oppositeUnmatchedAverage=avg,
                bid=bid,ask=ask,askPairPass=eligible,
                pairAskExcess=None if avg is None or ask is None else avg+ask-1,
                inventoryUP=inv['UP'],inventoryDOWN=inv['DOWN'],
                boundary='Quote feasibility only; not native Active option or value')


def receipt_anatomy(receipts, expected):
    un={'UP':deque(),'DOWN':deque()};pairs=[];cost=0.;inv={'UP':0.,'DOWN':0.}
    for i,r in enumerate(receipts,1):
        assert r['sequence']==i
        side='UP' if r['side']==1 else 'DOWN';opp='DOWN' if side=='UP' else 'UP'
        price=r['price'] if side=='UP' else 1-r['price'];q=r['qty']
        assert q>0 and 0<=price<=1
        cost+=q*price;inv[side]+=q;left=q
        while left>1e-9 and un[opp]:
            lot=un[opp][0];take=min(left,lot['qty'])
            pairs.append(dict(openSeq=lot['sequence'],closeSeq=i,qty=take,
                              margin=take*(1-lot['price']-price)))
            left-=take;lot['qty']-=take
            if lot['qty']<=1e-9:un[opp].popleft()
        if left>1e-9:un[side].append(dict(sequence=i,qty=left,price=price))
    residual=[dict(side=s,**x) for s in un for x in un[s]]
    margin=sum(p['margin'] for p in pairs);remaining_cost=sum(x['qty']*x['price'] for x in residual)
    endpoints={s:margin-remaining_cost+sum(x['qty'] for x in residual if x['side']==s) for s in un}
    assert abs(cost-expected['cost'])<1e-8
    assert all(abs(endpoints[s]-expected[s])<1e-8 for s in endpoints)
    return dict(pairedMargin=margin,positivePairMargin=sum(max(0.,p['margin']) for p in pairs),
                negativePairMargin=sum(min(0.,p['margin']) for p in pairs),
                pairedQty=sum(p['qty'] for p in pairs),residualCost=remaining_cost,
                residual=residual,endpoints=endpoints,pairs=pairs,
                warning='Realized FIFO attribution, NOT a retention/repair causal contrast')
