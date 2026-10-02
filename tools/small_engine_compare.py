"""Engine-level check of the small-amount mode (clip = max(s*15, 1 USDT/price) shares, cap = s*300; strategy_lab with SCALE=s) against the full-scale engine results on the same 185 markets.  Candidates: FAV, UNDERDOG, v1, v2 (rv_5m >= 3.1834e-05).
Per scale: scaled-up PnL (pnl/s) class means and overall (market bootstrap CI), generalized EACH, correlation of the per-market PnL with the full-scale engine PnL, average fill price, fee drag in USD (local model min(p,1-p) x 200 bps on the average price, NOT verified),
net PnL in USD per market after fees, cost per traded market and worst single market in USD.
usage: python tools/small_engine_compare.py FULL.json RV.json SCALE=file.json[,file.json...] ..."""
import sys, json, math, random, statistics as S
from toy_graduation import ci_mean
full = json.load(open(sys.argv[1])); rv = {int(k): v['rv_5m'] for k, v in json.load(open(sys.argv[2])).items()}; TH = 3.1834e-05; rng = random.Random(3)
fav0 = {r['market']: r for r in full['FAV_TAKER']}; ud0 = {r['market']: r for r in full['UNDER_TAKER']}
def corr(a, b):
    ma, mb = S.fmean(a), S.fmean(b); c = sum((x - ma) * (y - mb) for x, y in zip(a, b)); va = sum((x - ma) ** 2 for x in a); vb = sum((y - mb) ** 2 for y in b); return c / math.sqrt(va * vb) if va * vb else float('nan')
def fee(r):
    sh = r['up'] + r['dn']; return 0. if sh <= 0 else sh * min(r['cost'] / sh, 1 - r['cost'] / sh) * .02
def pick(kind, m, F, U, idle=False):
    if kind == 'FAV': return F[m]
    if kind == 'UNDERDOG': return U[m]
    if kind == 'v1': return U[m] if rv[m] >= TH else F[m]
    if kind == 'v2': return U[m] if rv[m] >= TH else None
for arg in sys.argv[3:]:
    s = float(arg.split('=')[0]); F = {}; U = {}
    for f in arg.split('=')[1].split(','):
        d = json.load(open(f)); F.update({r['market']: r for r in d['FAV_TAKER']}); U.update({r['market']: r for r in d['UNDER_TAKER']})
    ms = sorted(m for m in F if m in U and m in fav0 and m in rv); print('\n=== scale x%.1f, %d markets ===' % (s, len(ms)))
    print('%-10s | scaled-up class means noflip false true | overall [CI] | EACH | corr w/ full | avg px | fee USD/mkt | net USD/mkt | cost USD/traded mkt | worst USD' % 'candidate')
    for kind in ('FAV', 'UNDERDOG', 'v1', 'v2'):
        pn = []; pn0 = []; cl = []; fees = []; costs = []; px = []
        for m in ms:
            r = pick(kind, m, F, U); r0 = pick(kind, m, fav0, ud0)
            pn.append(r['pnl'] / s if r else 0.); pn0.append(r0['pnl'] if r0 else 0.); cl.append(fav0[m]['cls']); fees.append(fee(r) if r else 0.); costs.append(r['cost'] if r else 0.)
            if r and r['up'] + r['dn'] > 0: px.append(r['cost'] / (r['up'] + r['dn']))
        g = {c: [p for p, k in zip(pn, cl) if k == c] for c in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {c: S.fmean(v) for c, v in g.items()}
        neg = [c for c, v in mn.items() if v < 0]; pos = [v for v in mn.values() if v > 0]; ok = len(neg) == 0 or (len(neg) == 1 and len(pos) == 2 and -mn[neg[0]] < min(pos)); lo, hi = ci_mean(pn, rng, 1000)
        usd = [p * s for p in pn]
        print('%-10s | %6.1f %6.1f %6.1f | %5.1f [%5.1f,%5.1f] | %s | %.3f | %.3f | %.2f | %6.2f | %6.1f | %7.1f' % (kind, mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], S.fmean(pn), lo, hi, 'PASS' if ok else 'fail', corr(pn, pn0), S.fmean(px) if px else float('nan'),
              S.fmean(fees), S.fmean(usd) - S.fmean(fees), S.fmean(c for c in costs if c > 0), min(usd)))
