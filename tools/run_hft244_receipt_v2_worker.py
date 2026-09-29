"""Reuse frozen worker toolchain; keep V1 sources/results and backend intact."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

BUNDLE=Path(__file__).resolve().parent
V1_BUNDLE=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1')
spec=importlib.util.spec_from_file_location('native_build_v1',V1_BUNDLE/'run_hft244_worker_repair_v1.py')
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
V1=d.ROOT
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_receipt_v2_env1_20260910')
SOURCE=ROOT/'.tmp/hft244_accounting_source_v1'; BUILD=ROOT/'.tmp/hft244_accounting_build_v1'
d.SOURCE=SOURCE; d.BUILD=BUILD  # Toolchain and cache ROOT stay on worker V1.


def main():
    parser=argparse.ArgumentParser(); parser.add_argument('phase',choices=['build','extended','controls']); phase=parser.parse_args().phase
    assert 'BTC5M_LAN_RESULT_DIR' in os.environ
    assert Path(sys.executable).resolve()==Path('C:/BTC5M-worker/.venv/Scripts/python.exe').resolve()
    assert d.digest(d.OLD)==d.OLD_SHA
    manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
    for item in manifest['files']:
        assert d.digest(BUNDLE/item['name'])==item['sha256'], item['name']
    result=dict(phase=phase,marketBE=0,promotion=False)
    try:
        if phase=='build':
            assert not ROOT.exists(), 'immutable V2 root exists'
            oldsource=V1/'.tmp/hft244_accounting_source_v1'
            assert d.digest(oldsource/'Cargo.lock')==d.LOCK_SHA
            oldmanifest=json.loads(Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-worker-candidate-20260910-v1/SOURCE_MANIFEST.json').read_text())
            for name,sha in oldmanifest['files'].items(): assert d.digest(oldsource/name)==sha, name
            readme=oldsource/'py-hftbacktest/README.rst'
            assert readme.is_symlink() and not readme.exists(), 'expected packaging-only dangling README'
            shutil.copytree(oldsource,SOURCE,ignore=lambda folder,names: ['README.rst'] if Path(folder)==oldsource/'py-hftbacktest' else [])
            d.save(d.RESULT/'COPY_EXCEPTION.json',dict(skipped='py-hftbacktest/README.rst',target=os.readlink(readme),reason='verified dangling documentation link, not compiled input'))
            for name in ['local.rs','partialfillexchange.rs']:
                shutil.copy2(BUNDLE/name,SOURCE/'hftbacktest/src/backtest/proc'/name)
            for folder in ['tools','data/research/r4_v0/p0_provenance_v1','.tmp/hftbacktest_244']:
                shutil.copytree(V1/folder,ROOT/folder)
            shutil.copy2(BUNDLE/'check_hft244_receipt_v2_controls.py',ROOT/'tools/check_hft244_receipt_v2_controls.py')
            BUILD.mkdir(parents=True)
            shutil.copy2(V1/'.tmp/hft244_accounting_build_v1/baseline-smoke.json',BUILD/'baseline-smoke.json')
            shutil.copy2(BUNDLE/'MANIFEST.json',ROOT/'INPUT_MANIFEST.json')
            d.save(d.RESULT/'SOURCE_MANIFEST.json',dict(files={str(p.relative_to(SOURCE)):d.digest(p) for p in SOURCE.rglob('*') if p.is_file()},lockSha256=d.LOCK_SHA))
            d.bounded('v2-build',[str(V1/'rust/bin/cargo.exe'),'build','--locked','--offline','-p','py-hftbacktest','-j','4'],600)
            shutil.copy2(V1/'target/debug/hftbacktest.dll',BUILD/'candidate.dll')
            d.bounded('v2-smoke',[sys.executable,str(ROOT/'tools/check_hft244_rebuilt_smoke_v1.py'),'--variant','candidate'],60)
        elif phase=='extended':
            d.bounded('v2-extended',[sys.executable,str(ROOT/'tools/check_hft244_extended_receipts_v1.py')],90)
        else:
            d.bounded('v2-controls',[sys.executable,str(ROOT/'tools/check_hft244_receipt_v2_controls.py')],90)
        result['verdict']='PHASE_PASS'
    except Exception as exc: result.update(verdict='STOP_V2',error=type(exc).__name__+': '+str(exc))
    finally:
        result['originalUnchanged']=d.digest(d.OLD)==d.OLD_SHA
        if not result['originalUnchanged']: result['verdict']='STOP_ORIGINAL_CHANGED'
        for name in ['candidate-smoke.json','candidate-extended.json','candidate-extended-progress.json','v2-controls.json','v2-controls-progress.json']:
            if (BUILD/name).exists(): shutil.copy2(BUILD/name,d.RESULT/name)
        d.save(d.RESULT/'COMPACT.json',result); print(json.dumps(result),flush=True)
    if result['verdict']!='PHASE_PASS': raise SystemExit(2)


if __name__=='__main__': main()
