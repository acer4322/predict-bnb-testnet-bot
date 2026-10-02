"""What market conditions make the Target's pair cost <1 (profit)?  Per market (105): Target PnL and pair cost vs book features measured from public books: mean spread, mid path length (sum|dmid|, 12-290 s), number of .50 crossings, time-averaged |mid-.5|, first-60s path.  Spearman correlations (all, first half, second half by id) + half comparison of the features themselves.
usage: python tools/target_regime_drivers.py target_fills.json.gz PUBLIC_ROOT"""
import sys, gzip, json, collections, statistics as S
import real_replay_stop as rr
exp, root = sys.argv[1:3]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; ids = sorted(M); F = {}
for m in ids:
    mk = M[m]; mids = []; sp = []
    for t in range(12, 291, 2):
        b = rr.at(mk, float(t))
        if b: mids.append((b['best_bid'] + b['best_ask']) / 2); sp.append(b['best_ask'] - b['best_bid'])
    path = sum(abs(a - b) for a, b in zip(mids, mids[1:])); cross = sum((a - .5) * (b - .5) < 0 for a, b in zip(mids, mids[1:]))
    sh = {'UP': 0., 'DOWN': 0.}; cs = {'UP': 0., 'DOWN': 0.}
    for r in by[m]: sh[r['side']] += r['shares']; cs[r['side']] += r['shares'] * r['price']
    pc = cs['UP'] / sh['UP'] + cs['DOWN'] / sh['DOWN'] if sh['UP'] and sh['DOWN'] else None
    F[m] = dict(pnl=by[m][0]['net_pnl_usdt'], pc=pc, spread=S.fmean(sp), path=path, cross=cross, dev=S.fmean(abs(x - .5) for x in mids), vol=sum(r['shares'] for r in by[m]), path60=sum(abs(a - b) for a, b in zip(mids[:24], mids[1:25])))
def rank(x): s = sorted(range(len(x)), key=lambda i: x[i]); r = [0] * len(x); [r.__setitem__(i, k) for k, i in enumerate(s)]; return r
def sp_(a, b):
    ra, rb = rank(a), rank(b); ma, mb = S.fmean(ra), S.fmean(rb); c = sum((x - ma) * (y - mb) for x, y in zip(ra, rb)); return c / (sum((x - ma) ** 2 for x in ra) * sum((y - mb) ** 2 for y in rb)) ** .5
half = [ids[:len(ids) // 2], ids[len(ids) // 2:]]
print('Spearman(feature, Target PnL) | (feature, pair cost) ; all | first half | second half')
for k in ('spread', 'path', 'path60', 'cross', 'dev', 'vol'):
    out = []
    for sub in (ids, half[0], half[1]):
        s2 = [m for m in sub if F[m]['pc'] is not None]; out.append('%+.2f|%+.2f' % (sp_([F[m][k] for m in sub], [F[m]['pnl'] for m in sub]), sp_([F[m][k] for m in s2], [F[m]['pc'] for m in s2])))
    print('  %-7s' % k, '  '.join(out))
print('\nfeature means first half vs second half; Target pnl, pair cost:')
for k in ('spread', 'path', 'cross', 'dev', 'vol', 'pnl', 'pc'):
    print('  %-7s %.3f | %.3f' % (k, *[S.fmean(F[m][k] for m in sub if F[m][k] is not None) for sub in half]))
