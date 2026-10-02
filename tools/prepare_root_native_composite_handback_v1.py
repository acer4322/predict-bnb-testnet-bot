"""Immutable reuse of current observer package and selected source contract."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]


def main():
    src=ROOT/'.lan_worker_v1/root_pre_active_option_audit3_20260910_v1'
    out=ROOT/'.lan_worker_v1/root_native_composite_handback3_20260910_v1'
    assert not out.exists(); out.mkdir()
    m=json.loads((src/'AUDIT_MANIFEST.json').read_text()); files={}
    for rel,sha in m['files'].items():
        if rel=='PREREG.md': continue
        p=src/rel; assert hashlib.sha256(p.read_bytes()).hexdigest()==sha
        d=out/rel; d.parent.mkdir(parents=True,exist_ok=True); shutil.copy2(p,d); files[rel]=sha
    for source,rel in [
        ('tools/run_root_native_composite_handback_v1.py','tools/run_root_native_composite_handback_v1.py'),
        ('data/research/r4_v0/p0_provenance_v1/ROOT_NATIVE_COMPOSITE_HANDBACK_SOURCE_SMOKE3_PREREG_V1_20260910.md','PREREG.md')]:
        shutil.copy2(ROOT/source,out/rel); files[rel]=hashlib.sha256((out/rel).read_bytes()).hexdigest()
    manifest=dict(files=files,markets=[2022527,2022538,2022602],maxBE=6,
        parentSha256=hashlib.sha256((src/'AUDIT_MANIFEST.json').read_bytes()).hexdigest())
    (out/'HANDBACK_MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=str(out),bytes=sum((out/p).stat().st_size for p in files),
        manifestSha256=hashlib.sha256((out/'HANDBACK_MANIFEST.json').read_bytes()).hexdigest())))


if __name__=='__main__': main()
