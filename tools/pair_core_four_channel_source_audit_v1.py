"""Bounded reuse of inert3 pre-decision traces; no new HFT or corpus."""
from collections import Counter,defaultdict
import gzip
import hashlib
import json
from pathlib import Path
import statistics
from tools.pair_core_route_value_contract_v1 import describe

ROOT=Path('data/research/lan_worker_returns/pair-core-intent-realization3-20260910-v1')


def main():
    out=ROOT/'FOUR_CHANNEL_SOURCE_AUDIT.json';assert not out.exists(),'immutable output'
    compact_raw=(ROOT/'COMPACT.json').read_bytes();assert len(compact_raw)<2*1024**2
    assert hashlib.sha256(compact_raw).hexdigest()=='b54b6a15ae22a6fde0d5fb82ac23a2a5282cac800e5f9ed6f46b38eee1df3bb9'
    compact=json.loads(compact_raw)
    trace=ROOT/'DECISIONS.json.gz';assert trace.stat().st_size<1024**2
    trace_raw=trace.read_bytes()
    assert hashlib.sha256(trace_raw).hexdigest()==compact['decisionTraceSha256']
    with gzip.open(trace,'rb') as s:raw=s.read(32*1024**2+1)
    assert len(raw)<=32*1024**2;frames=json.loads(raw)
    rows=[];witnesses={};totals=Counter()
    for market in compact['rows']:
        mid=market['marketId'];fs=frames[str(mid)];groups=defaultdict(list);late=0
        for f in fs:
            if not f['decisions']:
                late+=1;continue # Never reconstruct a role the frozen controller did not invoke.
            assert len(f['decisions'])==1
            side,role,_,_=f['decisions'][0];quote=f['menu'][side]['first']
            if quote is None:continue
            d=describe(f,side,role,quote[0],quote[1])
            d['passiveActuallyAdmitted']=any(a['ok'] for a in f['submits'])
            d['sourceOutcome']=f['outcome'];a=d['active']
            groups[d['purpose']].append(d)
            if a and d['passiveActuallyAdmitted']:
                # Earliest qualifying source clock, not future fill/terminal PnL.
                key=f'{mid}_{d["purpose"]}'
                witnesses.setdefault(key,d)
        for purpose,ds in groups.items():
            active=[d for d in ds if d['active']]
            admitted=[d for d in active if d['passiveActuallyAdmitted']]
            c=Counter(decisionClocks=len(ds),passiveAdmitted=len(admitted),quotedActive=len(active),
                quotePairPass=sum(d['active']['inheritedPairPricePass'] for d in active),
                noPotentialOwnCross=sum(not d['active']['potentialCrossOwners'] for d in active),
                admittedPairPassNoCross=sum(d['active']['inheritedPairPricePass'] and not d['active']['potentialCrossOwners'] for d in admitted),
                admittedMoreThanOriginalTicket=sum(not d['active']['withinOriginalQuotedTicket'] for d in admitted),
                activeNativeAuthorized=0,identifiedActionValueLabels=0)
            premiums=[d['active']['premiumOverPassive'] for d in admitted]
            rows.append(dict(marketId=mid,purpose=purpose,counts=dict(c),
                admittedSameQtyPremium=dict(min=min(premiums) if premiums else None,
                    median=statistics.median(premiums) if premiums else None,max=max(premiums) if premiums else None),
                lateClocksWithoutRoleInvocation=late))
            totals.update(c)
        assert sum(len(ds) for ds in groups.values())==market['intent']['actualDecisions']
        assert sum(d['passiveActuallyAdmitted'] for ds in groups.values() for d in ds)==len(market['intent']['admitted'])
    result=dict(verdict='FOUR_CHANNEL_COST_BASIS_EXPOSED_ACTIVE_AUTHORITY_VALUE_NOT_IDENTIFIED',
        sourceSha256=compact['decisionTraceSha256'],decompressedBytes=len(raw),
        HFT=0,training=False,freshUsed=0,rows=rows,totals=dict(totals),earliestWitnesses=witnesses,
        boundary='Observed original first120s role decisions only; quote projection not native fill/authority or full-horizon coverage')
    payload=json.dumps(result,indent=2,allow_nan=False).encode();assert len(payload)<1024**2
    out.write_bytes(payload)
    print(json.dumps(dict(verdict=result['verdict'],sha256=hashlib.sha256(payload).hexdigest(),bytes=len(payload),
                         totals=result['totals'],rows=rows)))


if __name__=='__main__':main()
