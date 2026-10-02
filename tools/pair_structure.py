"""Read-only: pair-formation structure of OUR fills (all routes), the right unit for a two-sided strategy.
For each path: UP/DOWN fills from native receipts (UP buy price = price, DOWN buy price = 1 - price).
 * average-cost decomposition: M = min(inv_UP, inv_DOWN); locked = M*(1 - pU - pD) (pays 1 per matched pair whatever the outcome);
   residual = excess shares e on the larger side: e*(1-p) if that side wins else -e*p  (needs offline labels; optional).
 * FIFO matching in time: every fill matches the oldest unmatched opposite-side shares; pair cost = p_new + p_old, lag = t_new - t_old.
Reports qty-weighted pair cost overall / by lag bin / by route pair, and the locked-vs-residual split of PnL.
  python tools/pair_structure.py ROOT [--labels OFFLINE_SETTLEMENT_LABELS.json] [--boot 1000]
ROOT as for fill_conditions.py (execution_clock.json + flips.json (+ result.json)). 'parity' directories are skipped.
"""
import argparse, collections, json, gzip, random, statistics as S
from pathlib import Path


def jload(p):
    p = Path(p); return json.loads((gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')).read())


def path_fills(d):
    ex = jload(d / 'execution_clock.json'); car = {int(k.rsplit('_', 1)[1]): (k.split('_')[0], v['route']) for k, v in ex['carriers'].items()}
    mid = int(next(iter(sorted(d.glob('public_*.json.gz')))).name.split('_')[1].split('.')[0]) if list(d.glob('public_*.json.gz')) else None
    rows = []
    for r in ex['receipts']:
        q = float(r['qty'])
        if q < 1e-9 or r['order_id'] not in car: continue
        side, route = car[r['order_id']]
        rows.append((int(r['exchange_ts'] // 1_000_000), side, route, 'M' if r['maker'] else 'T', q, float(r['price']) if side == 'UP' else 1. - float(r['price'])))
    rows.sort(); return mid, rows


def analyse(rows):
    inv = {'UP': 0., 'DOWN': 0.}; cs = {'UP': 0., 'DOWN': 0.}
    for t, s, rt, mk, q, p in rows: inv[s] += q; cs[s] += q * p
    if inv['UP'] < 1e-9 or inv['DOWN'] < 1e-9: return None
    pu, pd = cs['UP'] / inv['UP'], cs['DOWN'] / inv['DOWN']; M = min(inv.values())
    big = 'UP' if inv['UP'] > inv['DOWN'] else 'DOWN'; e = abs(inv['UP'] - inv['DOWN']); pb = pu if big == 'UP' else pd
    # FIFO
    qs = {'UP': collections.deque(), 'DOWN': collections.deque()}; pairs = []
    for t, s, rt, mk, q, p in rows:
        o = 'DOWN' if s == 'UP' else 'UP'; rem = q
        while rem > 1e-9 and qs[o]:
            t0, p0, q0, mk0 = qs[o][0]; m = min(rem, q0)
            pairs.append((m, p + p0, (t - t0) / 1000., mk + mk0 if s == 'UP' else mk0 + mk)); rem -= m
            if q0 - m < 1e-9: qs[o].popleft()
            else: qs[o][0] = (t0, p0, q0 - m, mk0)
        if rem > 1e-9: qs[s].append((t, p, rem, mk))
    return dict(M=M, pu=pu, pd=pd, locked=M * (1 - pu - pd), big=big, e=e, pb=pb, pairs=pairs, cost=cs['UP'] + cs['DOWN'])


def wavg(xs): w = sum(a for a, _ in xs); return sum(a * b for a, b in xs) / w if w else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--labels', default=''); ap.add_argument('--boot', type=int, default=1000)
    a = ap.parse_args(); rng = random.Random(1)
    lab = {int(r['market_id']): r['winner'] for r in jload(a.labels).get('records', [])} if a.labels else {}
    per = []
    for d in sorted(p.parent for p in Path(a.root).rglob('execution_clock.json') if 'parity' not in str(p.parent)):
        mid, rows = path_fills(d); r = analyse(rows)
        if r is None: continue
        r['market'] = mid; w = lab.get(mid)
        if w is not None: r['resid'] = r['e'] * (1 - r['pb']) if w == r['big'] else -r['e'] * r['pb']; r['pnl'] = r['locked'] + r['resid']
        per.append(r)
    n = len(per); allp = [x for r in per for x in r['pairs']]
    print('paths with both sides filled: %d | matched shares %.0f | residual shares %.0f' % (n, sum(r['M'] for r in per), sum(r['e'] for r in per)))
    pc = wavg([(m, c) for m, c, l, k in allp])
    print('FIFO pair cost (UP price + DOWN price), qty-weighted: %.4f  (<1 means locked profit; %.2f c per matched pair)' % (pc, 100 * (1 - pc)))
    print('average-cost view: mean locked profit per path %.1f | mean pair cost %.4f' % (S.fmean(r['locked'] for r in per), S.fmean(r['pu'] + r['pd'] for r in per)))
    bins = [(0, 5), (5, 15), (15, 40), (40, 100), (100, 400)]
    print('\n%-12s %10s %9s %10s' % ('lag (s)', 'matched sh', 'pair cost', 'edge (c)'))
    for lo, hi in bins:
        s = [(m, c) for m, c, l, k in allp if lo <= l < hi]
        if s: print('%-12s %10.0f %9.4f %+10.2f' % ('%d-%d' % (lo, hi), sum(m for m, _ in s), wavg(s), 100 * (1 - wavg(s))))
    print('\n%-12s %10s %9s %10s' % ('legs UP/DOWN', 'matched sh', 'pair cost', 'edge (c)'))
    for k in sorted({k for _, _, _, k in allp}):
        s = [(m, c) for m, c, l, kk in allp if kk == k]; print('%-12s %10.0f %9.4f %+10.2f' % (k + ' (M=maker T=taker)', sum(m for m, _ in s), wavg(s), 100 * (1 - wavg(s))))
    if lab:
        L = [r for r in per if 'pnl' in r]
        print('\nPnL decomposition over %d labelled paths with both sides: locked %.1f | residual %.1f | total %.1f (mean per path)' % (len(L), S.fmean(r['locked'] for r in L), S.fmean(r['resid'] for r in L), S.fmean(r['pnl'] for r in L)))
        def ci(xs):
            ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(a.boot)); return ms[int(.025 * a.boot)], ms[int(.975 * a.boot)]
        print('  locked 95%% CI [%.1f, %.1f] | residual 95%% CI [%.1f, %.1f]' % (*ci([r['locked'] for r in L]), *ci([r['resid'] for r in L])))
        tw = sorted(L, key=lambda r: r['e'] / max(1e-9, r['M'] + r['e'])); k = len(tw) // 3
        for nm, g in (('most balanced third', tw[:k]), ('middle third', tw[k:2 * k]), ('most one-sided third', tw[2 * k:])):
            print('  %-22s locked %7.1f resid %7.1f total %7.1f | mean imbalance %.0f%%' % (nm, S.fmean(r['locked'] for r in g), S.fmean(r['resid'] for r in g), S.fmean(r['pnl'] for r in g), 100 * S.fmean(r['e'] / (r['M'] + r['e']) for r in g)))


if __name__ == '__main__':
    main()
