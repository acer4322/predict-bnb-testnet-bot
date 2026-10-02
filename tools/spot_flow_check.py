"""Spot order flow (Binance BTCUSDT 1 s klines, taker-buy quote volume) as a signal for the Predict market (pre-declared 2026-10-02).  spot OFI_W(t) = (2*taker_buy_quote - quote_volume) / quote_volume summed over the last W seconds,
W in {10, 30}.  (1) pooled correlation of OFI_W(t) with (a) the next-H-second SPOT return and (b) the next-H-second Predict UP mid change (H in {10, 30}); (2) taker rule: buy UP at the ask (t+1 s quotes) if OFI30 >= theta, DOWN if <= -theta,
theta in {.1,.2,.3}, every 4 s from 30 to 270 s, edge = 1[win]-ask per-market mean, market bootstrap CI, first 60% (discovery) vs last 40% (confirmation) of the 519 true-labelled markets.   usage: python tools/spot_flow_check.py PUBLIC_ROOT LABELS.json btc_1s_tb.json.gz"""
import sys, json, gzip, math, pathlib, random, statistics as S
sys.argv, SPOT = sys.argv[:3], sys.argv[3]
import entry_timing_wf as E
import real_replay_stop as rr
P, M, ids = E.P, E.M, E.ids
sp = {int(k): v for k, v in json.load(gzip.open(SPOT, 'rt')).items()}
WS = {}
for p in pathlib.Path(sys.argv[1]).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
def ofi(w0, t, W):
    tb = qv = 0.
    for s in range(w0 + t - W + 1, w0 + t + 1):
        v = sp.get(s)
        if v: tb += v[3]; qv += v[1]
    return (2 * tb - qv) / qv if qv > 0 else None
def ret(w0, t, H):
    a, b = sp.get(w0 + t), sp.get(w0 + t + H)
    return math.log(b[0] / a[0]) if a and b else None
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]
for nm, sub in (('DISC', D), ('CONF', T)):
    for W in (10, 30):
        for H in (10, 30):
            a = []; b = []; c = []
            for m in sub:
                for t in range(20, 271, 4):
                    f = ofi(WS[m], t, W); r = ret(WS[m], t, H)
                    if f is None or r is None or t + H > 300: continue
                    a.append(f); b.append(r); c.append(P[m][min(t + H, 290)][0] - P[m][t][0])
            print('%s corr(spot OFI_%d, next %2ds): spot return %+.3f | Predict mid change %+.3f (n=%d)' % (nm, W, H, corr(a, b), corr(a, c), len(a)))
rng = random.Random(9)
def ci(xs, nb=400): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
def edge(m, th):
    w = 1. if M[m]['win'] == 'UP' else 0.; es = []
    for t in range(30, 271, 4):
        f = ofi(WS[m], t, 30)
        if f is None: continue
        A = P[m][t]; au, ad = min(.99, A[1][0]), min(.99, A[2][0])
        if f >= th: es.append(w - au)
        elif f <= -th: es.append((1 - w) - ad)
    return S.fmean(es) if es else None
print('\nspot-flow-following taker edge (cents/share): disc | conf')
for th in (.1, .2, .3):
    row = []
    for sub in (D, T):
        xs = [x for x in (edge(m, th) for m in sub) if x is not None]; lo, hi = ci(xs); row.append('%3d %+6.2f [%+6.2f,%+6.2f]' % (len(xs), 100 * S.fmean(xs), 100 * lo, 100 * hi))
    print('theta %.1f | %s | %s' % (th, row[0], row[1]))
