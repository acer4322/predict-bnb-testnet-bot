"""New acceptance contract; frozen fixtures and native binary, no rebuild."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_native_v31_20260910')
BUILD=ROOT/'.tmp/hft244_accounting_build_v1'
RESULT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
SPECS={
 'extended':('check_hft244_extended_receipts_v1.py','ec78121e4c3d82a7f2726af475368f4a5e095bf3da884d5745a8d6c6e8b9aaaf',10),
 'controls':('check_hft244_receipt_v2_controls_fix2.py','62e9eac941e3cb7af85d850c0974f08d7abd1fa9e4a8718163eeb0c00a0afb85',12),
 'joint':('check_hft244_native_v3_joint.py','6af58fe5aa5c406798bdf4b960810262ae7fd1a9c38c0b5fb223ee398eebe904',14)}


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path); module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def digest(path): return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    p=argparse.ArgumentParser(); p.add_argument('phase',choices=list(SPECS));p.add_argument('--child',action='store_true');a=p.parse_args()
    assert sys.executable.lower()=='c:\\btc5m-worker\\.venv\\scripts\\python.exe'
    for item in json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))['files']: assert digest(BUNDLE/item['name'])==item['sha256']
    assert digest(ROOT/'.tmp/hft244_accounting_source_v1/hftbacktest/src/backtest/proc/partialfillexchange.rs')=='0ff437ffba959bf1163ac0c37e3c5c7d9429b66d71ba59128ddcd50484018b6a'
    old_native=Path('C:/BTC5M-worker/.tmp/hftbacktest_244/hftbacktest/_hftbacktest.cp313-win_amd64.pyd')
    original='74af885fe5bab4873e4672422befb0c3c5acf513ec8da0e3f80562a0c56505c6'
    assert digest(old_native)==original
    audit=load('numeric_audit',BUNDLE/'audit_hft244_v31_numerical_equivalence.py')
    evidence=audit.run(ROOT/'data/research/r4_v0/p0_provenance_v1/HFT244_PARTIAL_RECEIPT_ACCOUNTING_COMPACT_V1_20260910.json',BUILD/'candidate-smoke.json')
    package=BUILD/'candidate_python/hftbacktest'; assert digest(package/'_hftbacktest.cp313-win_amd64.pyd')==audit.NATIVE
    if a.phase!='extended':
        prev='extended' if a.phase=='controls' else 'controls'
        gate=json.loads((Path('C:/BTC5M-worker/.lan_worker_v1/results')/('hft244-v31-'+prev+'-20260910-v1')/'PHASE.json').read_text())
        assert gate['verdict']=='PHASE_PASS' and gate['passed']==SPECS[prev][2] and gate['nativeSha256']==audit.NATIVE
    if not a.child:
        (RESULT/'NUMERICAL_AUDIT.json').write_text(json.dumps(evidence,indent=2))
        helper=load('bounded_helper',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
        try: helper.bounded('v31-'+a.phase,[sys.executable,str(Path(__file__).resolve()),a.phase,'--child'],90)
        finally: assert digest(old_native)==original
        return
    output=RESULT/'PHASE.json'; assert not output.exists()
    filename,sha,cap=SPECS[a.phase]; script=BUNDLE/filename; assert digest(script)==sha
    module=load('frozen_fixture',script)
    sys.path.insert(0,str(package.parent));import hftbacktest as h;import numpy as np
    assert Path(h.__file__).resolve().parent==package.resolve()
    result=dict(phase=a.phase,nativeSha256=audit.NATIVE,marketBE=0,promotion=False,rows=[]);start=time.monotonic()
    try:
        for scenario in module.SCENARIOS:
            for side in ['BUY','SELL']:
                row=dict(scenario=scenario,side=side,**{'pass':False});result['rows'].append(row)
                (RESULT/'PROGRESS.json').write_text(json.dumps(dict(attempted=len(result['rows']),scenario=scenario,side=side)))
                module.fixture(h,np,scenario,side,row)
        assert len(result['rows'])==cap and all(r['pass'] for r in result['rows'])
        result['verdict']='PHASE_PASS'
    except Exception as exc: result.update(verdict='CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc))
    result.update(attempted=len(result['rows']),passed=sum(r['pass'] for r in result['rows']),elapsedSeconds=time.monotonic()-start,originalUnchanged=digest(old_native)==original)
    output.write_text(json.dumps(result,indent=2));print(json.dumps({k:v for k,v in result.items() if k!='rows'}),flush=True)
    if result['verdict']!='PHASE_PASS': raise SystemExit(2)


if __name__=='__main__': main()
