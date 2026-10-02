"""Simple 1 Hz replay vs the HFT engine on the SAME markets and the SAME rules (FAV freeze band .55-.70 clip 15 cap 300; UNDERDOG fav mid >= .75 clip 15 cap 300).  Both start from the real event files (the simple replay rebuilds a 1 Hz best bid/ask
from the depth events, quotes at t+1 s, taker at the displayed ask, no latency/queue/impact; the engine = strategy_lab FAV_TAKER / UNDER_TAKER with 250/250 ms latency and the risk queue; engine FAV stops buying at 270 s, so the simple FAV uses 270 s too).
Per market: PnL difference engine - simple, correlation, average fill price difference, share difference.   usage: python tools/simple_vs_engine.py LABELS... -- ENGINE_JSON:EVENTS_DIR... """
import sys, json, math, statistics as S
import numpy as np
parts = ' '.join(sys.argv[1:]).split(' -- '); LAB = parts[0].split(); PAIRS = [x.split(':') for x in parts[1].split()]
import event_series as F
F.lab = {}
for f_ in LAB: F.lab.update({int(r['market_id']): r['winner'] for r in json.load(open(f_))['records']})
import pathlib
def simple(bb, ba, win, strat, stop):
    cap, clip = 300., 15.; sh = cost = 0.; fav = None; frozen = False; mids = {}
    for t in range(12, 290, 2):
        if np.isnan(bb[t]) or np.isnan(ba[t]) or np.isnan(bb[t + 1]) or np.isnan(ba[t + 1]): continue
        m = (bb[t] + ba[t]) / 2; mids[t] = m
        if fav is None: fav = 'UP' if m >= .5 else 'DOWN'
        if strat == 'FAV':
            fm = m if fav == 'UP' else 1 - m
            if not frozen and t > 12 and fm <= .4: frozen = True
            if frozen or t >= stop or sh >= cap - 1e-9: continue
            cur = 'UP' if m >= .5 else 'DOWN'; cm = m if cur == 'UP' else 1 - m
            if not (.55 <= fm <= .70 and .55 <= cm <= .70): continue
            side = cur
        else:
            cur = 'UP' if m >= .5 else 'DOWN'; cm = m if cur == 'UP' else 1 - m
            if cm < .75 or sh >= cap - 1e-9: continue
            side = 'DOWN' if cur == 'UP' else 'UP'
        px = min(.99, ba[t + 1]) if side == 'UP' else min(.99, 1 - bb[t + 1]); q = min(clip, cap - sh); sh += q; cost += q * px; hold = 'UP' if side == 'UP' else 'DOWN'
        if strat == 'FAV': pass
        # settle later (per-side holdings)
        simple.hold.append((side, q, px))
    pnl = sum(q * ((1. if s == win else 0.) - p) for s, q, p in simple.hold); simple.hold = []; return pnl, sh, cost
simple.hold = []
rows = []
for pair in PAIRS:
    ej, ed = pair; res = json.load(open(ej)); eng = {s: {r['market']: r for r in res[s]} for s in ('FAV_TAKER', 'UNDER_TAKER')}
    lab = F.lab; base = pathlib.Path(ed)
    for m in sorted(eng['FAV_TAKER']):
        p = base / str(m)
        if not (p / 'META.json').exists(): continue
        _, bb, ba, sg, vol = F.series(p)
        if m not in lab: continue
        win = lab[m]
        for strat, key, stop in (('FAV', 'FAV_TAKER', 270), ('UNDER', 'UNDER_TAKER', 290)):
            e = eng[key][m]; pnl, sh, cost = simple(bb, ba, win, strat, stop); esh = e['up'] + e['dn']
            rows.append(dict(m=m, strat=strat, cls=e['cls'], eng=e['pnl'], sim=pnl, esh=esh, ssh=sh, epx=(e['cost'] / esh) if esh else None, spx=(cost / sh) if sh else None))
print('markets', len({r['m'] for r in rows}))
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
for strat in ('FAV', 'UNDER'):
    rs = [r for r in rows if r['strat'] == strat]; d = [r['eng'] - r['sim'] for r in rs]
    print('\n%s (n=%d): mean PnL engine %.1f | simple %.1f | diff %.1f (sd %.1f) | corr %.3f | |diff|>10: %.0f%% | mean shares engine %.0f simple %.0f' % (strat, len(rs), S.fmean(r['eng'] for r in rs), S.fmean(r['sim'] for r in rs), S.fmean(d), S.pstdev(d), corr([r['eng'] for r in rs], [r['sim'] for r in rs]), 100 * sum(abs(x) > 10 for x in d) / len(d), S.fmean(r['esh'] for r in rs), S.fmean(r['ssh'] for r in rs)))
    px = [(r['epx'], r['spx']) for r in rs if r['epx'] and r['spx']]; print('   avg fill price: engine %.4f simple %.4f (diff %+.4f per share) | n=%d' % (S.fmean(a for a, _ in px), S.fmean(b for _, b in px), S.fmean(a - b for a, b in px), len(px)))
    for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        z = [r for r in rs if r['cls'] == c]; print('   %-10s n=%3d engine %7.1f simple %7.1f diff %6.1f' % (c, len(z), S.fmean(r['eng'] for r in z), S.fmean(r['sim'] for r in z), S.fmean(r['eng'] - r['sim'] for r in z)))
