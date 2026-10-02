"""Second-worker native integration: baseline vs whole-plan sham, then one control probe."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/minimal_student_native_system_plan_20260910_v1')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'


def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


class Trace:
    def __init__(self,out,name):
        self.path=out/(name+'.jsonl.gz');self.file=gzip.open(self.path,'wt',encoding='utf-8',newline='\n')
        self.hashes={k:hashlib.sha256() for k in ['actions','receipts','states']}
        self.counts={k:0 for k in self.hashes};self.now=0;self.bytes=0;self.plan_count=0
    def emit(self,stream,x):
        text=json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False)+'\n'
        if stream in self.hashes:
            self.hashes[stream].update(text.encode());self.counts[stream]+=1
        self.bytes+=len(text.encode());assert self.bytes<32*1024**2,'bounded trace exceeded'
        self.file.write(json.dumps(dict(stream=stream,payload=x),separators=(',',':'),allow_nan=False)+'\n')
    def action(self,x):self.emit('actions',x)
    def process(self,sim,t):
        for rr in sim._receipt_delta_rows:self.emit('receipts',rr)
        self.emit('states',dict(t=int(t),inv=sim.inv,cost=sim.cost,
            owners={k:dict(cum=o.get('cum'),status=sim.snap(o).get('status')) for k,o in sim.orders.items()}))
    def plan(self,x):
        self.emit('whole_plan',x);self.plan_count+=1
        if self.plan_count%300==0:print(json.dumps(dict(stage='native_plan_heartbeat',file=self.path.name,frames=self.plan_count)),flush=True)
    def close(self):
        self.file.close()
        return dict(hashes={k:v.hexdigest() for k,v in self.hashes.items()},counts=self.counts,
                    trace=self.path.name,trace_sha256=sha(self.path),trace_bytes=self.path.stat().st_size,
                    decoded_payload_bytes=self.bytes,plan_count=self.plan_count)


class AuditBT:
    def __init__(self,native,trace):self.native=native;self.trace=trace
    def __getattr__(self,name):return getattr(self.native,name)
    def cancel(self,asset,n,wait):
        rc=self.native.cancel(asset,n,wait)
        self.trace.action(dict(kind='CANCEL',t=self.trace.now,n=int(n),rc=int(rc)))
        return rc


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        spec=importlib.util.spec_from_file_location('bound',p);bound=importlib.util.module_from_spec(spec);spec.loader.exec_module(bound)
        bound.bounded('minimal-native-system-plan',[sys.executable,str(Path(__file__).resolve()),'--child'],240);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(version='MINIMAL_STUDENT_NATIVE_SYSTEM_PLAN_V1',verdict='RUNNING',attemptedBE=0,completedBE=0,
        comparisons=[],runs=[],tests=None,modelFits=0,liveChanges=0,newMarkets=0,
        host=os.environ.get('COMPUTERNAME'),maxThreads=4,capabilities=['PASSIVE'],
        targetTeacher=False,profitabilityClaim=False)
    sim=None;trace=None
    def save():
        result['elapsedSeconds']=time.monotonic()-start
        data=json.dumps(result,indent=2,allow_nan=False).encode();assert len(data)<160000
        (out/'COMPACT.json').write_bytes(data)
    try:
        assert not ROOT.exists(),'immutable run root exists'
        package=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,m in package['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE) and p.stat().st_size==m['bytes'] and sha(p)==m['sha256'],rel
        assert sha(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
        stage=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text(encoding='utf-8'))
        for rel,want in stage['files'].items():
            if not rel.endswith('.py'):continue
            p=(FROZEN/rel).resolve();assert p.is_relative_to(FROZEN) and p.stat().st_size<2*1024**2 and sha(p)==want
            dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
        for rel in package['files']:
            if rel.startswith(('tools/','tests/','tapes/')):
                dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/rel,dst)
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        p=ROOT/'tests/test_minimal_student_native_system_plan_v1.py'
        spec=importlib.util.spec_from_file_location('contract_tests',p);unit=importlib.util.module_from_spec(spec);spec.loader.exec_module(unit)
        buf=io.StringIO();tested=unittest.TextTestRunner(stream=buf,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(unit))
        (out/'UNIT_TESTS.txt').write_text(buf.getvalue(),encoding='utf-8')
        result['tests']=dict(run=tested.testsRun,failures=len(tested.failures),errors=len(tested.errors),passed=tested.wasSuccessful())
        save();print(json.dumps(dict(stage='unit_gate',tests=result['tests'])),flush=True)
        if not tested.wasSuccessful():result['verdict']='UNIT_GATE_FAILED_NO_NATIVE';save();return
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class,QuantityIntent,VenueGrid
        from tools.minimal_student_native_system_plan_v1 import make_native_class,LegacyWholePlanProducer,CausalWholePlanProbe
        from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger,Grant
        install(minimal.v2.base,binary);base=minimal.v2.base
        old_submit=base.ex.submit_native
        def checked_submit(*args,**kwargs):
            rc=old_submit(*args,**kwargs)
            if int(rc)!=0:raise RuntimeError('native send rc='+str(rc))
            return rc
        base.ex.submit_native=checked_submit
        Exact=make_student_class(minimal.MinimalPairRoleSim)
        Native=make_native_class(minimal.MinimalPairRoleSim,Exact)
        class Reference(Exact):
            def __init__(self,*a,trace,**kw):self.trace=trace;super().__init__(*a,**kw)
            def process(self,t):
                self.trace.now=int(t);super().process(t);self.trace.process(self,t)
            def submit(self,t,side,p,q):
                n=self.n;super().submit(t,side,p,q)
                self.trace.action(dict(kind='NEW',t=t,n=n,side=side,price=float(p),qty=float(q)))
        hashes={n:sha(Path(m.__file__)) for n,m in list(sys.modules.items())
                if n.startswith(('tools.','src.')) and getattr(m,'__file__',None)}
        for n,m in list(sys.modules.items()):
            if n in hashes:assert Path(m.__file__).resolve().is_relative_to(ROOT),n
        assert not any('r2_47' in n for n in hashes)
        result.update(nativeSha256=NATIVE_SHA,sourceStageSha256=STAGE_SHA,loadedSourceHashes=hashes)
        grid=VenueGrid(.01,.01,.01,0.,'PINNED_NATIVE_RESEARCH_GRID')
        def run_case(mid,q,mode):
            nonlocal sim,trace
            t0=time.monotonic();result['attemptedBE']+=1;assert result['attemptedBE']<=5;save()
            ledger=EconomicGrantLedger(100.)
            for pid,side in [(1,'UP'),(2,'DOWN')]:ledger.issue(Grant(pid,'fixed-smoke',side,0.,110.,50.,'PREREG_ACQUISITION_FIXTURE_NOT_TARGET_DEBT'))
            trace=Trace(out,f'{mode}_{mid}')
            if mode=='REFERENCE':
                def provider(ctx):return QuantityIntent(1 if ctx['side']=='UP' else 2,q,'PASSIVE','PINNED_SHAM_CASE')
                sim=Reference(ROOT/f'tapes/{mid}.json.xz',4,False,trace=trace,quantity_provider=provider,
                    quantity_ledger=ledger,quantity_asset='BTC',quantity_grid=grid,audit_sink=None)
            else:
                producer=LegacyWholePlanProducer(Exact,q) if mode=='SHAM' else CausalWholePlanProbe(q)
                sim=Native(ROOT/f'tapes/{mid}.json.xz',4,False,producer=producer,ledger=ledger,trace=trace)
            sim.bt=AuditBT(sim.bt,trace)
            print(json.dumps(dict(stage='native_start',market=mid,mode=mode)),flush=True)
            if mode=='REFERENCE':sim.run_minimal('UP');active_ledger=sim.quantity_ledger
            else:sim.run_whole(base);active_ledger=sim.gateway.ledger
            active_ledger.invariants();sim._receipt_ledger.reconcile(sim.bt.state_values(0))
            assert not sim._receipt_invalid
            assert all(abs(sum(c.filled for c in active_ledger.carriers.values() if active_ledger.grants[c.parent_id].side==s)-sim.inv[s])<1e-7 for s in ('UP','DOWN'))
            assert abs(sum(c.payment+c.fees for c in active_ledger.carriers.values())-sim.cost)<1e-7
            owners=[dict(key=k,side=o['side'],qty=o['qty'],price=o['price'],filled=o.get('cum',0.),
                         status=sim.snap(o).get('status'),state=active_ledger.carriers[k].state) for k,o in sim.orders.items()]
            unresolved=[k for k,c in active_ledger.carriers.items() if c.state!='TERMINAL']
            assert not unresolved,'unreconciled terminal reservations'
            cutoff=int(sim.payload['market']['window_end_ms'])-180000
            assert all(o['placed']<cutoff and o['qty']==q for o in sim.orders.values())
            r=dict(marketId=mid,mode=mode,requestedCase=q,submits=sim.submits,
                nativeReceipts=len(sim._receipt_ledger.seen),inventory=dict(sim.inv),cost=sim.cost,
                UPBranch=sim.inv['UP']-sim.cost,DOWNBranch=sim.inv['DOWN']-sim.cost,
                zeroFillOrders=sum(o['filled']==0 for o in owners),
                partialTerminalOrders=sum(0<o['filled']<o['qty']-1e-8 for o in owners),
                unresolved=unresolved,maxSlots=sim.max_simultaneous_slots,owners=owners,
                elapsedSeconds=time.monotonic()-t0,trace=trace.close())
            trace=None
            if mode!='REFERENCE':
                r.update(planFrames=sim.frame_count,producerCalls=sim.producer.calls,
                    multiNewPlans=sim.multi_new_plans,postReceiptPlans=sim.post_receipt_plans,
                    planKinds=dict(sim.plan_kinds),policyIds=sorted(sim._executed_policy_ids),
                    hiddenNativeDecisionFallbacks=0)
            sim.close();sim=None;result['runs'].append(r);result['completedBE']+=1;save()
            print(json.dumps(dict(stage='native_complete',market=mid,mode=mode,submits=r['submits'],receipts=r['nativeReceipts'])),flush=True)
            return r
        for mid,q in [(2022527,30.),(2022538,55.)]:
            reference=run_case(mid,q,'REFERENCE');sham=run_case(mid,q,'SHAM')
            streams={k:reference['trace']['hashes'][k]==sham['trace']['hashes'][k] and reference['trace']['counts'][k]==sham['trace']['counts'][k]
                for k in ['actions','receipts','states']}
            same_owners=reference['owners']==sham['owners']
            row=dict(marketId=mid,exactStreams=streams,exactOwners=same_owners,
                     passed=all(streams.values()) and same_owners)
            result['comparisons'].append(row);save()
            if not row['passed']:
                result['verdict']='NATIVE_SHAM_MISMATCH_STOPPED';save();return
        probe=run_case(2022527,30.,'CONTROL_PROBE')
        ref=result['runs'][0]
        assert probe['multiNewPlans']>0,'joint-action control not exercised'
        assert probe['postReceiptPlans']>0 and probe['nativeReceipts']>0,'no actual receipt-conditioned continuation'
        assert probe['trace']['hashes']['actions']!=ref['trace']['hashes']['actions'],'alternate producer did not alter native actions'
        result['verdict']='NATIVE_WHOLE_PLAN_SHAM_AND_CONTROL_AUTHORITY_PASS'
        result['scopeLimits']=['Two consumed markets; same-policy equivalence is not economic improvement.',
            'Control probe is hand-coded, not Target imitation or a model fit.',
            'PASSIVE-only native adapter; Active rejected, not implemented.',
            'Existing Pair/slot4/180s restrictions and test100 capital/two50 grants remain.',
            'Original Target qty/private intent and full economic teacher remain unknown.',
            'Native identity within current replay clocks is not live fill/market impact certification.']
    except Exception as exc:
        result.update(verdict='NATIVE_SYSTEM_PLAN_EXECUTION_ERROR',error=type(exc).__name__+': '+str(exc),
                      traceback=traceback.format_exc(limit=12))
    finally:
        if trace is not None:
            try:result['partialTrace']=trace.close()
            except Exception:pass
        if sim is not None:
            try:sim.close()
            except Exception:pass
        save()
    print(json.dumps(dict(verdict=result['verdict'],attemptedBE=result['attemptedBE'],completedBE=result['completedBE'])),flush=True)
    if result['verdict']=='NATIVE_SYSTEM_PLAN_EXECUTION_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
