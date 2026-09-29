"""Offline fixed-scale comparison of the frozen boundary contrast."""
import hashlib
import json
from pathlib import Path
from tools.pair_core_payoff_path_metrics_v1 import path_metrics,distance


def main():
    root=Path('data/research');our=root/'lan_worker_returns/pair-core-full-horizon3-20260910-v1/COMPACT.json'
    target=root/'pair_core_target_geometry10_20260910_v1/TARGET_COMPACT.json'
    assert hashlib.sha256(target.read_bytes()).hexdigest()=='0e0e82235cb9dfb5ab013bd0535da241b71b9e756af0cdbd1d55e65d03d8fb67'
    r=json.loads(our.read_text());assert r['verdict']=='FULL_HORIZON_SMOKE3_CAPTURE_SUPPORTED'
    ts={x['marketId']:x for x in json.loads(target.read_text())['rows']};bs={x['marketId']:x for x in r['rows'] if x['arm']=='A'}
    metrics={};comparisons=[]
    for row in r['rows']:
        mid=row['marketId'];t=ts[mid];b=bs[mid]
        m=dict(marketId=mid,arm=row['arm'],recordedWinnerGross=row[t['recordedWinner']],
               terminalOwnTurnoverSensitivity=distance(row,t['endpoints'],row['cost'],t['buyNotional']))
        for clock in ('exchange_ts','receive_ts'):
            m[clock]=path_metrics(row['receipts'],t['trace'],t['start'],t['end'],b['cost'],t['buyNotional'],clock)
        metrics[mid,row['arm']]=m
        if row['arm']=='F':
            a=metrics[mid,'A'];f=m
            # Coarse upper bound: even free pending shares cannot add more than
            # their quantity to the recorded-winner payout. No assumed future fill.
            pending_gain=sum(p['qty'] for p in row['pendingAtTapeEnd'] if p['side']==t['recordedWinner'])
            delta=f['recordedWinnerGross']-a['recordedWinnerGross']
            comparisons.append(dict(marketId=mid,A=a,F=f,
                endpointDelta={s:row[s]-b[s] for s in ('UP','DOWN')},recordedWinnerGrossDelta=delta,
                maxPendingWinnerGainIgnoringCost=pending_gain,deltaPlusMaxPendingGain=delta+pending_gain,
                activityRatios={k:row[k]/b[k] if b[k] else None for k in ('fills','submits','alternations','cost')},
                riskRatios={k:row['risk'][k]/b['risk'][k] if b['risk'][k] else None for k in ('peakAbsNet','peakGross','absNetIntegral','grossIntegral')},
                lateAdmissions=row['lateAdmissions'],pendingOwners=len(row['pendingAtTapeEnd'])))
    summary=dict(ARecordedGross=sum(c['A']['recordedWinnerGross'] for c in comparisons),FRecordedGross=sum(c['F']['recordedWinnerGross'] for c in comparisons),
        lateAdmissions=sum(c['lateAdmissions'] for c in comparisons),
        terminalDistanceImproved=sum(c['F']['exchange_ts']['terminalDistance']<c['A']['exchange_ts']['terminalDistance'] for c in comparisons),
        pathDistanceImproved=sum(c['F']['exchange_ts']['meanDistance']<c['A']['exchange_ts']['meanDistance'] for c in comparisons),
        pendingClosureMarkets=sum(c['pendingOwners']>0 for c in comparisons))
    result=dict(verdict='REJECT_FULL_HORIZON_ALONE_AS_SUFFICIENT_IMITATION_FIX',
        closure='PENDING_OWNER_CLOSURE_UNRESOLVED_IN_TWO_MARKETS',ourSha256=hashlib.sha256(our.read_bytes()).hexdigest(),
        summary=summary,comparisons=comparisons)
    out=our.parent/'COMPARISON.json';assert not out.exists(),'immutable output'
    out.write_text(json.dumps(result,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(summary=summary,comparisons=comparisons),allow_nan=False))


if __name__=='__main__':main()
