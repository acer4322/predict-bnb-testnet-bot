"""Does the FAV edge drift over time?  519 true-labelled markets ordered by market_id (~time).  Per bin of ~52 markets: BASE mean PnL, ROI on cost,
no-flip share, mean favourite win-rate minus mean paid price (the 'underpricing' edge), reversal rates; plus a linear trend test (market-bootstrap).
usage: python tools/edge_drift_check.py PUBLIC_ROOT LABELS_ALL_TRUE.json"""
import sys, random, statistics as S
import real_replay_stop as rr
lab = {int(r['market_id']): r['winner'] for r in rr.jl(sys.argv[2])['records']}
M = {m: v for m, v in rr.load(sys.argv[1], lab).items() if v['labelled']}; ids = sorted(M)
rows = []
for m in ids:
    pnl, cost = rr.run(M[m], None, 1.0); b = rr.at(M[m], 12.); fav = 'UP' if (b['best_bid'] + b['best_ask']) / 2 >= .5 else 'DOWN'
    rows.append(dict(m=m, pnl=pnl, cost=cost, cls=rr.classify(M[m]), favwin=fav == M[m]['win'], favmid=max((b['best_bid'] + b['best_ask']) / 2, 1 - (b['best_bid'] + b['best_ask']) / 2)))
print('%-19s %4s | %7s %7s | noflip%% false%% true%% | fav win%% vs fav mid@12s (edge pp)' % ('market id range', 'n', 'mean', 'ROI%'))
k = 10; sz = len(rows) // k
for i in range(k):
    rs = rows[i * sz:(i + 1) * sz if i < k - 1 else None]; c = [r['cost'] for r in rs]
    print('%d-%d %4d | %7.1f %6.1f%% | %5.0f%% %5.0f%% %5.0f%% | %5.1f%% vs %5.1f%% (%+.1f)' % (rs[0]['m'], rs[-1]['m'], len(rs), S.fmean(r['pnl'] for r in rs), 100 * sum(r['pnl'] for r in rs) / sum(c),
          *(100 * sum(r['cls'] == q for r in rs) / len(rs) for q in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')), 100 * S.fmean(r['favwin'] for r in rs), 100 * S.fmean(r['favmid'] for r in rs),
          100 * (S.fmean(r['favwin'] for r in rs) - S.fmean(r['favmid'] for r in rs))))
rng = random.Random(3); n = len(rows)
def slope(rs):
    x = [i for i in range(len(rs))]; y = [r['pnl'] for r in rs]; mx, my = S.fmean(x), S.fmean(y)
    return sum((a - mx) * (b - my) for a, b in zip(x, y)) / sum((a - mx) ** 2 for a in x)
s0 = slope(rows) * 100
bs = sorted(slope([rng.choice(rows) for _ in rows]) * 100 for _ in range(500))
print('\nlinear trend of per-market PnL: %+.2f per 100 markets (bootstrap CI treats order as fixed-by-index; indicative only)' % s0)
a, b = rows[:n // 2], rows[n // 2:]
print('first half mean %.1f, second half mean %.1f; fav win%% %.1f vs %.1f; mean fav mid %.1f vs %.1f' % (S.fmean(r['pnl'] for r in a), S.fmean(r['pnl'] for r in b), 100 * S.fmean(r['favwin'] for r in a), 100 * S.fmean(r['favwin'] for r in b),
      100 * S.fmean(r['favmid'] for r in a), 100 * S.fmean(r['favmid'] for r in b)))
