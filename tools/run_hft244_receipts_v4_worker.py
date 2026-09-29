"""Isolated V4 additive receipt build on LAN only; one bounded contract run."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys

BUNDLE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('builder',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
V31=Path('C:/BTC5M-worker/.tmp/hft244_native_v31_20260910')
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910');SOURCE=ROOT/'.tmp/hft244_accounting_source_v1';BUILD=ROOT/'.tmp/hft244_accounting_build_v1'


def main():
    assert 'BTC5M_LAN_RESULT_DIR' in os.environ and not ROOT.exists()
    assert d.digest(d.OLD)==d.OLD_SHA
    for item in json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))['files']:assert d.digest(BUNDLE/item['name'])==item['sha256']
    result=dict(marketBE=0,promotion=False)
    try:
        oldsource=V31/'.tmp/hft244_accounting_source_v1'
        frozen=json.loads(Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-native-v31-build-20260910-v1/SOURCE_MANIFEST.json').read_text())
        for name,sha in frozen['files'].items():assert d.digest(oldsource/name)==sha
        for phase,count in [('extended',10),('controls',12),('joint',14)]:
            gate=json.loads((Path('C:/BTC5M-worker/.lan_worker_v1/results')/f'hft244-v31-{phase}-20260910-v1/PHASE.json').read_text())
            assert gate['verdict']=='PHASE_PASS' and gate['passed']==count
        shutil.copytree(oldsource,SOURCE)
        for name,rel in {'local.rs':'hftbacktest/src/backtest/proc/local.rs','proc_mod.rs':'hftbacktest/src/backtest/proc/mod.rs','backtest_mod.rs':'hftbacktest/src/backtest/mod.rs','py_backtest.rs':'py-hftbacktest/src/backtest.rs'}.items():shutil.copy2(BUNDLE/name,SOURCE/rel)
        assert d.digest(SOURCE/'hftbacktest/src/backtest/proc/partialfillexchange.rs')=='0ff437ffba959bf1163ac0c37e3c5c7d9429b66d71ba59128ddcd50484018b6a'
        for folder in ['tools','data/research/r4_v0/p0_provenance_v1']:
            shutil.copytree(V31/folder,ROOT/folder)
        for name in ['hft244_receipt_adapter_v1.py','check_hft244_receipts_v4.py']:shutil.copy2(BUNDLE/name,ROOT/'tools'/name)
        BUILD.mkdir(parents=True)
        shutil.copytree(V31/'.tmp/hft244_accounting_build_v1/candidate_python',BUILD/'candidate_python')
        shutil.copy2(V31/'.tmp/hft244_accounting_build_v1/candidate-smoke.json',BUILD/'v31-smoke-reference.json')
        d.save(d.RESULT/'SOURCE_MANIFEST.json',dict(files={str(p.relative_to(SOURCE)):d.digest(p) for p in SOURCE.rglob('*') if p.is_file()}))
        d.SOURCE=SOURCE
        d.bounded('v4-build',[str(d.ROOT/'rust/bin/cargo.exe'),'build','--locked','--offline','-p','py-hftbacktest','-j','4'],600)
        shutil.copy2(d.ROOT/'target/debug/hftbacktest.dll',BUILD/'candidate.dll')
        shutil.copy2(BUILD/'candidate.dll',BUILD/'candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd')
        result['nativeSha256']=d.digest(BUILD/'candidate.dll')
        d.bounded('v4-contract',[sys.executable,str(ROOT/'tools/check_hft244_receipts_v4.py')],120)
        result['verdict']='RECEIPT_V4_PHASE_PASS'
    except Exception as exc:result.update(verdict='RECEIPT_V4_STOP',error=type(exc).__name__+': '+str(exc))
    finally:
        for name in ['v4-receipt-contract.json','v4-progress.json']:
            if (BUILD/name).exists():shutil.copy2(BUILD/name,d.RESULT/name)
        result['originalUnchanged']=d.digest(d.OLD)==d.OLD_SHA
        d.save(d.RESULT/'COMPACT.json',result);print(json.dumps(result),flush=True)
    if result['verdict']!='RECEIPT_V4_PHASE_PASS' or not result['originalUnchanged']:raise SystemExit(2)


if __name__=='__main__':main()
