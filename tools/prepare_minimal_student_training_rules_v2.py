"""Host bounded copy/hash/compile; no market decoding, fitting or replay."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_native_system_plan_20260910_v1'
QTY=ROOT/'.lan_worker_v1/minimal_student_quantity_smoke3_20260910_v1'
OUT=ROOT/'.lan_worker_v1/minimal_student_training_rules_v2_20260910'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert not OUT.exists(),'immutable staged package exists'
    old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'));files={}
    frozen={}
    for rel,meta in old['files'].items():
        if rel.startswith('tools/') or rel in ('tapes/2022527.json.xz','tapes/2022538.json.xz'):
            p=OLD/rel;assert p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256'];files[rel]=p
            if rel.startswith('tools/'):frozen[rel]=meta['sha256']
    q=json.loads((QTY/'MANIFEST.json').read_text(encoding='utf-8'))
    rel='tapes/2022602.json.xz';p=QTY/rel
    assert p.stat().st_size==q['files'][rel]['bytes'] and sha(p)==q['files'][rel]['sha256'];files[rel]=p
    for rel in ['tools/minimal_student_training_world_v2.py','tests/test_minimal_student_training_world_v2.py']:
        files[rel]=ROOT/rel
    files['run_minimal_student_training_rules_v2_worker.py']=ROOT/'tools/run_minimal_student_training_rules_v2_worker.py'
    files['PREREG.md']=R/'MINIMAL_STUDENT_TRAINING_RULES_V2_PREREG_20260910.md'
    for rel,p in files.items():
        assert p.stat().st_size<5*1024**2
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    OUT.mkdir(parents=True)
    for rel,p in files.items():
        dst=OUT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
    meta={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    m=dict(version='MINIMAL_STUDENT_TRAINING_RULES_V2',files=meta,frozenOriginalSources=frozen,
        consumedMarkets=[2022527,2022538,2022602],maxNativeAttempts=3,modelFits=0,liveChanges=0,
        maxThreads=4,maxLiveOwnersResourceFixture=32,initialTotalCapitalUnchanged=100,
        priorNativeBundleSha256=sha(OLD/'MANIFEST.json'),latestUserOverridesResearch180Fence=True)
    (OUT/'MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),files=len(meta),
        bytes=sum(x['bytes'] for x in meta.values()),manifestSha256=sha(OUT/'MANIFEST.json'))))


if __name__=='__main__':main()
