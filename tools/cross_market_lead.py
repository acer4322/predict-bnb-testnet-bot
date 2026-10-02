"""Cross-market lead/lag between BTC5M and ETH5M books on the SAME window (new information source: the other asset's MARKET price).
(1) lag cross-correlation of 1 s UP-mid changes (+-15 s) pooled over matched windows; (2) predictive regression: ETH UP-mid change over next h s on BTC UP-mid change over last k s, controlling own last-k change; (3) naive taker rule: when BTC UP mid moved >= d in last k s and ETH UP ask has not -> buy ETH UP (or DOWN for the mirror), hold to resolution; edge per share by split-half of windows.
usage: python tools/cross_market_lead.py ETH_CACHE.pkl BTC_CACHE.pkl ETH_PUBLIC_ROOT BTC_PUBLIC_ROOT"""
import sys, pickle, json, gzip, statistics as S, math, random
ec, bc, er, br = sys.argv[1:5]
E = pickle.load(open(ec, 'rb')); B = pickle.load(open(bc, 'rb'))
def ws(root, ids): return {m: int(json.load(gzip.open('%s/%d/public_%d.json.gz' % (root, m, m), 'rt'))['market']['window_start_ms']) // 1000 for m in ids}
WE = ws(er + '/public_markets' if not er.endswith('public_markets') else er, E[0]); WB = ws(br, B[0]); inv = {w: m for m, w in WB.items()}
pairs = [(m, inv[w]) for m, w in WE.items() if w in inv]; print('matched windows', len(pairs), 'of ETH', len(E[0]), 'BTC', len(B[0]))
if not pairs: sys.exit()
pairs.sort(key=lambda p: WE[p[0]]); mid = lambda X, m, t: X[1][m][t][0]
def cc(lag):
    xs = []; ys = []
    for me, mb in pairs:
        for t in range(20, 270):
            a = mid(B, mb, t) - mid(B, mb, t - 1); b = mid(E, me, t + lag) - mid(E, me, t + lag - 1); xs.append(a); ys.append(b)
    mx, my = S.fmean(xs), S.fmean(ys); sx, sy = S.pstdev(xs), S.pstdev(ys); return S.fmean((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy + 1e-12)
print('corr(BTC dMid(t), ETH dMid(t+lag)):', {l: round(cc(l), 3) for l in (-10, -5, -3, -2, -1, 0, 1, 2, 3, 5, 10)})
print('(positive lag>0 = BTC market leads ETH market)')
def rule(sub, k, d, h_hold=None):
    pnl = []; 
    for me, mb in sub:
        ups = 0; w = E[2][me]
        for t in range(30, 270, 4):
            db = mid(B, mb, t) - mid(B, mb, t - k); de = mid(E, me, t) - mid(E, me, t - k)
            if abs(db) >= d and abs(de) < d / 2 and (db > 0) == (db * (mid(B, mb, t) - .5) > -1e9 and db > 0):
                side = 'UP' if db > 0 else 'DOWN'; p = min(.99, E[1][me][t][1] if side == 'UP' else E[1][me][t][2]); pnl.append((1. if w == side else 0.) - p); break
    return pnl
half = len(pairs) // 2
for k, d in ((5, .03), (5, .05), (10, .05), (10, .08), (20, .10)):
    out = []
    for nm, sub in (('first half', pairs[:half]), ('second half', pairs[half:])):
        e = rule(sub, k, d); out.append('%s n=%d edge/share %+.3f (sd-err %.3f)' % (nm, len(e), S.fmean(e) if e else 0, S.pstdev(e) / math.sqrt(len(e)) if len(e) > 2 else 0))
    print('rule k=%ds d=%.2f: ' % (k, d) + ' | '.join(out))
