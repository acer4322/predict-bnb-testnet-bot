"""Post-hoc diagnostic of the flow signal found in flow_signal_check.py (AUC of nOFI30 for the winner below .5).  Does the flow carry information BEYOND the mid?  For t in {60,120,180,240}: AUC of nOFI30 (and nOFI10) for UP winning,
overall and within terciles of the UP mid (terciles computed on the pooled markets), for discovery and confirmation.  Also the contrarian (fade) taker rule: if nOFI30 >= th buy DOWN, <= -th buy UP; edge per share, per-market equal weight.
Everything here is exploratory on data already seen (confirmation markets included); a formal test needs new markets."""
import sys
sys.argv = [sys.argv[0]] + sys.argv[1:]
import flow_signal_check as F
import numpy as np, statistics as S, random
D, C, lab, nofi = F.D, F.C, F.lab, F.nofi
def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y == 1]; neg = [x for x, y in zip(xs, ys) if y == 0]
    return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg)) if pos and neg and len(pos) * len(neg) else float('nan')
def rows(ds, t, W):
    out = []
    for m, (_, bb, ba, sg, vol) in ds.items():
        f = nofi(sg, vol, t, W)
        if f is None or np.isnan(bb[t]) or np.isnan(ba[t]): continue
        out.append((f, (bb[t] + ba[t]) / 2, 1 if lab[m] == 'UP' else 0))
    return out
print('t | W | AUC(nOFI->UP) disc / conf | within mid terciles (low/mid/high) conf')
for t in (60, 120, 180, 240):
    for W in (10, 30):
        rd, rc = rows(D, t, W), rows(C, t, W); allm = sorted(r[1] for r in rd + rc); t1, t2 = allm[len(allm) // 3], allm[2 * len(allm) // 3]
        terc = []
        for lo, hi in ((0, t1), (t1, t2), (t2, 2)):
            z = [r for r in rc if lo <= r[1] < hi]; terc.append('%.2f(n%d)' % (auc([a for a, _, _ in z], [c for _, _, c in z]), len(z)))
        print('%3d| %2d | %.3f / %.3f | %s' % (t, W, auc([a for a, _, _ in rd], [c for _, _, c in rd]), auc([a for a, _, _ in rc], [c for _, _, c in rc]), ' '.join(terc)))
rng = random.Random(8)
def edge(m, ds, th):
    _, bb, ba, sg, vol = ds[m]; w = 1. if lab[m] == 'UP' else 0.; es = []
    for t in range(30, 271, 2):
        f = nofi(sg, vol, t, 30)
        if f is None or np.isnan(ba[t + 1]) or np.isnan(bb[t + 1]): continue
        au, ad = min(.99, ba[t + 1]), min(.99, 1 - bb[t + 1])
        if f >= th: es.append((1 - w) - ad)
        elif f <= -th: es.append(w - au)
    return S.fmean(es) if es else None
def ci(xs, nb=400): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
print('\nFADE (contrarian) taker edge cents/share: disc | conf')
for th in (.2, .3, .5, .7):
    row = []
    for ds in (D, C):
        xs = [x for x in (edge(m, ds, th) for m in ds) if x is not None]; lo, hi = ci(xs); row.append('%3d %+6.2f [%+6.2f,%+6.2f]' % (len(xs), 100 * S.fmean(xs), 100 * lo, 100 * hi))
    print('th %.1f | %s | %s' % (th, row[0], row[1]))
