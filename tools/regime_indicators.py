"""Which observable predicts the market state (flip / UNDERDOG better than FAV)?  Per market (519 true-labelled, id order): targets  Y_flip = a flip happens after 12 s (fixed rule);
Y_ud = UNDERDOG harvest PnL - FAV(freeze) PnL > 0.   Features known at the start of the market (t<=12 s): trailing flip rate of the previous K markets (K=10,20,40), trailing 'favourite overpricing' of the previous K markets
(mean of 1[fav won]-ask when fav mid>=.70), early range of the mid in 0-12 s, spread at 12 s, favourite mid at 12 s, top-3 depth imbalance at 12 s, UTC hour of the market window (bin means learned on the discovery half),
day-of-week, and the gap to the previous market's start (minutes).  Univariate AUC on discovery (first 60%) and on test (last 40%); a feature 'works' if AUC is >.55 in discovery and the test AUC points the same way (>.52)."""
import sys, json, gzip, pathlib, statistics as S, collections, datetime
sys.argv = sys.argv[:3]
import fav_improve_wf as F
from fav_regime_blocks import underdog, fav_edge
import real_replay_stop as rr
P, M, ids, CL = F.P, F.M, F.ids, F.CL
root = sys.argv[1]; WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    if 'parity' in str(p): continue
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms'])
fav = {m: F.run(m, .55, .70, 'FREEZE', .40, 300.) for m in ids}; ud = {m: (underdog(m) or 0.) for m in ids}
flip = {m: 1. if CL[m] != 'NO_FLIP' else 0. for m in ids}; yud = {m: 1. if ud[m] > fav[m] else 0. for m in ids}
fe = {m: (fav_edge(m) or (None,))[0] for m in ids}
def auc(xs, ys):
    pos = [x for x, y in zip(xs, ys) if y == 1]; neg = [x for x, y in zip(xs, ys) if y == 0]
    if not pos or not neg: return float('nan')
    return sum((p > n) + .5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))
feat = collections.defaultdict(dict)
for i, m in enumerate(ids):
    A = P[m]; mids0 = [A[t][0] for t in range(0, 13)]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'
    feat['early_range(0-12s)'][m] = max(mids0) - min(mids0); a = rr.at(M[m], 12.); feat['spread@12'][m] = a['best_ask'] - a['best_bid']
    feat['fav_mid@12'][m] = max(A[12][0], 1 - A[12][0]); bd = sum(x[1] for x in a['bids'][:3]); ad = sum(x[1] for x in a['asks'][:3]); feat['depth_imb@12(bid-ask)'][m] = (bd - ad) / (bd + ad) if bd + ad else 0.
    d = datetime.datetime.utcfromtimestamp(WS[m] / 1000); feat['utc_hour'][m] = d.hour; feat['weekday'][m] = d.weekday(); feat['gap_min_to_prev'][m] = (WS[m] - WS[ids[i - 1]]) / 60000. if i else 5.
    for K in (10, 20, 40):
        if i >= K: feat['trail_flip_K%d' % K][m] = S.fmean(flip[ids[j]] for j in range(i - K, i)); es = [fe[ids[j]] for j in range(i - K, i) if fe[ids[j]] is not None]; feat['trail_favedge_K%d' % K][m] = S.fmean(es) if es else None
cut = int(.6 * len(ids)); D, T = ids[:cut], ids[cut:]
def cat_encode(name):  # learn bin means on discovery, apply to all
    byv = collections.defaultdict(list)
    for m in D: byv[feat[name][m]].append(flip[m])
    mu = {v: S.fmean(x) for v, x in byv.items()}; g = S.fmean(flip[m] for m in D); return {m: mu.get(feat[name][m], g) for m in ids}
print('%-26s | Y_flip AUC disc / test | Y_ud(UNDERDOG>FAV) AUC disc / test' % 'feature')
for name in list(feat):
    vals = feat[name] if name not in ('utc_hour', 'weekday') else cat_encode(name)
    row = []
    for tgt in (flip, yud):
        r = []
        for sub in (D, T):
            xs = [vals[m] for m in sub if vals.get(m) is not None]; ys = [tgt[m] for m in sub if vals.get(m) is not None]; r.append(auc(xs, ys))
        row.append(r)
    print('%-26s | %.3f / %.3f | %.3f / %.3f' % (name, *row[0], *row[1]))
