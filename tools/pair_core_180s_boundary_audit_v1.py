"""Zero-HFT source-backed boundary audit of already frozen Target/OUR captures."""
import gzip
import hashlib
import json
from pathlib import Path

ROOT=Path('data/research')


def frozen(path,digest):
    p=ROOT/path;raw=p.read_bytes();assert hashlib.sha256(raw).hexdigest()==digest
    return json.loads(raw)


def main():
    target=frozen(Path('pair_core_target_geometry10_20260910_v1/TARGET_COMPACT.json'),'0e0e82235cb9dfb5ab013bd0535da241b71b9e756af0cdbd1d55e65d03d8fb67')
    ours=frozen(Path('lan_worker_returns/pair-core-target-geometry10-20260910-v2/COMPACT.json'),'c1dd0b211dac4dfdd95848b2ebcdff91fb2d64ccfe7bbd3205965cb923bc019d')
    om={r['marketId']:r for r in ours['rows']};rows=[];late_cash=total_cash=0.
    for m in target['rows']:
        cutoff=m['end']-180000
        before=[r for r in m['trace'] if r['eventMs']<=cutoff]
        after=[r for r in m['trace'] if r['eventMs']>cutoff]
        assert before and after
        p=before[-1];o=om[m['marketId']];lc=m['buyNotional']-p['buyNotional']
        late_cash+=lc;total_cash+=m['buyNotional']
        late=[r for r in o['receipts'] if r['exchange_ts']>cutoff*1000000]
        rows.append(dict(marketId=m['marketId'],cutoff=cutoff,targetLateParents=sum(r['parents'] for r in after),
            targetAllParents=m['parentEvents'],targetLateBuyNotional=lc,targetBuyNotional=m['buyNotional'],
            targetLateNotionalShare=lc/m['buyNotional'],targetLateTakerBatches=sum('TAKER' in r['routes'] for r in after),
            targetLastFillRemainingS=(m['end']-m['trace'][-1]['eventMs'])/1000,
            ourLateNativeReceipts=len(late),ourLateVeto=o['veto'].get('LATE_180S',0),
            ourLastFillRemainingS=(m['end']*1000000-max(r['exchange_ts'] for r in o['receipts']))/1e9,
            targetPrefixToTerminalRecordedWinnerChange=m['endpoints'][m['recordedWinner']]-p[m['recordedWinner']]))
    trace=ROOT/'lan_worker_returns/pair-core-intent-realization3-20260910-v1/DECISIONS.json.gz'
    assert hashlib.sha256(trace.read_bytes()).hexdigest()=='efd3056ce96c75e59f080aab68101f93be6bac9ee5228c5bd827832dfeea947b'
    with gzip.open(trace,'rb') as stream:raw=stream.read(32*1024**2+1)
    assert len(raw)<=32*1024**2
    controls=[]
    for mid,fs in json.loads(raw).items():
        late=[f for f in fs if f['remainingMs']<=180000]
        c=dict(marketId=int(mid),lateClocks=len(late),actualRoleCalls=sum(bool(f['decisions']) for f in late),
               submitCalls=sum(len(f['submits']) for f in late),emptySlotsUnmatchedQuoteFits=0)
        for f in late:
            weak='UP' if f['inventory']['UP']<f['inventory']['DOWN'] else 'DOWN'
            menu=f['menu'][weak];can=menu['first']
            if not f['owners'] and can and can[1]<=menu['oppositeUnmatched']+1e-9:
                c['emptySlotsUnmatchedQuoteFits']+=1
        controls.append(c)
    result=dict(version='PAIR_CORE_180S_BOUNDARY_READONLY_V1',newBE=0,newPolicy=False,rows=rows,observedControls=controls,
        summary=dict(targetLateParents=sum(r['targetLateParents'] for r in rows),targetAllParents=sum(r['targetAllParents'] for r in rows),
            targetLateBuyNotionalShare=late_cash/total_cash,targetLateTakerBatches=sum(r['targetLateTakerBatches'] for r in rows),
            ourLateVeto=sum(r['ourLateVeto'] for r in rows),ourLateNativeReceipts=sum(r['ourLateNativeReceipts'] for r in rows),
            targetRecordedWinnerGeometryImproved=sum(r['targetPrefixToTerminalRecordedWinnerChange']>1e-9 for r in rows),
            targetRecordedWinnerGeometryWorsened=sum(r['targetPrefixToTerminalRecordedWinnerChange']< -1e-9 for r in rows)),
        warning='Descriptive matched coverage; Target posting times unknown; prefix payoff deltas not a no-late-trading causal replay')
    out=ROOT/'pair_core_target_geometry10_20260910_v1/BOUNDARY180_AUDIT.json'
    assert not out.exists(),'immutable output';out.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(summary=result['summary'],controls=controls),allow_nan=False))


if __name__=='__main__':main()
