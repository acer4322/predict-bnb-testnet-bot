"""Low-volatility throttle (user idea 2026-10-02), pre-declared: high volatility (rv_5m >= th) -> UNDERDOG harvest; low volatility -> idle (s=0) or FAV at size fraction s in {0.25, 0.5} (s=1 is the frozen switch).  th in {q.50 = 3.1834e-05, q.67 = 3.9759e-05} (fixed).
Replay (519 markets): test half (id >= 2768492) and expanding-window blocks 2-6 pooled (threshold from earlier blocks), averages INCLUDE the skipped markets (PnL 0) so 'overall' is per market in the whole stream; class means likewise.
Reference only (not clean): the 70 engine markets (research-data c547dc8a), engine PnL x s.   Generalized EACH: all positive, or exactly one losing class with |loss| < min(other two).
usage: python tools/spot_lowvol_throttle.py PUBLIC_ROOT LABELS.json btc_1s.json.gz under70.json events70_markets_dir"""
import sys, json, gzip, math, pathlib, random, statistics as S, bisect
args = sys.argv[:]; sys.argv = args[:4]; U70, EV70 = args[4:6]
import spot_switch_robustness as R  # builds replay PnL + RV (slow import)
from toy_graduation import ci_mean
ids, CL, RV, T = R.ids, R.CL, R.RV, R.T
A = R.arrays(1); fav = {m: R.fav(m, A[m], 0.) for m in ids}; ud = {m: R.under(m, A[m], 0.) for m in ids}
rng = random.Random(5); TH = {'q.50': 3.1834e-05, 'q.67': 3.9759e-05}
def ev(pn, sub):
    g = {k: [pn[m] for m in sub if CL[m] == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else 0.) for k, v in g.items()}
    neg = [k for k, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 0 or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos))
    lo, hi = ci_mean([pn[m] for m in sub], rng, 400); return mn, ok, S.fmean(pn[m] for m in sub), lo, hi
def pol(th, s): return {m: ud[m] if RV[m] >= th else s * fav[m] for m in ids}
print('REPLAY test half n=%d' % len(T)); print('%-26s | noflip false true | overall [CI] | rule | share of markets traded' % 'policy')
for q, th in TH.items():
    for s in (0., .25, .5, 1.):
        pn = pol(th, s); mn, ok, ov, lo, hi = ev(pn, T); tr = sum(RV[m] >= th or s > 0 for m in T) / len(T)
        print('rv>=%s, low-vol FAV x%.2f | %6.1f %6.1f %6.1f | %5.1f [%5.1f,%5.1f] | %s | %.0f%%' % (q, s, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, lo, hi, 'PASS' if ok else 'fail', 100 * tr))
print('\nEXPANDING blocks 2-6 pooled (threshold = same quantile of earlier blocks)')
k = 6; sz = len(ids) // k; blocks = [ids[b * sz:(b + 1) * sz if b < k - 1 else None] for b in range(k)]
for qn, q in (('q.50', .5), ('q.67', .67)):
    for s in (0., .25, .5, 1.):
        pn = {}
        for b in range(1, k):
            prior = sorted(RV[m] for bb in range(b) for m in blocks[bb]); th = prior[int(q * (len(prior) - 1))]
            for m in blocks[b]: pn[m] = ud[m] if RV[m] >= th else s * fav[m]
        sub = [m for b in range(1, k) for m in blocks[b]]; mn, ok, ov, lo, hi = ev(pn, sub)
        print('rv>=%s, low-vol FAV x%.2f | %6.1f %6.1f %6.1f | %5.1f [%5.1f,%5.1f] | %s' % (qn, s, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], ov, lo, hi, 'PASS' if ok else 'fail'))
# reference sets (engine pnl)
spot70 = R.px
def rv_for(ws, n=300): return R.rv(ws, n)
u = json.load(open(U70)); ud70 = {r['market']: r for r in u['UNDER_TAKER']}; fv70 = {r['market']: r for r in u['FAV_TAKER']}
ws70 = {int(p.name): int(json.load(open(p / 'META.json'))['window_start_ms']) // 1000 for p in pathlib.Path(EV70).iterdir()}
def ref(tag, udd, fvv, rvm):
    ms = sorted(udd); print('\nREFERENCE %s (n=%d)' % (tag, len(ms)))
    for q, th in TH.items():
        for s in (0., .5, 1.):
            pn = {m: (udd[m]['pnl'] if rvm[m] >= th else s * fvv[m]['pnl']) for m in ms}; cl = {m: udd[m]['cls'] for m in ms}
            g = {c: [pn[m] for m in ms if cl[m] == c] for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {c: (S.fmean(v) if v else float('nan')) for c, v in g.items()}
            print('  rv>=%s low-vol FAV x%.1f | %6.1f %6.1f %6.1f | overall %5.1f | UD in %d of %d' % (q, s, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(pn.values()), sum(rvm[m] >= th for m in ms), len(ms)))
ref('70 newest markets (engine)', ud70, fv70, {m: rv_for(ws70[m]) for m in ud70})
