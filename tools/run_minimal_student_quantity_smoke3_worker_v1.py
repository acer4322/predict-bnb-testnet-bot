"""Bounded LAN component gate -> <=3 exact-size native receipt smoke cases."""
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback
import unittest

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/minimal_student_quantity_smoke3_20260910_v1')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(262144),b''):h.update(block)
    return h.hexdigest()


def main():
    if '--child' not in sys.argv:
        helper=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',helper)
        bound=importlib.util.module_from_spec(spec);spec.loader.exec_module(bound)
        bound.bounded('minimal-student-quantity',[sys.executable,str(Path(__file__).resolve()),'--child'],180)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR')
    assert int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);started=time.monotonic()
    result=dict(version='MINIMAL_STUDENT_EXACT_QUANTITY_SMOKE3_V1',verdict='PRECHECK',
        attemptedBE=0,completedBE=0,rows=[],modelFits=0,liveChanges=0,freshUsed=0,
        promotion=False,exactTargetTeacher=False,fullCostCertified=False,
        virtualCapitalFixture=100.,grantCashPerSide=50.,grantQuantityPerSide=110.,
        tests=None,scope='CONTROL_INTERFACE_AND_NATIVE_ACCOUNTING_NOT_ECONOMIC_STUDY')
    def save():
        result['elapsedSeconds']=time.monotonic()-started
        blob=json.dumps(result,indent=2,allow_nan=False).encode()
        assert len(blob)<180000
        (out/'COMPACT.json').write_bytes(blob)
    sim=None
    try:
        assert not ROOT.exists(),'immutable run root already exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,meta in manifest['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE)
            assert p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256'],rel
        assert sha(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
        original=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text(encoding='utf-8'))
        for rel,want in original['files'].items():
            if not rel.endswith('.py'):continue
            src=(FROZEN/rel).resolve();assert src.is_relative_to(FROZEN)
            assert src.stat().st_size<2*1024**2 and sha(src)==want
            dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        for rel in manifest['files']:
            if not rel.startswith(('tools/','tests/','tapes/')):continue
            dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/rel,dst)
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        spec=importlib.util.spec_from_file_location('seam_tests',ROOT/'tests/test_minimal_student_quantity_seam_v1.py')
        unit=importlib.util.module_from_spec(spec);spec.loader.exec_module(unit)
        buffer=io.StringIO()
        tested=unittest.TextTestRunner(stream=buffer,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(unit))
        result['tests']=dict(run=tested.testsRun,failures=len(tested.failures),errors=len(tested.errors),passed=tested.wasSuccessful())
        (out/'UNIT_TESTS.txt').write_text(buffer.getvalue(),encoding='utf-8')
        print(json.dumps(dict(componentTests=result['tests'])),flush=True);save()
        if not tested.wasSuccessful():
            result['verdict']='COMPONENT_GATE_FAIL_NO_HFT';save();return
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd'
        assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class,QuantityIntent,VenueGrid
        from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger,Grant
        assert minimal.MinimalPairRoleSim.__bases__==(minimal.v3.RoleSeparatedMultiSlotSim,)
        assert sha(Path(minimal.__file__))=='75ca35073983175905c3bf97c6968ff41d567208d250fbe8db0691fbefbb9607'
        install(minimal.v2.base,binary)
        ex=minimal.v2.base.ex
        original_submit=ex.submit_native
        def checked_submit(*a,**kw):
            rc=original_submit(*a,**kw)
            if int(rc)!=0:raise RuntimeError('native submit rejected/ambiguous rc='+str(rc))
            return rc
        ex.submit_native=checked_submit
        source=Path(ex.__file__).read_text(encoding='utf-8')
        assert '.tick_size(0.01)' in source and '.lot_size(0.01)' in source
        loaded={n:sha(Path(m.__file__)) for n,m in list(sys.modules.items())
                if n.startswith(('tools.','src.')) and getattr(m,'__file__',None)}
        assert not any('r2_47' in n or 'repair_overflow_split' in n for n in loaded)
        for n,m in list(sys.modules.items()):
            if n in loaded:assert Path(m.__file__).resolve().is_relative_to(ROOT),n
        result.update(loadedSourceHashes=loaded,nativeSha256=NATIVE_SHA,stageSha256=STAGE_SHA)
        Student=make_student_class(minimal.MinimalPairRoleSim)
        grid=VenueGrid(.01,.01,.01,0.,'EXACT_FROZEN_RESEARCH_BACKEND_NOT_LIVE_CERTIFIED')
        for mid,qty in [(2022527,30.),(2022538,55.),(2022602,55.)]:
            t0=time.monotonic();result['attemptedBE']+=1;save()
            ledger=EconomicGrantLedger(100.)
            for pid,side in [(1,'UP'),(2,'DOWN')]:
                ledger.issue(Grant(pid,'fixed-smoke',side,0.,110.,50.,'PREREG_ACQUISITION_FIXTURE_NOT_TARGET_DEBT'))
            def provider(context,q=qty):
                return QuantityIntent(1 if context['side']=='UP' else 2,q,'PASSIVE',
                                      'CONTROL_TRANSPORT_CASE_NOT_TRAINED_OR_TARGET_ORIGINAL_QTY')
            trace=out/f'OWN_TRAJECTORY_{mid}.jsonl.gz';counts={};native_receipts=0
            with gzip.open(trace,'wt',encoding='utf-8',newline='\n') as log:
                def sink(ev):
                    counts[ev['event']]=counts.get(ev['event'],0)+1
                    assert sum(counts.values())<=30000,'bounded trace exceeded; never truncate silently'
                    log.write(json.dumps(ev,separators=(',',':'),allow_nan=False)+'\n')
                sim=Student(ROOT/f'tapes/{mid}.json.xz',4,False,quantity_provider=provider,
                    quantity_ledger=ledger,quantity_asset='BTC',quantity_grid=grid,audit_sink=sink)
                print(json.dumps(dict(market=mid,stage='native_initialized',requestedCase=qty)),flush=True)
                row=sim.run_minimal('UP') # both terminal branches are diagnostic, winner never read
                ledger.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
                assert not sim._receipt_invalid
                assert abs(sum(c.filled for c in ledger.carriers.values())-sum(sim.inv.values()))<1e-7
                assert abs(sum(c.payment for c in ledger.carriers.values())-sim.cost)<1e-7
                assert all(abs(sim.inv[s]-sim._receipt_ledger.inv[s])<1e-8 for s in ('UP','DOWN'))
                assert all(o['qty']==qty for o in sim.orders.values())
                assert row['maxSimultaneousSlots']<=4 and sim.serialize_same_side is False
                cutoff=int(sim.payload['market']['window_end_ms'])-180000
                assert all(o['placed']<cutoff for o in sim.orders.values())
                orders=[dict(key=k,side=o['side'],requestedQty=o['qty'],
                    filledQty=o.get('cum',0.),price=o['price'],status=sim.snap(o).get('status'),
                    ledgerState=ledger.carriers[k].state) for k,o in sim.orders.items()]
                unresolved=[k for k,c in ledger.carriers.items() if c.state!='TERMINAL']
                r=dict(marketId=mid,requestedCase=qty,submits=sim.submits,fillEvents=sim.fills,
                    nativeReceipts=len(sim._receipt_ledger.seen),UP=sim.inv['UP'],DOWN=sim.inv['DOWN'],
                    cost=sim.cost,UPBranch=sim.inv['UP']-sim.cost,DOWNBranch=sim.inv['DOWN']-sim.cost,
                    zeroFillOrders=sum(o['filledQty']==0 for o in orders),
                    partiallyFilledOrders=sum(0<o['filledQty']<o['requestedQty']-1e-8 for o in orders),
                    unresolvedCount=len(unresolved),unresolved=unresolved,orders=orders,
                    minSubmittedPrice=min((o['price'] for o in orders),default=None),
                    maxSlots=row['maxSimultaneousSlots'],roleSubmits=row['roleSubmits'],
                    rejects=sim.quantity_rejections,traceEvents=counts,sourceMeta=sim.meta,
                    correctness=True,elapsedSeconds=time.monotonic()-t0,
                    trace=trace.name,traceSha256=None)
            r['traceSha256']=sha(trace);r['traceBytes']=trace.stat().st_size
            sim.close();sim=None
            result['rows'].append(r);result['completedBE']+=1
            print(json.dumps(dict(market=mid,stage='collected',submits=r['submits'],
                nativeReceipts=r['nativeReceipts'],unresolved=len(unresolved),seconds=r['elapsedSeconds'])),flush=True)
            if unresolved:
                result['verdict']='SIZE_CAPTURE_SUPPORTED_TERMINAL_CLOSURE_BLOCKED';save();return
            if not r['submits'] or not r['nativeReceipts']:
                result['verdict']='NATIVE_MECHANISM_NOT_EXERCISED';save();return
            save()
        result['verdict']='EXACT_QUANTITY_NATIVE_CAPTURE_SMOKE3_PASS_NOT_TRAINING_READY'
        result['next']='Define reliable observable-action supervision and same-era scale support; original qty remains missing. No automatic large test or training.'
    except Exception as exc:
        result.update(verdict='EXECUTION_ERROR_STOPPED',error=type(exc).__name__+': '+str(exc),
                      traceback=traceback.format_exc(limit=10))
    finally:
        if sim is not None:
            try:sim.close()
            except Exception:pass
        save()
    print(json.dumps(dict(verdict=result['verdict'],attemptedBE=result['attemptedBE'],completedBE=result['completedBE'])),flush=True)
    if result['verdict']=='EXECUTION_ERROR_STOPPED':raise SystemExit(2)


if __name__=='__main__':main()
