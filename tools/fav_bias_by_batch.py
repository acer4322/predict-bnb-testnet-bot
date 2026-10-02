"""Read-only: favourite underpricing (realised win rate minus price, pp) from historical public books, by market-id block, market-clustered CI.
Winner = offline label if given, else inferred from the last book mid (100% agreement with labels on the 100 labelled fresh-100a markets).
  python tools/fav_bias_by_batch.py BOOKS_ROOT [--labels L.json] [--blocks 2656000 2662200 2671700]"""
import argparse, bisect, collections, gzip, json, random, statistics as S
from pathlib import Path


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--labels', default=''); ap.add_argument('--blocks', type=int, nargs='*', default=[2656000, 2662200, 2671700])
    a = ap.parse_args(); lab = {}
    if a.labels: lab = {int(r['market_id']): r['winner'] for r in json.loads(open(a.labels, 'rb').read()).get('records', [])}
    M = {}
    for d in Path(a.root).rglob('public_*.json.gz'):
        if 'parity' in str(d): continue
        pb = json.loads(gzip.open(d).read()); m = int(pb['market']['market_id']); st = int(pb['market']['window_start_ms'])
        bs = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
        if len(bs) >= 50 and m not in M: M[m] = ([(b['source_ms'] - st) / 1000. for b in bs], [(b['best_bid'] + b['best_ask']) / 2 for b in bs])
    blk = lambda m: sum(m >= b for b in a.blocks)
    rows = collections.defaultdict(list)
    for m, (ts, mids) in M.items():
        w = lab.get(m) or ('UP' if mids[-1] > .5 else 'DOWN')
        for t in (30, 60, 90, 120, 150, 180, 210, 240):
            i = bisect.bisect_right(ts, t) - 1
            if i < 0: continue
            fav = 'UP' if mids[i] >= .5 else 'DOWN'; fm = mids[i] if fav == 'UP' else 1 - mids[i]
            if .55 <= fm <= .85: rows[blk(m)].append((m, (1. if fav == w else 0.) - fm))
    rng = random.Random(2)
    def st(r):
        by = collections.defaultdict(list)
        for m, v in r: by[m].append(v)
        ids = list(by); ms = sorted(S.fmean([x for i in (rng.choice(ids) for _ in ids) for x in by[i]]) for _ in range(600))
        return S.fmean([v for _, v in r]), ms[15], ms[585], len(ids)
    for k in sorted(rows):
        mu, l, h, n = st(rows[k]); print('block %d: %+5.1f [%+5.1f, %+5.1f] pp, markets %d' % (k, 100 * mu, 100 * l, 100 * h, n))
    mu, l, h, n = st([x for r in rows.values() for x in r]); print('ALL: %+5.1f [%+5.1f, %+5.1f] pp, markets %d' % (100 * mu, 100 * l, 100 * h, n))


if __name__ == '__main__':
    main()
