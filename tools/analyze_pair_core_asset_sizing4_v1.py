"""Bounded consumed compact/trace analysis; no engine or market data query."""
from collections import Counter
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]/'data/research/lan_worker_returns/pair-core-asset-sizing4-20260910-v2'


def classify(asset, counts, orders, filled, parity):
    if asset=='ETH':return 'ETH_FULL_PARITY' if parity else 'CORRECTNESS_STOP'
    if orders:return 'EXECUTION_EXERCISED' if filled>1e-9 else 'NATIVE_SUBMIT_EXERCISED_ZERO_FILL'
    if not counts.get('earlySelectedSideHasAddedDomain',0):return 'NOT_EXERCISED'
    if counts.get('candidateInAddedDomain',0):return 'NEW_DOMAIN_CANDIDATE_NOT_ADMITTED'
    return 'PRICE_DOMAIN_ONLY_NOT_SELECTED'


def main():
    p=ROOT/'COMPACT.json';assert p.stat().st_size<1024**2
    data=json.loads(p.read_bytes());assert data['verdict']=='ASSET_SIZING_CORRECTNESS_CAPTURE_SUPPORTED'
    result=dict(sourceSha256=hashlib.sha256(p.read_bytes()).hexdigest(),rows=[],newBE=0,
                executionBE=data['attemptedBE'],historicalLineageAfter=data['historicalLineageAfter'])
    for mid in (1830119,2022527,2026429,2029246):
        control=next(r for r in data['rows'] if r['marketId']==mid and r['arm']=='C12')
        sized=next(r for r in data['rows'] if r['marketId']==mid and r['arm']=='S')
        trace=ROOT/sized['trace']['file'];assert trace.stat().st_size<128*1024
        assert hashlib.sha256(trace.read_bytes()).hexdigest()==sized['trace']['sha256']
        with gzip.open(trace,'rb') as stream:raw=stream.read(1024**2+1)
        assert len(raw)<=1024**2 and len(raw)==sized['trace']['bytes']
        frames=json.loads(raw);counts=Counter();candidate_prices=Counter()
        for f in frames:
            if f['remainingMs']<=180000:
                counts['lateBlockedBeforeRole']+=not f['roles'];continue
            counts['earlyClocks']+=1
            assert len(f['roles'])==1
            side=f['roles'][0][0]
            counts['earlySelectedSideHasAddedDomain']+=bool(f['domain'][side]['added18'])
            if not f['candidates']:
                counts['beforeCandidateSideCap']+=1
                assert f['veto'].get('SIDE_SLOT_CAP_FULL')==1
                continue
            assert len(f['candidates'])==1
            option=f['candidates'][0]['result']
            if option is None:counts['candidateNone']+=1;continue
            price=option[0];candidate_prices[str(price)]+=1
            counts['candidateAtOrAboveOldFloor']+=price>=1/12-1e-9
            counts['candidateInAddedDomain']+=price<1/12-1e-9
            counts['admitted']+=any(a['ok'] for a in f['submits'])
            counts['globalCapAfterCandidate']+=f['veto'].get('GLOBAL_SLOT_CAP_FULL',0)
        new_orders=[a for a in sized['observer']['admitted'] if a['price']<1/12-1e-9]
        result['rows'].append(dict(marketId=mid,asset=sized['asset'],counts=dict(counts),
            candidatePriceCounts=dict(candidate_prices),newDomainOrders=len(new_orders),
            newDomainFilledQty=sum(a['filledQty'] for a in new_orders),
            behaviorParity=control['observer']['behaviorDigest']==sized['observer']['behaviorDigest'],
            frameParity=control['observer']['framesDigest']==sized['observer']['framesDigest'],
            receiptParity=control['receipts']==sized['receipts'],
            delta={k:sized[k]-control[k] for k in ('UP','DOWN','cost','fills','submits','alternations')},
            terminalPending=len(sized['pending']),
            verdict=classify(sized['asset'],counts,len(new_orders),
                sum(a['filledQty'] for a in new_orders),
                control['observer']['behaviorDigest']==sized['observer']['behaviorDigest']
                and control['observer']['framesDigest']==sized['observer']['framesDigest']
                and control['receipts']==sized['receipts'])))
    target=ROOT/'SIZING_COMPARISON.json'
    target.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps({**result,'rows':[{k:v for k,v in r.items() if k!='candidatePriceCounts'} for r in result['rows']]}))


if __name__=='__main__':main()
