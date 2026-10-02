"""Where does the Target earn: MAKER or TAKER fills?  Per-share edge to resolution (1[side wins] - price) and to the Predict mid +5 s / +30 s (markout), qty-weighted, market-clustered bootstrap CI; per half (id).
Plus the spot-direction test: signed Binance 1 s return over the 5 s after the fill, for taker fills split by whether spot moved toward the bought side in the 3 s BEFORE (latency-arb signature).
usage: python tools/target_role_edge.py target_fills.json.gz PUBLIC_ROOT spot1s.json.gz [more]"""
import sys, gzip, json, collections, random, math, pathlib, statistics as S
import real_replay_stop as rr
exp, root, *spf = sys.argv[1:]; rows = json.loads(gzip.open(exp).read()); by = collections.defaultdict(list)
for r in rows: by[int(r['market_id'])].append(r)
sp = {}
for f in spf: sp.update({int(k): v[0] for k, v in json.load(gzip.open(f, 'rt')).items()})
M = {m: v for m, v in rr.load(root, {}).items() if m in by}; WS = {}
for p in pathlib.Path(root).rglob('public_*.json.gz'):
    m = int(p.name.split('_')[1].split('.')[0])
    if m in M and m not in WS: WS[m] = int(rr.jl(p)['market']['window_start_ms'])
def smid(m, t, side):
    b = rr.at(M[m], t)
    if not b: return None
    x = (b['best_bid'] + b['best_ask']) / 2; return x if side == 'UP' else 1 - x
rng = random.Random(4); ids = sorted(M)
def per_market(sub, role, key):
    out = []
    for m in sub:
        a = b = 0.
        for r in by[m]:
            if r['role'] != role: continue
            t = (r['event_ms'] - WS[m]) / 1000.
            if key == 'res': v = (1. if r['side'] == r['winner'] else 0.) - r['price']
            else:
                x = smid(m, t + key, r['side']); 
                if x is None: continue
                v = x - r['price']
            a += r['shares'] * v; b += r['shares']
        if b: out.append((a, b))
    return out
def ci(pm):
    est = sum(a for a, _ in pm) / sum(b for _, b in pm); bs = []
    for _ in range(1000):
        s = [rng.choice(pm) for _ in pm]; bs.append(sum(a for a, _ in s) / sum(b for _, b in s))
    bs.sort(); return est, bs[25], bs[974]
for nm, sub in (('ALL', ids), ('first half', ids[:len(ids) // 2]), ('second half', ids[len(ids) // 2:])):
    for role in ('MAKER', 'TAKER'):
        print('%-11s %-5s' % (nm, role), ' | '.join('%s %+.4f [%+.4f,%+.4f]' % ((k if k == 'res' else '+%ds' % k), *ci(per_market(sub, role, k))) for k in (5, 30, 'res')))
# latency-arb signature for taker fills
acc = collections.defaultdict(lambda: [0., 0., 0.])
for r in rows:
    if int(r['market_id']) not in M: continue
    s = r['event_ms'] // 1000; sg = 1 if r['side'] == 'UP' else -1; a0, a1, a2 = sp.get(s - 4), sp.get(s - 1), sp.get(s + 5)
    if not (a0 and a1 and a2): continue
    pre = sg * math.log(a1 / a0) * 1e4; post = sg * math.log(a2 / a1) * 1e4; k = (r['role'], 'spot moved toward side >=0.5bp' if pre >= .5 else 'against <=-0.5bp' if pre <= -.5 else 'flat')
    acc[k][0] += r['shares']; acc[k][1] += r['shares'] * post; acc[k][2] += r['shares'] * ((1. if r['side'] == r['winner'] else 0.) - r['price'])
print('\nfills by spot move in the 3 s before (signed toward bought side): share of role volume | signed spot bps next 5 s | edge to resolution per share')
for role in ('MAKER', 'TAKER'):
    tot = sum(v[0] for k, v in acc.items() if k[0] == role)
    for k in sorted(x for x in acc if x[0] == role): v = acc[k]; print('  %-5s %-32s %.0f%% | %+.2f | %+.4f' % (role, k[1], 100 * v[0] / tot, v[1] / v[0], v[2] / v[0]))
