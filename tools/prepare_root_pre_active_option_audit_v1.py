"""Reuse only already-frozen three-market inputs; no extraction or new data."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    source = ROOT/'.lan_worker_v1/root_dual_legal_smoke3_20260910_v1'
    dest = ROOT/'.lan_worker_v1/root_pre_active_option_audit3_20260910_v1'
    assert not dest.exists(), 'immutable package already exists'
    parent = json.loads((source/'DUAL_MANIFEST.json').read_text())
    dest.mkdir()
    files = {}

    def add(src, rel, expected=None):
        assert src.stat().st_size <= 5*1024**2, src
        sha = hashlib.sha256(src.read_bytes()).hexdigest()
        assert expected is None or sha == expected, src
        target = dest/rel; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target); files[rel] = sha

    for rel, sha in parent['files'].items():
        if rel.endswith('.py') or rel.startswith('tapes/') or rel == 'dual_public.json':
            add(source/rel, rel, sha)
    add(ROOT/'tools/run_root_pre_active_option_audit_v1.py', 'tools/run_root_pre_active_option_audit_v1.py')
    add(ROOT/'data/research/lan_worker_returns/root-dual-legal-label-smoke3-20260910-v1/COMPACT.json',
        'prior.json', 'af94a92fe2675d1a43737393df2089fb9086a2c34494980e53553d96bc20a169')
    add(ROOT/'data/research/r4_v0/p0_provenance_v1/ROOT_PRE_ACTIVE_NATIVE_OPTION_SUPPORT_PREREG_V1_20260910.md', 'PREREG.md')
    manifest = dict(files=files, markets=[2022527, 2022538, 2022602], maxBE=3,
                    parentSha256=hashlib.sha256((source/'DUAL_MANIFEST.json').read_bytes()).hexdigest())
    (dest/'AUDIT_MANIFEST.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(json.dumps(dict(package=str(dest), bytes=sum((dest/p).stat().st_size for p in files),
                         manifestSha256=hashlib.sha256((dest/'AUDIT_MANIFEST.json').read_bytes()).hexdigest())))


if __name__ == '__main__':
    main()
