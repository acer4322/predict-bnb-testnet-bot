"""Read-only REAL-PATH replay of the toy 'favourite + one-and-done stop-loss' mechanism on historical public books (no engine, no queue):
 buy 15 shares of the current favourite every 2 s from t=12 s while its mid in [lo,hi] (cap 300 shares), paying the side's ASK at t+DELAY;
 exit: when a held side's mid <= EXIT, sell all at that side's BID at t+DELAY, then stop trading that market. STOP290 respected.
 Prices: UP ask=best_ask, UP bid=best_bid; DOWN ask=1-best_bid, DOWN bid=1-best_ask. Depth ignored (15-share clips), zero fee.
 Winner: offline label where available, else inferred from the last book mid (flagged). G1-G4 as in toy_graduation.py.
  python tools/real_replay_stop.py BOOKS_ROOT [--labels L.json] [--exit .44] [--delay 1.0]"""
import argparse, bisect, gzip, json, random, statistics as S
from pathlib import Path


def jl(p): p = Path(p); return json.loads((gzip.open(p, 'rb') if p.suffix == '.gz' else open(p, 'rb')).read())


def load(root, labels):
    out = {}
    for d in Path(root).rglob('public_*.json.gz'):
        if 'parity' in str(d): continue
        pb = jl(d); m = int(pb['market']['market_id']); st = int(pb['market']['window_start_ms'])
        bs = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
        if len(bs) < 50 or m in out: continue
        ts = [(b['source_ms'] - st) / 1000. for b in bs]; lastmid = (bs[-1]['best_bid'] + bs[-1]['best_ask']) / 2
        w = labels.get(m) or ('UP' if lastmid > .5 else 'DOWN')
        out[m] = dict(ts=ts, bs=bs, win=w, labelled=m in labels)
    return out


def at(mk, t):
    i = bisect.bisect_right(mk['ts'], t) - 1
    return mk['bs'][i] if i >= 0 else None


def quotes(b, side):
    return (b['best_ask'], b['best_bid']) if side == 'UP' else (1 - b['best_bid'], 1 - b['best_ask'])  # (ask, bid)


def run(mk, exit_lvl, delay, lo=.60, hi=.80, cap=300., tick=15., dec_t=12.):
    inv = {'UP': 0., 'DOWN': 0.}; net = 0.; done = False; t = dec_t
    while t < 290.:
        b = at(mk, t)
        if b is None: t += 2; continue
        mid = (b['best_bid'] + b['best_ask']) / 2
        if exit_lvl is not None and not done:
            for s in ('UP', 'DOWN'):
                if inv[s] > 0 and (mid if s == 'UP' else 1 - mid) <= exit_lvl:
                    bx = at(mk, t + delay) or b; net -= inv[s] * max(.01, quotes(bx, s)[1]); inv[s] = 0.; done = True
        if not done:
            cur = 'UP' if mid >= .5 else 'DOWN'; cm = mid if cur == 'UP' else 1 - mid
            if lo <= cm <= hi and inv['UP'] + inv['DOWN'] < cap:
                bx = at(mk, t + delay) or b; inv[cur] += tick; net += tick * min(.99, quotes(bx, cur)[0])
        t += 2.
    w = mk['win']; return (inv[w] - net), net


def classify(mk, dec_t=12.):
    b = at(mk, dec_t); fav = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'
    flip = any(((x['best_bid'] + x['best_ask']) / 2 if fav == 'UP' else 1 - (x['best_bid'] + x['best_ask']) / 2) <= .4 for t, x in zip(mk['ts'], mk['bs']) if dec_t <= t < 290)
    return 'NO_FLIP' if not flip else ('FALSE_FLIP' if fav == mk['win'] else 'TRUE_FLIP')


def tail(xs, q=.05): s = sorted(xs); return S.fmean(s[:max(1, int(len(s) * q))])


def ci(xs, rng, n=1000): ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(n)); return ms[int(.025 * n)], ms[int(.975 * n)]


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('root'); ap.add_argument('--labels', default=''); ap.add_argument('--exits', type=float, nargs='*', default=[.45, .44, .42])
    ap.add_argument('--delay', type=float, default=1.0); a = ap.parse_args(); rng = random.Random(3)
    lab = {int(r['market_id']): r['winner'] for r in jl(a.labels).get('records', [])} if a.labels else {}
    M = load(a.root, lab)
    for nm, sub in (('LABELLED markets', {m: v for m, v in M.items() if v['labelled']}), ('ALL markets (winner inferred from last mid where unlabelled)', M)):
        ms = sorted(sub); grp = {m: classify(sub[m]) for m in ms}; n = len(ms)
        f = {k: sum(grp[m] == k for m in ms) / n for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}
        base = {m: run(sub[m], None, a.delay) for m in ms}
        part = lambda r: ([r[m][0] for m in ms if grp[m] == 'NO_FLIP'], [r[m][0] for m in ms if grp[m] != 'NO_FLIP'], [r[m][0] for m in ms if grp[m] == 'TRUE_FLIP'], [r[m][0] for m in ms if grp[m] == 'FALSE_FLIP'])
        bnf, bfl, _, _ = part(base)
        print('\n=== %s | n=%d | no-flip %.0f%% false %.0f%% true %.0f%% | delay %.1fs' % (nm, n, 100 * f['NO_FLIP'], 100 * f['FALSE_FLIP'], 100 * f['TRUE_FLIP'], a.delay))
        print('%-14s %8s %8s %8s %8s | %5s %5s %5s | G1 G2 G3 G4' % ('strategy', 'overall', 'noflip', 'flip', 'flip w5%', 'R', 'Lm', 'Lt'))
        for name, ex in [('BASE no stop', None)] + [('stop <= %.2f' % e, e) for e in a.exits]:
            r = base if ex is None else {m: run(sub[m], ex, a.delay) for m in ms}; nf, fl, tr, fa = part(r); pn = [r[m][0] for m in ms]
            lo_, hi_ = ci(pn, rng); l2 = ci(nf, rng)[0] if len(nf) > 4 else -1
            R = S.fmean(nf) / S.fmean(bnf); Lm = S.fmean(fl) / S.fmean(bfl) if S.fmean(bfl) else float('nan'); Lt = tail(fl) / tail(bfl) if tail(bfl) else float('nan')
            G4 = all(f['NO_FLIP'] * S.fmean(nf) + (f['TRUE_FLIP'] + f['FALSE_FLIP']) * (w * S.fmean(tr) + (1 - w) * S.fmean(fa)) > 0 for w in (.4, .5, .6, .7, .8)) if tr and fa else False
            G3 = ex is not None and R >= .8 and Lm <= .7 and Lt <= .7
            print('%-14s %8.1f %8.1f %8.1f %8.1f | %5.2f %5.2f %5.2f | %2s %2s %2s %2s   overall CI [%.1f, %.1f]' % (name, S.fmean(pn), S.fmean(nf), S.fmean(fl), tail(fl), R, Lm, Lt, 'Y' if lo_ > 0 else '.', 'Y' if l2 > 0 else '.', 'Y' if G3 else '.', 'Y' if G4 else '.', lo_, hi_))


if __name__ == '__main__':
    main()
