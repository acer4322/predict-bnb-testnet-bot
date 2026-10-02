"""Offline report for the spot-latency SHADOW logs (tools/spot_latency_shadow.py).  Pre-registered metrics: docs/research_specs/SPOT_LATENCY_SHADOW_SPEC_20261003_ZH.md.
Two views:  EXCH = signal on Binance trade time T, book on Predict updateTimestampMs (same method as the historical replay; assumes the two venues' clocks agree);
            LIVE = signal when OUR host received the aggTrade (includes our real Binance feed delay); the order reaches Predict L ms later; the host time is mapped to
                   Predict time with offset o = 1st percentile of (frame recv - updateTimestampMs).  This is slightly OPTIMISTIC by Predict's minimum feed transit
                   (o includes it), so read LIVE with L >= measured send latency.  NOTE: never judge on the local book as received (it lags the venue and
                   makes stale-looking quotes that were already gone at the venue).
Signal: log return of the last 500 ms >= X bp (checked every 100 ms, market seconds 15-285, 2 s cooldown).  Execution: VWAP of 15 shares on that side's asks at signal+L
(DOWN asks = 1 - UP bids).  Fee 2% of notional.  Markout = side mid at +1 s / +5 s after execution - price.  Resolution edge if --labels given (else final-frame mid, flagged INFERRED).
usage: python tools/spot_latency_shadow_report.py LOG_DIR_OR_FILES... [--labels LABELS.json] [--json OUT.json]"""
import sys, gzip, json, math, random, statistics as S, glob, os, bisect, argparse, collections
ap = argparse.ArgumentParser(); ap.add_argument('paths', nargs='+'); ap.add_argument('--labels'); ap.add_argument('--json'); a = ap.parse_args()
files = []
for p in a.paths: files += sorted(glob.glob(os.path.join(p, '*.ndjson.gz'))) if os.path.isdir(p) else [p]
mk = {}; pb = collections.defaultdict(list); st = []
for f in files:
    for line in gzip.open(f, 'rt'):
        r = json.loads(line)
        if r['k'] == 'market': mk[r['id']] = r
        elif r['k'] == 'pb' and r.get('src'): pb[r['id']].append(r)
        elif r['k'] == 'st': st.append(r)
st.sort(key=lambda r: r['T']); lab = {}
if a.labels: lab = {int(r['market_id']): r['winner'] for r in json.load(open(a.labels))['records']}
print('files %d markets %d book frames %d spot trades %d' % (len(files), len(mk), sum(len(v) for v in pb.values()), len(st)))
d1 = sorted(r['recv'] - r['src'] for v in pb.values() for r in v); d2 = sorted(r['recv'] - r['E'] for r in st)
q = lambda x, p: x[int(p * (len(x) - 1))] if x else float('nan')
print('Predict frame recv - updateTimestampMs (ms; includes clock offset): q50 %.0f q95 %.0f | Binance recv - E: q50 %.0f q95 %.0f' % (q(d1, .5), q(d1, .95), q(d2, .5), q(d2, .95)))
OFF = q(d1, .01)
print('LIVE view host->Predict clock offset o (1st pct of recv - updateTimestampMs): %.0f ms' % OFF)
def series(view):
    # returns spot series in the clock of the signal and books in Predict time; LIVE shifts host-received spot times into Predict time by -OFF
    if view == 'EXCH': S_t = [r['T'] for r in st]; S_p = [r['p'] for r in st]
    else:
        o = sorted(range(len(st)), key=lambda i: st[i]['recv']); S_t = [st[i]['recv'] - OFF for i in o]; S_p = [st[i]['p'] for i in o]
    B = {}
    for m, v in pb.items():
        v2 = sorted(v, key=lambda r: r['src']); B[m] = ([r['src'] for r in v2], v2)
    return S_t, S_p, B
def at(T, X, t):
    i = bisect.bisect_right(T, t) - 1; return X[i] if i >= 0 else None
def fill(levels, qty=15.):
    got = cost = 0.
    for p, s in sorted(levels):
        x = min(s, qty - got); got += x; cost += x * p
        if got >= qty - 1e-9: break
    return (cost / got, got) if got else (None, 0.)
def mid(fr):
    if not fr['asks'] or not fr['bids']: return None
    return (min(p for p, _ in fr['asks']) + max(p for p, _ in fr['bids'])) / 2
def run(view, X, L, D=500):
    S_t, S_p, B = series(view); out = collections.defaultdict(list)
    for m, info in mk.items():
        if m not in B or len(B[m][0]) < 20: continue
        T, FR = B[m]; ws = info['ws']; w = lab.get(m)
        if w is None:
            fe = at(T, FR, info['we']); mm = mid(fe) if fe else None; w = None if mm is None else ('UP' if mm > .5 else 'DOWN')
        last = -1e18
        for t in range(int(ws) + 15_000, int(ws) + 285_000, 100):
            if t - last < 2000: continue
            p0, p1 = at(S_t, S_p, t - D), at(S_t, S_p, t)
            if not p0 or not p1: continue
            r = math.log(p1 / p0) * 1e4
            if abs(r) < X: continue
            side = 'UP' if r > 0 else 'DOWN'; f0, fe = at(T, FR, t), at(T, FR, t + L)
            if not fe or not f0: continue
            lv = lambda f: f['asks'] if side == 'UP' else [(round(1 - p, 4), s) for p, s in f['bids']]
            px, got = fill(lv(fe)); px0, _ = fill(lv(f0))
            if px is None or px >= .99: continue
            def smid(tt):
                g = at(T, FR, tt); mm = mid(g) if g else None
                return None if mm is None else (mm if side == 'UP' else 1 - mm)
            m1, m5 = smid(t + L + 1000), smid(t + L + 5000)
            if m5 is None: continue
            out[m].append(dict(m1=(m1 - px) if m1 is not None else None, m5=m5 - px, fee=.02 * px, res=None if w is None else (1. if w == side else 0.) - px, stale=(px0 is not None and abs(px - px0) < 1e-9)))
            last = t
    return out
rng = random.Random(11)
def ci(v): b = sorted(S.fmean(rng.choice(v) for _ in v) for _ in range(1000)); return b[25], b[974]
res = {}
print('\nview X L | markets signals | stale-at-exec %% | +5 s net (2%% fee) [CI] | +1 s net | resolution net%s' % ('' if lab else ' (INFERRED winner)'))
for view in ('EXCH', 'LIVE'):
    for X in (1., 2., 3.):
        for L in (0, 100, 165, 250, 325, 500):
            o = run(view, X, L); ms = [m for m in o if o[m]]
            if len(ms) < 5: print('%s X=%.0f L=%3d | markets %d (too few)' % (view, X, L, len(ms))); continue
            n5 = [S.fmean(x['m5'] - x['fee'] for x in o[m]) for m in ms]; n1 = [S.fmean(x['m1'] - x['fee'] for x in o[m] if x['m1'] is not None) for m in ms if any(x['m1'] is not None for x in o[m])]
            rr = [S.fmean(x['res'] - x['fee'] for x in o[m] if x['res'] is not None) for m in ms if any(x['res'] is not None for x in o[m])]
            lo, hi = ci(n5); st_ = 100 * S.fmean(x['stale'] for m in ms for x in o[m])
            res['%s_X%d_L%d' % (view, X, L)] = dict(markets=len(ms), signals=sum(len(o[m]) for m in ms), net5=S.fmean(n5), ci5=[lo, hi], net1=S.fmean(n1) if n1 else None, res=S.fmean(rr) if rr else None, stale_pct=st_)
            print('%s X=%.0f L=%3d | %3d %4d | %3.0f%% | %+.2fc [%+.2f,%+.2f] | %+.2fc | %s' % (view, X, L, len(ms), sum(len(o[m]) for m in ms), st_, 100 * S.fmean(n5), 100 * lo, 100 * hi, 100 * (S.fmean(n1) if n1 else float('nan')), ('%+.2fc' % (100 * S.fmean(rr))) if rr else 'NA'))
p = res.get('EXCH_X3_L250')
print('\nPRE-REGISTERED PRIMARY (EXCH, X=3 bp, L=250 ms, >=60 markets with signals, net +5 s CI lower > 0):',
      'NOT ENOUGH DATA' if not p or p['markets'] < 60 else ('PASS' if p['ci5'][0] > 0 else 'FAIL'), '' if not p else '(markets %d, net %+.2fc, CI [%+.2f,%+.2f])' % (p['markets'], 100 * p['net5'], 100 * p['ci5'][0], 100 * p['ci5'][1]))
h = res.get('LIVE_X3_L165'); print('SECONDARY (LIVE, X=3, L=165):', 'NA' if not h else '%+.2fc CI [%+.2f,%+.2f], markets %d' % (100 * h['net5'], 100 * h['ci5'][0], 100 * h['ci5'][1], h['markets']))
if a.json: json.dump(res, open(a.json, 'w'), indent=1)
