"""Zero-BE interpretation of the frozen intent trace. Not a fill-value model."""
from collections import defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics

ROOT=Path('data/research/lan_worker_returns/pair-core-intent-realization3-20260910-v1')


def main():
    compact=json.loads((ROOT/'COMPACT.json').read_text())
    assert compact['verdict']=='INTENT_REALIZATION_SMOKE3_PARITY_SUPPORTED'
    trace=ROOT/'DECISIONS.json.gz'
    assert hashlib.sha256(trace.read_bytes()).hexdigest()==compact['decisionTraceSha256']
    with gzip.open(trace,'rb') as stream:raw=stream.read(32*1024**2+1)
    assert len(raw)<=32*1024**2
    frames=json.loads(raw);result=dict(version='INTENT_FRONTIER_AUDIT_V1',traceSha256=compact['decisionTraceSha256'],
        uncompressedBytes=len(raw),rows=[],warning='Bid distance and realization are associated; no causal fill counterfactual or hidden Target intent')
    for market in compact['rows']:
        fs=frames[str(market['marketId'])];joined={r['orderId']:r for r in market['intent']['admitted']}
        for side in ('UP','DOWN'):
            chosen=[f for f in fs if f['decisions'] and f['decisions'][0][0]==side]
            submissions=[(f,a) for f in fs for a in f['submits'] if a['ok'] and a['side']==side]
            gaps=[f['qv'][side]['bid']-a['price'] for f,a in submissions]
            strata=defaultdict(lambda:dict(owners=0,filledOwners=0,qty=0.,filledQty=0.,paid=0.))
            for (f,a),gap in zip(submissions,gaps):
                k='AT_BID' if abs(gap)<=1e-9 else 'BEHIND_BID' if gap>0 else 'ABOVE_BID'
                x=strata[k];j=joined[a['orderId']]
                x['owners']+=1;x['filledOwners']+=j['filledQty']>1e-9;x['qty']+=a['qty']
                x['filledQty']+=j['filledQty'];x['paid']+=j['paid']
            result['rows'].append(dict(marketId=market['marketId'],side=side,actualChosenClocks=len(chosen),
                chosenClocksWithPairRejectedUnusedPrices=sum(f['menu'][side]['pairRejectedUnusedLevels']>0 for f in chosen),
                admittedOwners=len(submissions),medianBidGap=statistics.median(gaps),maxBidGap=max(gaps),
                behindBidOwners=sum(g>1e-9 for g in gaps),strata=dict(strata)))
    out=ROOT/'FRONTIER_AUDIT.json';assert not out.exists(),'immutable output'
    out.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(result,allow_nan=False))


if __name__=='__main__':main()
