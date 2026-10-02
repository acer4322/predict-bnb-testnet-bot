"""Read-only: use INFERRED Target maker-order lifecycles (V2.1) to separate 'placed deep' from 'became deep'.
Input  target_orders_export.json.gz : list of {market_id, side UP|DOWN, price, qty_placed, place_ms, cancel_ms|null, fill_qty,
                                      first_fill_ms|null, last_fill_ms|null, confidence (number or label), method}
       (place/cancel times are INFERRED, not observed -> every number here inherits that uncertainty; confidence is reported.)
Books  from our public_<market>.json.gz (markets in --books-root). UP book; DOWN best bid = 1 - best UP ask.
Outputs: placement offset (ticks below best bid AT PLACEMENT) vs offset at first fill; lifetime; fill probability and filled-qty share by
placement depth; and the value at +5 s of the filled part by placement depth.  --shift-ms aligns Target time to our book time (default 1000,
the handoff's measured ~1 s; 0 also shown).
  python tools/target_lifecycle_depth.py target_orders_export.json.gz --books-root FILLS_DIR [--min-conf X]
"""
import argparse, bisect, collections, gzip, json, statistics as S
from pathlib import Path


def jload(p):
    p = Path(p); return json.loads((gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')).read())


def load_books(root):
    out = {}
    for d in Path(root).rglob('public_*.json.gz'):
        pb = jload(d); bs = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
        out[int(pb['market']['market_id'])] = ([b['source_ms'] for b in bs], bs)
    return out


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('export'); ap.add_argument('--books-root', required=True)
    ap.add_argument('--min-conf', type=float, default=None); ap.add_argument('--shift-ms', type=int, nargs='*', default=[1000, 0])
    a = ap.parse_args(); orders = jload(a.export); books = load_books(a.books_root)
    if a.min_conf is not None: orders = [o for o in orders if isinstance(o.get('confidence'), (int, float)) and o['confidence'] >= a.min_conf]
    orders = [o for o in orders if o['market_id'] in books and o.get('place_ms') is not None]
    print('orders with our book: %d | markets %d | methods %s' % (len(orders), len({o['market_id'] for o in orders}), dict(collections.Counter(o.get('method') for o in orders))))
    conf = [o['confidence'] for o in orders if isinstance(o.get('confidence'), (int, float))]
    if conf: print('confidence: median %.2f, p10 %.2f' % (S.median(conf), sorted(conf)[len(conf) // 10]))
    def at(m, t):
        ts, bs = books[m]; i = bisect.bisect_right(ts, t) - 1; return bs[i] if i >= 0 else None
    for shift in a.shift_ms:
        rows = []
        for o in orders:
            m = o['market_id']; s = o['side']; px = float(o['price']); tp = o['place_ms'] + shift
            b = at(m, tp)
            if not b: continue
            bb = lambda bk: bk['best_bid'] if s == 'UP' else 1. - bk['best_ask']
            off_place = round((bb(b) - px) / .01)
            ft = o.get('first_fill_ms'); fq = float(o.get('fill_qty') or 0); q = float(o.get('qty_placed') or 0)
            off_fill = v5 = None
            if ft is not None and fq > 0:
                bf = at(m, ft + shift); b5 = at(m, ft + shift + 5000)
                if bf: off_fill = round((bb(bf) - px) / .01)
                if bf and b5 and ft + shift + 5000 <= books[m][0][-1]:
                    sm = lambda bk: (bk['best_bid'] + bk['best_ask']) / 2 if s == 'UP' else 1. - (bk['best_bid'] + bk['best_ask']) / 2
                    v5 = sm(b5) - px
            life = ((o['cancel_ms'] - o['place_ms']) / 1000.) if o.get('cancel_ms') else None
            rows.append(dict(op=off_place, of=off_fill, fq=fq, q=q, v5=v5, life=life, tfill=((ft - o['place_ms']) / 1000.) if ft else None))
        print('\n--- time shift %+d ms | orders %d ---' % (shift, len(rows)))
        print('%-22s %7s %9s %9s %9s %10s' % ('placement depth', 'orders', 'fill rate', 'qty share', 'filled sh', 'value+5s c'))
        W = sum(r['fq'] for r in rows)
        for nm, f in (('at/above best bid', lambda r: r['op'] <= 0), ('1 tick below', lambda r: r['op'] == 1), ('2 ticks below', lambda r: r['op'] == 2), ('3+ ticks below', lambda r: r['op'] >= 3)):
            g = [r for r in rows if f(r)]
            if not g: continue
            fl = [r for r in g if r['fq'] > 0]; w = sum(r['fq'] for r in g); vv = [(r['fq'], r['v5']) for r in fl if r['v5'] is not None]
            print('%-22s %7d %8.0f%% %8.0f%% %9.0f %10s' % (nm, len(g), 100 * len(fl) / len(g), 100 * w / W if W else 0, w, ('%+.2f' % (100 * sum(a * b for a, b in vv) / sum(a for a, _ in vv))) if vv else 'NA'))
        fl = [r for r in rows if r['fq'] > 0 and r['of'] is not None]
        if fl:
            print('filled orders: placement offset vs offset at first fill (qty-weighted share):')
            T = collections.defaultdict(float)
            for r in fl:
                T[('placed ' + ('<=0' if r['op'] <= 0 else '1' if r['op'] == 1 else '2+'), 'fill ' + ('<=0' if r['of'] <= 0 else '1' if r['of'] == 1 else '2+'))] += r['fq']
            Wf = sum(T.values())
            for k in sorted(T): print('   %-10s -> %-9s %5.1f%%' % (k[0], k[1], 100 * T[k] / Wf))
            deep_fill = sum(v for (p, f), v in T.items() if f == 'fill 2+'); became = sum(v for (p, f), v in T.items() if f == 'fill 2+' and p == 'placed <=0')
            if deep_fill: print('   of volume filled 2+ ticks deep, placed at/above best bid: %.0f%%  (=> became deep after market moved)' % (100 * became / deep_fill))
        lf = [r['life'] for r in rows if r['life'] is not None]
        if lf: print('lifetime of cancelled orders (s): median %.1f p10 %.1f p90 %.1f | filled-order time-to-first-fill median %.1f s' % (S.median(lf), sorted(lf)[len(lf) // 10], sorted(lf)[9 * len(lf) // 10], S.median([r['tfill'] for r in rows if r['tfill'] is not None] or [float('nan')])))


if __name__ == '__main__':
    main()
