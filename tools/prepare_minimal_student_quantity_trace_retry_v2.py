"""Logging-only retry of the interrupted third native smoke. Policies stay pinned."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OLD=ROOT/'.lan_worker_v1/minimal_student_quantity_smoke3_20260910_v1'
NEW=ROOT/'.lan_worker_v1/minimal_student_quantity_trace_retry_20260910_v2'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not NEW.exists(),'immutable retry package exists'
    previous=json.loads((ROOT/'data/research/lan_worker_returns/minimal-student-quantity-smoke3-20260910-v1/COMPACT.json').read_text(encoding='utf-8'))
    assert previous['completedBE']==2 and previous['attemptedBE']==3
    assert previous['error']=='AssertionError: bounded trace exceeded; never truncate silently'
    oldmanifest=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
    for rel,meta in oldmanifest['files'].items():
        p=OLD/rel;assert p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256']
    shutil.copytree(OLD,NEW)
    original=OLD/'run_minimal_student_quantity_smoke3_worker_v1.py'
    text=original.read_text(encoding='utf-8')
    edits={
        "ROOT=Path('C:/BTC5M-worker/.tmp/minimal_student_quantity_smoke3_20260910_v1')":
        "ROOT=Path('C:/BTC5M-worker/.tmp/minimal_student_quantity_trace_retry_20260910_v2')",
        "for mid,qty in [(2022527,30.),(2022538,55.),(2022602,55.)]:":
        "for mid,qty in [(2022602,55.)]:",
        "trace=out/f'OWN_TRAJECTORY_{mid}.jsonl.gz';counts={};native_receipts=0":
        "trace=out/f'OWN_TRAJECTORY_{mid}.jsonl.gz';counts={};logical_bytes=[0]",
        "                    assert sum(counts.values())<=30000,'bounded trace exceeded; never truncate silently'\n                    log.write(json.dumps(ev,separators=(',',':'),allow_nan=False)+'\\n')":
        "                    encoded=json.dumps(ev,separators=(',',':'),allow_nan=False)+'\\n'\n                    logical_bytes[0]+=len(encoded.encode('utf-8'))\n                    assert logical_bytes[0]<=32*1024**2,'32MiB uncompressed trace bound exceeded; no silent truncation'\n                    log.write(encoded)",
        "EXACT_QUANTITY_NATIVE_CAPTURE_SMOKE3_PASS_NOT_TRAINING_READY":
        "EXACT_QUANTITY_NATIVE_CAPTURE_RETRY1_PASS_NOT_TRAINING_READY",
    }
    for old,new in edits.items():
        assert text.count(old)==1,old
        text=text.replace(old,new)
    runner=ROOT/'tools/run_minimal_student_quantity_trace_retry_worker_v2.py'
    assert not runner.exists()
    runner.write_text(text,encoding='utf-8');py_compile.compile(str(runner),doraise=True)
    shutil.copy2(runner,NEW/runner.name)
    amendment='''\n\n## Logging-only amendment after V1 terminal error\n25/25 component tests and2022527/2022538 native accounting passed. The third market2022602 was interrupted by an instrumentation-only30000-event trace count bound, not a strategy assertion. Preserve its incomplete trace as incomplete. Retry ONLY2022602 with the identical quantity55, same native/source/policy hashes, grants, market clock, slots, Pair and continuation. Change only logger capacity accounting from number of events to a32MiB uncompressed streaming byte bound; no sampling/truncation or policy changes. One additional native attempt is disclosed, totalattemptedBE4 for up to3 completed cases. No rerun or reselection of the two completed markets, no economic score optimization.\n'''
    with (NEW/'PREREG.md').open('a',encoding='utf-8') as f:f.write(amendment)
    note=ROOT/'data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_QUANTITY_TRACE_RETRY_AMENDMENT_V2_20260910.md'
    assert not note.exists();note.write_text(amendment,encoding='utf-8')
    files={p.relative_to(NEW).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p))
           for p in NEW.rglob('*') if p.is_file() and p.name!='MANIFEST.json'}
    manifest=dict(oldmanifest,files=files,markets=[2022602],retryOriginalJob='minimal-student-quantity-smoke3-20260910-v1',
                  policyChange=False,loggingOnly=True,logicalTraceByteLimit=32*1024**2)
    (NEW/'MANIFEST.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    for rel in oldmanifest['files']:
        if rel.startswith(('tools/','tests/','tapes/')):
            assert sha(OLD/rel)==sha(NEW/rel),'policy/source data drift'
    print(json.dumps(dict(package=NEW.relative_to(ROOT).as_posix(),
                         runner=runner.name,policyChange=False,onlyMarket=2022602,
                         manifestSha256=sha(NEW/'MANIFEST.json'))))


if __name__=='__main__':main()
