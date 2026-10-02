"""Target-like share-neutral two-sided ladder WITH a spot guard (our improvement candidate).  Why: the Target's maker fills lose when Binance spot moved against the bid side just before (-2.8 c/share),
its taker fills win in that state (+5.8 c): resting bids get picked off by latency traders.  Guard: every 200 ms, if the spot log return over the last D ms is <= -X bp, pull the UP bids (>= +X bp: pull the DOWN bids),
and do not re-quote that side for HOLD ms; cancels take effect only after LC ms (our latency).  Quoting otherwise as ladder_front_queue_bound (30-sh bids at best bid - k, k<LV, both sides, side paused while it leads
by > G shares, stop at 270 s, taker completion to equal shares at 275 s).  Fill rule: BACK of queue (pessimistic, displayed queue ahead must trade first) or FRONT (optimistic).
V1 event times are shifted by -SHIFT ms (default 900; V1 local_ts lags venue source_ms by ~0.7-1.0 s) to align with Binance aggTrade time.
usage: python tools/ladder_spot_guard.py LABELS.json AGGTRADES.json.gz EVENTS_BATCH_DIR..."""
import sys, json, gzip, math, os, random, statistics as S
from pathlib import Path
import numpy as np
from hftbacktest import DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT
from event_series import series
lab = {int(r['market_id']): r['winner'] for r in json.load(open(sys.argv[1]))['records']}; agg = json.load(gzip.open(sys.argv[2], 'rt')); dirs = sys.argv[3:]
SHIFT = int(os.environ.get('SHIFT', '900'))
def run(p, LV, G, front, D, X, LC, HOLD):
    meta = json.load(open(p / 'META.json')); ws = int(meta['window_start_ms']); ev = np.load(p / 'events.npz')['data']; ev = ev[np.argsort(ev['local_ts'], kind='stable')]
    a = np.array(agg[p.name]); ST = a[:, 0].astype(np.int64); SPX = a[:, 1]
    def spot(t): i = np.searchsorted(ST, t, side='right') - 1; return SPX[i] if i >= 0 else np.nan
    bids, asks = {}, {}; orders = []; sh = {'UP': 0., 'DOWN': 0.}; cost = 0.; nxt = ws + 200; w = lab[int(p.name)]; pause = {'UP': -1, 'DOWN': -1}; fills = 0
    def best():
        return max((x for x, q in bids.items() if q > 0), default=None), min((x for x, q in asks.items() if q > 0), default=None)
    def lvl(side, px): return bids.get(px, 0.) if side == 'UP' else asks.get(round(1 - px, 2), 0.)
    for e in ev:
        ms = int(e['local_ts']) // 1_000_000 - SHIFT; f = int(e['ev']); px = round(float(e['px']), 2); q = float(e['qty'])
        while ms >= nxt and nxt < ws + 290_000:
            t = nxt; sec = (t - ws) / 1000.; bb, ba = best()
            if X > 0:
                s0, s1 = spot(t - D), spot(t)
                if s0 == s0 and s1 == s1:
                    r = math.log(s1 / s0) * 1e4
                    for side, bad in (('UP', r <= -X), ('DOWN', r >= X)):
                        if bad:
                            pause[side] = t + HOLD
                            for o in orders:
                                if o['side'] == side and o.get('cx') is None: o['cx'] = t + LC
            if (t - ws) % 1000 == 0 and bb is not None and ba is not None and bb < ba:
                want = {'UP': bb, 'DOWN': round(1 - ba, 2)}
                for side in ('UP', 'DOWN'):
                    opp = 'DOWN' if side == 'UP' else 'UP'; ok = sec < 270 and sh[side] - sh[opp] <= G and t >= pause[side]
                    prices = {round(want[side] - .01 * k, 2) for k in range(LV)} if ok else set()
                    for o in orders:
                        if o['side'] == side and o['px'] not in prices and o.get('cx') is None: o['cx'] = t + LC
                    have = {o['px'] for o in orders if o['side'] == side and o.get('cx') is None}
                    for x in prices - have:
                        if x >= .02: orders.append(dict(side=side, px=x, rem=30., qa=lvl(side, x), live_at=t + LC))
                if 275 <= sec < 276:
                    g = sh['UP'] - sh['DOWN']
                    if abs(g) >= 1: s_ = 'DOWN' if g > 0 else 'UP'; aa = ba if s_ == 'UP' else round(1 - bb, 2); sh[s_] += abs(g); cost += abs(g) * min(.99, aa)
            orders[:] = [o for o in orders if o.get('cx') is None or o['cx'] > t]
            nxt += 200
        if (f & TRADE_EVENT) == TRADE_EVENT:
            sell = (f & SELL_EVENT) == SELL_EVENT
            for o in orders:
                if o['rem'] <= 0 or ms < o['live_at'] or (o.get('cx') is not None and ms >= o['cx']): continue
                lp = o['px'] if o['side'] == 'UP' else round(1 - o['px'], 2)
                hit = (o['side'] == 'UP' and sell and px <= lp + 1e-9) or (o['side'] == 'DOWN' and not sell and px >= lp - 1e-9)
                if not hit: continue
                avail = q
                if not front:
                    if abs(px - lp) < 1e-9: use = min(o['qa'], avail); o['qa'] -= use; avail -= use
                    else: o['qa'] = 0.
                x = min(o['rem'], avail)
                if x > 0: o['rem'] -= x; sh[o['side']] += x; cost += x * o['px']; fills += 1
            orders[:] = [o for o in orders if o['rem'] > 0]
        elif (f & DEPTH_EVENT) == DEPTH_EVENT or (f & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT:
            (bids if (f & BUY_EVENT) == BUY_EVENT else asks)[px] = q
    return sh[w] - cost, sh, cost
mk = []
for d in dirs:
    for p in sorted((Path(d) / 'markets').iterdir()):
        if int(p.name) in lab and p.name in agg and len(agg[p.name]) > 100: mk.append(p)
CL = {}
for p in mk:
    _, bb, ba, _, _ = series(p); mid = (bb + ba) / 2; m12 = mid[12] if mid[12] == mid[12] else .5; F = 'UP' if m12 >= .5 else 'DOWN'
    fm = mid if F == 'UP' else 1 - mid; flip = any(x <= .4 for x in fm[13:290] if x == x); CL[int(p.name)] = 'NO_FLIP' if not flip else 'FALSE_FLIP' if lab[int(p.name)] == F else 'TRUE_FLIP'
ids = sorted(int(p.name) for p in mk); n = len(ids); DISC = set(ids[:int(.6 * n)]); rng = random.Random(8)
GRID = [(0., 0, 0, 0)] + [(X, D, LC, HOLD) for X in (.5, 1., 2.) for D in (500, 1000) for LC in (300, 600) for HOLD in (1000, 3000)]
if os.environ.get('QUICK'): GRID = GRID[:1] + [(1., 500, 300, 3000), (.5, 500, 300, 3000), (1., 1000, 600, 3000)]
print('markets', len(mk), 'discovery', len(DISC))
for front in (False, True):
    for X, D, LC, HOLD in GRID:
        R = {int(p.name): run(p, 1, 90, front, D, X, LC, HOLD) for p in mk}
        out = []
        for nm, sub in (('DISC', [m for m in ids if m in DISC]), ('CONF', [m for m in ids if m not in DISC])):
            pn = [R[m][0] for m in sub]; mn = {k: S.fmean([R[m][0] for m in sub if CL[m] == k] or [0]) for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
            pc = sum(R[m][2] for m in sub) / max(1e-9, sum(min(R[m][1].values()) for m in sub)); b = sorted(S.fmean(rng.choice(pn) for _ in pn) for _ in range(600))
            out.append('%s %+6.1f[%+.1f,%+.1f] pc %.4f NO %+.1f FA %+.1f TR %+.1f' % (nm, S.fmean(pn), b[15], b[584], pc, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP']))
        print('%-5s guard X=%.1f D=%4d LC=%3d HOLD=%4d | %s' % ('FRONT' if front else 'BACK', X, D, LC, HOLD, ' || '.join(out)), flush=True)
