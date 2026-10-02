"""Does the TARGET's net side imbalance carry information? (105 Target-export markets, ids 2648950-2685217, joined to public books; fills are second-stamped; the information would only be usable if Target's fills were visible in real time).
imb(t) = (UP shares - DOWN shares) / (UP + DOWN) over the Target's fills in (t-60, t] (needs >= 30 shares).  AUC of imb for UP winning, versus the AUC of the UP mid itself and within terciles of the mid, at t = 60, 120, 180, 240;
plus the taker edge of following the imbalance (theta .3, every 4 s, quotes at t+1 s, edge=1[win]-ask, per-market mean with market bootstrap CI).  usage: python tools/target_flow_signal.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, random, pathlib, statistics as S
import real_replay_stop as rr
rows = json.loads(gzip.open(sys.argv[1]).read()); M = rr.load(sys.argv[2], {}); by = collections.defaultdict(list); WS = {}
for r in rows: by[int(r['market_id'])].append(r)
win = {m: v[0]['winner'] for m, v in by.items()}
for m in M:
    if m in by: WS[m] = int(rr.jl(next(p for p in pathlib.Path(sys.argv[2]).rglob('public_%d.json.gz' % m) if 'parity' not in str(p)))['market']['window_start_ms']) // 1000
ms = sorted(m for m in M if m in by and win[m] in ('UP', 'DOWN')); print('markets', len(ms))
def imb(m, t, W=60):
    up = dn = 0.
    for r in by[m]:
        s = int(r['event_ms']) // 1000 - WS[m]
        if t - W < s <= t: (up if r['side'] == 'UP' else dn).__class__; up += float(r['shares']) if r['side'] == 'UP' else 0.; dn += float(r['shares']) if r['side'] == 'DOWN' else 0.
    return (up - dn) / (up + dn) if up + dn >= 30 else None
def mid(m, t): b = rr.at(M[m], float(t)); return (b['best_bid'] + b['best_ask']) / 2 if b else None
def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y == 1]; neg = [x for x, y in zip(xs, ys) if y == 0]; return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg)) if pos and neg else float('nan')
print('t | n | AUC(Target imbalance -> UP wins) | AUC(mid) | within mid terciles')
for t in (60, 120, 180, 240):
    z = [(imb(m, t), mid(m, t), 1 if win[m] == 'UP' else 0) for m in ms]; z = [x for x in z if x[0] is not None and x[1] is not None]
    allm = sorted(x[1] for x in z); t1, t2 = allm[len(allm) // 3], allm[2 * len(allm) // 3]
    ter = ' '.join('%.2f(n%d)' % (auc([a for a, _, _ in w], [c for _, _, c in w]), len(w)) for w in ([x for x in z if x[1] < t1], [x for x in z if t1 <= x[1] < t2], [x for x in z if x[1] >= t2]))
    print('%3d | %d | %.3f | %.3f | %s' % (t, len(z), auc([a for a, _, _ in z], [c for _, _, c in z]), auc([b for _, b, _ in z], [c for _, _, c in z]), ter))
rng = random.Random(3)
def edge(m, th):
    w = 1. if win[m] == 'UP' else 0.; es = []
    for t in range(60, 271, 4):
        f = imb(m, t); b = rr.at(M[m], t + 1.)
        if f is None or not b: continue
        au, ad = min(.99, b['best_ask']), min(.99, 1 - b['best_bid'])
        if f >= th: es.append(w - au)
        elif f <= -th: es.append((1 - w) - ad)
    return S.fmean(es) if es else None
xs = [x for x in (edge(m, .3) for m in ms) if x is not None]; bs = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(400))
print('follow Target imbalance (theta .3): edge %+.2f c [%+.2f,%+.2f] (n markets %d)' % (100 * S.fmean(xs), 100 * bs[10], 100 * bs[389], len(xs)))
