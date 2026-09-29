"""Small host hash/copy/syntax only; all native work on second LAN worker."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_quantity_smoke3_20260910_v1'
OUT=ROOT/'.lan_worker_v1/minimal_student_native_system_plan_20260910_v1'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert not OUT.exists(),'immutable package exists'
    old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
    files={}
    for rel,m in old['files'].items():
        if not rel.startswith('tools/') and rel not in ('tapes/2022527.json.xz','tapes/2022538.json.xz'):continue
        p=OLD/rel;assert p.stat().st_size==m['bytes'] and sha(p)==m['sha256']
        files[rel]=p
    for rel in ['tools/minimal_student_system_plan_v1.py','tools/minimal_student_native_system_plan_v1.py',
                'tests/test_minimal_student_native_system_plan_v1.py']:
        files[rel]=ROOT/rel
    files['run_minimal_student_native_system_plan_worker_v1.py']=ROOT/'tools/run_minimal_student_native_system_plan_worker_v1.py'
    files['PREREG.md']=R/'MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_PREREG_V1_20260910.md'
    assert sha(ROOT/'tools/minimal_student_system_plan_v1.py')=='3eb0b02556e3e97988ec217a26d15a51a0be0905672ead548b110e274b35c2a9'
    for rel,p in files.items():
        assert p.stat().st_size<5*1024**2
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    OUT.mkdir(parents=True)
    for rel,p in files.items():
        dst=OUT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
    mm={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    manifest=dict(version='MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_V1',files=mm,
        pinned_source_package_sha256=sha(OLD/'MANIFEST.json'),
        consumed_markets=[2022527,2022538],maxBE=5,maxThreads=4,modelFits=0,liveChanges=0)
    (OUT/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),bytes=sum(x['bytes'] for x in mm.values()),
                         manifestSha256=sha(OUT/'MANIFEST.json'),files=len(mm))))


if __name__=='__main__':main()
