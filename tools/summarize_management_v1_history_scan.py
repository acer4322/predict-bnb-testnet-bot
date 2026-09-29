import json, statistics, collections, argparse
from pathlib import Path
ap=argparse.ArgumentParser(); ap.add_argument('path'); a=ap.parse_args()
d=json.loads(Path(a.path).read_text(encoding='utf-8'))
xs=[]
for r in d['rows']:
    for e in r['events']:
        xs.append({'marketId':r['marketId'], **e})
ages=[x['history']['priorCleanAgeMs'] for x in xs]
runs=[x['history']['priorCleanRunLength'] for x in xs]
out={
 'allBehaviorParity':d['allBehaviorParity'],
 'eligibleStateCount':d['eligibleStateCount'],
 'eligibleMarketCount':d['eligibleMarketCount'],
 'pendingActiveCount':sum(bool(x.get('qPendingActive')) for x in xs),
 'qLadderPresent':sum(x.get('qLadder') is not None for x in xs),
 'sidePairs':dict(collections.Counter(f"{x['marketProposalSide']}<-{x['historyForcedSide']}" for x in xs)),
 'historyForcedRoles':dict(collections.Counter(x['historyForcedRole'] for x in xs)),
 'historyAgeMs':{'median':statistics.median(ages),'p25':statistics.quantiles(ages,n=4)[0],'p75':statistics.quantiles(ages,n=4)[2],'min':min(ages),'max':max(ages)},
 'runLength':dict(sorted(collections.Counter(runs).items())),
 'marketsWithEligible':sum(1 for r in d['rows'] if r['eligibleCount']>0),
 'maxEligiblePerMarket':max(r['eligibleCount'] for r in d['rows']),
 'firstEligibleCount':len(d['firstEligiblePerMarket'])
}
print(json.dumps(out,ensure_ascii=False,indent=2))
