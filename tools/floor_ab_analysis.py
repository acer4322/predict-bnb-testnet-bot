"""Paired ON/OFF analysis of the POSTFLIP_RISK_FLOOR A/B from a research pack (spec: RISK_FLOOR_AB_SPEC_20261001_ZH.md, sections 9-10).

  python tools/floor_ab_analysis.py PACK_DIR [--label-file OFFLINE_SETTLEMENT_LABELS.json ...] [--out result.json] [--boot 5000]

PACK_DIR = research_pack/ (needs INDEX.json). ON = arm dir name contains 'CG1AT' and not 'OFF'; OFF = contains 'OFF'. Pairs by market_id.
d = pnl_ON - pnl_OFF, pnl = final_inventory[winner] - final_cost (labels offline only). Prints the pre-registered report items; does NOT
declare a verdict unless --final is given (spec: only n=132 decides). Parity uses PARITY.json (inclusive) when present, and notes it.
"""
import argparse, collections, json, random, statistics as S
from pathlib import Path


def boot_ci(xs, n, rng):
    if len(xs) < 3: return (None, None)
    ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(n))
    return ms[int(.025 * n)], ms[int(.975 * n)]


def cvar_k(xs, k): return S.fmean(sorted(xs)[:k]) if xs else None


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('pack'); ap.add_argument('--label-file', nargs='*', default=[])
    ap.add_argument('--out', default=''); ap.add_argument('--boot', type=int, default=5000); ap.add_argument('--final', action='store_true')
    a = ap.parse_args(); P = Path(a.pack); idx = json.loads((P / 'INDEX.json').read_text(encoding='utf-8'))
    lab = {}
    for f in (a.label_file or [str(p) for p in (P / 'labels').glob('*.json')]):
        for r in json.loads(Path(f).read_text(encoding='utf-8')).get('records', []): lab[int(r['market_id'])] = r['winner']
    arms = collections.defaultdict(dict)
    for e in idx['entries']:
        name = e['path'].replace('\\', '/').split('/')[-1]
        arm = 'OFF' if 'OFF' in name else ('ON' if 'CG1AT' in name else None)
        if arm and e.get('market_id') is not None: arms[int(e['market_id'])].setdefault(arm, e)
    rows = []
    for m, d in sorted(arms.items()):
        if 'ON' not in d or 'OFF' not in d or m not in lab: continue
        res = {k: json.loads((P / d[k]['path'] / 'result.json').read_text(encoding='utf-8')) for k in ('ON', 'OFF')}
        w = lab[m]; f = lambda r: float(r['final_inventory'][w]) - float(r['final_cost'])
        ev = res['ON']['v12g']['events']; dec = next(x['side'] for x in ev if x['kind'] == 'DECIDE')
        conf = res['ON'].get('v63_confirm') or {}; low = bool(conf.get('lowmode_freeze') and conf.get('m0') is not None and conf['m0'] < conf['lowconv_th'])
        pj = P / d['OFF']['path'] / 'PARITY.json'
        par = json.loads(pj.read_text(encoding='utf-8')).get('status') if pj.exists() else None
        batch = d['ON']['path'].split('/')[0]
        rows.append(dict(m=m, batch=batch, grp='FALSE_FLIP' if dec == w else 'TRUE_FLIP', low=low, nfl=sum(x['kind'] == 'FLIP' for x in ev),
                         on=f(res['ON']), off=f(res['OFF']), cost_on=float(res['ON']['final_cost']), cost_off=float(res['OFF']['final_cost']), parity_inclusive=par))
    n = len(rows); rng = random.Random(1)
    d = [r['on'] - r['off'] for r in rows]
    print('paired markets n=%d (ON %d, OFF %d in pack, labels %d)' % (n, sum('ON' in v for v in arms.values()), sum('OFF' in v for v in arms.values()), len(lab)))
    if not n: return 1
    lo, hi = boot_ci(d, a.boot, rng)
    print('PRIMARY  mean d (ON-OFF) = %.1f  95%% CI [%.0f, %.0f]  (sd %.0f, se %.1f)' % (S.fmean(d), lo, hi, S.stdev(d), S.stdev(d) / n ** .5))
    top = max(rows, key=lambda r: abs(r['on'] - r['off']))
    ex = [r['on'] - r['off'] for r in rows if r is not top]; elo, ehi = boot_ci(ex, a.boot, rng)
    print('median d %.1f | excluding largest |d| (m=%d, d=%.0f): mean %.1f CI [%.0f, %.0f] | improve %d regress %d same %d' % (
        S.median(d), top['m'], top['on'] - top['off'], S.fmean(ex), elo, ehi, sum(x > 1e-9 for x in d), sum(x < -1e-9 for x in d), sum(abs(x) <= 1e-9 for x in d)))
    print('mean pnl ON %.1f OFF %.1f | mean cost ON %.0f OFF %.0f ratio %.2f | worst5 mean ON %.1f OFF %.1f | worst ON %.1f OFF %.1f' % (
        S.fmean(r['on'] for r in rows), S.fmean(r['off'] for r in rows), S.fmean(r['cost_on'] for r in rows), S.fmean(r['cost_off'] for r in rows),
        S.fmean(r['cost_on'] for r in rows) / S.fmean(r['cost_off'] for r in rows), cvar_k([r['on'] for r in rows], 5), cvar_k([r['off'] for r in rows], 5),
        min(r['on'] for r in rows), min(r['off'] for r in rows)))
    out = dict(n=n, mean_d=S.fmean(d), ci=[lo, hi], median_d=S.median(d), groups={})
    print('\n%-26s %4s %8s %18s %9s %9s' % ('group', 'n', 'mean d', '95% CI', 'ON mean', 'OFF mean'))
    def grp(name, rs):
        if len(rs) < 1: return
        dd = [r['on'] - r['off'] for r in rs]; l, h = boot_ci(dd, a.boot, rng)
        print('%-26s %4d %8.1f %18s %9.1f %9.1f' % (name, len(rs), S.fmean(dd), '[%s, %s]' % (('%.0f' % l) if l is not None else 'NA', ('%.0f' % h) if h is not None else 'NA'), S.fmean(r['on'] for r in rs), S.fmean(r['off'] for r in rs)))
        out['groups'][name] = dict(n=len(rs), mean_d=S.fmean(dd))
    for g in ('TRUE_FLIP', 'FALSE_FLIP'): grp(g, [r for r in rows if r['grp'] == g])
    for lo_ in (False, True):
        for g in ('TRUE_FLIP', 'FALSE_FLIP'): grp('%s %s' % ('LOWCONV' if lo_ else 'HIGHCONV', g), [r for r in rows if r['low'] == lo_ and r['grp'] == g])
    for b in sorted({r['batch'] for r in rows}): grp('batch ' + b[:32], [r for r in rows if r['batch'] == b])
    pc = collections.Counter(r['parity_inclusive'] for r in rows)
    print('\ninclusive PARITY.json statuses (informational; spec section 9 gate is strictly-before-FLIP): %s' % dict(pc))
    if a.final:
        print('\nVERDICT (n=%d, spec s10): %s' % (n, 'BETTER (ON improves mean)' if lo > 0 else 'WORSE (ON worsens mean)' if hi < 0 else 'INDETERMINATE'))
    else:
        print('\n(no verdict: spec s10 allows a decision only at n=132 with --final)')
    if a.out: Path(a.out).write_text(json.dumps(dict(summary=out, rows=rows), indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
