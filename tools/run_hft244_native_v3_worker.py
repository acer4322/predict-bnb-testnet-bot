import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

BUNDLE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('native_build_v1',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
V1=d.ROOT; V2=Path('C:/BTC5M-worker/.tmp/hft244_receipt_v2_env1_20260910')
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_native_v3_20260910')
SOURCE=ROOT/'.tmp/hft244_accounting_source_v1'; BUILD=ROOT/'.tmp/hft244_accounting_build_v1'
d.SOURCE=SOURCE


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=['build','extended','controls','joint']); phase=parser.parse_args().phase
    assert 'BTC5M_LAN_RESULT_DIR' in os.environ and d.digest(d.OLD)==d.OLD_SHA
    manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
    for item in manifest['files']: assert d.digest(BUNDLE/item['name'])==item['sha256']
    result=dict(phase=phase,marketBE=0,promotion=False)
    try:
        if phase=='build':
            assert not ROOT.exists(), 'immutable V3 root'
            oldsource=V2/'.tmp/hft244_accounting_source_v1'
            frozen=json.loads(Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-receipt-v2-build-env1-20260910-v1/SOURCE_MANIFEST.json').read_text())
            for name,sha in frozen['files'].items(): assert d.digest(oldsource/name)==sha
            shutil.copytree(oldsource,SOURCE)
            shutil.copy2(BUNDLE/'partialfillexchange.rs',SOURCE/'hftbacktest/src/backtest/proc/partialfillexchange.rs')
            for folder in ['tools','data/research/r4_v0/p0_provenance_v1','.tmp/hftbacktest_244']:
                shutil.copytree(V2/folder,ROOT/folder)
            shutil.copy2(BUNDLE/'check_hft244_native_v3_joint.py',ROOT/'tools/check_hft244_native_v3_joint.py')
            BUILD.mkdir(parents=True)
            shutil.copy2(V2/'.tmp/hft244_accounting_build_v1/baseline-smoke.json',BUILD/'baseline-smoke.json')
            shutil.copy2(BUNDLE/'MANIFEST.json',ROOT/'INPUT_MANIFEST.json')
            assert d.digest(SOURCE/'Cargo.lock')==d.LOCK_SHA
            d.save(d.RESULT/'SOURCE_MANIFEST.json',dict(files={str(p.relative_to(SOURCE)):d.digest(p) for p in SOURCE.rglob('*') if p.is_file()},lockSha256=d.LOCK_SHA))
            d.bounded('v3-build',[str(V1/'rust/bin/cargo.exe'),'build','--locked','--offline','-p','py-hftbacktest','-j','4'],600)
            shutil.copy2(V1/'target/debug/hftbacktest.dll',BUILD/'candidate.dll')
            d.bounded('v3-smoke',[sys.executable,str(ROOT/'tools/check_hft244_rebuilt_smoke_v1.py'),'--variant','candidate'],60)
        else:
            scripts={'extended':'check_hft244_extended_receipts_v1.py','controls':'check_hft244_receipt_v2_controls_fix2.py','joint':'check_hft244_native_v3_joint.py'}
            d.bounded('v3-'+phase,[sys.executable,str(ROOT/'tools'/scripts[phase])],90)
        result['verdict']='PHASE_PASS'
    except Exception as exc: result.update(verdict='STOP_V3',error=type(exc).__name__+': '+str(exc))
    finally:
        result['originalUnchanged']=d.digest(d.OLD)==d.OLD_SHA
        for name in ['candidate-smoke.json','candidate-extended.json','candidate-extended-progress.json','v2-controls-fix2.json','v2-controls-fix2-progress.json','v3-joint.json','v3-progress.json']:
            if (BUILD/name).exists(): shutil.copy2(BUILD/name,d.RESULT/name)
        d.save(d.RESULT/'COMPACT.json',result); print(json.dumps(result),flush=True)
    if result['verdict']!='PHASE_PASS' or not result['originalUnchanged']: raise SystemExit(2)


if __name__=='__main__': main()
