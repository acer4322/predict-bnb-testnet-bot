"""Anatomy of FAV true-flip loss (descriptive, no selection). Per asset (BTC 474 / ETH 669 usable markets), TRUE_FLIP markets under frozen FAV (band .55-.70, 15 sh/2 s from t=12, cap 300, freeze at fav mid <= .40).
Loss = total cost (favourite pays 0).  Decomposes cost by: when bought (time bin), price at purchase, state at purchase (falling vs stable), distance to freeze, how many buys, cap reached; flip speed.
usage: python tools/true_flip_loss_anatomy.py ETH_CACHE.pkl BTC_CACHE.pkl"""
import sys, pickle, statistics as S, collections
def run(name, path):
    ids, A_, WIN, CL = pickle.load(open(path, 'rb')); rows = []; allrows = []
    for m in ids:
        A = A_[m]; Fv = 'UP' if A[12][0] >= .5 else 'DOWN'; fm = lambda t: A[t][0] if Fv == 'UP' else 1 - A[t][0]
        buys = []; sh = 0.; frz = None
        for t in range(12, 290, 2):
            x = fm(t)
            if frz is None and t > 12 and x <= .40: frz = t
            if frz is not None or not (.55 <= x <= .70) or sh >= 300: continue
            p = min(.99, A[t][1] if Fv == 'UP' else A[t][2]); sh += 15; buys.append((t, x, p, fm(max(12, t - 10))))
        r = dict(m=m, cls=CL[m], buys=buys, frz=frz, cost=sum(15 * b[2] for b in buys), pnl=(sh if WIN[m] == Fv else 0.) - sum(15 * b[2] for b in buys), cap=sh >= 300, fm12=fm(12))
        # peak before freeze, last time in-band-top
        if frz: r['t_last_ge65'] = max([t for t in range(12, frz) if fm(t) >= .65] or [12]); r['fall_time'] = frz - r['t_last_ge65']; r['peak'] = max(fm(t) for t in range(12, frz))
        allrows.append(r)
        if CL[m] == 'TRUE_FLIP': rows.append(r)
    print('\n==== %s: markets %d, TRUE_FLIP %d (%.0f%%), mean PnL true-flip %.1f ====' % (name, len(ids), len(rows), 100 * len(rows) / len(ids), S.fmean(r['pnl'] for r in rows)))
    tot = sum(r['cost'] for r in rows)
    print('mean buys %.1f, mean cost %.1f, mean avg buy price %.3f, cap(300sh) reached %.0f%%, no-trade %.0f%%' % (S.fmean(len(r['buys']) for r in rows), S.fmean(r['cost'] for r in rows), tot / max(1, 15 * sum(len(r['buys']) for r in rows)), 100 * S.fmean(r['cap'] for r in rows), 100 * S.fmean(not r['buys'] for r in rows)))
    c = sorted(r['cost'] for r in rows); print('cost quantiles q10/50/90/99: %.0f %.0f %.0f %.0f ; top-10%% markets carry %.0f%% of loss' % (c[len(c) // 10], c[len(c) // 2], c[int(.9 * len(c))], c[int(.99 * len(c)) - 1], 100 * sum(c[int(.9 * len(c)):]) / tot))
    bt = collections.defaultdict(float); bp = collections.defaultdict(float); bs_ = collections.defaultdict(float); bd = collections.defaultdict(float)
    for r in rows:
        f = r['frz']
        for t, x, p, x10 in r['buys']:
            bt[min(t // 60, 4)] += 15 * p; bp[int((x - .55) / .05)] += 15 * p; bs_['falling>=.05 in 10s' if x10 - x >= .05 else ('rising/stable' if x10 - x < .02 else 'drift down .02-.05')] += 15 * p
            if f: bd['<=10s' if f - t <= 10 else '10-30s' if f - t <= 30 else '30-90s' if f - t <= 90 else '>90s'] += 15 * p
            else: bd['never froze'] += 15 * p
    pr = lambda d, nm, order=None: print(nm, {k: '%.0f%%' % (100 * v / tot) for k, v in (sorted(d.items()) if order is None else [(k, d[k]) for k in order if k in d])})
    pr(bt, 'share of loss by buy minute (0=12-60s..4=240s+):'); pr(bp, 'by fav mid at buy (0=.55-.60,1=.60-.65,2=.65-.70):'); pr(bs_, 'by state at buy:'); pr(bd, 'by time before freeze:', ['<=10s', '10-30s', '30-90s', '>90s', 'never froze'])
    fr = [r for r in rows if r['frz']]; ft = [r['fall_time'] for r in fr]; print('freeze reached %.0f%%; seconds from last mid>=.65 to freeze: q25/50/75 = %s ; peak fav mid before freeze median %.2f' % (100 * len(fr) / len(rows), [sorted(ft)[int(q * (len(ft) - 1))] for q in (.25, .5, .75)], S.median(r['peak'] for r in fr)))
    # who is exposed: fav mid at t=12 and time of freeze
    for lab, f in (('fav mid at 12s <.60', lambda r: r['fm12'] < .60), ('.60-.70', lambda r: .60 <= r['fm12'] < .70), ('>=.70', lambda r: r['fm12'] >= .70)):
        s = [r for r in rows if f(r)]; a = [r for r in allrows if f(r)]; print('  %-20s true-flip n=%d mean loss %.1f | share of all markets in group that true-flip %.0f%% | group FAV mean pnl %.1f' % (lab, len(s), S.fmean(r['pnl'] for r in s) if s else 0, 100 * len(s) / max(1, len(a)), S.fmean(r['pnl'] for r in a) if a else 0))
    for lab, lo_, hi_ in (('freeze <60s', 0, 60), ('60-120s', 60, 120), ('120-180s', 120, 180), ('180-240s', 180, 240), ('>=240s or none', 240, 999)):
        s = [r for r in rows if (r['frz'] or 999) >= lo_ and (r['frz'] or 999) < hi_]
        if s: print('  %-16s n=%d mean loss %.1f mean buys %.1f' % (lab, len(s), S.fmean(r['pnl'] for r in s), S.fmean(len(r['buys']) for r in s)))
    nf = [r for r in allrows if r['cls'] == 'NO_FLIP' and r['buys']]; ff = [r for r in allrows if r['cls'] == 'FALSE_FLIP']
    print('compare: NO_FLIP mean buys %.1f cost %.0f | FALSE_FLIP mean buys %.1f cost %.0f | TRUE_FLIP mean buys %.1f cost %.0f' % (S.fmean(len(r['buys']) for r in nf), S.fmean(r['cost'] for r in nf), S.fmean(len(r['buys']) for r in ff), S.fmean(r['cost'] for r in ff), S.fmean(len(r['buys']) for r in rows), S.fmean(r['cost'] for r in rows)))
    # breakeven: payoff per share if win=1 -> edge per share by class
    for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP'):
        s = [r for r in allrows if r['cls'] == k]; sh = sum(15 * len(r['buys']) for r in s); print('  %-10s per-share pnl %.3f (avg buy price %.3f)' % (k, sum(r['pnl'] for r in s) / sh, sum(r['cost'] for r in s) / sh))
run('ETH', sys.argv[1]); run('BTC', sys.argv[2])
