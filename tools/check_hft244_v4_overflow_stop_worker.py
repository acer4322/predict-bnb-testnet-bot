"""One targeted negative-control clarification; never inspect invalid engine state."""
import ast
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import time

ROOT=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910')
RESULT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def main():
    if '--child' not in sys.argv:
        d=load('bounded',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
        d.bounded('overflow-clarification',[sys.executable,str(Path(__file__).resolve()),'--child'],30);return
    native=ROOT/'.tmp/hft244_accounting_build_v1/candidate_python/hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
    assert hashlib.sha256(native.read_bytes()).hexdigest()=='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
    old_result=json.loads((ROOT/'.tmp/hft244_accounting_build_v1/v4-receipt-contract.json').read_text())
    assert old_result['passed']==20 and old_result['attempted']==21 and old_result['rows'][-1]['kind']=='OVERFLOW'
    path=ROOT/'tools/check_hft244_receipts_v4.py';text=path.read_text()
    manifest=json.loads(Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_receipts_v4_20260910/MANIFEST.json').read_text(encoding='utf-8-sig'))
    expected=next(r['sha256'] for r in manifest['files'] if r['name']==path.name)
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    module=load('frozen_v4_test',path)
    old='record.update(overflowStopped=True,code=rc,retainedReceipts=reader.peek());return'
    new='record.update(overflowStopped=True,code=rc);return'
    assert text.count(old)==1
    tree=ast.parse(text.replace(old,new));fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='custom')
    scope=dict(module.__dict__);exec(compile(ast.Module(body=[fn],type_ignores=[]),str(path)+'[no-invalid-read]', 'exec'),scope)
    row={};start=time.monotonic()
    result=dict(marketBE=0,newEngines=1,nativeChanged=False,oldVerdict=old_result['verdict'],originalTestSha256=expected)
    try:
        scope['custom']('OVERFLOW',row)
        assert row==dict(overflowStopped=True,code=13)
        result.update(verdict='OVERFLOW_HARD_STOP_SUPPORTED',observation=row)
    except Exception as exc:result.update(verdict='OVERFLOW_CLARIFICATION_STOP',error=type(exc).__name__+': '+str(exc),observation=row)
    result['elapsedSeconds']=time.monotonic()-start
    (RESULT/'COMPACT.json').write_text(json.dumps(result,indent=2));print(json.dumps(result),flush=True)
    if result['verdict']!='OVERFLOW_HARD_STOP_SUPPORTED':raise SystemExit(2)


if __name__=='__main__':main()
