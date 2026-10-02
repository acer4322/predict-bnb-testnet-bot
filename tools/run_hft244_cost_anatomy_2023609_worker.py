"""One consumed-market changed-accounting integration diagnosis, never training."""
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sys
import time
import traceback

BUNDLE=Path(__file__).resolve().parent
ROOT=Path('C:/BTC5M-worker/.tmp/hft244_owner_anatomy_2023609_20260910')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
RESULT=Path(os.environ['BTC5M_LAN_RESULT_DIR'])
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'


def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    if '--child' not in sys.argv:
        spec=importlib.util.spec_from_file_location('bound',Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py'))
        d=importlib.util.module_from_spec(spec);spec.loader.exec_module(d)
        d.bounded('owner-integration',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert not ROOT.exists(),'immutable integration root'
    package=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8-sig'))
    for f in package['files']:assert digest(BUNDLE/f['name'])==f['sha256']
    assert digest(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
    assert digest(BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd')==NATIVE_SHA
    m=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text());verified={}
    for rel,sha in m['files'].items():
        if not (rel.endswith('.py') or rel in ['tapes/2023609.json.xz','public/2023609.json']):continue
        p=(FROZEN/rel).resolve();assert p.is_relative_to(FROZEN) and p.stat().st_size<=2*1024**2
        assert digest(p)==sha,rel
        dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest);verified[rel]=sha
    for name in ['hft244_receipt_adapter_v1.py','hft244_research_owner_accounting_v1.py']:shutil.copy2(BUNDLE/name,ROOT/'tools'/name)
    (RESULT/'INPUT_PROVENANCE.json').write_text(json.dumps(dict(stageSha256=STAGE_SHA,nativeSha256=NATIVE_SHA,files=verified,package=package),indent=2))
    sys.path.insert(0,str(BACKEND));import hftbacktest as h
    assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
    sys.path.insert(0,str(ROOT));os.chdir(ROOT)
    from tools.run_root_native_composite_handback_v1 import make_fork,lab,util
    mod=importlib.import_module('tools.run_eth_ms4_r2_47_bounded_core_service_favorable_recycle')
    from tools.hft244_research_owner_accounting_v1 import install
    install(mod.v2.base,mod.v2,mod.r1.v82.v8,mod.v2.base.ex,BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd',preview_lab=lab)
    Fork=make_fork(mod.BoundedCoreServiceFavorableRecycleSim,mod)
    class Checked(Fork):
        def process(self,t):
            before=len(self.splitEvents)
            prior=dict(inv=dict(self.inv),cost=self.cost,scopeSide=self.scopeSide,generation=self.scopeGeneration,
                       riskCreditTotal=self.scopeRiskCreditTotal,riskCreditConsumed=self.scopeRiskCreditConsumed,
                       repairQuotas=dict(self.keyRepairQuotaRemaining),overflowQuotas=dict(self.keyOverflowQtyRemaining))
            value=super().process(t)
            assert util.cash_check(self.inv,self.cost,util.native_state(self))['pass'],'physical/native ledger mismatch'
            fresh=[e for e in self.splitEvents[before:] if e.get('event')=='ROLE_FILL_SPLIT']
            assert len(fresh)==len(self._receipt_delta_rows),'physical/role receipt cardinality'
            for e,r in zip(fresh,self._receipt_delta_rows):
                assert e['key']==r['key'] and e['receiptSequence']==r['sequence']
                assert abs(e['repairAllocated']+e['overflowRealized']-e['fillInc'])<=1e-8 or e['role'] not in {'ECONOMIC_CORE','SATELLITE_REPAIR','SATELLITE_EXPAND'}
                assert abs(e['price']-r['contractPrice'])<1e-12
            assert self.unauthorizedOverflowQty<=1e-9 and self.repairQuotaExcessMax<=1e-9
            if self._receipt_delta_rows:
                history=self._lab.setdefault('costAnatomy',[])
                assert len(history)<100
                history.append(dict(t=t,before=prior,receipts=list(self._receipt_delta_rows),splits=fresh,
                    after=dict(inv=dict(self.inv),cost=self.cost,scopeSide=self.scopeSide,generation=self.scopeGeneration,
                               riskCreditTotal=self.scopeRiskCreditTotal,riskCreditConsumed=self.scopeRiskCreditConsumed)))
            if len(self._receipt_ledger.seen) and len(self._receipt_ledger.seen)%50==0:
                (RESULT/'PROGRESS.json').write_text(json.dumps(dict(t=t,receipts=len(self._receipt_ledger.seen),fills=self.fills,cost=self.cost)))
            return value
    for name,module in list(sys.modules.items()):
        if name.startswith('tools.') and getattr(module,'__file__',None):assert Path(module.__file__).resolve().is_relative_to(ROOT),name
    sim=None;be=0;start=time.monotonic();result=dict(marketId=2023609,branch='P',nativeSha256=NATIVE_SHA,training=False,freshUsed=0,promotion=False,oldWholePolicyParityRequired=False)
    try:
        source=json.loads((ROOT/'public/2023609.json').read_text())
        be=1;(RESULT/'BE_ACCOUNTING.json').write_text(json.dumps(dict(attemptedBE=1,marketId=2023609,branch='P',historicalLineageBefore=45)))
        sim=Checked(ROOT/'tapes/2023609.json.xz','P',source)
        output=sim.run_r247('UP') # reporting endpoint only, not a known winner selection
        ob=sim._lab['observer'];journal=list(sim._receipt_ledger.seen.values());roles=Counter(e['role'] for e in sim.splitEvents if e.get('event')=='ROLE_FILL_SPLIT')
        correct=(output['r247ServiceCorrectnessPass'] and sim.unauthorizedOverflowQty<=1e-9 and sim.repairQuotaExcessMax<=1e-9 and sim.max_simultaneous_slots<=4 and all(v<=1e-8 for v in ob.max_error.values()))
        result.update(verdict='ACCOUNTING_INTEGRATION_SUPPORTED_ONE_CONSUMED_MARKET' if correct and journal else 'INTEGRATION_CORRECTNESS_OR_NOT_EXERCISED_STOP',
            correctness=correct,receipts=len(journal),makerReceipts=sum(r['maker']==1 for r in journal),takerReceipts=sum(r['maker']==0 for r in journal),
            fills=sim.fills,submits=sim.submits,qty=sum(sim.inv.values()),cost=sim.cost,UP=sim.inv['UP']-sim.cost,DOWN=sim.inv['DOWN']-sim.cost,
            floor=min(sim.inv.values())-sim.cost,best=max(sim.inv.values())-sim.cost,native=util.native_state(sim),nativeFees=sim._receipt_ledger.native['fee'],cashMaxError=ob.max_error,
            roleReceiptCounts=dict(roles),repairAllocated=sim.totalRepairAllocated,overflowRealized=sim.totalOverflowRealized,
            scopeCompletions=sim.scopeCompletions,scopeFlips=sim.scopeFlips,peakAbsNet=ob.peak_abs_net,netIntegralShareMs=ob.abs_net_integral_ms,
            serviceCorrectness=output['r247ServiceCorrectnessPass'],serviceChecks=dict(sim.serviceChecks),maxSlots=sim.max_simultaneous_slots,
            selectedPassive=sim._lab['selectedPassive'],fullNetCost='UNRESOLVED',economicComparison='NOT_IDENTIFIED_SINGLE_CHANGED_ENGINE_BRANCH')
        reference=json.loads(Path('C:/BTC5M-worker/.lan_worker_v1/results/hft244-owner-integration-2023609-20260910-v2/COMPACT.json').read_text())
        keys=[k for k in reference if k not in ['elapsedSeconds']]
        assert all(util.clean(result.get(k))==reference[k] for k in keys if k!='attemptedBE'), 'anatomy changed integration outcome'
        assert result['verdict']=='ACCOUNTING_INTEGRATION_SUPPORTED_ONE_CONSUMED_MARKET'
        result.update(verdict='COST_ANATOMY_PARITY_SUPPORTED',referenceParity=True,executionReceipts=journal,
                      costAnatomy=sim._lab['costAnatomy'],scopeCreditEvents=sim.scope_credit_events,serviceLedger=sim.serviceLedger)
    except Exception as exc:
        result.update(verdict='INTEGRATION_STOP',error=type(exc).__name__+': '+str(exc),errorTrace=traceback.format_exc(limit=6))
        if sim is not None:result['partial']=dict(fills=sim.fills,cost=sim.cost,inv=dict(sim.inv),receiptSequence=sim._receipt_ledger.sequence,receiptInvalid=sim._receipt_invalid)
    finally:
        if sim is not None:sim.close()
    result.update(attemptedBE=be,elapsedSeconds=time.monotonic()-start)
    blob=json.dumps(util.clean(result),indent=2).encode();assert len(blob)<256*1024;(RESULT/'COMPACT.json').write_bytes(blob)
    print(json.dumps({k:v for k,v in result.items() if k not in ['selectedPassive','serviceChecks','native','executionReceipts','costAnatomy','scopeCreditEvents','serviceLedger']}),flush=True)
    if result['verdict']!='COST_ANATOMY_PARITY_SUPPORTED':raise SystemExit(2)


from collections import Counter
if __name__=='__main__':main()
