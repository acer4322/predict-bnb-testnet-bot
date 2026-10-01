"""CG1AT + freeze gate, in-sample 40 (fresh-40f + 40g): GATE vs CG1AT (reused). Overall, low/high conviction, per cohort;
gate drops by channel; markets whose pnl changed. Offline labels."""
import json, statistics, sys
from pathlib import Path
P = Path(__file__).resolve().parent; R = P.parent; L = R / 'lan_worker_returns'
sys.path.insert(0, str(R / 'v12g_passive_mirror_and_size_fresh40_20260928_v67')); from capital import peak
read = lambda p: json.loads(Path(p).read_bytes())
proto = read(P / 'PROTOCOL.json'); J = proto['job_id']; res = read(L / J / 'RESULT.json')
assert res['status'].startswith('COMPLETE') and all(p['status'] == 'PASS' for p in res['paths']), res['status']
paths = {p['market']: p for p in res['paths']}
W = {}
for f in ('btc5m_atbid_validation_fresh40f_20260929', 'btc5m_cg1at_validation_fresh40g_20260929'):
    W.update({r['market_id']: r['winner'] for r in read(R / f / 'OFFLINE_SETTLEMENT_LABELS.json')['records']})
rows = []; chan = {}
for m in proto['markets']:
    w = W[m]; row = dict(market=m, cohort=proto['selection']['cohorts'][str(m)], low=bool(paths[m].get('lowconv_low')))
    for k, arm in (('CG1AT', R / proto['baseline'][str(m)]['local_source']), ('GATE', L / J / 'arms' / f'cg1g_GATE_{m}')):
        r = read(arm / 'result.json'); row[k] = r['final_inventory'][w] - r['final_cost']; row['cost_' + k] = r['final_cost']; row['peak_' + k] = peak(arm)[0]
    g = json.loads((L / J / 'arms' / f'cg1g_GATE_{m}' / 'freeze_gate_trace.json').read_text()); row['dropped'] = g['dropped']
    for c, n in g['by_channel'].items(): chan[c] = chan.get(c, 0) + n
    rows.append(row)
(P / 'RESULTS_CG1GATE_40.json').write_text(json.dumps(rows, indent=1), encoding='utf-8')
def st(rs, k):
    x = sorted(r[k] for r in rs)
    return f"n={len(x):2d} mean {statistics.mean(x):7.1f} med {statistics.median(x):7.1f} std {statistics.pstdev(x):6.1f} worst2 {statistics.mean(x[:2]):7.1f} <=-100 {sum(v <= -100 for v in x):2d} profit {sum(v > 0 for v in x):2d} ret/cost {sum(x)/max(1e-9,sum(r['cost_' + k] for r in rs)):+.4f} peak {statistics.mean(r['peak_' + k] for r in rs):5.0f}"
for g, rs in (('ALL', rows), ('LOW (CG1 frozen)', [r for r in rows if r['low']]), ('HIGH', [r for r in rows if not r['low']]), ('F40F', [r for r in rows if r['cohort'] == 'F40F']), ('F40G', [r for r in rows if r['cohort'] == 'F40G'])):
    if rs: print(f'== {g}'); [print(f'   {k:5s} {st(rs, k)}') for k in ('CG1AT', 'GATE')]
d = [r['GATE'] - r['CG1AT'] for r in rows]
print(f"GATE-CG1AT: mean {statistics.mean(d):+.2f} median {statistics.median(d):+.2f} better {sum(x > .5 for x in d)} worse {sum(x < -.5 for x in d)}")
print('gate drops by channel (all markets):', chan, '| markets with drops', sum(r['dropped'] > 0 for r in rows))
print('changed markets:', [(r['market'], 'low' if r['low'] else 'high', round(r['CG1AT'], 1), round(r['GATE'], 1), r['dropped']) for r in rows if abs(r['GATE'] - r['CG1AT']) > .5])
