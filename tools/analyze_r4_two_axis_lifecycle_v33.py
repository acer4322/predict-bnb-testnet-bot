import json
from pathlib import Path
from collections import Counter

B = Path('data/research/r4_v0/p0_provenance_v1')
adv = json.loads((B/'r4_adverse_selection_v30_information_score.json').read_text(encoding='utf-8'))
q = json.loads((B/'r4_carrier_queuepath_v32_3_labeled_anatomy.json').read_text(encoding='utf-8'))
amap = {
    int(x[0]): {'cohort': x[1], 'label': x[2], 'deltaPnl': x[3], 'adverse': x[4]}
    for x in adv['orderedCases']
}
rows = []
for r in q['rows']:
    m = int(r['marketId'])
    if m not in amap:
        continue
    p = r['queuePath']['original']['1000']
    qs = p.get('queueStartShares')
    qc = p.get('queueChangeShares')
    decay = None if qs is None or qs <= 0 else -qc / qs
    a = amap[m]['adverse']
    if decay is None:
        state = 'UNDEFINED_QUEUE_START'
    elif a <= 0 and decay >= 0:
        state = 'LOW_ADVERSE__QUEUE_ADVANCING_OR_FLAT'
    elif a <= 0 and decay < 0:
        state = 'LOW_ADVERSE__QUEUE_REBUILDING'
    elif a > 0 and decay >= 0:
        state = 'HIGH_ADVERSE__QUEUE_ADVANCING_OR_FLAT'
    else:
        state = 'HIGH_ADVERSE__QUEUE_REBUILDING'
    rows.append({
        'marketId': m, 'cohort': amap[m]['cohort'], 'label': amap[m]['label'],
        'deltaPnl': amap[m]['deltaPnl'], 'adverse': a,
        'queueDecay1s': decay, 'state': state,
    })

stats = {}
for s in sorted({r['state'] for r in rows}):
    rr = [r for r in rows if r['state'] == s]
    ben = sum(r['label'] == 'BENEFICIAL' for r in rr)
    stats[s] = {
        'n': len(rr), 'beneficial': ben, 'harmful': len(rr)-ben,
        'beneficialRate': ben/len(rr),
        'deltaPnlSum': sum(r['deltaPnl'] for r in rr),
        'cohorts': dict(Counter(r['cohort'] for r in rr)),
        'markets': [r['marketId'] for r in rr],
    }

out = {
    'version': 'R4_TWO_AXIS_LIFECYCLE_V33_ANATOMY',
    'researchOnly': True,
    'preregistered': 'r4_two_axis_lifecycle_v33_preregistered.json',
    'nAdverseComplete': len(amap), 'nJoined': len(rows),
    'stateStats': stats, 'rows': rows,
    'boundaryNote': 'Both boundaries were frozen natural zero points before this label join; no tuning performed.'
}
path = B/'r4_two_axis_lifecycle_v33_anatomy.json'
path.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'artifact': str(path), 'n': len(rows), 'stats': stats}, ensure_ascii=False))
