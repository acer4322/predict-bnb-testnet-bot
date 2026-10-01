"""Read-only: where is PASSIVE (maker) fill value >= 0?  Pre-registered design (see chat / spec): discovery = first 60% of markets by id,
confirmation = last 40%; a cell 'replicates' only if its market-clustered bootstrap 95% CI lower bound is > 0 in BOTH.
value = side_mid(t+5s) - buy_price  (cents per share, qty-weighted; positive = good for us).  Features are known AT FILL TIME
(placement time is not in the data): side-mid bin, spread (1 tick vs wider), own-queue depth share (top3), side-mid change over the
previous 5 s, seconds since window start. Settlement labels are not used.

  python tools/fill_conditions.py ROOT [--boot 1000] [--min-markets 15] [--out fill_conditions.json]
ROOT: directories with execution_clock.json + public_<market>.json.gz + flips.json (as written by pack_risk_floor_inputs.py --fills).
"""
import argparse, bisect, collections, gzip, json, random
from pathlib import Path


def jload(p):
    p = Path(p); return json.loads((gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')).read())


def fills_of(d):
    ex = jload(d / 'execution_clock.json'); pb = jload(next(iter(sorted(d.glob('public_*.json.gz')))))
    ev = jload(d / 'flips.json')['events'] if (d / 'flips.json').exists() else jload(d / 'result.json')['v12g']['events']
    flip = next((e['t'] for e in ev if e['kind'] == 'FLIP'), None)
    start = int(pb['market']['window_start_ms'])
    books = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
    ts = [b['source_ms'] for b in books]; mids = [(b['best_bid'] + b['best_ask']) / 2. for b in books]
    car = {int(k.rsplit('_', 1)[1]): (k.split('_')[0], v['route']) for k, v in ex['carriers'].items()}
    mid = lambda t: (mids[i] if (i := bisect.bisect_right(ts, t) - 1) >= 0 else None)
    book = lambda t: (books[i] if (i := bisect.bisect_right(ts, t) - 1) >= 0 else None)
    out = []
    for r in ex['receipts']:
        q = float(r['qty'])
        if q < 1e-9 or not r['maker'] or r['order_id'] not in car: continue
        side, _ = car[r['order_id']]
        price = float(r['price']) if side == 'UP' else 1. - float(r['price'])
        t = int(r['exchange_ts'] // 1_000_000)
        m0, m5, mm5, b = mid(t), mid(t + 5000), mid(t - 5000), book(t)
        if None in (m0, m5, mm5, b) or t + 5000 > ts[-1]: continue
        sm = (lambda m: m) if side == 'UP' else (lambda m: 1. - m)
        own = b['bids'] if side == 'UP' else b['asks']; opp = b['asks'] if side == 'UP' else b['bids']
        od = sum(x[1] for x in own[:3]); pd = sum(x[1] for x in opp[:3])
        out.append(dict(market=int(pb['market']['market_id']), q=q, v5=sm(m5) - price, sm=sm(m0), spread=round(b['best_ask'] - b['best_bid'], 4),
                        dshare=od / (od + pd) if od + pd > 0 else None, dm5=sm(m0) - sm(mm5), tsec=(t - start) / 1000.,
                        post=flip is not None and t >= flip))
    return out


def feats(f):
    sm = f['sm']; yield 'mid', ('<.30' if sm < .3 else '.30-.50' if sm < .5 else '.50-.70' if sm < .7 else '>=.70')
    yield 'spread', ('1tick' if f['spread'] <= .0101 else 'wider')
    if f['dshare'] is not None: yield 'ownq', ('thin<.35' if f['dshare'] < .35 else 'mid' if f['dshare'] < .65 else 'deep>=.65')
    yield 'dm5', ('fell>=2c' if f['dm5'] <= -.02 else 'rose>=2c' if f['dm5'] >= .02 else 'flat')
    t = f['tsec']; yield 'time', ('<30s' if t < 30 else '30-120' if t < 120 else '120-240' if t < 240 else '>=240s')
    yield 'phase', ('post-flip' if f['post'] else 'pre-flip')


def wmean(rs): w = sum(r['q'] for r in rs); return sum(r['q'] * r['v5'] for r in rs) / w if w else None


def ci(rs, nb, rng):
    by = collections.defaultdict(list)
    for r in rs: by[r['market']].append(r)
    ids = list(by); ms = []
    for _ in range(nb):
        s = [r for i in (rng.choice(ids) for _ in ids) for r in by[i]]; m = wmean(s)
        if m is not None: ms.append(m)
    ms.sort(); return (ms[int(.025 * len(ms))], ms[int(.975 * len(ms))]) if len(ms) > 20 else (None, None)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--boot', type=int, default=1000)
    ap.add_argument('--min-markets', type=int, default=15); ap.add_argument('--out', default='')
    a = ap.parse_args(); rng = random.Random(1)
    dirs = [p.parent for p in Path(a.root).rglob('execution_clock.json') if 'parity' not in str(p.parent)]
    allf = []
    for d in sorted(dirs):
        try: allf += fills_of(d)
        except Exception as ex: print('skip', d.name, type(ex).__name__, ex)
    mk = sorted({f['market'] for f in allf}); cut = mk[int(.6 * len(mk))]
    disc = [f for f in allf if f['market'] < cut]; test = [f for f in allf if f['market'] >= cut]
    print('markets=%d fills=%d (maker only) | discovery markets<%d: %d fills, confirmation: %d fills' % (len(mk), len(allf), cut, len(disc), len(test)))
    print('ALL maker fills: discovery %.2fc, confirmation %.2fc' % (100 * wmean(disc), 100 * wmean(test)))
    def cells(fs):
        c = collections.defaultdict(list)
        for f in fs:
            for k, v in feats(f): c[(k, v)].append(f)
        return c
    cd, ct = cells(disc), cells(test); rows = []
    for key in sorted(cd):
        if key not in ct: continue
        a1, a2 = cd[key], ct[key]
        if len({r['market'] for r in a1}) < a.min_markets or len({r['market'] for r in a2}) < a.min_markets: continue
        l1, h1 = ci(a1, a.boot, rng); l2, h2 = ci(a2, a.boot, rng)
        rows.append(dict(feature=key[0], bin=key[1], d_n=len(a1), d_mean=wmean(a1), d_lo=l1, d_hi=h1, t_n=len(a2), t_mean=wmean(a2), t_lo=l2, t_hi=h2,
                         qty=sum(r['q'] for r in a1 + a2)))
    print('\n%-8s %-10s | %6s %8s %17s | %6s %8s %17s | replicates(>0 in both)' % ('feature', 'bin', 'd_n', 'd mean', 'd 95% CI', 't_n', 't mean', 't 95% CI'))
    for r in rows:
        rep = r['d_lo'] is not None and r['t_lo'] is not None and r['d_lo'] > 0 and r['t_lo'] > 0
        f = lambda v: 'NA' if v is None else '%+.2f' % (100 * v)
        print('%-8s %-10s | %6d %8s [%6s,%6s] | %6d %8s [%6s,%6s] | %s' % (r['feature'], r['bin'], r['d_n'], f(r['d_mean']), f(r['d_lo']), f(r['d_hi']),
              r['t_n'], f(r['t_mean']), f(r['t_lo']), f(r['t_hi']), 'YES' if rep else ''))
    if a.out: Path(a.out).write_text(json.dumps(dict(cut=cut, cells=rows), indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
