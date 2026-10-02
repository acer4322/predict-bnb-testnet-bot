"""Spot-latency taker re-run with the MEASURED Predict.fun direct latency (research-data 18cfa109): spot event -> order at Predict ~100-325 ms
(Binance->host 28 + decide/sign 9 + write->ACK 126 = ~165 ms to ACK on a warm connection; conservative = orderAccepted receipt ~325 ms).
Uses public book frames (exchange source_ms) with depth: execution price = VWAP of the 15 shares on that side's ask ladder at signal+L (DOWN asks = 1 - UP bids);
fee = 2% of notional (observed), i.e. 0.02*price per share.  Signal: Binance BTCUSDT aggTrade log return over the last 500 ms >= X bp, checked every 100 ms, 15-285 s, 2 s cooldown.
Reports per-share +1 s / +5 s markout vs the side mid and edge to resolution, gross and net of fee, for the 300 exploration markets and the 219 confirmation markets separately.
usage: python tools/spot_latency_direct.py LABELS.json AGGTRADES.json.gz PUBLIC_ROOT"""
import sys, json, gzip, math, os, random, statistics as S
import numpy as np
lab = {int(r['market_id']): r['winner'] for r in json.load(open(sys.argv[1]))['records']}; agg = json.load(gzip.open(sys.argv[2], 'rt')); PUB = sys.argv[3]; SPLIT = 2766328; Q = 15.
DATA = {}
for name in sorted(os.listdir(PUB)):
    if not name.isdigit() or int(name) not in lab or name not in agg or len(agg[name]) < 100: continue
    pb = json.load(gzip.open('%s/%s/public_%s.json.gz' % (PUB, name, name), 'rt')); fr = [b for b in pb['books'] if b.get('best_bid') is not None and b.get('best_ask') is not None]
    a = np.array(agg[name]); DATA[int(name)] = (int(pb['market']['window_start_ms']), np.array([b['source_ms'] for b in fr]), fr, a[:, 0].astype(np.int64), a[:, 1])
print('markets', len(DATA))
def fill(levels, q):
    got = cost = 0.
    for p, s in levels:
        x = min(s, q - got); got += x; cost += x * p
        if got >= q - 1e-9: break
    return (cost / got, got) if got > 0 else (None, 0.)
def run(m, X, L, D=500):
    ws, T, FR, ST, SP = DATA[m]; w = lab[m]; out = []; last = -1e18
    def frame(t): i = np.searchsorted(T, t, side='right') - 1; return FR[i] if i >= 0 else None
    def spot(t): i = np.searchsorted(ST, t, side='right') - 1; return SP[i] if i >= 0 else None
    for t in range(ws + 15_000, ws + 285_000, 100):
        if t - last < 2000: continue
        a, b = spot(t - D), spot(t)
        if not a or not b: continue
        r = math.log(b / a) * 1e4
        if abs(r) < X: continue
        side = 'UP' if r > 0 else 'DOWN'; f = frame(t + L)
        if f is None: continue
        lv = [(p, s) for p, s in f['asks']] if side == 'UP' else [(round(1 - p, 2), s) for p, s in f['bids']]
        px, got = fill(sorted(lv), Q)
        if px is None or px >= .99: continue
        def smid(tt):
            g = frame(tt); mm = (g['best_bid'] + g['best_ask']) / 2; return mm if side == 'UP' else 1 - mm
        out.append(dict(m1=smid(t + L + 1000) - px, m5=smid(t + L + 5000) - px, res=(1. if w == side else 0.) - px, fee=.02 * px, got=got)); last = t
    return out
rng = random.Random(7)
def ci(v): b = sorted(S.fmean(rng.choice(v) for _ in v) for _ in range(1000)); return b[25], b[974]
ids = sorted(DATA); groups = (('EXPLORE(300)', [m for m in ids if m <= SPLIT]), ('CONFIRM(219)', [m for m in ids if m > SPLIT]))
print('per-market mean per share [market bootstrap 95% CI]; net = minus 2% notional fee')
for X in (1., 2., 3.):
    for L in (100, 165, 250, 325):
        row = []
        for nm, sub in groups:
            R = [r for r in (run(m, X, L) for m in sub) if r]
            if len(R) < 10: row.append('%s n=%d' % (nm, len(R))); continue
            m5 = [S.fmean(x['m5'] for x in r) for r in R]; n5 = [S.fmean(x['m5'] - x['fee'] for x in r) for r in R]; nr = [S.fmean(x['res'] - x['fee'] for x in r) for r in R]
            lo, hi = ci(n5); lr, hr = ci(nr)
            row.append('%s mk=%d tr/mk %.1f fill %.0f%% | +5s gross %+.2fc net %+.2fc[%+.2f,%+.2f] | res net %+.2fc[%+.2f,%+.2f]' % (nm, len(R), S.fmean(len(r) for r in R), 100 * S.fmean(x['got'] / Q for r in R for x in r), 100 * S.fmean(m5), 100 * S.fmean(n5), 100 * lo, 100 * hi, 100 * S.fmean(nr), 100 * lr, 100 * hr))
        print('X=%.0fbp L=%3dms || %s' % (X, L, ' || '.join(row)), flush=True)
