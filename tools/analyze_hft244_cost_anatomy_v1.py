"""Tiny FIFO cost decomposition from persisted receipts, no engine or corpus."""
from collections import deque,defaultdict
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'data/research/lan_worker_returns/hft244-cost-anatomy-2023609-20260910-v1/COMPACT.json'


def main():
    data=json.loads(SOURCE.read_text());assert data['verdict']=='COST_ANATOMY_PARITY_SUPPORTED' and data['referenceParity']
    receipts=data['executionReceipts'];assert len(receipts)==8
    roles={e['receiptSequence']:e for clock in data['costAnatomy'] for e in clock['splits']}
    un={'UP':deque(),'DOWN':deque()};inv={'UP':0.,'DOWN':0.};cost=0.;pairs=[];events=[]
    for seq,r in enumerate(receipts,1):
        assert r['sequence']==seq
        side='UP' if r['side']==1 else 'DOWN';opp='DOWN' if side=='UP' else 'UP'
        price=r['price'] if side=='UP' else 1-r['price'];qty=r['qty'];inv[side]+=qty;cost+=qty*price
        left=qty;margin=0.
        while left>1e-9 and un[opp]:
            entry=un[opp][0];q=min(left,entry['qty']);profit=q*(1-price-entry['price'])
            pairs.append(dict(openSequence=entry['sequence'],closeSequence=seq,openOwner=entry['owner'],closeOwner=roles[seq]['key'],openRole=entry['role'],closeRole=roles[seq]['role'],qty=q,openPrice=entry['price'],closePrice=price,pairSum=entry['price']+price,grossMargin=profit))
            margin+=profit;left-=q;entry['qty']-=q
            if entry['qty']<=1e-9:un[opp].popleft()
        if left>1e-9:un[side].append(dict(sequence=seq,owner=roles[seq]['key'],role=roles[seq]['role'],qty=left,price=price))
        events.append(dict(sequence=seq,owner=roles[seq]['key'],role=roles[seq]['role'],side=side,qty=qty,price=price,maker=bool(r['maker']),repair=roles[seq]['repairAllocated'],overflow=roles[seq]['overflowRealized'],pairedMarginAdded=margin,UP=inv['UP']-cost,DOWN=inv['DOWN']-cost))
    locked=sum(p['grossMargin'] for p in pairs);by_origin=defaultdict(float)
    for p in pairs:by_origin[p['openOwner']]+=p['grossMargin']
    remaining=[dict(side=side,**r) for side,rows in un.items() for r in rows]
    rem_cost=sum(r['qty']*r['price'] for r in remaining)
    endpoints={side:locked-rem_cost+sum(r['qty'] for r in remaining if r['side']==side) for side in ['UP','DOWN']}
    assert abs(cost-data['cost'])<1e-10
    assert all(abs(endpoints[k]-data[k])<1e-10 for k in endpoints)
    result=dict(verdict='FIFO_COST_DECOMPOSITION_RECONCILED',sourceSha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),marketId=2023609,
        matchedQty=sum(p['qty'] for p in pairs),matchedGrossMargin=locked,marginByOpeningOwner=dict(by_origin),remaining=remaining,remainingCost=rem_cost,endpoints=endpoints,
        modeledFees=sum(r['fee'] for r in receipts),newEngines=0,marketBE=0,events=events,pairs=pairs,fullNetCost='UNRESOLVED',generalization='NOT_IDENTIFIED')
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
