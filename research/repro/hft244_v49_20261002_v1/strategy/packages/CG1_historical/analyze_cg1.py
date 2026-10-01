"""CG1 out-of-sample validation: CG1 vs FULL on fresh-40d. Labels read after all replays (offline); censored markets excluded."""
import json, sqlite3, statistics, time
from pathlib import Path
from capital import peak
P = Path(__file__).resolve().parent; R = P.parent; ROOT = R.parent; L = R / 'lan_worker_returns'
read = lambda p: json.loads(Path(p).read_bytes())
proto = read(P / 'PROTOCOL.json'); res = read(L / proto['job_id'] / 'RESULT.json')
assert res['status'].startswith('COMPLETE'), res['status']
assert all(p['status'] in ('PASS', 'CENSORED') for p in res['paths'])
cens = sorted({p['market'] for p in res['paths'] if p['status'] == 'CENSORED'})
markets = [m for m in proto['markets'] if m not in cens]
LB = P / 'OFFLINE_SETTLEMENT_LABELS.json'
if not LB.exists():
    con = sqlite3.connect(f'file:{ROOT / "target_wallet_official_v1.db"}?mode=ro', uri=True, timeout=10)
    rows = con.execute(f"select market_id,asset,title,winner,resolved_at_ms,accounting_version from target_market_results where market_id in ({','.join('?' * len(markets))})", markets).fetchall()
    LB.write_text(json.dumps(dict(source='data/target_wallet_official_v1.db', table='target_market_results', read_only=True, offline_only=True, read_after_all_replays=True, timestamp=time.time(),
                                  records=[dict(zip(['market_id', 'asset', 'title', 'winner', 'resolved_at_ms', 'accounting_version'], r)) for r in rows]), indent=1), encoding='utf-8')
W = {r['market_id']: r['winner'] for r in read(LB)['records']}
markets = [m for m in markets if m in W]
paths = {(p['arm'], p['market']): p for p in res['paths']}
arm = lambda a, m: L / proto['job_id'] / 'arms' / f'cg1v_{a}_{m}'
rows = []
for m in markets:
    f, l = paths[('FULL', m)], paths[('CG1', m)]; w = W[m]
    pf, cf = peak(arm('FULL', m)); pl, cl = peak(arm('CG1', m))
    rows.append(dict(market=m, low=bool(l.get('lowconv_low')), m0=l.get('lowconv_m0'), full=f['UP'] if w == 'UP' else f['DOWN'], lc=l['UP'] if w == 'UP' else l['DOWN'],
                     cost_full=cf, cost_lc=cl, peak_full=pf, peak_lc=pl, vetoes=l.get('lowconv_vetoes')))
    rows[-1]['delta'] = rows[-1]['lc'] - rows[-1]['full']
def st(rs, k, ck, pk):
    x = [r[k] for r in rs]; s = sorted(x); pks = sorted(r[pk] for r in rs)
    return dict(n=len(x), mean=statistics.mean(x), median=statistics.median(x), std=statistics.pstdev(x) if len(x) > 1 else 0., worst2=statistics.mean(s[:2]) if len(s) > 1 else s[0],
                worst5=statistics.mean(s[:5]) if len(s) >= 5 else None, le100=sum(y <= -100 for y in x), profitable=sum(y > 0 for y in x),
                ret_cost=sum(x) / sum(r[ck] for r in rs), mean_cost=statistics.mean(r[ck] for r in rs), mean_peak=statistics.mean(pks), p90_peak=pks[int(.9 * (len(pks) - 1))], max_peak=pks[-1])
out = {}
for g, rs in (('ALL', rows), ('LOW(<0.56)', [r for r in rows if r['low']]), ('HIGH(>=0.56)', [r for r in rows if not r['low']])):
    if not rs: continue
    a = st(rs, 'full', 'cost_full', 'peak_full'); b = st(rs, 'lc', 'cost_lc', 'peak_lc')
    out[g] = dict(full=a, lc=b, improved=sum(r['delta'] > 1e-6 for r in rs), worse=sum(r['delta'] < -1e-6 for r in rs))
A = out['ALL']; f, l = A['full'], A['lc']
gate = dict(mean=l['mean'] > f['mean'], ret_cost=l['ret_cost'] > f['ret_cost'], mean_over_std=(l['mean'] / l['std']) > (f['mean'] / f['std']), le100=l['le100'] <= f['le100'])
gate['pass'] = all(gate.values())
(P / 'RESULTS_CG1.json').write_text(json.dumps(dict(censored=cens, scored=len(markets), gate=gate, groups=out, rows=rows), indent=2, default=float), encoding='utf-8')
print('censored', cens, 'scored', len(markets)); print('gate', gate)
for g, v in out.items():
    for arm_ in ('full', 'lc'):
        t = v[arm_]
        print(f"{g:13s} {arm_:4s} n={t['n']:2d} mean {t['mean']:7.1f} med {t['median']:7.1f} std {t['std']:6.1f} worst2 {t['worst2']:7.1f} worst5 {t['worst5'] if t['worst5'] is None else round(t['worst5'],1)} <=-100 {t['le100']:2d} profit {t['profitable']:2d} ret/cost {t['ret_cost']:.4f} cost {t['mean_cost']:.0f} peak {t['mean_peak']:.0f}/{t['p90_peak']:.0f}/{t['max_peak']:.0f}")
    print(f"{'':13s} improved {v['improved']} worse {v['worse']}")
