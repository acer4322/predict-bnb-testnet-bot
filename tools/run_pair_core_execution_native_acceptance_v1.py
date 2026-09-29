"""Worker-only consumed3 PASSIVE interface exercise of the opt-in runner.

No actor optimization, Target action labels, historical blocked-trace audit, fake
receipts or initial holdings. The first case gates the two fixed remaining cases.
"""
from pathlib import Path
import gzip,hashlib,importlib.util,json,math,os,shutil,sys,time,traceback

BUNDLE=Path(__file__).resolve().parent
OLD=Path('C:/BTC5M-worker/.lan_worker_v1/staging/minimal_student_training_rules_v2_clockfix_20260910')
FROZEN=Path('C:/BTC5M-worker/.lan_worker_v1/staging/root_family_support_stagea12_20260910_v1')
ROOT=Path('C:/BTC5M-worker/.tmp/pair_core_execution_native_acceptance_20260911_v1')
BACKEND=Path('C:/BTC5M-worker/.tmp/hft244_receipts_v4_20260910/.tmp/hft244_accounting_build_v1/candidate_python')
NATIVE_SHA='7de2335528234bea9381bea4208a90594a7bec9795d09b913f430ff1f8cf8ebf'
STAGE_SHA='da996c4bfd798d91c0286d2c4dae58afc7c75c9a044229e39a40945468b8debb'
WORLD_SHA='077e3d6ab20c591d474dc60d8d6e7eb3ca4cb721bad805ff6d038dd3a939c5f4'
MARKETS=(2022527,2022538,2022602)


def sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for b in iter(lambda:f.read(262144),b''):h.update(b)
    return h.hexdigest()


def load(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


class Trace:
    def __init__(self,out,mid):
        self.path=out/(str(mid)+'_TRACE.jsonl.gz')
        self.file=gzip.open(self.path,'xt',encoding='utf-8',newline='\n')
        self.now=0;self.bytes=0;self.receipts=0;self.actions=0;self.frames=0
        self.receipt_hash=hashlib.sha256();self.terminal_once=set()
    def emit(self,stream,x):
        text=json.dumps(dict(stream=stream,payload=x),sort_keys=True,separators=(',',':'),allow_nan=False)+'\n'
        self.bytes+=len(text.encode());assert self.bytes<=32*1024**2,'RESOURCE_CENSOR_TRACE_BYTES'
        self.file.write(text);self.file.flush()
    def action(self,x):self.actions+=1;self.emit('physical_native_action',x)
    def execution(self,x):self.emit('execution_control',x)
    def process(self,sim,t):
        for r in sim._receipt_delta_rows:
            self.receipts+=1;self.receipt_hash.update(json.dumps(r,sort_keys=True,separators=(',',':')).encode())
            self.emit('canonical_receipt',r)
        for key,c in sim.gateway.ledger.carriers.items():
            if c.state=='TERMINAL' and key not in self.terminal_once:
                self.emit('terminal_owner_once',dict(key=key,qty=c.qty,filled=c.filled,payment=c.payment,fees=c.fees,
                    transport=sim.gateway.transport.get(key),t=t));self.terminal_once.add(key)
        self.emit('own_state',dict(t=t,inventory=sim.inv,cost=sim.cost,
            funding=sim.gateway.ledger.funding_demand(),native_owners={k:sim.snap(o) for k,o in sim.orders.items()}))
    def plan(self,x):
        self.emit('whole_plan',x);self.frames+=1
        if self.frames%300==0:print(json.dumps(dict(stage='native_interface_heartbeat',market=self.path.name,frames=self.frames)),flush=True)
    def close(self):
        self.file.close()
        return dict(path=self.path.name,sha256=sha(self.path),bytes=self.path.stat().st_size,
            decoded_bytes=self.bytes,canonical_receipts=self.receipts,receipt_sha256=self.receipt_hash.hexdigest(),
            action_records=self.actions,complete_plan_records=self.frames,terminal_records=len(self.terminal_once))


class FixedInterfaceProducer:
    policy_id='AUTHORIZED_REJECTION_RECEIPT_INTERFACE_V1_NOT_TRAINED'
    continuation_id='TWO_EXPLICIT_INDEPENDENT_WORKS'
    provenance='FIXED_NATIVE_INTERFACE_STIMULUS_NOT_TARGET_POLICY'
    def __init__(self,cancel_on):
        self.calls=0;self.stage='PROPOSE_KNOWN_INADMISSIBLE';self.cancel_on=cancel_on
        self.rejection_observed=False;self.requested_keys=[]
    def produce(self,f):
        from tools.minimal_student_training_world_v2 import envelope
        self.calls+=1;ops=[];book=f['book'];ledger=f['ledger'];live={k:c for k,c in ledger.carriers.items() if c.state!='TERMINAL'}
        feedback=f.get('execution_feedback') or {}
        if self.stage=='AWAIT_REJECTION':
            if feedback.get('status')!='REJECTED_REPLAN_ON_NEXT_OBSERVATION' or feedback.get('reason')!='DECLARED_ENDPOINT_AUTHORITY_EXCEEDED':
                raise RuntimeError('EXPECTED_REJECTION_FEEDBACK_NOT_DELIVERED')
            self.rejection_observed=True;self.stage='SUBMIT_FIXED_PAIR'
        if self.stage in ('PROPOSE_KNOWN_INADMISSIBLE','SUBMIT_FIXED_PAIR'):
            if f['start']<=f['t']<f['end'] and book['bids'] and book['asks']:
                ub=float(max(book['bids']));ua=float(min(book['asks']));db=round(1.-ua,10)
                if 0<ub<ua<1 and 0<db<1:
                    if self.stage=='PROPOSE_KNOWN_INADMISSIBLE':
                        qty=math.ceil((101./ub)*100)/100
                        ops=[dict(kind='NEW',key=f"UP_{f['own_view']['n']}",parent_id=2,side='UP',price=ub,qty=qty,route='PASSIVE',role='DECLARED_REJECTION_STIMULUS')]
                        self.stage='AWAIT_REJECTION'
                    else:
                        n=f['own_view']['n']
                        for side,p,pid in [('DOWN',db,1),('UP',ub,2)]:
                            qty=math.ceil(max(18.,1./p)*100-1e-8)/100
                            key=f'{side}_{n}';n+=1;self.requested_keys.append(key)
                            ops.append(dict(kind='NEW',key=key,parent_id=pid,side=side,price=p,qty=qty,route='PASSIVE',role='FIXED_AUTHORIZED_INTERFACE_ACQUISITION'))
                        self.stage='OBSERVE_AND_CANCEL'
        elif self.stage=='OBSERVE_AND_CANCEL':
            receipt_seen=any(c.filled>0 for c in ledger.carriers.values())
            cancel_ready=self.cancel_on=='ACKNOWLEDGEMENT' or receipt_seen
            if cancel_ready:
                for key,c in live.items():
                    if c.state!='CANCEL_PENDING' and f['cancellable'].get(key,False):
                        ops.append(dict(kind='CANCEL',key=key,origin='WHOLE_POLICY',reason='PREREG_INTERFACE_CANCEL_STIMULUS'))
            if not live:self.stage='DONE'
        return envelope(f,self,ops)


def main():
    if '--child' not in sys.argv:
        supervisor=Path('C:/BTC5M-worker/.lan_worker_v1/staging/hft244_worker_repair_20260910_v1/run_hft244_worker_repair_v1.py')
        load('native_interface_bounded_supervisor',supervisor).bounded('authorized-execution-interface',
            [sys.executable,str(Path(__file__).resolve()),'--child'],240)
        return
    assert os.environ.get('BTC5M_LAN_RESULT_DIR') and int(os.environ.get('OMP_NUM_THREADS','999'))<=4
    assert os.environ.get('COMPUTERNAME')=='DESKTOP-JIERAGF'
    out=Path(os.environ['BTC5M_LAN_RESULT_DIR']);t0=time.monotonic();sim=None;trace=None
    result=dict(version='PAIR_CORE_EXECUTION_NATIVE_ACCEPTANCE_V1',verdict='PREFLIGHT',native_attempts=0,
        completed_native_markets=0,model_updates=0,worker=os.environ['COMPUTERNAME'],max_threads=4,
        active_native_supported=False,capital_cap=None,cases=[],historical_open_funding_audit_reopened=False)
    def save():
        result['elapsed_seconds']=time.monotonic()-t0
        text=json.dumps(result,indent=2,ensure_ascii=False,allow_nan=False);assert len(text.encode())<120000
        tmp=out/'COMPACT.json.tmp';tmp.write_text(text,encoding='utf-8');os.replace(tmp,out/'COMPACT.json')
    try:
        save();assert not ROOT.exists(),'immutable isolated runtime already exists'
        pack=json.loads((BUNDLE/'MANIFEST.json').read_text(encoding='utf-8'))
        assert pack['markets']==list(MARKETS)
        for rel,meta in pack['files'].items():
            p=(BUNDLE/rel).resolve();assert p.is_relative_to(BUNDLE) and p.stat().st_size==meta['bytes'] and sha(p)==meta['sha256'],rel
        assert sha(OLD/'MANIFEST.json')==WORLD_SHA
        old=json.loads((OLD/'MANIFEST.json').read_text(encoding='utf-8'))
        assert set(map(str,MARKETS))==set(old['marketWindows'])
        assert sha(FROZEN/'STAGE_MANIFEST.json')==STAGE_SHA
        stage=json.loads((FROZEN/'STAGE_MANIFEST.json').read_text(encoding='utf-8'))
        for rel,h in stage['files'].items():
            if not rel.endswith('.py'):continue
            p=(FROZEN/rel).resolve();assert p.is_relative_to(FROZEN) and p.stat().st_size<2*1024**2 and sha(p)==h
            dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
        for rel,meta in old['files'].items():
            if not rel.startswith(('tools/','tapes/')):continue
            p=OLD/rel;assert sha(p)==meta['sha256'];dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dst)
        for rel in pack['files']:
            if not rel.startswith('tools/'):continue
            dst=ROOT/rel;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BUNDLE/rel,dst)
        sys.path.insert(0,str(ROOT));os.chdir(ROOT)
        binary=BACKEND/'hftbacktest/_hftbacktest.cp313-win_amd64.pyd';assert sha(binary)==NATIVE_SHA
        sys.path.insert(0,str(BACKEND));import hftbacktest as h
        assert Path(h.__file__).resolve().parent==(BACKEND/'hftbacktest').resolve()
        from tools import run_eth_role_separated_minimal_pair_safety_smoke as minimal
        from tools.hft244_minimal_pair_accounting_v1 import install
        from tools.minimal_student_quantity_seam_v1 import make_student_class
        from tools.minimal_student_open_funding_v1 import OpenFundingProfile,OpenFundingLedger
        from tools.pair_core_economic_grant_ledger_v1 import Grant
        from tools.pair_core_authorized_outcome_preview_v1 import EndpointAuthority
        from tools.pair_core_authorized_execution_runner_v1 import make_execution_student
        install(minimal.v2.base,binary);base=minimal.v2.base;oldsend=base.ex.submit_native
        def strict_send(*a,**kw):
            rc=oldsend(*a,**kw)
            if int(rc)!=0:raise RuntimeError('STRICT_NATIVE_NONZERO_UNCERTAIN:'+str(rc))
            return rc
        base.ex.submit_native=strict_send
        Student=make_execution_student(minimal.MinimalPairRoleSim,make_student_class(minimal.MinimalPairRoleSim))
        loaded={name:dict(path=str(Path(mod.__file__).resolve()),sha256=sha(Path(mod.__file__))) for name,mod in list(sys.modules.items())
            if name.startswith(('tools.','src.')) and getattr(mod,'__file__',None)}
        assert all(Path(m['path']).is_relative_to(ROOT) for m in loaded.values())
        assert not any('r2_47' in name for name in loaded)
        result.update(verdict='NATIVE_READY',native_sha256=NATIVE_SHA,world_manifest_sha256=WORLD_SHA,
                      loaded_sources=loaded,strict_return_code_guard_installed=True)
        save();print(json.dumps(dict(stage='native_interface_ready',native_sha=NATIVE_SHA)),flush=True)
        for i,mid in enumerate(MARKETS):
            profile=OpenFundingProfile(asset='BTC');ledger=OpenFundingLedger(profile)
            ledger.issue(Grant(1,'PROTECTION_ACQUISITION','DOWN',24.,0.,0.,'PREDECLARED_INTERFACE_NOT_TARGET'))
            ledger.issue(Grant(2,'EXPOSURE_ACQUISITION','UP',0.,0.,0.,'PREDECLARED_INTERFACE_NOT_TARGET'))
            producer=FixedInterfaceProducer('ACKNOWLEDGEMENT' if i==0 else 'CONFIRMED_RECEIPT')
            trace=Trace(out,mid);result['native_attempts']+=1;result['verdict']='NATIVE_RUNNING';save()
            sim=Student(ROOT/f'tapes/{mid}.json.xz',profile.max_live_owners,False,
                producer=producer,ledger=ledger,trace=trace,verified_window=old['marketWindows'][str(mid)],
                window_source_sha256=old['marketWindowSourceSha256'],
                endpoint_authority=EndpointAuthority(-100.,-100.,'PREDECLARED_INTERFACE_ONLY_NOT_POLICY_DEFAULT'),
                execution_sink=trace.execution,execution_evidence='PINNED_NATIVE_STRICT_RC_ADAPTER')
            verdict=sim.run_whole(base)
            exact_ok=False
            try:
                sim._receipt_ledger.reconcile(sim.bt.state_values(0));assert not sim._receipt_invalid;exact_ok=True
            except Exception as exc:verdict['independent_native_accounting_error']=repr(exc)
            c=verdict['counts'];owners=verdict['owner_census']['owners']
            coverage=dict(positive_receipts=trace.receipts,partially_filled_owner_count=sum(0<o['confirmed_qty']<o['quantity']-1e-8 for o in owners),
                partial_delta_witnesses=None,zero_terminal=verdict['owner_census']['terminal_zero'],
                pending=verdict['owner_census']['unresolved_owners'],
                unknown_fault_injected=False,no_fake_terminal=True)
            ok=(verdict['status']=='SOURCE_ENDED_RECONCILED' and verdict['completed_source'] and exact_ok and
                c.get('rejected',0)==1 and c.get('sent',0)==2 and not c.get('resource_censor',0) and
                not c.get('unknown_send',0) and producer.rejection_observed)
            tr=trace.close();trace=None
            item=dict(market_id=mid,cancel_stimulus=producer.cancel_on,interface_pass=ok,run=verdict,
                native_gateway_reconciliation=exact_ok,coverage=coverage,trace=tr,
                final_inventory=dict(sim.inv),final_cost=sim.cost,simulated_hft_not_live=True,
                strategy_performance_evaluated=False)
            (out/(str(mid)+'_RESULT.json')).write_text(json.dumps(item,ensure_ascii=False,indent=2),encoding='utf-8')
            result['cases'].append(item)
            result['completed_native_markets']+=int(verdict['completed_source'])
            sim.close();sim=None
            result['verdict']='PASSIVE_INTERFACE_CASE_PASS' if ok else 'NATIVE_INTERFACE_GAP_STOP'
            save();print(json.dumps(dict(stage='native_interface_case_complete',market=mid,passed=ok,counts=c,coverage=coverage)),flush=True)
            if not ok:break
        if len(result['cases'])==3 and all(x['interface_pass'] for x in result['cases']):
            result['verdict']='PASSIVE_NATIVE_INTERFACE_SMOKE3_SUPPORTED_NOT_POLICY_PROMOTION'
        result['historical_zero_fill_mismatch_resolved']=False
        result['historical_20pending_resolved']=False
        result['historical_51censors_resolved']=False
        save()
    except Exception as exc:
        result['verdict']='ERROR_STOP_NO_AUTOMATIC_RETRY';result['error']=type(exc).__name__+': '+str(exc)
        result['traceback']=traceback.format_exc()[-6500:]
        if sim is not None:
            try:result['partial_owner_state']=sim.gateway.own_state()
            except Exception as snap:result['snapshot_error']=str(snap)
        if trace is not None:
            try:result['partial_trace']=trace.close();trace=None
            except Exception as log:result['trace_close_error']=str(log)
        save();raise
    finally:
        if trace is not None:trace.file.close()
        if sim is not None:sim.close()


if __name__=='__main__':main()
