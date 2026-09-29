"""Prepare a small immutable, hash-pinned worker package; never run HFT here."""
from pathlib import Path
import hashlib
import json
import py_compile
import shutil
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.lan_worker_v1/minimal_student_quantity_smoke3_20260910_v1'
MIDS=(2022527,2022538,2022602)
FILES=[
    'tools/minimal_student_quantity_seam_v1.py',
    'tests/test_minimal_student_quantity_seam_v1.py',
    'tools/pair_core_asset_route_sizing_v2.py',
    'tools/pair_core_economic_grant_ledger_v1.py',
    'tools/pair_core_objective_quantity_planner_v1.py',
    'tools/allocation_ledger_v2.py',
    'tools/hft244_pair_route_legality_v1.py',
    'tools/run_eth_role_separated_minimal_pair_safety_smoke.py',
    'tools/run_eth_role_separated_multislot_v3_smoke.py',
    'tools/run_eth_target_grounded_distinct_multislot_v2_smoke.py',
    'tools/hft244_minimal_pair_accounting_v1.py',
    'tools/hft244_research_owner_accounting_v1.py',
    'tools/hft244_receipt_adapter_v1.py',
]
EXPECTED={
    'tools/run_eth_role_separated_minimal_pair_safety_smoke.py':'75ca35073983175905c3bf97c6968ff41d567208d250fbe8db0691fbefbb9607',
    'tools/run_eth_role_separated_multislot_v3_smoke.py':'c43d250725780e676b5961ab48198e95298808763c65f919306691a91e391642',
    'tools/run_eth_target_grounded_distinct_multislot_v2_smoke.py':'8a67ffab052b379448cbe71b51d2a4b4d4afdb4b38545dfd5725dec63d5db587',
}


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(262144),b''):h.update(block)
    return h.hexdigest()


def main():
    t=time.monotonic();assert not OUT.exists(),'immutable package already exists'
    source=ROOT/'data/research/market_capsule_v1/source_bundle_50_v1'
    manifest_path=source/'manifest.json'
    assert sha(manifest_path)=='f55b618bd9eaae077fb66682fd3e776808b53b3c4a9bd1eb6a85a8e9b45a6dd5'
    tapes={int(t['marketId']):t for t in json.loads(manifest_path.read_text(encoding='utf-8'))['tapes']}
    for rel in FILES:
        p=ROOT/rel;assert p.stat().st_size<30000
        if rel in EXPECTED:assert sha(p)==EXPECTED[rel],rel
        py_compile.compile(str(p),doraise=True)
    runner=ROOT/'tools/run_minimal_student_quantity_smoke3_worker_v1.py'
    py_compile.compile(str(runner),doraise=True)
    OUT.mkdir(parents=True)
    for rel in FILES:
        p=OUT/rel;p.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(ROOT/rel,p)
    for mid in MIDS:
        row=tapes[mid];p=source/row['file']
        assert p.stat().st_size==row['bytes'] and p.stat().st_size<5*1024**2 and sha(p)==row['sha256']
        dest=OUT/f'tapes/{mid}.json.xz';dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    shutil.copy2(runner,OUT/runner.name)
    prereg=ROOT/'data/research/r4_v0/p0_provenance_v1/MINIMAL_STUDENT_QUANTITY_SEAM_SMOKE3_PREREG_V1_20260910.md'
    shutil.copy2(prereg,OUT/'PREREG.md')
    files={p.relative_to(OUT).as_posix():dict(bytes=p.stat().st_size,sha256=sha(p))
           for p in OUT.rglob('*') if p.is_file()}
    (OUT/'MANIFEST.json').write_text(json.dumps(dict(version='MINIMAL_STUDENT_QUANTITY_SMOKE3_V1',
        markets=list(MIDS),files=files,originalPolicyHashes=EXPECTED,
        sourceManifestSha256=sha(manifest_path),recentTargetRuntimeRows=0,
        preparationSeconds=time.monotonic()-t),indent=2),encoding='utf-8')
    print(json.dumps(dict(package=OUT.relative_to(ROOT).as_posix(),
        files=len(files),bytes=sum(v['bytes'] for v in files.values()),
        manifestSha256=sha(OUT/'MANIFEST.json'),seconds=time.monotonic()-t)))


if __name__=='__main__':main()
