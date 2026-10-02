"""Cross-venue check: Polymarket BTC 5-minute market (same window, slug btc-updown-5m-<window_start>) vs the Predict book.  PM UP price path = last executed UP-equivalent trade price per second (forward filled; trades are second-stamped).
(1) agreement of the final PM outcome with our label; (2) Brier of PM price vs Predict mid vs both at t=12,60,120,180,240 (is PM better calibrated?); (3) lead-lag: corr of 10 s changes of PM price (lagged k s) with the next 10 s change of the Predict UP mid;
(4) value of buying on Predict the side that PM prices higher by > delta (taker at ask, quotes t+1 s), every 2 s, per-market equal weight, discovery vs confirmation halves.   usage: python tools/pm_cross_venue.py PUBLIC_ROOT LABELS.json pm.json.gz"""
import sys, json, gzip, pathlib, math, random, statistics as S, bisect
sys.argv, PMF = sys.argv[:3], sys.argv[3]
import entry_timing_wf as E
import real_replay_stop as rr
P, M, ids = E.P, E.M, E.ids
pm = {int(k): v for k, v in json.load(gzip.open(PMF, 'rt')).items()}
WS = {}
for p in pathlib.Path(sys.argv[1]).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
def path(m):
    r = pm.get(WS[m]); 
    if not r or r['n'] < 30: return None
    tr = sorted(r['trades']); ts = [x[0] for x in tr]; out = {}; last = None; j = 0
    for t in range(-60, 301):
        sec = WS[m] + t
        while j < len(tr) and tr[j][0] <= sec: last = tr[j][1]; j += 1
        out[t] = last
    return out
PP = {m: path(m) for m in ids}; have = [m for m in ids if PP[m] and PP[m][300] is not None]
agree = sum((PP[m][300] > .5) == (M[m]['win'] == 'UP') for m in have if abs(PP[m][300] - .5) > .3); nn = sum(abs(PP[m][300] - .5) > .3 for m in have)
print('markets with PM trades %d of %d | PM final price side agrees with our label: %.1f%% (n=%d decisive)' % (len(have), len(ids), 100 * agree / nn, nn))
print('\nBrier (lower better): Predict mid | PM last price | blend')
for t in (12, 60, 120, 180, 240):
    z = [(P[m][t][0], PP[m][t], 1. if M[m]['win'] == 'UP' else 0.) for m in have if PP[m][t] is not None]
    print('t=%3ds n=%d  predict %.4f  PM %.4f  blend %.4f' % (t, len(z), S.fmean((a - w) ** 2 for a, b, w in z), S.fmean((b - w) ** 2 for a, b, w in z), S.fmean(((a + b) / 2 - w) ** 2 for a, b, w in z)))
print('\nlead-lag: corr( PM 10s change ending at t-k , Predict mid change t..t+10 ), pooled, t every 10 s from 20 to 270')
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
for k in (0, 2, 5, 10, -5, -10):
    a = []; b = []
    for m in have:
        for t in range(20, 271, 10):
            x0, x1 = PP[m].get(t - k - 10), PP[m].get(t - k)
            if x0 is None or x1 is None: continue
            a.append(x1 - x0); b.append(P[m][t + 10][0] - P[m][t][0])
    print('  PM leads by k=%3ds: corr %.3f (n=%d)' % (k, corr(a, b), len(a)))
rng = random.Random(2); half = len(ids) // 2; Dm, Tm = set(ids[:half]), set(ids[half:])
def ci(xs, nb=300): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
def edge(m, d):
    es = []; w = 1. if M[m]['win'] == 'UP' else 0.
    for t in range(12, 290, 2):
        pmp = PP[m].get(t)
        if pmp is None: continue
        A = P[m][t]; au, ad = min(.99, A[1][0]), min(.99, A[2][0])
        if pmp - au > d: es.append(w - au)
        elif (1 - pmp) - ad > d: es.append((1 - w) - ad)
    return S.fmean(es) if es else None
print('\nbuy on Predict the side PM prices above its ask by > delta: edge cents/share (discovery | confirmation)')
for d in (.02, .04, .06, .10):
    out = []
    for sub in (Dm, Tm):
        xs = [edge(m, d) for m in have if m in sub]; xs = [x for x in xs if x is not None]; lo, hi = ci(xs) if len(xs) > 10 else (float('nan'),) * 2
        out.append('%3d %+6.2f [%+6.2f,%+6.2f]' % (len(xs), 100 * S.fmean(xs), 100 * lo, 100 * hi))
    print('  delta %.2f | %s | %s' % (d, out[0], out[1]))
