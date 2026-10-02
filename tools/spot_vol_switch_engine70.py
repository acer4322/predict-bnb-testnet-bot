"""Out-of-sample check of the spot-volatility switch on the 70 newest real-event markets (engine results: FAV_TAKER and UNDER_TAKER from strategy_lab, true labels c547dc8a).
Thresholds = discovery quantiles of the 519 replay set (no refit).  Reports all 70 and the 25 markets with id > 2807162 that no earlier analysis used.
usage: python tools/spot_vol_switch_engine70.py PUBLIC_ROOT LABELS.json btc_1s.json.gz under70.json events_markets_dir"""
import sys, json, random, statistics as S, pathlib
args = sys.argv[:]; sys.argv = args[:4]; U70, EVD = args[4], args[5]
import spot_regime_auc as R
u = json.load(open(U70)); ud = {r['market']: r for r in u['UNDER_TAKER']}; fv = {r['market']: r for r in u['FAV_TAKER']}
ws = {int(p.name): int(json.load(open(p / 'META.json'))['window_start_ms']) // 1000 for p in pathlib.Path(EVD).iterdir()}
feat = {m: {'rv_5m': R.rv(ws[m], 300), 'rv_30m': R.rv(ws[m], 1800)} for m in ud}
rng = random.Random(3); last = max(R.ids)
def qv(name, q): v = sorted(R.feat[m][name] for m in R.D if R.feat[m][name]); return v[int(q * (len(v) - 1))]
def rep(tag, ms, name, q):
    th = qv(name, q); pn = []; cls = []; mode = 0
    for m in ms:
        use = feat[m][name] is not None and feat[m][name] >= th; mode += use; r = ud[m] if use else fv[m]; pn.append(r['pnl']); cls.append(r['cls'])
    g = {k: [p for p, c in zip(pn, cls) if c == k] for k in ('NO_FLIP', 'FALSE_FLIP', 'TRUE_FLIP')}; mn = {k: (S.fmean(v) if v else float('nan')) for k, v in g.items()}
    bs = sorted(S.fmean(rng.choice(pn) for _ in pn) for _ in range(400))
    print('%-24s %-7s q%.2f n=%2d UD %3.0f%% | noflip %6.1f false %6.1f true %6.1f (n %d/%d/%d) | overall %5.1f [%5.1f,%5.1f]' % (tag, name, q, len(ms), 100 * mode / len(ms), mn['NO_FLIP'], mn['FALSE_FLIP'], mn['TRUE_FLIP'], *[len(g[k]) for k in g], S.fmean(pn), bs[10], bs[389]))
allm = sorted(ud); unseen = [m for m in allm if m > last]
print('FAV only (all 70) %.1f | UNDERDOG only %.1f | unseen %d: FAV %.1f UNDERDOG %.1f' % (S.fmean(fv[m]['pnl'] for m in allm), S.fmean(ud[m]['pnl'] for m in allm), len(unseen), S.fmean(fv[m]['pnl'] for m in unseen), S.fmean(ud[m]['pnl'] for m in unseen)))
for name in ('rv_5m', 'rv_30m'):
    for q in (.50, .67): rep('all 70', allm, name, q); rep('unseen (id>%d)' % last, unseen, name, q)
