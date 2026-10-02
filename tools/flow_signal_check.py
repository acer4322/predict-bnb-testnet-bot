"""Predict TRADE FLOW as a signal (pre-declared 2026-10-02).  From each market's public event file (real V1 events: depth + public trades with aggressor side; trade times are second-granular and shifted by the V1 half-second offset,
trade completeness UNKNOWN) build 1 Hz series from the window start: best bid/ask of the UP book and signed trade quantity (BUY aggressor +, SELL aggressor -, UP-equivalent).  Features at t (every 2 s, 20..270 s):
nOFI_W = sum(signed qty over (t-W, t]) / sum(qty over (t-W, t]) for W in {10, 30} s (needs >= 30 shares of volume).
(1) pooled correlation of nOFI_W(t) with the next-H-second UP mid change (H in {10, 30});  (2) flow-following taker rule: buy UP at the ask (quotes at t+1 s) if nOFI30 >= theta, buy DOWN if <= -theta, theta in {.3,.5,.7},
every 2 s, edge = 1[side wins] - ask, per-market mean (equal weight), market bootstrap CI;  (3) AUC of nOFI30 at t=120 for the final winner versus the AUC of the mid itself.
Discovery = older batches (research-data events feed_v1 labelled markets + fresh batch01), confirmation = fresh batch02 + batch03 (100 markets).  usage: python tools/flow_signal_check.py LABEL_JSON... -- EVENTS_DIR_DISC... -- EVENTS_DIR_CONF..."""
import sys, json, pathlib, random, math, statistics as S
import numpy as np
from hftbacktest import DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT, TRADE_EVENT, BUY_EVENT, SELL_EVENT
parts = ' '.join(sys.argv[1:]).split(' -- '); LAB = parts[0].split(); DISC = parts[1].split(); CONF = parts[2].split()
lab = {}
for f in LAB: lab.update({int(r['market_id']): r['winner'] for r in json.load(open(f))['records']})
def series(p):
    meta = json.load(open(p / 'META.json')); ws = int(meta['window_start_ms']) // 1000; ev = np.load(p / 'events.npz')['data']; order = np.argsort(ev['local_ts'], kind='stable'); ev = ev[order]
    bids, asks = {}, {}; bb = np.full(301, np.nan); ba = np.full(301, np.nan); sg = np.zeros(301); vol = np.zeros(301); sec_idx = 0
    ts = ev['local_ts'] // 1_000_000_000 - ws
    def snap(s):
        bb[s] = max((p_ for p_, q in bids.items() if q > 0), default=np.nan); ba[s] = min((p_ for p_, q in asks.items() if q > 0), default=np.nan)
    last = -1
    for i in range(len(ev)):
        t = int(ts[i]); e = int(ev['ev'][i]); px = float(ev['px'][i]); q = float(ev['qty'][i])
        while last < min(t, 300) - 1 and last < 300:
            last += 1
            if last >= 0: snap(last)
        if (e & TRADE_EVENT) == TRADE_EVENT:
            if 0 <= t <= 300: sg[t] += q if (e & BUY_EVENT) == BUY_EVENT else -q; vol[t] += q
        elif (e & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT or (e & DEPTH_EVENT) == DEPTH_EVENT:
            d = bids if (e & BUY_EVENT) == BUY_EVENT else asks
            if (e & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT and q == 0: pass
            d[px] = q
    while last < 300: last += 1; snap(last)
    return int(p.name), bb, ba, sg, vol
def load(dirs):
    out = {}
    for d in dirs:
        for p in sorted((pathlib.Path(d) / 'markets').iterdir()):
            m = int(p.name)
            if m in lab and m not in out: out[m] = series(p)
    return out
D, C = load(DISC), load(CONF); print('discovery markets', len(D), 'confirmation markets', len(C))
def nofi(sg, vol, t, W):
    v = vol[t - W + 1:t + 1].sum(); return (sg[t - W + 1:t + 1].sum() / v) if v >= 30 else None
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
for nm, ds in (('DISC', D), ('CONF', C)):
    for W in (10, 30):
        for H in (10, 30):
            a = []; b = []
            for m, (_, bb, ba, sg, vol) in ds.items():
                mid = (bb + ba) / 2
                for t in range(20, 271, 2):
                    f = nofi(sg, vol, t, W)
                    if f is None or t + H > 300 or np.isnan(mid[t]) or np.isnan(mid[t + H]): continue
                    a.append(f); b.append(mid[t + H] - mid[t])
            print('%s corr(nOFI_%d, mid change next %2ds) = %+.3f (n=%d)' % (nm, W, H, corr(a, b), len(a)))
rng = random.Random(6)
def ci(xs, nb=400): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
def edge(m, ds, th):
    _, bb, ba, sg, vol = ds[m]; w = 1. if lab[m] == 'UP' else 0.; es = []
    for t in range(30, 271, 2):
        f = nofi(sg, vol, t, 30)
        if f is None or np.isnan(ba[t + 1]) or np.isnan(bb[t + 1]): continue
        au, ad = min(.99, ba[t + 1]), min(.99, 1 - bb[t + 1])
        if f >= th: es.append(w - au)
        elif f <= -th: es.append((1 - w) - ad)
    return S.fmean(es) if es else None
print('\nflow-following taker edge (cents/share, per-market mean, market bootstrap CI)')
for th in (.3, .5, .7):
    row = []
    for ds in (D, C):
        xs = [x for x in (edge(m, ds, th) for m in ds) if x is not None]; lo, hi = ci(xs) if len(xs) > 10 else (float('nan'),) * 2; row.append('%3d %+6.2f [%+6.2f,%+6.2f]' % (len(xs), 100 * S.fmean(xs), 100 * lo, 100 * hi))
    print('theta %.1f | disc %s | conf %s' % (th, row[0], row[1]))
def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y == 1]; neg = [x for x, y in zip(xs, ys) if y == 0]; return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))
print()
for nm, ds in (('DISC', D), ('CONF', C)):
    z = [(nofi(sg, vol, 120, 30), (bb[120] + ba[120]) / 2, 1 if lab[m] == 'UP' else 0) for m, (_, bb, ba, sg, vol) in ds.items() if nofi(sg, vol, 120, 30) is not None and not np.isnan(bb[120]) and not np.isnan(ba[120])]
    print('%s t=120: AUC nOFI30 -> UP wins %.3f | AUC mid -> UP wins %.3f (n=%d)' % (nm, auc([a for a, _, _ in z], [c for _, _, c in z]), auc([b for _, b, _ in z], [c for _, _, c in z]), len(z)))
