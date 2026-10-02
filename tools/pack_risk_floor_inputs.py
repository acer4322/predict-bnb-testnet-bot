"""Pack only the small files needed by tools/risk_floor_lock_stats.py into one zip (read-only on the source tree).

  python tools/pack_risk_floor_inputs.py ROOT OUT.zip [--filter CG1AT] [--with-clock] [--extra labels.json ...]

Includes per path: risk_floor_trace.json.gz, result.json (+ clock_trace.json.gz with --with-clock). Prints the size first.
"""
import argparse, sys, zipfile
from pathlib import Path

NAMES = ['risk_floor_trace.json.gz', 'result.json']


def pack_fills(root, a):
    import json
    import re
    dirs = sorted({p.parent for p in root.rglob('execution_clock.json') if a.filter in str(p.parent)})
    proot = Path(a.public_root).resolve() if a.public_root else root.parent
    index = {}
    for p in proot.rglob('public_*.json.gz'):
        m = re.fullmatch(r'public_(\d+)\.json\.gz', p.name)
        if m: index.setdefault(int(m.group(1)), p)
    print('public book files indexed under %s: %d markets' % (proot, len(index)))
    members, missing = [], []
    for d in dirs:
        rj = d / 'result.json'
        if not rj.exists():
            missing.append(str(d) + ' (no result.json)'); continue
        res = json.loads(rj.read_text(encoding='utf-8'))
        pub = [index[int(res['market_id'])]] if res.get('market_id') is not None and int(res['market_id']) in index else sorted(d.glob('public_*.json.gz'))
        if not pub:
            missing.append(str(d) + ' (no public book for market %s)' % res.get('market_id')); continue
        flips = json.dumps(dict(market_id=res.get('market_id'), events=(res.get('v12g') or {}).get('events') or [])).encode()
        rel = d.relative_to(root)
        members += [(str(rel / 'execution_clock.json'), (d / 'execution_clock.json').read_bytes()),
                    (str(rel / pub[0].name), pub[0].read_bytes()), (str(rel / 'flips.json'), flips)]
    print('paths=%d packed=%d missing_public_or_result=%d size=%.1f MB (uncompressed)' % (len(dirs), len(members) // 3, len(missing), sum(len(b) for _, b in members) / 1e6))
    for m in missing[:5]: print('  missing:', m)
    if not members: return 1
    with zipfile.ZipFile(a.out, 'w', zipfile.ZIP_DEFLATED) as z:
        for name, b in members: z.writestr(name.replace('\\', '/'), b)
    print('wrote', a.out, '(%.1f MB)' % (Path(a.out).stat().st_size / 1e6))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('out')
    ap.add_argument('--filter', default='')
    ap.add_argument('--with-clock', action='store_true')
    ap.add_argument('--extra', nargs='*', default=[])
    ap.add_argument('--public-root', default='', help='where to look for public_<market>.json.gz (default: parent of ROOT)')
    ap.add_argument('--fills', action='store_true', help='pack execution_clock.json + public_*.json.gz + slim flips.json instead')
    a = ap.parse_args()
    if a.fills:
        return pack_fills(Path(a.root).resolve(), a)
    root = Path(a.root).resolve()
    names = NAMES + (['clock_trace.json.gz'] if a.with_clock else [])
    dirs = sorted({p.parent for p in root.rglob('risk_floor_trace.json.gz') if a.filter in str(p.parent)})
    files = [(d / n, d.relative_to(root) / n) for d in dirs for n in names if (d / n).exists()]
    extra = [(Path(e), Path('extra') / Path(e).name) for e in a.extra if Path(e).exists()]
    total = sum(p.stat().st_size for p, _ in files + extra)
    print('paths=%d files=%d size=%.1f MB' % (len(dirs), len(files) + len(extra), total / 1e6))
    if not dirs:
        print('no risk_floor_trace.json.gz found under', root); return 1
    with zipfile.ZipFile(a.out, 'w', zipfile.ZIP_DEFLATED) as z:
        for src, arc in files + extra:
            z.writestr(str(arc).replace('\\', '/'), src.read_bytes())
    print('wrote', a.out)
    return 0


if __name__ == '__main__':
    sys.exit(main())
