"""Sub-second spot -> Predict lag and a latency-aware taker rule (the Target's TAKER fills earn +2 c at +5 s; Predict's 1 Hz mid lags spot by ~1 s).
Data: Predict V1 event files (ms local receive time) and Binance BTCUSDT aggTrades (ms exchange time).  Clock offset between the two is unknown (both NTP-ish) -> latency grid.
(1) lag profile: corr of spot log-return in 100 ms bins with Predict UP-mid change in the bin shifted by lag (0..3000 ms).
(2) rule (pre-declared grid): every 100 ms from 15 s to 285 s, r = spot log return over the last D ms (bp); if |r| >= X, buy the side spot moved toward at that side's best ask observed at decision+L ms
    (L = our reaction incl. network/venue latency), qty = min(15, size at ask); 2 s cooldown per market.  Edge per share: side mid at +5 s / +30 s after execution minus ask, and to resolution.
    Grid D in {500,1000} ms, X in {1,2,3} bp, L in {200,400,700,1000} ms.  Discovery = first 60% of markets by id, confirmation = rest.
usage: python tools/spot_latency_arb.py LABELS.json AGGTRADES.json.gz EVENTS_BATCH_DIR..."""
import sys, json, gzip, math, bisect, random, statistics as S, collections
from pathlib import Path
import numpy as np
from hftbacktest import DEPTH_EVENT, DEPTH_SNAPSHOT_EVENT, BUY_EVENT
lab = {int(r['market_id']): r['winner'] for r in json.load(open(sys.argv[1]))['records']}; agg = json.load(gzip.open(sys.argv[2], 'rt')); dirs = sys.argv[3:]
import os
SHIFT = int(os.environ.get('SHIFT', '0')); PUB = os.environ.get('PUB')   # SHIFT ms subtracted from V1 local_ts; PUB=public_markets root -> use public book frames (source_ms) instead
def book_series(p):
    if PUB:
        pb = json.load(gzip.open('%s/%s/public_%s.json.gz' % (PUB, p.name, p.name), 'rt')); fr = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
        return np.array([b['source_ms'] for b in fr]), np.array([b['best_bid'] for b in fr]), np.array([b['best_ask'] for b in fr]), np.zeros(len(fr)), np.zeros(len(fr))
    ev = np.load(p / 'events.npz')['data']; ev = ev[np.argsort(ev['local_ts'], kind='stable')]; bids, asks = {}, {}; T = []; BB = []; BA = []; BAS = []; BBS = []
    for e in ev:
        f = int(e['ev'])
        if not ((f & DEPTH_EVENT) == DEPTH_EVENT or (f & DEPTH_SNAPSHOT_EVENT) == DEPTH_SNAPSHOT_EVENT): continue
        (bids if (f & BUY_EVENT) == BUY_EVENT else asks)[round(float(e['px']), 2)] = float(e['qty'])
        bb = max((x for x, q in bids.items() if q > 0), default=np.nan); ba = min((x for x, q in asks.items() if q > 0), default=np.nan)
        T.append(int(e['local_ts']) // 1_000_000 - SHIFT); BB.append(bb); BA.append(ba); BAS.append(asks.get(ba, 0.) if ba == ba else 0.); BBS.append(bids.get(bb, 0.) if bb == bb else 0.)
    return np.array(T), np.array(BB), np.array(BA), np.array(BAS), np.array(BBS)
MK = []
if PUB and not dirs:
    for name in sorted(os.listdir(PUB)):
        if name.isdigit() and int(name) in lab and name in agg and len(agg[name]) > 100: MK.append((int(name), Path(PUB) / name))
for d in dirs:
    for p in sorted((Path(d) / 'markets').iterdir()):
        m = int(p.name)
        if m in lab and p.name in agg and len(agg[p.name]) > 100 and (not PUB or os.path.exists('%s/%s' % (PUB, p.name))): MK.append((m, p))
MIN_ID = int(os.environ.get('MINID', '0')); MK = [x for x in MK if x[0] > MIN_ID]; MK.sort(); print('markets', len(MK), '(id >', MIN_ID, ')')
DATA = {}
for m, p in MK:
    ws = int(json.load(open(p / 'META.json'))['window_start_ms']) if (p / 'META.json').exists() else int(json.load(gzip.open(p / ('public_%s.json.gz' % p.name), 'rt'))['market']['window_start_ms']); T, BB, BA, BAS, BBS = book_series(p); a = np.array(agg[p.name]); DATA[m] = (ws, T, BB, BA, BAS, BBS, a[:, 0].astype(np.int64), a[:, 1])
def at(T, X, t):
    i = np.searchsorted(T, t, side='right') - 1; return X[i] if i >= 0 else np.nan
def spot(ST, SP, t):
    i = np.searchsorted(ST, t, side='right') - 1; return SP[i] if i >= 0 else np.nan
# (1) lag profile
lags = [0, 100, 200, 300, 500, 700, 1000, 1500, 2000, 3000]; xs = {l: ([], []) for l in lags}
for m, (ws, T, BB, BA, BAS, BBS, ST, SPX) in DATA.items():
    grid = np.arange(ws + 15_000, ws + 285_000, 100)
    sp_ = np.array([spot(ST, SPX, t) for t in grid]); r = np.diff(np.log(sp_)) * 1e4
    for l in lags:
        mid = np.array([(at(T, BB, t + l) + at(T, BA, t + l)) / 2 for t in grid]); dm = np.diff(mid); ok = np.isfinite(r) & np.isfinite(dm)
        xs[l][0].extend(r[ok]); xs[l][1].extend(dm[ok])
print('corr(spot 100 ms return, Predict UP-mid change in the same 100 ms bin shifted by lag ms):')
print('  ', {l: round(float(np.corrcoef(xs[l][0], xs[l][1])[0, 1]), 3) for l in lags})
# (2) rule
ids = sorted(DATA); n = len(ids); D_, C_ = ids[:int(.6 * n)], ids[int(.6 * n):]
JUDGE = bool(os.environ.get('JUDGE')); rng = random.Random(6)
def run(m, D, X, L):
    ws, T, BB, BA, BAS, BBS, ST, SPX = DATA[m]; w = lab[m]; last = -1e18; out = []
    for t in range(ws + 15_000, ws + 285_000, 100):
        if t - last < 2000: continue
        a, b = spot(ST, SPX, t - D), spot(ST, SPX, t)
        if not (a == a and b == b): continue
        r = math.log(b / a) * 1e4
        if abs(r) < X: continue
        side = 'UP' if r > 0 else 'DOWN'; te = t + L
        bb, ba = at(T, BB, te), at(T, BA, te)
        if not (bb == bb and ba == ba and 0 < bb < ba < 1): continue
        px = ba if side == 'UP' else round(1 - bb, 2)
        def smid(tt):
            x, y = at(T, BB, tt), at(T, BA, tt); mm = (x + y) / 2; return mm if side == 'UP' else 1 - mm
        out.append((smid(te + 5000) - px, smid(te + 30000) - px, (1. if w == side else 0.) - px)); last = t
    return out
def ci(v): b = sorted(S.fmean(rng.choice(v) for _ in v) for _ in range(800)); return b[20], b[779]
print('\nrule: per-market mean edge per share [market bootstrap 95% CI]  (+5 s markout | to resolution)')
GD = [int(x) for x in os.environ.get('GD', '500,1000').split(',')]; GX = [float(x) for x in os.environ.get('GX', '1,2,3').split(',')]; GL = [int(x) for x in os.environ.get('GL', '200,400,700,1000').split(',')]
for D in GD:
    for X in GX:
        for L in GL:
            row = []
            for nm, sub in ((('ALL(one-shot)', ids),) if JUDGE else (('DISC', D_), ('CONF', C_))):
                pm = [r for r in (run(m, D, X, L) for m in sub) if r]
                if len(pm) < 10: row.append('%s n=%d' % (nm, len(pm))); continue
                e5 = [S.fmean(x[0] for x in r if x[0] == x[0]) for r in pm if any(x[0] == x[0] for x in r)]; er = [S.fmean(x[2] for x in r) for r in pm]; a5, b5 = ci(e5); ar, br = ci(er)
                row.append('%s mk=%d tr/mk %.1f +5s %+.4f[%+.4f,%+.4f] res %+.4f[%+.4f,%+.4f]' % (nm, len(pm), S.fmean(len(r) for r in pm), S.fmean(e5), a5, b5, S.fmean(er), ar, br) + (' | +5s-1c %+.4f CIlo %+.4f' % (S.fmean(e5) - .01, a5 - .01) if JUDGE else ''))
            print('  D=%4d X=%.0f L=%4d | %s' % (D, X, L, ' || '.join(row)))
