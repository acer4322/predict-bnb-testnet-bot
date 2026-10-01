"""Read-only: pair-formation structure of the TARGET's fills (sanitised export) with the same method as pair_structure.py.
  python tools/target_pair_structure.py target_fills_export.json.gz [--ours-root FILLS_DIR --ours-labels LABELS.json] [--boot 1000]
Target role MAKER/TAKER -> M/T. DOWN price is the DOWN buy price (no conversion). Winner/pnl come from the export (not used for the pair cost).
If --ours-root is given, the same statistics are printed for OUR fills on the markets both have (like-for-like)."""
import argparse, collections, gzip, json, random, statistics as S
from pathlib import Path
from pair_structure import analyse, wavg, path_fills, jload


def summarize(title, per, allp, rng, boot):
    print('\n=== %s ===' % title)
    print('markets %d | matched sh %.0f | residual sh %.0f | FIFO pair cost %.4f (edge %+.2f c) | mean locked/market %.1f' % (
        len(per), sum(r['M'] for r in per), sum(r['e'] for r in per), wavg([(m, c) for m, c, l, k in allp]), 100 * (1 - wavg([(m, c) for m, c, l, k in allp])), S.fmean(r['locked'] for r in per)))
    print('%-10s %10s %9s %9s' % ('lag (s)', 'matched sh', 'pair cost', 'edge (c)'))
    for lo, hi in [(0, 5), (5, 15), (15, 40), (40, 100), (100, 400)]:
        s = [(m, c) for m, c, l, k in allp if lo <= l < hi]
        if s: print('%-10s %10.0f %9.4f %+9.2f' % ('%d-%d' % (lo, hi), sum(m for m, _ in s), wavg(s), 100 * (1 - wavg(s))))
    print('%-8s %10s %9s %9s' % ('legs', 'matched sh', 'pair cost', 'edge (c)'))
    for k in sorted({k for _, _, _, k in allp}):
        s = [(m, c) for m, c, l, kk in allp if kk == k]; print('%-8s %10.0f %9.4f %+9.2f' % (k, sum(m for m, _ in s), wavg(s), 100 * (1 - wavg(s))))
    L = [r for r in per if 'pnl' in r]
    if L:
        def ci(xs):
            ms = sorted(S.fmean(rng.choice(xs) for _ in xs) for _ in range(boot)); return ms[int(.025 * boot)], ms[int(.975 * boot)]
        print('PnL decomposition (%d labelled): locked %.1f [%.1f,%.1f] | residual %.1f [%.1f,%.1f] | total %.1f' % (len(L), S.fmean(r['locked'] for r in L), *ci([r['locked'] for r in L]),
              S.fmean(r['resid'] for r in L), *ci([r['resid'] for r in L]), S.fmean(r['pnl'] for r in L)))
        tw = sorted(L, key=lambda r: r['e'] / max(1e-9, r['M'] + r['e'])); k = len(tw) // 3
        for nm, g in (('most balanced third', tw[:k]), ('middle third', tw[k:2 * k]), ('most one-sided third', tw[2 * k:])):
            print('  %-22s locked %7.1f resid %7.1f total %7.1f | imbalance %.0f%%' % (nm, S.fmean(r['locked'] for r in g), S.fmean(r['resid'] for r in g), S.fmean(r['pnl'] for r in g), 100 * S.fmean(r['e'] / (r['M'] + r['e']) for r in g)))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('export'); ap.add_argument('--ours-root', default=''); ap.add_argument('--ours-labels', default=''); ap.add_argument('--boot', type=int, default=1000)
    a = ap.parse_args(); rng = random.Random(1)
    rows = json.loads(gzip.open(a.export).read()); by = collections.defaultdict(list); win = {}; pn = {}
    for r in rows:
        by[int(r['market_id'])].append((int(r['event_ms']), r['side'], 'T' if r['role'] == 'TAKER' else 'M', 'T' if r['role'] == 'TAKER' else 'M', float(r['shares']), float(r['price'])))
        win[int(r['market_id'])] = r.get('winner'); pn[int(r['market_id'])] = r.get('net_pnl_usdt')
    tper, mk = [], []
    for m, rs in sorted(by.items()):
        rs.sort(); x = analyse(rs)
        if x is None: continue
        x['market'] = m; w = win[m]
        if w in ('UP', 'DOWN'): x['resid'] = x['e'] * (1 - x['pb']) if w == x['big'] else -x['e'] * x['pb']; x['pnl'] = x['locked'] + x['resid']; x['export_pnl'] = pn[m]
        tper.append(x)
    summarize('TARGET (all %d markets with both sides)' % len(tper), tper, [p for r in tper for p in r['pairs']], rng, a.boot)
    L = [r for r in tper if 'pnl' in r]
    print('\nconsistency: mean export net_pnl %.1f vs reconstructed locked+residual %.1f over %d markets (export is fee-free fill reconstruction)' % (S.fmean(r['export_pnl'] for r in L), S.fmean(r['pnl'] for r in L), len(L)))
    if a.ours_root:
        lab = {int(r['market_id']): r['winner'] for r in jload(a.ours_labels).get('records', [])} if a.ours_labels else {}
        oper = []
        for d in sorted(p.parent for p in Path(a.ours_root).rglob('execution_clock.json') if 'parity' not in str(p.parent)):
            m, rs = path_fills(d)
            if m not in by: continue
            x = analyse(rs)
            if x is None: continue
            x['market'] = m; w = lab.get(m)
            if w: x['resid'] = x['e'] * (1 - x['pb']) if w == x['big'] else -x['e'] * x['pb']; x['pnl'] = x['locked'] + x['resid']
            oper.append(x)
        common = {r['market'] for r in oper}
        summarize('OURS on the %d common markets' % len(oper), oper, [p for r in oper for p in r['pairs']], rng, a.boot)
        summarize('TARGET on the same %d markets' % len(common), [r for r in tper if r['market'] in common], [p for r in tper if r['market'] in common for p in r['pairs']], rng, a.boot)


if __name__ == '__main__':
    main()
