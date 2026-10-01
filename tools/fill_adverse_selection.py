"""Read-only: signed value of OUR fills vs the public mid at fill time and +1/+5/+10 s, split by phase and route.

Per path directory (needs execution_clock.json, public_<id>.json.gz, and result.json or flips.json for DECIDE/FLIP times):
  fill = native receipt (maker=1 -> PASSIVE, maker=0 -> ACTIVE/taker); order_id -> carrier key 'UP_n'/'DOWN_n' gives our side.
  UP buy price = receipt price; DOWN buy price = 1 - receipt price (native book is the UP book; DOWN buys are sells of UP).
  value_h = side_mid(t+h) - buy_price (cents per share; positive = the fill was good for us). t = exchange_ts; mid from the public
  book rows with source_ms <= t (UP mid, 1-mid for DOWN).
Phases: PRE_DECIDE (before DECIDE), PRE_FLIP (decided, no FLIP yet), POST_FLIP (after the first FLIP).
Output: qty-weighted mean per phase x route with a path-clustered bootstrap 95% CI. Settlement labels are not used.

  python tools/fill_adverse_selection.py ROOT [--out fills_summary.json] [--boot 1000]
"""
import argparse, bisect, collections, gzip, json, random, statistics as S
from pathlib import Path

HORIZONS = (1000, 5000, 10000)


def jload(p):
    p = Path(p)
    return json.loads((gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')).read())


def path_rows(d):
    ex = jload(d / 'execution_clock.json')
    pub = next(iter(sorted(d.glob('public_*.json.gz'))))
    pb = jload(pub)
    ev_src = d / 'flips.json'
    ev = (jload(ev_src)['events'] if ev_src.exists() else jload(d / 'result.json')['v12g']['events'])
    dec = next((e['t'] for e in ev if e['kind'] == 'DECIDE'), None)
    flip = next((e['t'] for e in ev if e['kind'] == 'FLIP'), None)
    books = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
    ts = [b['source_ms'] for b in books]; mids = [(b['best_bid'] + b['best_ask']) / 2. for b in books]
    car = {int(k.rsplit('_', 1)[1]): (k.split('_')[0], v['route']) for k, v in ex['carriers'].items()}

    def mid(t_ms):
        i = bisect.bisect_right(ts, t_ms) - 1
        return mids[i] if i >= 0 else None

    rows = []
    for r in ex['receipts']:
        q = float(r['qty'])
        if q < 1e-9 or r['order_id'] not in car: continue
        side, _ = car[r['order_id']]
        price = float(r['price']) if side == 'UP' else 1. - float(r['price'])
        t = int(r['exchange_ts'] // 1_000_000)
        m0 = mid(t)
        if m0 is None: continue
        sm = (lambda m: m) if side == 'UP' else (lambda m: 1. - m)
        vals = {'v0': sm(m0) - price}
        for h in HORIZONS:
            mh = mid(t + h)
            vals['v%d' % (h // 1000)] = (sm(mh) - price) if mh is not None and t + h <= ts[-1] else None
        phase = 'PRE_DECIDE' if dec is None or t < dec else ('POST_FLIP' if flip is not None and t >= flip else 'PRE_FLIP')
        rows.append(dict(market=pb['market']['market_id'], t=t, side=side, route='PASSIVE' if r['maker'] else 'ACTIVE', phase=phase, qty=q, **vals))
    return rows


def wmean(rs, k):
    xs = [(r['qty'], r[k]) for r in rs if r.get(k) is not None]
    w = sum(q for q, _ in xs)
    return sum(q * v for q, v in xs) / w if w else None


def boot(by_path, keyf, k, nb, rng):
    ids = list(by_path); out = []
    for _ in range(nb):
        rs = [r for i in (rng.choice(ids) for _ in ids) for r in by_path[i] if keyf(r)]
        m = wmean(rs, k)
        if m is not None: out.append(m)
    out.sort()
    return (out[int(.025 * len(out))], out[int(.975 * len(out))]) if len(out) > 20 else (None, None)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--out', default=''); ap.add_argument('--boot', type=int, default=500)
    a = ap.parse_args()
    dirs = sorted({p.parent for p in Path(a.root).rglob('execution_clock.json')})
    by_path, errs = {}, []
    for d in dirs:
        try: by_path[str(d)] = path_rows(d)
        except Exception as ex: errs.append((str(d), type(ex).__name__ + ': ' + str(ex)))
    allr = [r for rs in by_path.values() for r in rs]
    rng = random.Random(1); res = {}
    print('paths=%d fills=%d errors=%d' % (len(by_path), len(allr), len(errs)))
    print('%-10s %-8s %7s %9s | %-26s | %-26s | %-26s' % ('phase', 'route', 'paths', 'qty', 'edge at fill  (cents)', '+5s value (cents)', '+10s value (cents)'))
    for ph in ('PRE_DECIDE', 'PRE_FLIP', 'POST_FLIP', 'ALL'):
        for rt in ('PASSIVE', 'ACTIVE'):
            keyf = lambda r, ph=ph, rt=rt: (ph == 'ALL' or r['phase'] == ph) and r['route'] == rt
            rs = [r for r in allr if keyf(r)]
            if not rs: continue
            cells = []
            for k in ('v0', 'v5', 'v10'):
                m = wmean(rs, k); lo, hi = boot(by_path, keyf, k, a.boot, rng) if m is not None else (None, None)
                cells.append((m, lo, hi))
            res['%s/%s' % (ph, rt)] = dict(paths=len({r['market'] for r in rs}), qty=sum(r['qty'] for r in rs), **{k: c for k, c in zip(('v0', 'v5', 'v10'), cells)})
            f = lambda c: 'NA' if c[0] is None else '%+6.2f [%+5.2f,%+5.2f]' % (100 * c[0], 100 * (c[1] or float('nan')), 100 * (c[2] or float('nan')))
            print('%-10s %-8s %7d %9.0f | %-26s | %-26s | %-26s' % (ph, rt, len({r['market'] for r in rs}), sum(r['qty'] for r in rs), f(cells[0]), f(cells[1]), f(cells[2])))
    for e in errs[:5]: print('ERROR', e)
    if a.out: Path(a.out).write_text(json.dumps(dict(summary=res, errors=errs), indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
