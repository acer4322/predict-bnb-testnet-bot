"""Build a small, indexed research pack from collected worker returns (read-only on the source tree) and optionally push it to a
SEPARATE data branch through a temporary git worktree (your working tree, index and current branch are never touched).

  python tools/sync_research_pack.py RETURNS_ROOT --job fresh100a --job floor-ab --limit 3 --out research_pack_out
  python tools/sync_research_pack.py RETURNS_ROOT --markets 2672250 2672737 --out research_pack_out --git-push --branch research-data

Per path directory it copies (when present): result.json, risk_floor_trace.json.gz, execution_clock.json, AUDIT.json, PARITY.json,
EXECUTION.json, and public_<market>.json.gz (found by market id under --public-root, default: parent of RETURNS_ROOT).
Never copied: clock_trace, databases, tapes, .env, logs. A text scan flags credential-like patterns and aborts unless --allow-flagged.
The scan is a convenience, NOT a guarantee: you remain responsible for confirming nothing private is in the files and the repo is private.
Writes <out>/<job>/<arm_dir>/... and <out>/INDEX.json (sizes, sha256, flips, parity/audit status).
"""
import argparse, gzip, hashlib, json, re, shutil, subprocess, sys, tempfile
from pathlib import Path

COPY = ['result.json', 'risk_floor_trace.json.gz', 'execution_clock.json', 'AUDIT.json', 'PARITY.json', 'EXECUTION.json']
FLAG = [re.compile(p, re.I) for p in (r'api[_-]?key', r'secret', r'passw(or)?d', r'private[_-]?key', r'authorization', r'bearer\s', r'\bsk-[a-z0-9]{16,}',
                                       r'mnemonic', r'seed phrase', r'BEGIN [A-Z ]*PRIVATE KEY')]


def sha(b): return hashlib.sha256(b).hexdigest()


def read_text(p):
    b = p.read_bytes()
    if p.suffix == '.gz': b = gzip.decompress(b)
    return b.decode('utf-8', 'ignore')


SLIM_KEYS = ('general_finite_active_rows',)  # ~90% of result.json, not used by the analysis tools; original sha256 + row count are kept


def content(p, slim):
    b = p.read_bytes()
    if slim and p.name == 'result.json':
        try:
            r = json.loads(b.decode('utf-8'))
            for k in SLIM_KEYS:
                if k in r and isinstance(r[k], list):
                    r[k] = dict(_omitted_rows=len(r[k]), _note='slimmed by sync_research_pack')
            return json.dumps(r, separators=(',', ':')).encode('utf-8'), True
        except Exception:
            return b, False
    return b, False


def scan(p):
    try: t = read_text(p)
    except Exception: return []
    return sorted({f.pattern for f in FLAG if f.search(t)})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root'); ap.add_argument('--out', default='research_pack_out')
    ap.add_argument('--job', action='append', default=[], help='substring of the job directory name (repeatable)')
    ap.add_argument('--arm', default='', help='substring of the path directory name, e.g. CG1AT or OFF')
    ap.add_argument('--markets', nargs='*', type=int, default=[])
    ap.add_argument('--markets-file', default='', help='JSON with stage lists, e.g. docs/research_specs/RISK_FLOOR_AB_MARKETS_20261001.json')
    ap.add_argument('--stage', action='append', default=[], help='stage key(s) from --markets-file (stage1, stage2, stage3)')
    ap.add_argument('--extra', nargs='*', default=[], help='small files (e.g. OFFLINE_SETTLEMENT_LABELS.json) copied to <out>/labels/')
    ap.add_argument('--limit', type=int, default=0, help='max number of path directories (pilot)')
    ap.add_argument('--public-root', default='')
    ap.add_argument('--max-mb', type=float, default=50.)
    ap.add_argument('--allow-flagged', action='store_true')
    ap.add_argument('--full-result', action='store_true', help='do not slim result.json (default drops general_finite_active_rows, ~90%% of its size)')
    ap.add_argument('--git-push', action='store_true'); ap.add_argument('--branch', default='research-data')
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args()
    if a.markets_file:
        mf = json.loads(Path(a.markets_file).read_text(encoding='utf-8')); a.markets = list(a.markets) + [m for k in (a.stage or ['stage1']) for m in mf[k]]
    root = Path(a.root).resolve(); proot = Path(a.public_root).resolve() if a.public_root else root.parent
    pub = {}
    for p in proot.rglob('public_*.json.gz'):
        m = re.fullmatch(r'public_(\d+)\.json\.gz', p.name)
        if m: pub.setdefault(int(m.group(1)), p)
    dirs = sorted({p.parent for p in root.rglob('result.json')})
    dirs = [d for d in dirs if (not a.job or any(j in str(d.relative_to(root)) for j in a.job)) and a.arm in d.name and '_auto_collect' not in str(d)]
    entries, flagged, total, extras = [], [], 0, []
    for x in a.extra:
        xp = Path(x)
        if not xp.exists(): print('missing extra:', x); continue
        fl = scan(xp)
        if fl: flagged.append((str(xp), fl))
        total += xp.stat().st_size; extras.append(dict(name=xp.name, bytes=xp.stat().st_size, sha256=sha(xp.read_bytes()), src=str(xp)))
    for d in dirs:
        try: res = json.loads((d / 'result.json').read_text(encoding='utf-8'))
        except Exception: continue
        mid = res.get('market_id')
        if a.markets and mid not in a.markets: continue
        if a.limit and len(entries) >= a.limit: break
        files = [(n, d / n) for n in COPY if (d / n).exists()]
        if mid is not None and int(mid) in pub: files.append((pub[int(mid)].name, pub[int(mid)]))
        ev = (res.get('v12g') or {}).get('events') or []
        e = dict(path=str(d.relative_to(root)).replace('\\', '/'), market_id=mid, status=res.get('status'), n_flips=sum(x['kind'] == 'FLIP' for x in ev),
                 decide_side=next((x['side'] for x in ev if x['kind'] == 'DECIDE'), None), files={})
        for n, p in files:
            fl = scan(p)
            if fl: flagged.append((str(p), fl))
            data, slimmed = content(p, not a.full_result)
            total += len(data)
            e['files'][n] = dict(bytes=len(data), sha256=sha(data), source_sha256=sha(p.read_bytes()), slimmed=slimmed)
        entries.append(e)
    print('paths=%d size=%.1f MB (uncompressed) public_books=%d' % (len(entries), total / 1e6, sum(any(k.startswith('public_') for k in e['files']) for e in entries)))
    for p, fl in flagged[:10]: print('FLAGGED', p, fl)
    if flagged and not a.allow_flagged: print('abort: flagged files (review them, then rerun with --allow-flagged if they are false positives)'); return 2
    if total > a.max_mb * 1e6: print('abort: over --max-mb %.0f (use --limit/--job/--markets to shrink)' % a.max_mb); return 3
    if a.dry_run or not entries: print('dry-run / nothing to do'); return 0
    out = Path(a.out).resolve(); out.mkdir(parents=True, exist_ok=True)
    for x in extras:
        (out / 'labels').mkdir(exist_ok=True); shutil.copy2(x['src'], out / 'labels' / x['name'])
    for e in entries:
        for n in e['files']:
            src = (root / e['path'] / n) if (root / e['path'] / n).exists() else pub[int(e['market_id'])]
            dst = out / e['path'] / n; dst.parent.mkdir(parents=True, exist_ok=True); dst.write_bytes(content(src, not a.full_result)[0])
    (out / 'INDEX.json').write_text(json.dumps(dict(root_hint=root.name, paths=len(entries), entries=entries, labels=[{k: v for k, v in x.items() if k != 'src'} for x in extras]), indent=1), encoding='utf-8')
    print('wrote', out)
    if not a.git_push:
        print('next: review the folder, then rerun with --git-push to publish it to branch', a.branch); return 0
    repo = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True, check=True).stdout.strip()
    git = lambda *x, cwd=repo: subprocess.run(['git', *x], cwd=cwd, capture_output=True, text=True)
    r = git('ls-remote', '--exit-code', '--heads', 'origin', a.branch)
    wt = Path(tempfile.mkdtemp(prefix='research_pack_wt_'))
    try:
        if r.returncode == 0:
            git('fetch', 'origin', a.branch); x = git('worktree', 'add', '--detach', str(wt), 'FETCH_HEAD')
        else:
            x = git('worktree', 'add', '--detach', str(wt), 'HEAD')
        if x.returncode: print('worktree failed:', x.stderr); return 4
        if r.returncode != 0:
            git('checkout', '--orphan', a.branch, cwd=str(wt)); git('rm', '-rf', '--quiet', '.', cwd=str(wt))
        else:
            git('checkout', '-B', a.branch, cwd=str(wt))
        dest = wt / 'research_pack'; dest.mkdir(exist_ok=True)
        old = {}
        if (dest / 'INDEX.json').exists(): old = {e['path']: e for e in json.loads((dest / 'INDEX.json').read_text())['entries']}
        for e in entries: old[e['path']] = e
        shutil.copytree(out, dest, dirs_exist_ok=True)
        oldlab = {}
        if (dest / 'INDEX.json').exists(): oldlab = {l['name']: l for l in json.loads((dest / 'INDEX.json').read_text()).get('labels', [])}
        for x in extras: oldlab[x['name']] = {k: v for k, v in x.items() if k != 'src'}
        (dest / 'INDEX.json').write_text(json.dumps(dict(paths=len(old), entries=list(old.values()), labels=list(oldlab.values())), indent=1), encoding='utf-8')
        git('add', 'research_pack', cwd=str(wt))
        c = git('commit', '-m', 'research pack: +%d paths' % len(entries), cwd=str(wt))
        print((c.stdout or c.stderr).strip().splitlines()[0] if (c.stdout or c.stderr) else 'commit?')
        p = git('push', '-u', 'origin', a.branch, cwd=str(wt)); print('push rc=%d %s' % (p.returncode, (p.stderr or p.stdout).strip().splitlines()[-1] if (p.stderr or p.stdout) else ''))
        return p.returncode
    finally:
        git('worktree', 'remove', '--force', str(wt))


if __name__ == '__main__':
    sys.exit(main())
