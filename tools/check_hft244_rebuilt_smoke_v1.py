"""Compare isolated native builds with the frozen original six-case evidence."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from tools.probe_hft244_partial_receipt_accounting_v1 import run_fixture


def compare(rows,reference,variant):
    assert len(rows)==len(reference)==6
    for row,ref in zip(rows,reference):
        assert (row['side'],row['route'])==(ref['side'],ref['route'])
        assert row['exercised'] and len(row['snapshots'])==len(ref['snapshots'])
        for got,old in zip(row['snapshots'],ref['snapshots']):
            for key in ['label','nowNs','order','status','req','cum']:
                assert got[key]==old[key],('execution trace changed',row['side'],row['route'],key)
            if variant=='baseline':
                assert got['native']==old['native'],('baseline accounting changed',row['side'],row['route'])
            else:
                assert got['accountPass'],('candidate accounting failed',row['side'],row['route'],got['label'])


def main():
    p=argparse.ArgumentParser(); p.add_argument('--variant',choices=['baseline','candidate'],required=True)
    a=p.parse_args(); build=ROOT/'.tmp/hft244_accounting_build_v1'
    original=ROOT/'.tmp/hftbacktest_244/hftbacktest'; native=build/(a.variant+'.dll')
    package=build/(a.variant+'_python')/'hftbacktest'; output=build/(a.variant+'-smoke.json')
    assert not package.exists() and not output.exists(),'immutable test already exists'
    assert native.exists()
    probe=ROOT/'tools/probe_hft244_partial_receipt_accounting_v1.py'
    assert hashlib.sha256(probe.read_bytes()).hexdigest()=='02df9ac39ef323cbdcda3ebb66931505c9b2244b7db7331ce797e27a6cfe04d5'
    refpath=ROOT/'data/research/r4_v0/p0_provenance_v1/HFT244_PARTIAL_RECEIPT_ACCOUNTING_COMPACT_V1_20260910.json'
    assert hashlib.sha256(refpath.read_bytes()).hexdigest()=='377301ea4bc11101522b3ed7b9fab9985e057d1f9b11bdd878fe1a021a74dd8d'
    reference=json.loads(refpath.read_text())
    if a.variant=='candidate':
        assert json.loads((build/'baseline-smoke.json').read_text())['verdict']=='REBUILT_BASELINE_PARITY_PASS'
    package.mkdir(parents=True); wrappers={}
    for src in original.glob('*.py'):
        assert src.stat().st_size<2*1024**2
        shutil.copy2(src,package/src.name)
        wrappers[src.name]=hashlib.sha256(src.read_bytes()).hexdigest()
    shutil.copy2(native,package/'_hftbacktest.cp313-win_amd64.pyd')
    sys.path.insert(0,str(package.parent))
    rows=[]; start=time.time()
    result=dict(variant=a.variant,marketBE=0,modelsTrained=0,freshUsed=0,
        nativeSha256=hashlib.sha256(native.read_bytes()).hexdigest(),pythonWrapperSha256=wrappers,
        cargoLockSha256=hashlib.sha256((ROOT/'.tmp/hft244_accounting_source_v1/Cargo.lock').read_bytes()).hexdigest())
    try:
        import numpy as np
        import hftbacktest as h
        assert Path(h.__file__).resolve().parent==package.resolve()
        for side in ['BUY','SELL']:
            for route in ['FULL','PARTIAL_CANCEL','PARTIAL_FULL']:
                rows.append(run_fixture(h,np,side,route))
        compare(rows,reference['rows'],a.variant)
        result['verdict']='REBUILT_BASELINE_PARITY_PASS' if a.variant=='baseline' else 'PARTIAL_ACCOUNTING_REPAIR_SMOKE_SUPPORTED'
    except Exception as e:
        result.update(verdict='STOP_BUILD_OR_SMOKE_NOT_COMPARABLE',error=type(e).__name__+': '+str(e))
    result.update(rows=rows,elapsedSeconds=time.time()-start,completedSyntheticEngines=len(rows),promotion=False)
    blob=json.dumps(result,indent=2).encode(); assert len(blob)<=64*1024
    output.write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k not in ['rows','pythonWrapperSha256']}),flush=True)
    if result['verdict']=='STOP_BUILD_OR_SMOKE_NOT_COMPARABLE': raise SystemExit(2)


if __name__=='__main__': main()
