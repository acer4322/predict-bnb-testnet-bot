"""Read-only: from PRE-COMPUTED V2.1 parent-lifecycle features, is the Target's deep maker volume 'placed deep' or 'became deep'?
Input target_lifecycle_features.json.gz : list of rows (one per V2.1 parent order that HAS fills; unfilled orders are not in V2.1):
  market_id, side(UP|DOWN), price (own-side buy price), filled_qty, placement_first_ms, first_target_ms, resting_ms,
  best_bid_at_placement, best_bid_at_fill   (own-side best bid, DOWN best bid = 1 - best UP ask, from the book at those instants),
  mid_side_at_fill, mid_side_at_fill_plus5s (nullable), confidence, placement_coverage, fill_allocation_coverage, post_action
Depth = round((best_bid - price)/0.01) ticks below the best bid (<=0: at/above).  Placement times are INFERRED (V2.1), second-level fills.
Because only filled orders exist, results describe filled volume, NOT how the Target quotes overall (deep orders fill less often).
  python tools/target_lifecycle_depth2.py FEATURES [--min-conf X] [--boot 500]
"""
import argparse, collections, gzip, json, random, statistics as S
from pathlib import Path


def jload(p):
    p = Path(p); return json.loads((gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')).read())


def d(bb, px): return round((bb - px) / .01)
def bucket(k): return '<=0' if k <= 0 else str(k) if k <= 2 else '3+'


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('features'); ap.add_argument('--min-conf', type=float, default=None); ap.add_argument('--boot', type=int, default=500)
    a = ap.parse_args(); rng = random.Random(1); rows = jload(a.features); n0 = len(rows)
    rows = [r for r in rows if r.get('placement_first_ms') is not None and r.get('best_bid_at_placement') is not None and r.get('best_bid_at_fill') is not None]
    bad = [r for r in rows if r['placement_first_ms'] > r['first_target_ms']]
    rows = [r for r in rows if r['placement_first_ms'] <= r['first_target_ms']]
    if a.min_conf is not None: rows = [r for r in rows if (r.get('confidence') or 0) >= a.min_conf]
    for r in rows: r['dp'] = d(r['best_bid_at_placement'], r['price']); r['df'] = d(r['best_bid_at_fill'], r['price'])
    W = sum(r['filled_qty'] for r in rows)
    print('parents: %d in, %d usable (%d dropped for missing books, %d for placement after first fill) | markets %d | filled shares %.0f' % (
        n0, len(rows), n0 - len(rows) - len(bad), len(bad), len({r['market_id'] for r in rows}), W))
    cf = [r['confidence'] for r in rows if r.get('confidence') is not None]
    if cf: print('confidence median %.2f p10 %.2f' % (S.median(cf), sorted(cf)[len(cf) // 10]))
    print('\nplacement depth -> depth at first fill (share of filled volume)')
    T = collections.defaultdict(float)
    for r in rows: T[(bucket(r['dp']), bucket(r['df']))] += r['filled_qty']
    ks = ['<=0', '1', '2', '3+']
    print('%-12s' % 'placed\\fill' + ''.join('%9s' % k for k in ks) + '   row total')
    for p in ks:
        tot = sum(T[(p, f)] for f in ks)
        if tot: print('%-12s' % p + ''.join('%8.1f%%' % (100 * T[(p, f)] / W) for f in ks) + '   %5.1f%%' % (100 * tot / W))
    deep = sum(T[(p, f)] for p in ks for f in ('2', '3+')); became = sum(T[('<=0', f)] + T[('1', f)] for f in ('2', '3+'))
    if deep: print('\nvolume filled >=2 ticks below best bid: %.1f%% of all | of it, PLACED at/above best bid or 1 tick below (became deep): %.0f%% | placed >=2 deep: %.0f%%' % (100 * deep / W, 100 * became / deep, 100 * (deep - became) / deep))
    print('\nby resting time (placement -> first fill):')
    for lo, hi in ((0, 2), (2, 10), (10, 30), (30, 61)):
        g = [r for r in rows if lo <= (r.get('resting_ms') or 0) / 1000. < hi]
        if g:
            w = sum(r['filled_qty'] for r in g); dd = sum(r['filled_qty'] for r in g if r['df'] >= 2)
            print('  %2d-%2ds  %5.1f%% of volume | deep(>=2) at fill %4.0f%% | placed deep(>=2) %4.0f%%' % (lo, hi, 100 * w / W, 100 * dd / w, 100 * sum(r['filled_qty'] for r in g if r['dp'] >= 2) / w))
    print('\nvalue +5 s by PLACEMENT depth (cents/share, qty-weighted, market-clustered 95%% CI):')
    by = collections.defaultdict(list)
    for r in rows:
        if r.get('mid_side_at_fill_plus5s') is not None: by[r['market_id']].append(r)
    def val(sel, rs):
        z = [(r['filled_qty'], r['mid_side_at_fill_plus5s'] - r['price']) for r in rs if sel(r)]; w = sum(a for a, _ in z); return sum(a * b for a, b in z) / w if w else None
    ids = list(by)
    for nm, sel in (('placed <=0', lambda r: r['dp'] <= 0), ('placed 1', lambda r: r['dp'] == 1), ('placed 2+', lambda r: r['dp'] >= 2), ('ALL', lambda r: True)):
        all_ = [r for i in ids for r in by[i]]; m = val(sel, all_)
        if m is None: continue
        bs = []
        for _ in range(a.boot):
            v = val(sel, [r for i in (rng.choice(ids) for _ in ids) for r in by[i]])
            if v is not None: bs.append(v)
        bs.sort(); print('  %-12s %+6.2f  [%+.2f, %+.2f]  (n parents %d)' % (nm, 100 * m, 100 * bs[int(.025 * len(bs))], 100 * bs[int(.975 * len(bs))], sum(1 for r in all_ if sel(r))))
    print('\nCAVEATS: filled parents only; inferred placement times; seconds-level fills; older market period.')


if __name__ == '__main__':
    main()
