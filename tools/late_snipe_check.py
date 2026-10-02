"""End-of-window snipe with exact spot (pre-declared 2026-10-02).  Resolution = end price vs start price (our labels agree with Binance 1 s sign 95.6%).  For t in {240,250,...,285} s of the window: S0 = spot at window start, S_t spot now,
z = ln(S_t/S0)/(sigma_5m*sqrt(300-t)) with sigma_5m = std of 1 s log returns over the previous 300 s (known at t).  Buy the side with z-implied fair Phi(|z|) when its ask (quotes t+1 s, taker) is below fair - delta, delta in {.03,.06,.10},
and ask <= .97; one trade per market-side-time bin (every 5 s), edge = 1[win] - ask, per-market mean, market bootstrap CI, discovery (first 60% by id) vs confirmation (last 40%).  Data: the 519 true-labelled markets (replay arrays) + spot 1 s.
usage: python tools/late_snipe_check.py PUBLIC_ROOT LABELS.json btc_1s.json.gz"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect
sys.argv, SPOT = sys.argv[:3], sys.argv[3]
import entry_timing_wf as E
import real_replay_stop as rr
P, M, ids = E.P, E.M, E.ids
spot = {int(k): v[0] for k, v in json.load(gzip.open(SPOT, 'rt')).items()}; ks = sorted(spot)
def px(s): i = bisect.bisect_right(ks, s) - 1; return spot[ks[i]] if i >= 0 else None
WS = {}
for p in pathlib.Path(sys.argv[1]).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
Phi = lambda x: .5 * (1 + math.erf(x / math.sqrt(2)))
def sig(w0, t):
    r = []; prev = px(w0 + t - 300)
    for s in range(w0 + t - 299, w0 + t + 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r)
rng = random.Random(5); cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]
def ci(xs, nb=400): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
def edge(m, d):
    w0 = WS[m]; s0 = px(w0); w = 1. if M[m]['win'] == 'UP' else 0.; es = []
    for t in range(240, 288, 5):
        sg = sig(w0, t); z = math.log(px(w0 + t) / s0) / (sg * math.sqrt(300 - t)); f = Phi(z)
        A = P[m][t]; au, ad = min(.99, A[1][0]), min(.99, A[2][0])
        if f - au > d and au <= .97: es.append(w - au)
        elif (1 - f) - ad > d and ad <= .97: es.append((1 - w) - ad)
    return S.fmean(es) if es else None
print('delta | disc: markets edge c [CI] | conf: markets edge c [CI]')
for d in (.03, .06, .10):
    row = []
    for sub in (D, T):
        xs = [x for x in (edge(m, d) for m in sub) if x is not None]; lo, hi = ci(xs) if len(xs) > 10 else (float('nan'),) * 2; row.append('%3d %+6.2f [%+6.2f,%+6.2f]' % (len(xs), 100 * S.fmean(xs) if xs else float('nan'), 100 * lo, 100 * hi))
    print('%.2f | %s | %s' % (d, row[0], row[1]))
# calibration of the late fair value vs market at t=270
z = []
for m in ids:
    w0 = WS[m]; s0 = px(w0); t = 270; sg = sig(w0, t); f = Phi(math.log(px(w0 + t) / s0) / (sg * math.sqrt(30))); z.append((f, P[m][t][0], 1. if M[m]['win'] == 'UP' else 0.))
print('t=270 Brier: spot-fair %.4f | market mid %.4f | blend %.4f (n=%d)' % (S.fmean((a - w) ** 2 for a, b, w in z), S.fmean((b - w) ** 2 for a, b, w in z), S.fmean(((a + b) / 2 - w) ** 2 for a, b, w in z), len(z)))
