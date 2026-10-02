"""Did the Target's maker fills require queue priority?  (no engine; raw V1 events of the 15 Target-lifecycle markets).
For each inferred Target maker parent (side, price p, placement_first_ms, resting_ms, filled_qty):  level on the UP book = bid p (UP) or ask 1-p (DOWN buy == UP sell side).
Q0 = displayed size at that level just BEFORE placement (queue ahead if Target joined at the back), V = public trade volume executed AT that level from placement to fill (placement+resting),
Q1 = displayed size at the fill instant.  Risk-adverse engine queue: Target fills only after V >= Q0 (+its own qty).  Reports share of filled qty with V < Q0 (fill impossible at the back of the queue
by trades alone -> needs queue priority or cancellations ahead), and the distribution of V/Q0; also how many fills happened with the level still standing (Q1 > 0) vs level wiped.
usage: python tools/target_queue_priority.py target_lifecycle_features.json.gz EVENTS_BATCH_DIR..."""
import sys, json, gzip, collections, bisect
from pathlib import Path
import numpy as np
from hftbacktest import DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT, DEPTH_CLEAR_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT
feat = collections.defaultdict(list)
for r in json.load(gzip.open(sys.argv[1])): feat[int(r['market_id'])].append(r)
dirs = sys.argv[2:]; res = []
for d in dirs:
    for p in sorted((Path(d) / 'markets').iterdir()):
        m = int(p.name)
        if m not in feat: continue
        ev = np.load(p / 'events.npz')['data']; ev = ev[np.argsort(ev['local_ts'], kind='stable')]
        # book snapshots as level dicts over time (store level size history per (side, price))
        hist = collections.defaultdict(list); trades = collections.defaultdict(list)   # key (bookside, px) -> [(ms, size)] ; trades key (bookside, px) -> [(ms, qty)]
        for e in ev:
            f = int(e['ev']); ms = int(e['local_ts']) // 1_000_000; px = round(float(e['px']), 2); q = float(e['qty'])
            if (f & TRADE_EVENT) == TRADE_EVENT:
                # aggressor SELL hits the bid; aggressor BUY lifts the ask
                trades[('bid' if (f & SELL_EVENT) == SELL_EVENT else 'ask', px)].append((ms, q))
            elif (f & DEPTH_EVENT) == DEPTH_EVENT or (f & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT:
                hist[('bid' if (f & BUY_EVENT) == BUY_EVENT else 'ask', px)].append((ms, q))
        def size_at(key, ms):
            h = hist.get(key, []); i = bisect.bisect_right([x[0] for x in h], ms) - 1; return h[i][1] if i >= 0 else 0.
        for r in feat[m]:
            if r['placement_first_ms'] is None or r['resting_ms'] is None: continue
            key = ('bid', round(r['price'], 2)) if r['side'] == 'UP' else ('ask', round(1 - r['price'], 2))
            t0 = int(r['placement_first_ms']); t1 = t0 + int(r['resting_ms'])
            Q0 = size_at(key, t0 - 1); Q1 = size_at(key, t1 + 1)
            import os; W0, W1 = int(os.environ.get('W0', '0')), int(os.environ.get('W1', '1000')); V = sum(q for ms, q in trades.get(key, []) if t0 - W0 <= ms <= t1 + W1)
            res.append(dict(m=m, Q0=Q0, V=V, Q1=Q1, q=r['filled_qty'], conf=r['confidence'], off=round(((r['best_bid_at_placement'] or r['price']) - r['price']) * 100)))
print('orders analysed', len(res), 'markets', len({x['m'] for x in res}))
tot = sum(x['q'] for x in res)
for nm, f in (('Q0 == 0 (Target first at a new/empty level)', lambda x: x['Q0'] <= 1e-9), ('V >= Q0 + q (fill explainable at back of queue by trades)', lambda x: x['Q0'] > 0 and x['V'] >= x['Q0'] + x['q'] - 1e-6),
              ('V < Q0 + q (needs priority / cancels ahead)', lambda x: x['Q0'] > 0 and x['V'] < x['Q0'] + x['q'] - 1e-6), ('no public trade at level within fill window', lambda x: x['V'] <= 1e-9)):
    s = [x for x in res if f(x)]; print('  %-62s %5.1f%% of filled qty (%d orders)' % (nm, 100 * sum(x['q'] for x in s) / tot, len(s)))
r2 = sorted(x['V'] / x['Q0'] for x in res if x['Q0'] > 0); print('V/Q0 for orders joining a non-empty level: q25 %.2f q50 %.2f q75 %.2f' % (r2[len(r2) // 4], r2[len(r2) // 2], r2[3 * len(r2) // 4]))
print('Q0 (queue ahead, shares) q25/50/75: %.0f %.0f %.0f' % tuple(sorted(x['Q0'] for x in res)[int(p * (len(res) - 1))] for p in (.25, .5, .75)))
print('level still standing after fill (Q1>0): %.0f%% of qty' % (100 * sum(x['q'] for x in res if x['Q1'] > 0) / tot))
for o in (0, 1, 2, 3):
    s = [x for x in res if x['off'] == o and x['Q0'] > 0]
    if s: print('  offset %d: n=%d share needing priority %.0f%%  median Q0 %.0f' % (o, len(s), 100 * sum(x['q'] for x in s if x['V'] < x['Q0'] + x['q'] - 1e-6) / sum(x['q'] for x in s), sorted(x['Q0'] for x in s)[len(s) // 2]))
