"""Spot-implied fair value vs market price (BTCUSDT 1 s from data-api.binance.vision, fetch_spot_1s.py).  For every true-labelled market: S0 = spot at the window start second, S_t spot at second t of the window,
sigma = std of 1 s log returns over the previous 1800 s, fair P(UP at t) = Phi( ln(S_t/S0) / (sigma*sqrt(300-t)) ), market mid from the replay arrays.
Outputs: (1) agreement of the label with sign(S_end - S0); (2) Brier score of fair vs market mid at t = 12, 60, 120, 180, 240; (3) value of buying the cheaper-than-fair side at the ask (taker, quotes at t+1 s):
for thresholds delta in {2,4,6,8,10} cents per share, every 2 s from 12 to 288 s; per-market mean edge with market bootstrap CI, discovery vs confirmation halves (by id).
usage: python tools/spot_fair_check.py PUBLIC_ROOT LABELS.json btc_1s.json.gz"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect
sys.argv, SPOT = sys.argv[:3], sys.argv[3]
import entry_timing_wf as E
import real_replay_stop as rr
P, M, ids = E.P, E.M, E.ids
spot = {int(k): v[0] for k, v in json.load(gzip.open(SPOT, 'rt')).items()}; ks = sorted(spot)
def px(sec):
    i = bisect.bisect_right(ks, sec) - 1; return spot[ks[i]] if i >= 0 else None
WS = {}
for p in pathlib.Path(sys.argv[1]).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
Phi = lambda x: .5 * (1 + math.erf(x / math.sqrt(2)))
def sigma(w0):
    r = []; prev = px(w0 - 1800)
    for s in range(w0 - 1799, w0, 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r) if len(r) > 100 else None
rows = {}; agree = 0; nag = 0
for m in ids:
    w0 = WS[m]; s0 = px(w0); sg = sigma(w0); se = px(w0 + 300)
    if None in (s0, sg, se) or sg <= 0: continue
    win = 1. if M[m]['win'] == 'UP' else 0.; nag += 1; agree += ((se >= s0) == (win == 1.))
    rows[m] = dict(w0=w0, s0=s0, sg=sg, win=win)
print('markets with spot %d of %d | label agrees with sign(S_end-S0): %.1f%%' % (len(rows), len(ids), 100 * agree / nag))
def fair(m, t):
    r = rows[m]; st = px(r['w0'] + t)
    if st is None: return None
    return Phi(math.log(st / r['s0']) / (r['sg'] * math.sqrt(max(1, 300 - t))))
print('\nBrier score (lower is better): fair(spot) vs market mid')
for t in (12, 60, 120, 180, 240):
    f = [(fair(m, t), P[m][t][0], rows[m]['win']) for m in rows if fair(m, t) is not None]
    print('t=%3ds  fair %.4f  market %.4f  | blend(.5/.5) %.4f' % (t, S.fmean((a - w) ** 2 for a, b, w in f), S.fmean((b - w) ** 2 for a, b, w in f), S.fmean(((a + b) / 2 - w) ** 2 for a, b, w in f)))
rng = random.Random(3); half = len(ids) // 2; Dm, Tm = set(ids[:half]), set(ids[half:])
def mk_edge(m, delta):
    r = rows[m]; es = []
    for t in range(12, 290, 2):
        f = fair(m, t)
        if f is None: continue
        A = P[m][t]; au, ad = min(.99, A[1][0]), min(.99, A[2][0])
        if f - au > delta: es.append(r['win'] - au)
        elif (1 - f) - ad > delta: es.append((1 - r['win']) - ad)
    return (S.fmean(es), len(es)) if es else None
def ci(xs, nb=300):
    ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(nb)); return ms[int(.025 * nb)], ms[int(.975 * nb)]
print('\nbuy the side whose spot-fair value exceeds its ask by > delta: mean edge per share (cents), market-equal weight')
print('delta | disc: n markets edge [CI] | conf: n markets edge [CI]')
for d in (.02, .04, .06, .08, .10):
    res = {m: mk_edge(m, d) for m in rows}; out = []
    for sub in (Dm, Tm):
        xs = [res[m][0] for m in sub if m in res and res[m]]; lo, hi = ci(xs) if len(xs) > 10 else (float('nan'),) * 2
        out.append('%3d %+6.2f [%+6.2f,%+6.2f]' % (len(xs), 100 * S.fmean(xs), 100 * lo, 100 * hi))
    print('%.2f | %s | %s' % (d, out[0], out[1]))
