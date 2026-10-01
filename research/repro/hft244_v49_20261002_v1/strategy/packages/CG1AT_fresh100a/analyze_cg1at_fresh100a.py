"""fresh-100a: FULL (V58) vs CG1AT (frozen). Pre-registered primary: CG1AT mean > 0 (with 95% interval: normal s.e. and bootstrap);
also CG1AT - V58 and the usual gate items. Labels read after all replays; censored markets excluded from both arms."""
import bisect, gzip, json, sqlite3, statistics, sys, time
from pathlib import Path
P = Path(__file__).resolve().parent; R = P.parent; ROOT = R.parent; L = R / 'lan_worker_returns'
sys.path.insert(0, str(R / 'v12g_passive_mirror_and_size_fresh40_20260928_v67')); from capital import peak
read = lambda p: json.loads(gzip.decompress(Path(p).read_bytes()) if str(p).endswith('.gz') else Path(p).read_bytes())
def px(xs):
    if isinstance(xs, dict): return [float(k) for k in xs]
    return [float(x[0]) if isinstance(x, (list, tuple)) else float(x) for x in xs]
proto = read(P / 'PROTOCOL.json'); J = proto['job_id']; res = read(L / J / 'RESULT.json')
assert res['status'].startswith('COMPLETE'), res['status']
paths = {(p['arm'], p['market']): p for p in res['paths']}
assert len(paths) == 200 and all(p['status'] in ('PASS', 'CENSORED') for p in paths.values())
cens = sorted({m for (a, m), p in paths.items() if p['status'] == 'CENSORED'})
markets = [m for m in proto['markets'] if m not in cens]
LB = P / 'OFFLINE_SETTLEMENT_LABELS.json'
if not LB.exists():
    con = sqlite3.connect(f'file:{ROOT / "target_wallet_official_v1.db"}?mode=ro', uri=True, timeout=10)
    q = con.execute(f"select market_id,asset,title,winner,resolved_at_ms,accounting_version from target_market_results where market_id in ({','.join('?' * len(markets))})", markets).fetchall()
    LB.write_text(json.dumps(dict(source='data/target_wallet_official_v1.db', table='target_market_results', read_only=True, offline_only=True, read_after_all_replays=True, timestamp=time.time(),
                                  records=[dict(zip(['market_id', 'asset', 'title', 'winner', 'resolved_at_ms', 'accounting_version'], r)) for r in q]), indent=1), encoding='utf-8')
W = {r['market_id']: r['winner'] for r in read(LB)['records']}
unlabeled = [m for m in markets if m not in W]; markets = [m for m in markets if m in W]
ARMS = ['FULL', 'CG1AT']
rows = []
for m in markets:
    w = W[m]; bt = []; um = []
    for b in read(P / 'base' / 'inputs' / f'public_{m}.json.gz')['books']:
        if b.get('bids') and b.get('asks'): bt.append(b['received_ms']); um.append((max(px(b['bids'])) + min(px(b['asks']))) / 2)
    def mid(t, s):
        i = bisect.bisect_left(bt, t) - 1; return None if i < 0 else (um[i] if s == 'UP' else 1 - um[i])
    row = dict(market=m, high=bool(paths[('CG1AT', m)].get('lowconv_low') is False))
    for a in ARMS:
        arm = L / J / 'arms' / f'c100_{a}_{m}'; r = read(arm / 'result.json'); tr = read(arm / 'clock_trace.json.gz'); clock = read(arm / 'execution_clock.json')
        row[a] = r['final_inventory'][w] - r['final_cost']; row['cost_' + a] = r['final_cost']; row['peak_' + a] = peak(arm)[0]
        ops = {int(o['key'].rsplit('_', 1)[1]): o for pl in tr['plans'] for o in pl['operations'] if o['kind'] == 'NEW'}
        q = a5 = 0.
        for x in clock['receipts']:
            o = ops[x['order_id']]
            if x['qty'] <= 0 or o['route'] != 'PASSIVE': continue
            s = o['side']; p = x['price'] if s == 'UP' else 1 - x['price']; m5 = mid(x['exchange_ts'] // 1_000_000 + 5000, s)
            if m5 is not None: q += x['qty']; a5 += x['qty'] * (m5 - p)
        row['pq_' + a] = q; row['mk5sum_' + a] = a5
    rows.append(row)
def qt(xs, p): xs = sorted(xs); return xs[min(len(xs) - 1, int(p * (len(xs) - 1) + .5))]
def st(rs, k):
    x = sorted(r[k] for r in rs); pk = [r['peak_' + k] for r in rs]
    return dict(n=len(x), mean=statistics.mean(x), median=statistics.median(x), std=statistics.pstdev(x), worst2=statistics.mean(x[:2]), worst5=statistics.mean(x[:5]),
                le100=sum(v <= -100 for v in x), le50=sum(v <= -50 for v in x), profitable=sum(v > 0 for v in x), ret_cost=sum(x) / sum(r['cost_' + k] for r in rs),
                mean_peak=statistics.mean(pk), p90_peak=qt(pk, .9), max_peak=max(pk), share400=sum(v <= 400 for v in pk) / len(pk),
                mk5=sum(r['mk5sum_' + k] for r in rs) / max(1e-9, sum(r['pq_' + k] for r in rs)))
groups = [('ALL', rows), ('LOW', [r for r in rows if not r['high']]), ('HIGH', [r for r in rows if r['high']])]
out = {g: {k: st(rs, k) for k in ARMS} for g, rs in groups if rs}
f, c = out['ALL']['FULL'], out['ALL']['CG1AT']
gate = dict(CG1AT=dict(mean=c['mean'] > f['mean'], ret_cost=c['ret_cost'] > f['ret_cost'], le100=c['le100'] <= f['le100'], mk5=c['mk5'] > f['mk5']))
gate['CG1AT']['pass'] = all(gate['CG1AT'].values()); gate['CG1AT']['stability_mean_positive'] = c['mean'] > 0
import random
def ci(xs):
    n = len(xs); mu = statistics.mean(xs); se = statistics.stdev(xs) / n ** .5
    rnd = random.Random(20260930); bs = sorted(statistics.mean(rnd.choices(xs, k=n)) for _ in range(5000))
    return dict(mean=mu, se=se, normal95=(mu - 1.96 * se, mu + 1.96 * se), boot95=(bs[124], bs[4874]), p_boot_le0=sum(v <= 0 for v in bs) / len(bs))
gate['CG1AT']['ci'] = ci([r['CG1AT'] for r in rows]); gate['DIFF'] = ci([r['CG1AT'] - r['FULL'] for r in rows]); gate['V58ci'] = ci([r['FULL'] for r in rows])
(P / 'RESULTS_CG1AT_FRESH100A.json').write_text(json.dumps(dict(censored=cens, unlabeled=unlabeled, scored=len(rows), gate=gate, groups=out, rows=rows), indent=2, default=float), encoding='utf-8')
print('censored', cens, 'unlabeled', unlabeled, 'scored', len(rows), '| high', sum(r['high'] for r in rows), 'low', sum(not r['high'] for r in rows))
print('PRE-REGISTERED:', gate)
for g, v in out.items():
    print('==', g)
    for k, t in v.items():
        print(f"   {k:5s} n={t['n']:2d} mean {t['mean']:7.1f} med {t['median']:7.1f} std {t['std']:6.1f} worst2 {t['worst2']:7.1f} worst5 {t['worst5']:7.1f} <=-100 {t['le100']:2d} <=-50 {t['le50']:2d} profit {t['profitable']:2d} ret/cost {t['ret_cost']:+.4f} mk5 {t['mk5']:+.4f} | peak {t['mean_peak']:5.0f} p90 {t['p90_peak']:5.0f} max {t['max_peak']:5.0f} <=400 {t['share400']:.2f}")
d = sorted(r['CG1AT'] - r['FULL'] for r in rows)
print(f"CG1AT-FULL: mean {statistics.mean(d):+.1f} median {statistics.median(d):+.1f} better {sum(x > .5 for x in d)} worse {sum(x < -.5 for x in d)}")
by = {}
for r in rows: by.setdefault(r['market'] // 1, None)
q = sorted(rows, key=lambda r: r['market']); n = len(q) // 4
print('by time quarter (CG1AT mean / V58 mean):', [(round(statistics.mean(r['CG1AT'] for r in q[i*n:(i+1)*n]), 1), round(statistics.mean(r['FULL'] for r in q[i*n:(i+1)*n]), 1)) for i in range(4)])
