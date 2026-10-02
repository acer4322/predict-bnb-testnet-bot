"""Pair frozen Target fill evidence with native OUR receipts; evaluation only."""
import argparse
import hashlib
import json
from pathlib import Path
import statistics


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def endpoint_at(receipts, cutoff, clock):
    inv={'UP':0.,'DOWN':0.};cost=0.;count=0
    for r in receipts:
        if r[clock]/1e6>cutoff:continue
        side='UP' if r['side']==1 else 'DOWN';p=r['price'] if side=='UP' else 1-r['price']
        inv[side]+=r['qty'];cost+=p*r['qty'];count+=1
    return dict(UP=inv['UP']-cost,DOWN=inv['DOWN']-cost,cost=cost,receipts=count)


def target_at(trace,cutoff):
    found=[r for r in trace if r['eventMs']<=cutoff]
    if not found:return dict(UP=0.,DOWN=0.,cost=0.,receipts=0)
    r=found[-1]
    return dict(UP=r['UP'],DOWN=r['DOWN'],cost=r['buyNotional'],receipts=sum(x['parents'] for x in found))


def normalized_distance(a,b):
    if a['cost']<=0 or b['cost']<=0:return None
    return sum(abs(a[s]/a['cost']-b[s]/b['cost']) for s in ('UP','DOWN'))/2


def compare(target,ours):
    om={r['marketId']:r for r in ours['rows']};rows=[]
    assert len(om)==len(target['rows'])==10
    for t in target['rows']:
        o=om[t['marketId']];assert o['correctness']
        winner=t['recordedWinner'];terminal_t=dict(**t['endpoints'],cost=t['buyNotional'])
        terminal_o={k:o[k] for k in ('UP','DOWN','cost')}
        # Native time fields are nanoseconds; Target event time is millisecond,
        # often second-quantized. Never align Target observed_at as decision time.
        for s in ('UP','DOWN'):
            assert abs(endpoint_at(o['receipts'],float('inf'),'receive_ts')[s]-o[s])<1e-7
        checkpoints=[]
        for phase in (.25,.4,.5,.75,1.):
            cutoff=t['start']+phase*(t['end']-t['start']);tg=target_at(t['trace'],cutoff)
            ex=endpoint_at(o['receipts'],cutoff,'exchange_ts');rx=endpoint_at(o['receipts'],cutoff,'receive_ts')
            checkpoints.append(dict(phase=phase,target=tg,ourExchange=ex,ourReceive=rx,
                                    distanceExchange=normalized_distance(tg,ex),distanceReceive=normalized_distance(tg,rx)))
        taker=sum(r['qty']*(r['price'] if r['side']==1 else 1-r['price']) for r in o['receipts'] if r['maker']==0)
        target_taker=t['route'].get('TAKER',{}).get('buy',0)
        pnlt=t['endpoints'][winner];pnlo=o[winner]
        direction_t=t['endpoints']['UP']-t['endpoints']['DOWN'];direction_o=o['UP']-o['DOWN']
        same=lambda a,b:(a>1e-9)-(a< -1e-9)==(b>1e-9)-(b< -1e-9)
        rows.append(dict(marketId=t['marketId'],targetEndpoints=t['endpoints'],ourEndpoints={s:o[s] for s in ('UP','DOWN')},
            winnerRecorded=winner,targetWinnerGross=pnlt,ourWinnerGross=pnlo,
            samePnlSign=same(pnlt,pnlo),sameTerminalSurplusSide=same(direction_t,direction_o),
            targetCost=t['buyNotional'],ourCost=o['cost'],
            targetParents=t['parentEvents'],ourReceipts=len(o['receipts']),ourFills=o['fills'],ourAlternations=o['alternations'],
            targetTakerNotionalShare=target_taker/t['buyNotional'] if t['buyNotional'] else None,
            ourTakerNotionalShare=taker/o['cost'] if o['cost'] else None,
            endpointDistance=normalized_distance(terminal_t,terminal_o),checkpoints=checkpoints,
            targetLateNotionalShare=1-target_at(t['trace'],t['end']-180000)['cost']/t['buyNotional'],
            ourLateNotionalShare=1-endpoint_at(o['receipts'],t['end']-180000,'exchange_ts')['cost']/o['cost'] if o['cost'] else None))
    pnls=[r['ourWinnerGross'] for r in rows];targets=[r['targetWinnerGross'] for r in rows]
    chronological=sorted(rows,key=lambda r:next(t['start'] for t in target['rows'] if t['marketId']==r['marketId']))
    running=peak=dd=0.
    for r in chronological:running+=r['ourWinnerGross'];peak=max(peak,running);dd=max(dd,peak-running)
    return dict(verdict='PAIRED_DIAGNOSTIC_NOT_SIMILARITY_OR_PROFITABILITY_CERTIFICATION',rows=rows,
        summary=dict(markets=10,ourWins=sum(p>1e-9 for p in pnls),targetRecordedWins=sum(p>1e-9 for p in targets),
            ourGross=sum(pnls),targetRecordedGross=sum(targets),ourWorst=min(pnls),ourBest=max(pnls),
            ourLOBO=sum(pnls)-max(pnls),ourSequentialDD=dd,ourNativeReceipts=sum(r['ourReceipts'] for r in rows),
            samePnlSign=sum(r['samePnlSign'] for r in rows),sameTerminalSurplusSide=sum(r['sameTerminalSurplusSide'] for r in rows),
            medianEndpointDistance=statistics.median(r['endpointDistance'] for r in rows if r['endpointDistance'] is not None)))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--target',required=True);ap.add_argument('--our',required=True);ap.add_argument('--output',required=True);a=ap.parse_args()
    tp=Path(a.target);op=Path(a.our);dest=Path(a.output);assert not dest.exists(),'immutable output'
    target=json.loads(tp.read_text());ours=json.loads(op.read_text())
    assert sha(tp)=='0e0e82235cb9dfb5ab013bd0535da241b71b9e756af0cdbd1d55e65d03d8fb67'
    assert ours['verdict']=='MATCHED_BASELINE10_CAPTURED_NOT_POLICY_PROMOTION'
    r=compare(target,ours);r.update(targetSha256=sha(tp),ourSha256=sha(op))
    dest.write_text(json.dumps(r,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(summary=r['summary'],rows=[{k:v for k,v in x.items() if k!='checkpoints'} for x in r['rows']]),allow_nan=False))


if __name__=='__main__':main()
