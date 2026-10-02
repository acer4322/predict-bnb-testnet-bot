"""Behavior-inert attribution for one known failed branch; no source repack."""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import time

MANIFEST_SHA = 'da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'
EXPECTED_SIG = '8ded9bd3c8ea9a7b28b466f69cc06ba9aefdff4200913e4b9d414afc234b85d3'


def native_order_fields(raw):
    # 2.4.4 Python Order does NOT expose maker. Do not infer liquidity from role.
    snap={k:float(getattr(raw,k)) for k in
          ['qty','leaves_qty','exec_qty','exec_price','exch_timestamp','local_timestamp']}
    snap.update(status=int(raw.status),req=int(raw.req),maker=None,
                makerMissingReason='NOT_EXPOSED_BY_PYTHON_ORDER_API',
                executionFieldsValid=int(raw.status) in (3,5))
    return snap


def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--frozen',type=Path,required=True); a=ap.parse_args()
    frozen=a.frozen.resolve(); manifest_path=frozen/'STAGE_MANIFEST.json'
    assert hashlib.sha256(manifest_path.read_bytes()).hexdigest()==MANIFEST_SHA
    m=json.loads(manifest_path.read_text()); verified={}
    for rel,sha in m['files'].items():
        if not (rel.endswith('.py') or rel in ['tapes/2023609.json.xz','public/2023609.json']): continue
        p=(frozen/rel).resolve(); assert p.is_relative_to(frozen) and p.stat().st_size<=2*1024**2
        assert hashlib.sha256(p.read_bytes()).hexdigest()==sha,rel
        verified[rel]=sha
    backend=Path('C:/BTC5M-worker/.tmp/hftbacktest_244')
    with (backend/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd').open('rb') as f:
        native_sha=hashlib.file_digest(f,'sha256').hexdigest()
    assert native_sha==m['externalNativeSha256']
    sys.path.insert(0,str(backend)); sys.path.insert(0,str(frozen))
    from tools.run_root_native_composite_handback_v1 import make_fork,lab,util
    mod=importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    Fork=make_fork(mod.BoundedCoreServiceFavorableRecycleSim,mod)
    for name,module in list(sys.modules.items()):
        if name.startswith('tools.') and getattr(module,'__file__',None):
            assert Path(module.__file__).resolve().is_relative_to(frozen),name

    class Trace(Fork):
        def process(self,t):
            value=super().process(t); d=self._lab
            before=d.setdefault('tracePrevious',{}); changed={}
            for key,o in self.orders.items():
                raw=self.bt.orders(0).get(o['n'])
                if raw is None: continue
                snap=native_order_fields(raw)
                if before.get(key)!=snap:
                    changed[key]=dict(nativeOrder=snap,ourOrder=util.clean(o))
                    before[key]=snap
            if changed:
                trace=d.setdefault('receiptTrace',[]); assert len(trace)<2000,'trace limit'
                native=util.native_state(self)
                trace.append(dict(t=t,native=native,ourInv=dict(self.inv),ourCost=self.cost,
                    cash=util.cash_check(self.inv,self.cost,native),changed=changed))
            return value

    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']); start=time.time(); be=0; sim=None
    result=dict(marketId=2023609,branch='P',modelsTrained=0,freshUsed=0,policyChanged=False,
        backendModified=False,stageManifestSha256=MANIFEST_SHA,nativeSha256=native_sha,
        runnerSha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),verifiedFiles=verified)
    try:
        source=json.loads((frozen/'public/2023609.json').read_text())
        be=1; (out/'BE_ACCOUNTING.json').write_text(json.dumps(dict(attemptedBE=be,marketId=2023609,branch='P')))
        sim=Trace(frozen/'tapes/2023609.json.xz','P',source)
        r=sim.run_r247('UP'); ob=sim._lab['observer']; sig=lab.signature(sim,r)
        assert sig==EXPECTED_SIG,'whole-policy signature mismatch'
        result.update(signature=sig,signatureParity=True,firstMismatches=ob.mismatches,cashMaxError=ob.max_error,
            terminalNative=util.native_state(sim),terminalOur=dict(inv=sim.inv,cost=sim.cost),
            terminalCashCheck=util.cash_check(sim.inv,sim.cost,util.native_state(sim)),
            originalOtherChecks=dict(service=r['r247ServiceCorrectnessPass'],unauthorizedOverflowQty=r['unauthorizedOverflowQty'],
                repairQuotaExcessMax=r['repairQuotaExcessMax'],maxSlots=sim.max_simultaneous_slots),
            receiptTrace=sim._lab.get('receiptTrace',[]),fillHistory=util.clean(sim.fillHist))
        reproduced=(bool(ob.mismatches) and abs(ob.max_error['DOWN']-1)<1e-8 and abs(ob.max_error['cost']-.74)<1e-8)
        result['verdict']='SAME_FAILURE_TRACE_CAPTURED' if reproduced else 'FAILURE_NOT_REPRODUCED'
    except Exception as e:
        result.update(verdict='DIAGNOSTIC_CORRECTNESS_STOP',error=type(e).__name__+': '+str(e))
    finally:
        if sim is not None: sim.close()
    result.update(attemptedBE=be,elapsedSeconds=time.time()-start,trainingReady=False)
    blob=json.dumps(util.clean(result),indent=2).encode(); assert len(blob)<=512*1024
    (out/'COMPACT.json').write_bytes(blob)
    print(json.dumps({k:result.get(k) for k in ['verdict','signatureParity','cashMaxError','attemptedBE','elapsedSeconds']}),flush=True)
    if result['verdict']=='DIAGNOSTIC_CORRECTNESS_STOP': raise SystemExit(2)


if __name__=='__main__': main()
