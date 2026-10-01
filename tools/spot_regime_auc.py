"""Does spot volatility identify the regime?  Per market features from BTCUSDT 1 s (known at the window start): realized vol over the previous 30 min / 5 min / 1 min (std of 1 s log returns),
vol ratio 5m/30m, absolute 30-min drift (|ln(S0/S_-1800)|), absolute 5-min drift, volume (quote) over the previous 5 min.  Targets: Y_flip (flip after 12 s, fixed rule) and Y_ud (UNDERDOG harvest PnL > FAV freeze PnL).
Univariate AUC on discovery (first 60% by id) and test (last 40%); also the flip rate and the UNDERDOG/FAV mean PnL by tercile of the 30-min vol (terciles from discovery).
usage: python tools/spot_regime_auc.py PUBLIC_ROOT LABELS.json btc_1s.json.gz"""
import sys, json, gzip, math, pathlib, statistics as S, bisect
sys.argv, SPOT = sys.argv[:3], sys.argv[3]
import fav_improve_wf as F
from fav_regime_blocks import underdog
import real_replay_stop as rr
P, M, ids, CL = F.P, F.M, F.ids, F.CL
sp = json.load(gzip.open(SPOT, 'rt')); spot = {int(k): v for k, v in sp.items()}; ks = sorted(spot)
def px(s, j=0):
    i = bisect.bisect_right(ks, s) - 1; return spot[ks[i]][j] if i >= 0 else None
WS = {}
for p in pathlib.Path(sys.argv[1]).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms']) // 1000
def rv(w0, n):
    r = []; prev = px(w0 - n)
    for s in range(w0 - n + 1, w0 + 1):
        c = px(s)
        if c and prev: r.append(math.log(c / prev))
        prev = c
    return S.pstdev(r) if len(r) > max(20, n // 10) else None
fav = {m: F.run(m, .55, .70, 'FREEZE', .40, 300.) for m in ids}; ud = {m: (underdog(m) or 0.) for m in ids}
flip = {m: 1. if CL[m] != 'NO_FLIP' else 0. for m in ids}; yud = {m: 1. if ud[m] > fav[m] else 0. for m in ids}
feat = {}
for m in ids:
    w0 = WS[m]; f = {'rv_30m': rv(w0, 1800), 'rv_5m': rv(w0, 300), 'rv_1m': rv(w0, 60)}
    f['vol_ratio_5m/30m'] = f['rv_5m'] / f['rv_30m'] if f['rv_5m'] and f['rv_30m'] else None
    a, b, c = px(w0), px(w0 - 1800), px(w0 - 300); f['abs_drift_30m'] = abs(math.log(a / b)) if a and b else None; f['abs_drift_5m'] = abs(math.log(a / c)) if a and c else None
    q = [spot[s][1] for s in range(w0 - 300, w0) if s in spot]; f['quote_volume_5m'] = sum(q) if q else None; feat[m] = f
def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y == 1]; neg = [x for x, y in zip(xs, ys) if y == 0]
    return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg)) if pos and neg else float('nan')
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]
print('%-20s | Y_flip AUC disc / test | Y_ud AUC disc / test' % 'feature')
for name in feat[ids[0]]:
    row = []
    for tgt in (flip, yud):
        r = []
        for sub in (D, T):
            z = [(feat[m][name], tgt[m]) for m in sub if feat[m][name] is not None]; r.append(auc([a for a, _ in z], [b for _, b in z]))
        row.append(r)
    print('%-20s | %.3f / %.3f | %.3f / %.3f' % (name, *row[0], *row[1]))
v = sorted(feat[m]['rv_30m'] for m in D if feat[m]['rv_30m']); t1, t2 = v[len(v) // 3], v[2 * len(v) // 3]
print('\nterciles of rv_30m (cut points from discovery %.2e, %.2e): flip rate | mean PnL FAV / UNDERDOG | n  (disc | test)' % (t1, t2))
for nm, sub in (('disc', D), ('test', T)):
    for lab, f in (('low', lambda x: x < t1), ('mid', lambda x: t1 <= x < t2), ('high', lambda x: x >= t2)):
        z = [m for m in sub if feat[m]['rv_30m'] and f(feat[m]['rv_30m'])]
        print('  %s %-4s n=%3d flip %.0f%% | FAV %+6.1f UNDERDOG %+6.1f' % (nm, lab, len(z), 100 * S.fmean(flip[m] for m in z), S.fmean(fav[m] for m in z), S.fmean(ud[m] for m in z)))
