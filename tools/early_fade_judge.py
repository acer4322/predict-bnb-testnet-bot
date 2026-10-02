"""One-shot judge for the frozen EARLY_FADE spec (docs/research_specs/EARLY_FADE_SPEC_20261002_ZH.md).
Rule: nOFI30(t) (>=30 sh volume in last 30 s) >= +.3 -> buy DOWN, <= -.3 -> buy UP; window 30<=t<90, every 2 s, taker at t+1 s ask, 15 sh, max 20 fills/market, hold to resolution, fee 0 (+1%/2% of price sensitivity).
Pass: market-equal-weight mean per-share edge, 95% market-bootstrap CI lower > 0 AND the 2%-fee mean CI lower > 0, with n >= 100 markets (id > MIN_ID, official labels).
Without --dry-run, markets with id <= 2814776 are REFUSED (they are burned).  --dry-run runs on any markets and prints 'NOT A JUDGMENT'.
usage: python tools/early_fade_judge.py LABELS.json EVENTS_DIR... [--dry-run]"""
import sys, json, pathlib, random, statistics as S
import numpy as np
from event_series import series
dry = '--dry-run' in sys.argv; args = [a for a in sys.argv[1:] if a != '--dry-run']; labf, dirs = args[0], args[1:]; MIN_ID = 2814776
lab = {int(r['market_id']): r['winner'] for r in json.load(open(labf))['records']}; rng = random.Random(21)
def nofi(sg, vol, t): v = vol[t - 29:t + 1].sum(); return sg[t - 29:t + 1].sum() / v if v >= 30 else None
def market(m, ser):
    _, bb, ba, sg, vol = ser; w = 1. if lab[m] == 'UP' else 0.; e = {0: [], .01: [], .02: []}; n = 0
    for t in range(30, 90, 2):
        if n >= 20: break
        f = nofi(sg, vol, t)
        if f is None or abs(f) < .3 or np.isnan(ba[t + 1]) or np.isnan(bb[t + 1]): continue
        if f >= .3: side, p = 0., min(.99, 1 - bb[t + 1])
        else: side, p = 1., min(.99, ba[t + 1])
        n += 1
        for fee in e: e[fee].append((w if side == 1. else 1 - w) - p - fee * p)
    return {k: v for k, v in e.items()}, n
def ci(x): b = sorted(S.fmean(rng.choice(x) for _ in x) for _ in range(2000)); return b[50], b[1949]
res = {}; refused = 0
for d in dirs:
    for p in sorted((pathlib.Path(d) / 'markets').iterdir()):
        m = int(p.name)
        if m not in lab or m in res: continue
        if m <= MIN_ID and not dry: refused += 1; continue
        e, n = market(m, series(p))
        if n: res[m] = {k: S.fmean(v) for k, v in e.items()}; res[m]['n'] = n
if refused: print('refused %d burned markets (id <= %d)' % (refused, MIN_ID))
print('markets with >=1 trade: %d (labelled markets scanned from %d dirs)' % (len(res), len(dirs)))
if dry: print('*** DRY RUN: NOT A JUDGMENT ***')
if len(res) < 100 and not dry: print('FEWER THAN 100 markets: cannot judge yet.'); sys.exit()
if not res: sys.exit()
for fee in (0, .01, .02):
    x = [r[fee] for r in res.values()]; lo, hi = ci(x); print('fee %.0f%%: mean edge/share %+.4f CI[%+.4f,%+.4f] worst market %+.3f' % (100 * fee, S.fmean(x), lo, hi, min(x)))
x0 = [r[0] for r in res.values()]; x2 = [r[.02] for r in res.values()]
if not dry: print('VERDICT:', 'PASS' if ci(x0)[0] > 0 and ci(x2)[0] > 0 else 'FAIL', '(pre-declared criteria; one-shot)')
