"""Two observer-only C/T executions, exact original signature required; LAN only."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent
BASE=Path('C:/BTC5M-worker')
OLD=BASE/'.tmp/hft244_pair_confirmed_handoff_20260910_v2'
ROOT=BASE/'.tmp/hft244_pair_postpaid_trace_20260910_v1'
BACKEND=BASE/'.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python'
REF=BASE/'.lan_worker_v1/results/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json'
REF_SHA='064196cb1f21100cca75a6afc523d43713f7c23d97993f45dffdbf6293cf3635'
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'

def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def sig(v):return hashlib.sha256(json.dumps(v,sort_keys=True,allow_nan=False).encode()).hexdigest()

def main():
    if '--child' not in sys.argv:
        p=BASE/'.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'
        spec=importlib.util.spec_from_file_location('bounded_existing',p)
        b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)
        b.bounded('postpaid-trace',[sys.executable,str(Path(__file__).resolve()),'--child'],180)
        return
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(verdict='PRECHECK',attemptedBE=0,priorKnownLineage=76,rows=[],newTraining=0,
                freshUsed=0,policyChanged=False,fullNetCost='UNRESOLVED')
    def save():
        result.update(elapsedSeconds=time.monotonic()-start,lineageIfNoConcurrentWork=76+result['attemptedBE'])
        blob=json.dumps(result,indent=2,allow_nan=False).encode();assert len(blob)<2*1024**2
        (out/'COMPACT.json').write_bytes(blob)
    def copy_checked(src,dst,digest):
        assert sha(src)==digest, 'source drift '+str(src)
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
    try:
        assert not ROOT.exists(),'immutable runtime root already exists'
        assert sha(REF)==REF_SHA,'reference changed'
        ref=json.loads(REF.read_text(encoding='utf-8-sig'))
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
        for name,digest in manifest['files'].items():assert sha(BUNDLE/name)==digest,name
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==NATIVE_SHA==ref['nativeSha256'],'wrong repaired native'
        for name,digest in ref['baseSourceHashes'].items():
            rel=Path(*name.split('.')).with_suffix('.py')
            if not (OLD/rel).exists():rel=Path(*name.split('.'))/'__init__.py'
            copy_checked(OLD/rel,ROOT/rel,digest)
        for name in ('hft244_pair_confirmed_handoff_v1.py','hft244_pair_route_legality_v1.py',
                     'hft244_pair_paid_probe_v1.py','hft244_pair_only_anatomy_v1.py'):
            copy_checked(OLD/'tools'/name,ROOT/'tools'/name,ref['inputHashes'][name])
        copy_checked(OLD/'tapes/2023609.json.xz',ROOT/'tapes/2023609.json.xz',ref['inputHashes']['2023609.json.xz'])
        name='hft244_pair_postpaid_trace_observer_v1.py'
        copy_checked(BUNDLE/name,ROOT/'tools'/name,manifest['files'][name])
        sys.path.insert(0,str(BACKEND))
        import hftbacktest as h
        import hftbacktest._hftbacktest as native
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        assert Path(native.__file__).resolve()==binary.resolve()
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.hft244_pair_confirmed_handoff_v1 import make_sim
        from tools.hft244_pair_postpaid_trace_observer_v1 import make_observer
        from tools.hft244_pair_only_anatomy_v1 import receipt_anatomy
        for name,digest in ref['baseSourceHashes'].items():
            mod=sys.modules.get(name)
            if mod is not None:assert Path(mod.__file__).resolve().is_relative_to(ROOT.resolve()) and sha(Path(mod.__file__))==digest,name
        install(minimal.v2.base,binary);Sim=make_observer(make_sim(minimal))
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in sys.modules)
        result.update(reference=str(REF),referenceSha256=REF_SHA,nativeSha256=sha(Path(native.__file__)),
                      nativePath=str(native.__file__),tapeSha256=ref['inputHashes']['2023609.json.xz'],observerSha256=manifest['files'][name] if name in manifest['files'] else sha(ROOT/'tools/hft244_pair_postpaid_trace_observer_v1.py'))
        for arm in ('C','T'):
            sim=None;result['attemptedBE']+=1;save()
            try:
                trace=out/f'2023609_{arm}_decisions.jsonl'
                sim=Sim(ROOT/'tapes/2023609.json.xz',arm,trace)
                output=sim.run_minimal('UP')
                sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                assert not sim._receipt_invalid and sim.max_simultaneous_slots<=4
                receipts=list(sim._receipt_ledger.seen.values())
                core=dict(output=output,orders=sim.orders,receipts=receipts,native=sim._receipt_ledger.native)
                prior=next(r for r in ref['rows'] if r['marketId']==2023609 and r['arm']==arm)
                fingerprint=sig(core)
                assert fingerprint==prior['signature'],'observer changed full output/orders/receipts/native '+arm
                assert sim._probe_mark==prior['mark'] and sim._probe_ready==prior['ready']
                assert receipts==prior['receipts'],'receipt parity'
                row=dict(marketId=2023609,arm=arm,signature=fingerprint,referenceSignature=prior['signature'],
                         exactParity=True,UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,
                         cost=sim.cost,fills=sim.fills,submits=sim.submits,mark=sim._probe_mark,ready=sim._probe_ready)
                anatomy=receipt_anatomy(receipts,row)
                sim.close_trace()
                orders=out/f'2023609_{arm}_orders.json'
                blob=json.dumps(dict(orders=sim.orders,keyRole=sim.key_role),indent=2,allow_nan=False).encode()
                assert len(blob)<1024**2,'order map cap'
                orders.write_bytes(blob)
                row.update(traceFile=trace.name,traceBytes=trace.stat().st_size,traceSha256=sha(trace),
                           observedClocks=sim._trace_clock,ordersFile=orders.name,ordersSha256=sha(orders),
                           receiptCount=len(receipts),anatomyEndpointCheck=anatomy['endpoints'])
                result['rows'].append(row);save()
            finally:
                if sim is not None:
                    if not sim._trace_stream.closed:sim.close_trace()
                    sim.close()
        result['verdict']='OBSERVER_FULL_POLICY_PARITY_PASS'
    except BaseException as exc:
        result.update(verdict='CAPTURE_CORRECTNESS_STOP',error=type(exc).__name__+': '+str(exc),
                      trace=traceback.format_exc(limit=8))
    save()
    print(json.dumps({k:v for k,v in result.items() if k not in ('rows','trace')},allow_nan=False),flush=True)
    if result['verdict']!='OBSERVER_FULL_POLICY_PARITY_PASS':raise SystemExit(2)

if __name__=='__main__':main()
