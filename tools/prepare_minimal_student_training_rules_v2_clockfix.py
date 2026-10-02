"""Schema-only recovery; preserve failed artifacts, policies, cases and financial limits."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_training_rules_v2_20260910'
NEW=ROOT/'.lan_worker_v1/minimal_student_training_rules_v2_clockfix_20260910'
R=ROOT/'data/research/r4_v0/p0_provenance_v1'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not NEW.exists()
    fp=ROOT/'data/research/lan_worker_returns/minimal-student-training-rules-v2-20260910/COMPACT.json'
    fail=json.loads(fp.read_text(encoding='utf-8'))
    assert fail['error']=="KeyError: 'window_start_ms'" and fail['nativeComplete']==0
    assert not fail['partialTrace']['actions'] and fail['unitTests']['passed']
    source=ROOT/'data/research/lan_worker_returns/minimal-student-preflight-smoke3-20260910-v1/COMPACT.json'
    assert sha(source)=='71cd9c4d3d2a2d00154f640f5a39fa38ca9e3e80d519f9073540722f2b27d131'
    w=json.loads(source.read_text(encoding='utf-8'));assert all(r['sourceCapturePass'] for r in w['rows'])
    windows={str(x['marketId']):x['window'] for x in w['rows']}
    assert list(windows)==['2022527','2022538','2022602']
    old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
    for rel,m in old['files'].items():assert sha(OLD/rel)==m['sha256']
    shutil.copytree(OLD,NEW)
    for rel,src in [('tools/minimal_student_training_world_v2.py','tools/minimal_student_training_world_v2.py'),
                    ('tests/test_minimal_student_training_world_v2.py','tests/test_minimal_student_training_world_v2.py'),
                    ('run_minimal_student_training_rules_v2_worker.py','tools/run_minimal_student_training_rules_v2_worker.py')]:
        py_compile.compile(str(ROOT/src),doraise=True);shutil.copy2(ROOT/src,NEW/rel)
    shutil.copy2(source,NEW/'WINDOW_SOURCE_COMPACT.json')
    # Bind metadata to a previously source-verified interval, never end-300000 guess.
    p=NEW/'run_minimal_student_training_rules_v2_worker.py';s=p.read_text(encoding='utf-8')
    needle="        assert sha(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA"
    inserted="""        window_source=BUNDLE/'WINDOW_SOURCE_COMPACT.json'
        assert sha(window_source)==manifest['marketWindowSourceSha256']=='71cd9c4d3d2a2d00154f640f5a39fa38ca9e3e80d519f9073540722f2b27d131'
        windows=json.loads(window_source.read_text(encoding='utf-8'))['rows']
        assert all(x['sourceCapturePass'] for x in windows)
        assert manifest['marketWindows']=={str(x['marketId']):x['window'] for x in windows}
"""
    assert s.count(needle)==1;s=s.replace(needle,inserted+needle);p.write_text(s,encoding='utf-8')
    py_compile.compile(str(p),doraise=True)
    amendment='''\n\n## Schema-only recovery, before any physical action\nInitial job39/39tests PASS, but first native frame could not read absent tape window_start_ms;0physical actions/0complete markets. Keep failed result/trace. Retry3fixed markets with exactly the same rule profile, witness, quantity, budget and execution. Bind market interval to the previously verified preflight COMPACT SHA71cd9c4d3d2a2d00154f640f5a39fa38ca9e3e80d519f9073540722f2b27d131 and require native tape end agreement; no fixed300-second inference. Add2source-window tests, total41 distinct tests. New isolated source/run root. Total native attempts may be4including the aborted pre-action initialization, only3complete. No economic retuning.\n'''
    with (NEW/'PREREG.md').open('a',encoding='utf-8') as f:f.write(amendment)
    note=R/'MINIMAL_STUDENT_TRAINING_RULES_V2_CLOCK_SCHEMA_AMENDMENT_20260910.md'
    assert not note.exists();note.write_text(amendment,encoding='utf-8')
    files={p.relative_to(NEW).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p))
           for p in NEW.rglob('*') if p.is_file() and p.name!='MANIFEST.json' and '__pycache__' not in p.parts}
    for rel,want in old['frozenOriginalSources'].items():assert sha(NEW/rel)==want
    m=dict(old,files=files,marketWindows=windows,marketWindowSourceSha256=sha(source),
           previousJob='minimal-student-training-rules-v2-20260910',previousResultSha256=sha(fp),
           previousNativeAttempts=1,previousPhysicalActions=0,schemaOnlyRetry=True)
    (NEW/'MANIFEST.json').write_text(json.dumps(m,indent=2),encoding='utf-8')
    print(json.dumps(dict(package=NEW.relative_to(ROOT).as_posix(),manifestSha256=sha(NEW/'MANIFEST.json'),
        bytes=sum(x['bytes'] for x in files.values()),schemaOnly=True)))


if __name__=='__main__':main()
