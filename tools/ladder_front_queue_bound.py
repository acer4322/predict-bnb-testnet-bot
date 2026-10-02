"""OPTIMISTIC upper bound for a Target-like two-sided ladder: assume our resting bids are always FIRST in the queue (the Target data show >=half of its maker qty filled with less public trading at
its level than the displayed queue ahead, i.e. it behaves as if at the front).  Replay on real V1 events (200 labelled BTC markets): every 1 s keep 30-sh bids at best bid - k ticks (k < LV) on BOTH sides
(DOWN bid at p == UP ask level 1-p); a side is paused while it leads by > G shares; quoting stops at 270 s; at 275 s buy the short side at the ask to equalise shares (C) or not (N).
Fill rule (front of queue): a resting bid at p fills from any public trade that hits price <= p on its side (SELL aggressor at UP bid level p for UP; BUY aggressor at UP ask level 1-p for DOWN), up to the trade qty,
or fully if the best level crosses through p.  Same markets also with the PESSIMISTIC rule (fill only after the displayed queue ahead at placement has traded) for comparison.
usage: python tools/ladder_front_queue_bound.py LABELS.json EVENTS_DIR... """
import sys, json, collections, statistics as S, random
from pathlib import Path
import numpy as np
from hftbacktest import DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT
lab = {int(r['market_id']): r['winner'] for r in json.load(open(sys.argv[1]))['records']}; dirs = sys.argv[2:]
def run(p, LV, G, comp, front):
    meta = json.load(open(p / 'META.json')); ws = int(meta['window_start_ms']); ev = np.load(p / 'events.npz')['data']; ev = ev[np.argsort(ev['local_ts'], kind='stable')]
    bids, asks = {}, {}; orders = []  # dict side, px, rem, qa (queue ahead for pessimistic)
    sh = {'UP': 0., 'DOWN': 0.}; cost = 0.; next_dec = ws + 1000; w = lab[int(p.name)]
    def best():
        bb = max((x for x, q in bids.items() if q > 0), default=None); ba = min((x for x, q in asks.items() if q > 0), default=None); return bb, ba
    def lvl_size(side, px): return bids.get(px, 0.) if side == 'UP' else asks.get(round(1 - px, 2), 0.)
    for e in ev:
        ms = int(e['local_ts']) // 1_000_000; f = int(e['ev']); px = round(float(e['px']), 2); q = float(e['qty'])
        while ms >= next_dec and next_dec < ws + 290_000:
            sec = (next_dec - ws) / 1000.; bb, ba = best()
            if bb is not None and ba is not None and bb < ba:
                want = {'UP': bb, 'DOWN': round(1 - ba, 2)}
                for side in ('UP', 'DOWN'):
                    opp = 'DOWN' if side == 'UP' else 'UP'; ok = sec < 270 and sh[side] - sh[opp] <= G
                    prices = {round(want[side] - .01 * k, 2) for k in range(LV)} if ok else set()
                    orders[:] = [o for o in orders if not (o['side'] == side and o['px'] not in prices)]
                    have = {o['px'] for o in orders if o['side'] == side}
                    for x in prices - have:
                        if x >= .02: orders.append(dict(side=side, px=x, rem=30., qa=lvl_size(side, x), t0=next_dec))
                if comp and 275 <= sec < 276:
                    g = sh['UP'] - sh['DOWN']
                    if abs(g) >= 1: s_ = 'DOWN' if g > 0 else 'UP'; a = ba if s_ == 'UP' else round(1 - bb, 2); sh[s_] += abs(g); cost += abs(g) * min(.99, a)
            next_dec += 1000
        if (f & TRADE_EVENT) == TRADE_EVENT:
            sell = (f & SELL_EVENT) == SELL_EVENT
            for o in orders:
                if o['rem'] <= 0: continue
                hit = (o['side'] == 'UP' and sell and px <= o['px'] + 1e-9) or (o['side'] == 'DOWN' and not sell and px >= round(1 - o['px'], 2) - 1e-9)
                if not hit: continue
                avail = q
                if not front:
                    if abs(px - (o['px'] if o['side'] == 'UP' else round(1 - o['px'], 2))) < 1e-9:
                        use = min(o['qa'], avail); o['qa'] -= use; avail -= use
                    else: o['qa'] = 0.   # traded through our level
                x = min(o['rem'], avail)
                if x > 0: o['rem'] -= x; sh[o['side']] += x; cost += x * o['px']; FILLS.append((int(p.name), ms, o['side'], x, o['px'], ms - o['t0']))
            orders[:] = [o for o in orders if o['rem'] > 0]
        elif (f & DEPTH_EVENT) == DEPTH_EVENT or (f & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT:
            (bids if (f & BUY_EVENT) == BUY_EVENT else asks)[px] = q
    return sh[w] - cost, sh, cost
mk = []
for d in dirs:
    for p in sorted((Path(d) / 'markets').iterdir()):
        if int(p.name) in lab and (p / 'events.npz').exists(): mk.append(p)
# class from 1 Hz mid path, reuse event_series
from event_series import series
CL = {}
for p in mk:
    _, bb, ba, _, _ = series(p); mid = (bb + ba) / 2; m12 = mid[12] if mid[12] == mid[12] else .5; F = 'UP' if m12 >= .5 else 'DOWN'
    fm = mid if F == 'UP' else 1 - mid; flip = any(x <= .4 for x in fm[13:290] if x == x); w = lab[int(p.name)]
    CL[int(p.name)] = 'NO_FLIP' if not flip else 'FALSE_FLIP' if w == F else 'TRUE_FLIP'
rng = random.Random(3); FILLS = []
import os
CFG = [(1, 90, True)] if os.environ.get('DUMP') else [(1, 90, True), (3, 90, True), (3, 300, True), (3, 90, False)]
for front in ((True,) if os.environ.get('DUMP') else (True, False)):
    for LV, G, comp in CFG:
        R = {int(p.name): run(p, LV, G, comp, front) for p in mk}; pn = {m: r[0] for m, r in R.items()}
        mn = {k: S.fmean([pn[m] for m in pn if CL[m] == k]) for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; x = list(pn.values()); b = sorted(S.fmean(rng.choice(x) for _ in x) for _ in range(1000))
        pc = sum(r[2] for r in R.values()) / sum(min(r[1].values()) for r in R.values() if min(r[1].values()) > 0)
        print('%-11s LV%d G%-3d %s | NO %+6.1f FALSE %+6.1f TRUE %+6.1f | overall %+6.1f CI[%+.1f,%+.1f] | cost/mkt %.0f | pair cost %.4f | |imb|/T end %.2f' % ('FRONT' if front else 'BACK(queue)', LV, G, 'C' if comp else 'N', mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(x), b[25], b[974], S.fmean(r[2] for r in R.values()), pc, S.fmean(abs(r[1]['UP'] - r[1]['DOWN']) / max(1, r[1]['UP'] + r[1]['DOWN']) for r in R.values())))

if os.environ.get('DUMP'): json.dump(FILLS, open(os.environ['DUMP'], 'w'))
