"""Host hash/copy/syntax preparation only; stateful tests run on second LAN worker."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.lan_worker_v1/minimal_student_system_logic_smoke3_20260910_v1'
SOURCES=[
 'tools/minimal_student_system_plan_v1.py',
 'tools/pair_core_economic_grant_ledger_v1.py',
 'tools/pair_core_asset_route_sizing_v2.py',
 'tools/allocation_ledger_v2.py',
 'tools/hft244_pair_route_legality_v1.py',
]


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    assert not OUT.exists(),'immutable package already exists'
    files={rel:ROOT/rel for rel in SOURCES}
    files['run_minimal_student_system_logic_smoke3_worker_v1.py']=ROOT/'tools/run_minimal_student_system_logic_smoke3_worker_v1.py'
    files['CONTRACT.md']=ROOT/'data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_SYSTEM_LOGIC_CONTRACT_V1_20260910.md'
    for rel,p in files.items():
        assert p.stat().st_size<32000
        if p.suffix=='.py':py_compile.compile(str(p),doraise=True)
    OUT.mkdir(parents=True)
    for rel,p in files.items():
        dest=OUT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    (OUT/'tools/__init__.py').write_text('# Isolated research fixture package; no production initialization.\n',encoding='utf-8')
    metadata={p.relative_to(OUT).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p)) for p in OUT.rglob('*') if p.is_file()}
    (OUT/'MANIFEST.json').write_text(json.dumps(dict(version='MINIMAL_STUDENT_SYSTEM_LOGIC_SMOKE3_V1',files=metadata,
        worker_only=True,max_threads=4,max_seconds=180,HFT=0,model_fits=0,market_rows_consumed=0,
        test_kind='SYNTHETIC_FAULT_INJECTION_NOT_FILL_SIMULATION'),indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),bytes=sum(x['bytes'] for x in metadata.values()),
                         manifest_sha256=sha(OUT/'MANIFEST.json'))))


if __name__=='__main__':main()
