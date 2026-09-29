"""Small immutable package: code/hash/syntax only on host. Native train on worker."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_training_rules_v2_clockfix_20260910'
SOURCE=ROOT/'.lan_worker_v1/minimal_student_preflight_smoke3_20260910_v1'
OUT=ROOT/'.lan_worker_v1/minimal_student_whole_episode_train_20260911_v1'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def main():
    assert not OUT.exists(),'immutable train package exists'
    wp=OLD/'MANIFEST.json';assert wp.stat().st_size<30000
    old=json.loads(wp.read_text(encoding='utf-8'))
    assert old['latestUserOverridesResearch180Fence'] is True
    assert old['files']['tools/minimal_student_training_world_v2.py']['sha256']=='e1c231f255ed8c77dc4f9a37464ec3960edc44effa7bd21b42ac170af608fa34'
    source=json.loads((SOURCE/'MANIFEST.json').read_text(encoding='utf-8'))
    files={
        'minimal_student_joint_policy_train_v1.py':ROOT/'tools/minimal_student_joint_policy_train_v1.py',
        'run_minimal_student_whole_episode_train_v1.py':ROOT/'tools/run_minimal_student_whole_episode_train_v1.py',
        'PREREG.md':R/'MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_PREREG_V1_20260911.md'}
    for mid in (2022527,2022538,2022602):
        name=f'input_{mid}.json.gz';p=SOURCE/name;m=source['files'][name]
        assert p.stat().st_size==m['bytes'] and sha(p)==m['sha256']
        files[name]=p
    for rel,p in files.items():
        assert p.stat().st_size<1024**2
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    OUT.mkdir(parents=True)
    for rel,p in files.items():shutil.copy2(p,OUT/rel)
    metadata={rel:dict(bytes=(OUT/rel).stat().st_size,sha256=sha(OUT/rel)) for rel in files}
    manifest=dict(version='MINIMAL_STUDENT_WHOLE_EPISODE_TRAIN_V1',files=metadata,
        world_manifest_sha256=sha(wp),target_source_manifest_sha256=sha(SOURCE/'MANIFEST.json'),
        train_markets=[2022527,2022538],pipeline_check=[2022602],max_native_runs=10,
        actual_joint_policy_update=True,private_target_action_teacher=False,
        max_threads=4,total_test_capital_unchanged=100,live_changes=0)
    (OUT/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),bytes=sum(m['bytes'] for m in metadata.values()),
        world_manifest_sha256=manifest['world_manifest_sha256'],manifest_sha256=sha(OUT/'MANIFEST.json'))))


if __name__=='__main__':main()
