"""Pack only the small files needed by tools/risk_floor_lock_stats.py into one zip (read-only on the source tree).

  python tools/pack_risk_floor_inputs.py ROOT OUT.zip [--filter CG1AT] [--with-clock] [--extra labels.json ...]

Includes per path: risk_floor_trace.json.gz, result.json (+ clock_trace.json.gz with --with-clock). Prints the size first.
"""
import argparse, sys, zipfile
from pathlib import Path

NAMES = ['risk_floor_trace.json.gz', 'result.json']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('out')
    ap.add_argument('--filter', default='')
    ap.add_argument('--with-clock', action='store_true')
    ap.add_argument('--extra', nargs='*', default=[])
    a = ap.parse_args()
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
