"""Second-worker-only rule-separation smoke and complete-frame capture. Zero model fit."""
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
ROOT=Path('C:/BTC5M-worker/.tmp/minimal_student_training_rules_v2_clockfix_20260910')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
MIDS=[2022527,2022538,2022602]


def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class Trace:
    def __init__(self,out,mid):
        self.path=out/f'WORLD_V2_COMPLETE_TRAJECTORY_{mid}.jsonl.gz'
        self.f=gzip.open(self.path,'wt',encoding='utf-8',newline='\n')
        self.counts={};self.action_rows=[];self.now=0;self.bytes=0;self.plan_count=0
    def emit(self,stream,x):
        line=json.dumps(dict(stream=stream,payload=x),separators=(',',':'),sort_keys=True,allow_nan=False)+'\n'
        self.bytes+=len(line.encode());assert self.bytes<=64*1024**2,'bounded full-frame output exceeded'
        self.f.write(line);self.counts[stream]=self.counts.get(stream,0)+1
    def action(self,x):
        self.action_rows.append(x);assert len(self.action_rows)<200
        self.emit('native_action',x)
    def process(self,sim,t):
        for rr in sim._receipt_delta_rows:self.emit('canonical_receipt',rr)
        self.emit('own_next_state',dict(t=int(t),inventory=dict(sim.inv),cost=sim.cost,
            owners={k:dict(cumulative=o.get('cum'),status=sim.snap(o).get('status')) for k,o in sim.orders.items()}))
    def plan(self,x):
        self.emit('full_input_and_plan',x);self.plan_count+=1
        if self.plan_count%500==0:print(json.dumps(dict(stage='native_capture_heartbeat',file=self.path.name,frames=self.plan_count)),flush=True)
    def close(self):
        self.f.close()
        return dict(path=self.path.name,sha256=sha(self.path),bytes=self.path.stat().st_size,
                    decoded_bytes=self.bytes,stream_counts=self.counts,actions=self.action_rows)


class AuditBT:
    def __init__(self,bt,trace):self.native=bt;self.trace=trace
    def __getattr__(self,n):return getattr(self.native,n)
    def cancel(self,asset,n,wait):
        rc=self.native.cancel(asset,n,wait)
        self.trace.action(dict(kind='CANCEL',t=self.trace.now,n=int(n),rc=int(rc)))
        return rc


def verify_capture(path,expected,world_profile):
    counters={};last_t=None;total=0;first_frame=None;has_false_supervision=True;max_claim=0.;keys=set()
    with gzip.open(path,'rb') as f:
        for line in f:
            total+=len(line);assert total<=64*1024**2
            r=json.loads(line);stream=r['stream'];p=r['payload'];counters[stream]=counters.get(stream,0)+1
            if stream=='full_input_and_plan':
                fr=p['input_frame'];t=fr['t'];assert last_t is None or t>=last_t;last_t=t
                assert fr['world_profile']==world_profile
                assert p['policy_supervision_mask'] is False and p['target_expert_policy_label'] is None
                assert set(fr).isdisjoint({'winner','target','updates','future','tape'})
                assert 'own_view' in fr and 'authority' in fr and 'snapshots' in fr and 'book' in fr
                if first_frame is None:first_frame=fr
                authority=fr['authority'];grants=authority['grants'];accounts=authority['accounts']
                assert sum(g['cash_limit'] for g in grants.values())<=authority['capital']+1e-8
                for pid,g in grants.items():
                    claim=accounts[pid]['spent']+accounts[pid]['reserved_cash'];max_claim=max(max_claim,claim)
                    assert claim<=g['cash_limit']+1e-8
                for op in p['operations']:
                    if op['kind']=='NEW':assert fr['start']<=t<fr['end']
            elif stream=='canonical_receipt':
                assert p['sequence'] not in keys;keys.add(p['sequence'])
    assert counters==expected
    return dict(stream_rows=sum(counters.values()),full_input_frames=counters.get('full_input_and_plan',0),
        exact_expert_label_rows=0,all_frames_have_world_own_authority_and_book=True,
        resource_censor_as_hold_rows=0,maximum_parent_committed_cash=max_claim,
        first_frame_input_schema=sorted(first_frame),independent_stream_read=True)


def main():
    if '--child' not in sys.argv:
        p=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        load('bounded_supervisor',p).bounded('student-training-world-v2',[sys.executable,str(Path(__file__).resolve()),'--child'],180);return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);start=time.monotonic()
    result=dict(version='MINIMAL_STUDENT_TRAINING_RULE_SEPARATION_V2',verdict='RUNNING',
        nativeAttempts=0,nativeComplete=0,rows=[],unitTests=None,modelFits=0,
        liveChanges=0,freshMarkets=0,worker=os.environ.get('COMPUTERNAME'),maxThreads=4,
        actualTargetOriginalQuantityLabels=0,nativeActiveImplemented=False)
    sim=None;tr=None
    def save():
        result['elapsedSeconds']=time.monotonic()-start
        raw=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False).encode();assert len(raw)<80000
        (out/'COMPACT.json').write_bytes(raw)
    try:
        assert not ROOT.exists(),'immutable isolated source root exists'
        manifest=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        for rel,m in manifest['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE)
            assert p.stat().st_size==m['bytes'] and sha(p)==m['sha256'],rel
        assert sha(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
        stage=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text(encoding='utf-8'))
        for rel,want in stage['files'].items():
            if not rel.endswith('.py'):continue
            p=(FROZEN/rel).resolve();assert p.is_relative_to(FROZEN) and p.stat().st_size<2*1024**2 and sha(p)==want
            dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
        for rel in manifest['files']:
            if rel.startswith(('tools/','tests/','tapes/')):
                dest=ROOT/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/rel,dest)
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        unit=load('rules_unit_tests',ROOT/'tests/test_minimal_student_training_world_v2.py')
        buf=io.StringIO();tested=unittest.TextTestRunner(stream=buf,verbosity=2).run(unittest.defaultTestLoader.loadTestsFromModule(unit))
        (out/'UNIT_TESTS.txt').write_text(buf.getvalue(),encoding='utf-8')
        result['unitTests']=dict(run=tested.testsRun,failures=len(tested.failures),errors=len(tested.errors),passed=tested.wasSuccessful())
        save();print(json.dumps(dict(stage='unit_gate',result=result['unitTests'])),flush=True)
        if not tested.wasSuccessful():result['verdict']='UNIT_GATE_FAIL_NO_NATIVE';save();return
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from dataclasses import asdict
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class
        from tools.minimal_student_training_world_v2 import WorldProfile,TrainingGrantLedger,make_training_class,RemovedRuleWitness
        from tools.pair_core_economic_grant_ledger_v1 import Grant
        install(minimal.v2.base,binary);base=minimal.v2.base;old_send=base.ex.submit_native
        def strict_send(*a,**kw):
            rc=old_send(*a,**kw)
            if int(rc)!=0:raise RuntimeError('native send uncertain/rejected rc='+str(rc))
            return rc
        base.ex.submit_native=strict_send
        exact=make_student_class(minimal.MinimalPairRoleSim);Student=make_training_class(minimal.MinimalPairRoleSim,exact)
        profile=WorldProfile();profile_dict=asdict(profile)
        result['worldProfile']=profile_dict;result['nativeSha256']=NATIVE_SHA
        hashes={name:sha(Path(m.__file__)) for name,m in list(sys.modules.items())
                if name.startswith(('tools.','src.')) and getattr(m,'__file__',None)}
        for name,m in list(sys.modules.items()):
            if name in hashes:assert Path(m.__file__).resolve().is_relative_to(ROOT),name
        assert not any('r2_47' in name for name in hashes)
        result['loadedSourceHashes']=hashes
        for mid in MIDS:
            t0=time.monotonic();result['nativeAttempts']+=1;save()
            ledger=TrainingGrantLedger(100.,profile)
            for pid,side in [(1,'UP'),(2,'DOWN')]:ledger.issue(Grant(pid,'fixed-initialization',side,0.,110.,50.,'DISCLOSED_TEST_INITIALIZATION_NOT_TEACHER'))
            producer=RemovedRuleWitness();tr=Trace(out,mid)
            sim=Student(ROOT/f'tapes/{mid}.json.xz',profile.max_live_owners,False,producer=producer,ledger=ledger,trace=tr,verified_window=manifest['marketWindows'][str(mid)],window_source_sha256=manifest['marketWindowSourceSha256'])
            sim.bt=AuditBT(sim.bt,tr)
            print(json.dumps(dict(stage='native_start',marketId=mid)),flush=True)
            sim.run_whole(base);actual=sim.gateway.ledger
            sim._receipt_ledger.reconcile(sim.bt.state_values(0));actual.invariants()
            assert not sim._receipt_invalid
            assert all(abs(sum(c.filled for c in actual.carriers.values() if actual.grants[c.parent_id].side==s)-sim.inv[s])<1e-7 for s in ('UP','DOWN'))
            assert abs(sum(c.payment+c.fees for c in actual.carriers.values())-sim.cost)<1e-7
            owners=[dict(key=k,side=o['side'],qty=o['qty'],price=o['price'],filled=o.get('cum',0.),placed=o['placed'],
                        status=sim.snap(o).get('status'),state=actual.carriers[k].state) for k,o in sim.orders.items()]
            unresolved=[k for k,c in actual.carriers.items() if c.state!='TERMINAL']
            trace=tr.close();tr=None
            submitted=[x for x in trace['actions'] if x['kind']=='NEW'];cancelled=[x for x in trace['actions'] if x['kind']=='CANCEL']
            verified=verify_capture(out/trace['path'],trace['stream_counts'],profile_dict)
            row=dict(marketId=mid,windowStartMs=sim.verified_market_start,windowEndMs=sim.verified_market_end,submits=sim.submits,nativeReceipts=len(sim._receipt_ledger.seen),
                lateNewOrders=sim.late_new_orders,maxLiveSlots=sim.max_simultaneous_slots,
                keptBeyondRemovedTTL=producer.kept_beyond_old_ttl,
                nonDisplayedNewPrices=producer.non_displayed_prices,
                explicitBudgetUpdates=producer.explicit_budget_updates,finalBudgets={str(k):g.cash_limit for k,g in actual.grants.items()},
                initialCapital=100.,finalCapital=actual.capital,unresolved=unresolved,
                completeInputFrames=sim.complete_input_frames,nativeOwnStates=trace['stream_counts'].get('own_next_state',0),
                stage=producer.stage,owners=owners,trace=trace,captureAudit=verified,
                ownInventory=dict(sim.inv),cost=sim.cost,
                UPBranch=sim.inv['UP']-sim.cost,DOWNBranch=sim.inv['DOWN']-sim.cost,
                earliestCancelAgeMs=min((c['t']-submitted[0]['t'] for c in cancelled),default=None),
                candidateIsTeacher=False,elapsedSeconds=time.monotonic()-t0)
            result['rows'].append(row);result['nativeComplete']+=1;sim.close();sim=None;save()
            if not (len(submitted)==5 and row['lateNewOrders']==5 and row['maxLiveSlots']>=5
                    and row['keptBeyondRemovedTTL'] and row['nonDisplayedNewPrices']>0
                    and row['explicitBudgetUpdates']==1 and not unresolved and row['stage']=='DONE'):
                result['verdict']='NATIVE_RULE_WITNESS_NOT_FULLY_EXERCISED';save();return
            assert row['earliestCancelAgeMs'] is not None and row['earliestCancelAgeMs']>5000
            assert all(row['windowStartMs']<=o['placed']<row['windowEndMs'] for o in owners)
            print(json.dumps(dict(stage='native_verified',marketId=mid,lateNew=5,maxOwners=row['maxLiveSlots'],
                completeFrames=row['completeInputFrames'],unresolved=len(unresolved))),flush=True)
        for name,m in list(sys.modules.items()):
            if name in hashes:assert sha(Path(m.__file__))==hashes[name]
        result['verdict']='TRAINING_WORLD_RULE_SEPARATION_AND_NATIVE_FRAME_CAPTURE_PASS'
        result['summary']=dict(unitTests=result['unitTests']['run'],completedMarkets=3,
            lateNewOrders=sum(x['lateNewOrders'] for x in result['rows']),
            nonDisplayedNewPrices=sum(x['nonDisplayedNewPrices'] for x in result['rows']),
            completeInputFrames=sum(x['completeInputFrames'] for x in result['rows']),
            nativeOwnStateObservations=sum(x['nativeOwnStates'] for x in result['rows']),
            nativeReceipts=sum(x['nativeReceipts'] for x in result['rows']),
            unresolvedReservations=sum(len(x['unresolved']) for x in result['rows']),
            compressedTraceBytes=sum(x['trace']['bytes'] for x in result['rows']))
        result['remainingGaps']=['Native ACTIVE capability still unsupported; no full4-channel claim.',
            'Current venue grids/minima/queue/latency assumptions remain explicit, not Target facts.',
            'Witness boundary timing and quote constants are regression fixtures, never training expert labels.',
            'Resource32 is censored engineering capacity, not learned slot policy.',
            'Zero fits: full system teacher/value support still required; no profitability claim.',
            'Current source epoch remains9/7; no relabeling to recent Target sizes.']
    except Exception as exc:
        result.update(verdict='TRAINING_WORLD_EXECUTION_ERROR',error=type(exc).__name__+': '+str(exc),traceback=traceback.format_exc(limit=12))
    finally:
        if tr is not None:
            try:result['partialTrace']=tr.close()
            except Exception:pass
        if sim is not None:
            try:sim.close()
            except Exception:pass
        save()
    print(json.dumps({k:v for k,v in result.items() if k in ('verdict','summary','error','elapsedSeconds')}),flush=True)
    if result['verdict']=='TRAINING_WORLD_EXECUTION_ERROR':raise SystemExit(2)


if __name__=='__main__':main()
