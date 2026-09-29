"""Evaluation-only payoff geometry, fixed baseline scale; never runtime features."""
from collections import defaultdict


def distance(our,target,baseline_cost,target_cost):
    if baseline_cost<=0 or target_cost<=0:raise ValueError('positive fixed scales required')
    return sum(abs(our[s]/baseline_cost-target[s]/target_cost) for s in ('UP','DOWN'))/2


def path_metrics(receipts,target_trace,start_ms,end_ms,baseline_cost,target_cost,clock='exchange_ts'):
    assert end_ms>start_ms
    events=defaultdict(list)
    for r in receipts:
        side='UP' if r['side']==1 else 'DOWN';price=r['price'] if side=='UP' else 1-r['price']
        cash=r['qty']*price
        events[int(r[clock])].append(('OUR',{s:(r['qty'] if s==side else 0)-cash for s in ('UP','DOWN')}))
    for r in target_trace:events[int(r['eventMs'])*1000000].append(('TARGET',{s:r[s] for s in ('UP','DOWN')}))
    start=int(start_ms)*1000000;end=int(end_ms)*1000000
    ours=dict(UP=0.,DOWN=0.);target=dict(UP=0.,DOWN=0.);integral=0.;zero_integral=0.;last=start
    for tick in sorted(set(events)|{start,end}):
        if tick>end:break
        if tick>start:
            dt=(tick-last)/1e6
            integral+=dt*distance(ours,target,baseline_cost,target_cost)
            zero_integral+=dt*distance(dict(UP=0.,DOWN=0.),target,baseline_cost,target_cost)
            last=tick
        for kind,value in events.get(tick,[]):
            if kind=='TARGET':target=value
            else:
                for s in ours:ours[s]+=value[s]
    return dict(meanDistance=integral/(end_ms-start_ms),terminalDistance=distance(ours,target,baseline_cost,target_cost),
                zeroPositionMeanDistance=zero_integral/(end_ms-start_ms),
                zeroPositionTerminalDistance=distance(dict(UP=0.,DOWN=0.),target,baseline_cost,target_cost))
