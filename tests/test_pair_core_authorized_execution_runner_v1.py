"""Actual new consume/run loop against deterministic I/O boundaries, NOT HFT.
The canonical gateway and receipt methods are real; price/fill stimuli are fixtures.
"""
from collections import Counter, deque
from copy import deepcopy
from types import SimpleNamespace
import unittest

from tools.minimal_student_native_system_plan_v1 import COPY_FIELDS
from tools.minimal_student_training_world_v2 import envelope
from tools.minimal_student_open_funding_v1 import OpenFundingLedger, OpenFundingProfile
from tools.pair_core_economic_grant_ledger_v1 import Grant
from tools.pair_core_authorized_outcome_preview_v1 import EndpointAuthority
from tools.pair_core_authorized_execution_runner_v1 import (
    make_execution_student, ExecutionStopped, owner_census)

BOOK = {'bids':{.4:100.}, 'asks':{.6:100.}}


class Trace:
    def __init__(self):
        self.now=0; self.actions=[]; self.plans=[]; self.processes=[]
    def action(self, row): self.actions.append(deepcopy(row))
    def plan(self, row): self.plans.append(deepcopy(row))
    def process(self, sim, t): self.processes.append((t, deepcopy(sim.inv), sim.cost))


class FrozenBoundary:
    """No queue, fill probability, feed delay or matching engine exists here."""
    def __init__(self, *, updates=None):
        for key in COPY_FIELDS: setattr(self, key, 0)
        self.inv={'UP':0.,'DOWN':0.}; self.cost=0.; self.un={'UP':deque(),'DOWN':deque()}
        self.orders={}; self.n=1; self.slot_key={}; self.key_role={}; self.placeHist=deque()
        self.slot_history=[]; self.role_submits=Counter(); self.role_budget_blocks=Counter()
        self.role_cancel_requests=Counter(); self.veto=Counter(); self.book=deepcopy(BOOK)
        self.payload={'market':{'window_end_ms':300000},'updates':updates or []}
        tt=[int(u[1]) for u in self.payload['updates']]
        self.meta={'firstReceivedMs':min(tt,default=1),'lastReceivedMs':max(tt,default=1)}
        self.native_rows={}; self.send_calls=[]; self.cancel_calls=[]; self.fail_send_at=None
        self.fail_after_local=False; self.wrong_identity=False; self.cancel_rc=0
        self.injected={}; self.terminal_at={}; self._receipt_delta_rows=[]
        self.bt=SimpleNamespace(orders=lambda _:self.native_rows,cancel=self._cancel)
        self.sample_count=0
    def _cancel(self, asset, n, wait):
        self.cancel_calls.append(n)
        return self.cancel_rc
    def submit(self, t, side, price, qty):
        self.send_calls.append((self.n,side,qty))
        fail=len(self.send_calls)==self.fail_send_at
        if fail and not self.fail_after_local: raise RuntimeError('INJECTED_UNCERTAIN_NEW')
        n=self.n; key=f'{side}_{n}'
        self.orders[key]={'n':n,'side':side,'price':price,'qty':qty,'cum':0.,'placed':t}
        self.native_rows[n]=SimpleNamespace(cancellable=True,status='NEW',cumExecQty=0.)
        self.n+=1; self.submits+=1
        if self.wrong_identity: self.orders[key]['qty']+=1
        if fail: raise RuntimeError('INJECTED_AFTER_LOCAL_ID')
    def process(self, t):
        self._receipt_delta_rows=self.injected.pop(t,[])
        for r in self._receipt_delta_rows:
            o=self.orders[r['key']]; self.inv[o['side']]+=r['qty']
            self.cost+=r['qty']*r['contractPrice']+r['fee']
            o['cum']=r['cumulative_qty']; self.native_rows[o['n']].cumExecQty=r['cumulative_qty']
        for key,status in self.terminal_at.pop(t,[]):
            self.native_rows[self.orders[key]['n']].status=status
    def snap(self, order):
        r=self.native_rows.get(order['n'])
        return {'status':r.status,'cumExecQty':r.cumExecQty} if r else {}
    def _refresh_slots(self, t):
        for slot,key in list(self.slot_key.items()):
            if self.gateway.ledger.carriers[key].state=='TERMINAL': del self.slot_key[slot]
    def _sample_occupancy(self): self.sample_count+=1


class Producer:
    policy_id='FIXED_INTERFACE_FIXTURE_NOT_TRAINED'
    continuation_id='SAME_INDEPENDENT_WORKS'
    provenance='DETERMINISTIC_BOUNDARY_NOT_NATIVE_MATCHING'
    def __init__(self, script=()): self.calls=0; self.script=list(script); self.frames=[]
    def produce(self, f):
        self.frames.append(deepcopy(f)); i=self.calls; self.calls+=1
        specs=self.script[i] if i<len(self.script) else []
        ops=[]; n=f['own_view']['n']
        for x in specs:
            if x[0]=='CANCEL':
                ops.append(dict(kind='CANCEL',key=x[1],origin='WHOLE_POLICY',reason='EXPLICIT_FIXTURE_CANCEL'))
            else:
                side,qty,p,*route=x
                ops.append(dict(kind='NEW',key=f'{side}_{n}',parent_id=1 if side=='DOWN' else 2,
                    side=side,price=p,qty=qty,route=route[0] if route else 'PASSIVE',role='EXPLICIT_FIXTURE_WORK'))
                n+=1
        return envelope(f,self,ops)


def make(script=(), *, updates=None, bounds=None, capacity=32, sink=None):
    L=OpenFundingLedger(OpenFundingProfile(asset='BTC',max_live_owners=capacity))
    L.issue(Grant(1,'repair','DOWN',24.,0.,0.,'SUPPLIED_REPAIR_FIXTURE'))
    L.issue(Grant(2,'add','UP',0.,0.,0.,'SUPPLIED_ADD_FIXTURE'))
    log=[]; trace=Trace(); producer=Producer(script)
    cls=make_execution_student(FrozenBoundary,object)
    s=cls(producer=producer,ledger=L,trace=trace,updates=updates,
          verified_window=(0,300000),window_source_sha256='a'*64,
          endpoint_authority=bounds or EndpointAuthority(-20.,-20.,'EXPLICIT_TEST_ALLOWANCE'),
          execution_sink=sink or log.append,execution_evidence='DETERMINISTIC_BOUNDARY_DOUBLE')
    return s,log


def frame(s, t=1000, index=0): return s.current_frame(t,300000,deepcopy(s.book),{},index)

def take(s,t=1000,index=0):
    f=frame(s,t,index);return s.consume(f,s.producer.produce(deepcopy(f)))

def base():
    advanced=[]
    def apply(book,u): book.update(deepcopy(u[2]))
    return SimpleNamespace(ex=SimpleNamespace(advance_to=lambda bt,t:advanced.append(t)),
                           apply=apply,quotes=lambda b:{'valid':True},advanced=advanced)


def partial(s,key,qty,t=2000,terminal=None):
    c=s.gateway.ledger.carriers[key]
    s.injected[t]=[dict(key=key,qty=qty-c.filled,contractPrice=c.limit,fee=0.,
                       cumulative_qty=qty,sequence=t)]
    if terminal: s.terminal_at[t]=[(key,terminal)]
    s.process(t)


class ExecutionRunnerTests(unittest.TestCase):
    def test_factory_is_opt_in_and_inherits_real_receipt_path(self):
        s,_=make();self.assertNotIn('process',type(s).__dict__)
        self.assertNotIn('submit',type(s).__dict__)
        self.assertIsNone(s.gateway.ledger.capital)
        self.assertEqual(s.gateway.capabilities,frozenset({'PASSIVE'}))

    def test_endpoint_rejection_does_not_commit_or_send(self):
        s,log=make([[('UP',400.,.2)]])
        before=s.gateway.snapshot_id();report=take(s)
        self.assertEqual(report['status'],'REJECTED_REPLAN_ON_NEXT_OBSERVATION')
        self.assertEqual(s.gateway.snapshot_id(),before)
        self.assertFalse(s.gateway.committed_ids);self.assertFalse(s.send_calls)
        self.assertIsNone(log[-1]['target_hold_label'])
        self.assertEqual(log[-1]['operations'][0]['qty'],400.)
        self.assertIn('authorized_outcome',log[-1]['input_frame']['own_view'])

    def test_refusal_does_not_drop_book_update_or_repeat_same_frame(self):
        changed={'bids':{.42:77.},'asks':{.61:88.}}
        s,_=make([[('UP',400.,.2)],[('UP',18.,.2)]],updates=[[1,1000,changed],[2,2000,{}]])
        r=s.run_whole(base())
        self.assertTrue(r['completed_source']);self.assertEqual(s.producer.calls,2)
        self.assertEqual(s.producer.frames[1]['previous_book'],changed)
        self.assertEqual(s.producer.frames[1]['execution_feedback']['status'],'REJECTED_REPLAN_ON_NEXT_OBSERVATION')
        self.assertEqual(s.execution_counts['committed'],1)
        self.assertEqual(s.frame_count,1)
        self.assertEqual(r['status'],'SOURCE_ENDED_WITH_UNRESOLVED_OWNERS')

    def test_rejected_frame_ids_not_counted_as_successful_plans(self):
        s,_=make([[('UP',400.,.2)],[],[]],updates=[[1,1000,{}],[2,1000,{}],[3,2000,{}]])
        r=s.run_whole(base());self.assertEqual(r['counts']['observations'],3)
        self.assertEqual(r['counts']['proposals'],3);self.assertEqual(r['counts']['rejected'],1)
        self.assertEqual(r['counts']['committed'],2)
        self.assertEqual(r['status'],'SOURCE_ENDED_NO_PHYSICAL_ACTIVITY')

    def test_partial_send_fail_stops_and_releases_only_uncalled_suffix(self):
        s,log=make([[('DOWN',18.,.2),('UP',18.,.2),('DOWN',18.,.19)]])
        s.fail_send_at=2
        with self.assertRaises(ExecutionStopped): take(s)
        self.assertEqual(s.gateway.transport,{'DOWN_1':'SENT','UP_2':'UNKNOWN','DOWN_3':'NOT_SENT_TERMINAL'})
        self.assertEqual(len(s.send_calls),2);self.assertEqual(s.n,4)
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].reserved_qty,18.)
        self.assertEqual(s.gateway.ledger.carriers['UP_2'].reserved_qty,18.)
        self.assertEqual(s.gateway.ledger.carriers['DOWN_3'].reserved_qty,0.)
        self.assertFalse(s.execution_stop['auto_resume'])
        self.assertEqual(s.gateway.ledger.account(1)['repair_remaining'],24.)

    def test_unknown_without_local_id_is_not_zero_terminal(self):
        s,_=make([[('DOWN',18.,.2),('UP',18.,.2)]]);s.fail_send_at=1
        with self.assertRaises(ExecutionStopped): take(s)
        r=owner_census(s);self.assertEqual(r['partitions']['UNKNOWN_SEND_ZERO'],1)
        self.assertEqual(r['partitions']['DEFINITE_NOT_SENT'],1)
        self.assertEqual(r['terminal_zero'],0)
        self.assertEqual(r['unresolved_owners'],1)
        self.assertFalse(r['owners'][0]['native_id_known'])

    def test_unknown_after_local_id_allows_later_authoritative_receipt(self):
        s,_=make([[('DOWN',18.,.2)]]);s.fail_send_at=1;s.fail_after_local=True
        with self.assertRaises(ExecutionStopped): take(s)
        partial(s,'DOWN_1',1.,terminal='CANCELED')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].state,'TERMINAL')
        self.assertEqual(s.gateway.ledger.account(1)['repair_remaining'],23.)
        self.assertEqual(owner_census(s)['partitions']['TERMINAL_PARTIAL'],1)

    def test_fail_stop_does_not_advance_engine_again(self):
        s,_=make([[('DOWN',18.,.2),('UP',18.,.2)]],updates=[[1,1000,{}],[2,2000,{}]])
        s.fail_send_at=1;b=base();r=s.run_whole(b)
        self.assertFalse(r['completed_source']);self.assertEqual(s.producer.calls,1)
        self.assertNotIn(2000,b.advanced);self.assertEqual(r['counts']['unknown_send'],1)

    def test_unsafe_automatic_resume_is_rejected(self):
        s,_=make(updates=[[1,1000,{}]]);s.run_whole(base())
        with self.assertRaisesRegex(RuntimeError,'RESUME_REQUIRES'): s.run_whole(base())

    def test_cancel_nonzero_preserves_original_order_and_unexecuted_new(self):
        s,_=make([[('DOWN',18.,.2)],[('CANCEL','DOWN_1'),('UP',18.,.2)]])
        take(s);s.cancel_rc=7
        with self.assertRaises(ExecutionStopped): take(s,2000,1)
        self.assertEqual(s.gateway.transport['DOWN_1'],'SENT')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].state,'CANCEL_PENDING')
        self.assertEqual(s.cancel_transport['DOWN_1'],'UNKNOWN')
        self.assertEqual(s.gateway.transport['UP_2'],'NOT_SENT_TERMINAL')
        self.assertEqual(len(s.send_calls),1)

    def test_cancel_accepted_is_not_terminal(self):
        s,_=make([[('DOWN',18.,.2)],[('CANCEL','DOWN_1')]])
        take(s);take(s,2000,1);r=owner_census(s)
        self.assertEqual(r['unresolved_owners'],1)
        self.assertEqual(r['owners'][0]['cancel_transport'],'REQUEST_ACCEPTED_NOT_TERMINAL')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].reserved_qty,18.)

    def test_unattempted_cancel_intention_is_not_reported_as_sent(self):
        s,_=make([[('DOWN',18.,.2)],[('UP',18.,.2),('CANCEL','DOWN_1')]])
        take(s);s.fail_send_at=2
        with self.assertRaises(ExecutionStopped):take(s,2000,1)
        self.assertFalse(s.cancel_calls)
        self.assertEqual(s.cancel_transport['DOWN_1'],'NOT_ATTEMPTED_CANCEL_INTENT_RETAINED')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].reserved_qty,18.)

    def test_maintenance_outside_tightened_authority_still_runs(self):
        s,log=make([[('DOWN',18.,.2)],[]]);take(s)
        s.gateway.replace_endpoint_authority(EndpointAuthority(-1.,-1.,'TIGHTENED_EXTERNAL'))
        r=take(s,2000,1)
        self.assertEqual(r['admission'],'MAINTENANCE_ONLY_OUTSIDE_AUTHORITY')
        self.assertEqual(len(s.send_calls),1)

    def test_cancelling_plan_rejected_for_new_order_does_not_cancel(self):
        s,_=make([[('DOWN',18.,.2)],[('CANCEL','DOWN_1'),('UP',400.,.2)]])
        take(s);before=s.gateway.snapshot_id();take(s,2000,1)
        self.assertEqual(before,s.gateway.snapshot_id());self.assertFalse(s.cancel_calls)
        self.assertNotEqual(s.gateway.ledger.carriers['DOWN_1'].state,'CANCEL_PENDING')

    def test_active_unsupported_stops_not_hold(self):
        s,_=make([[('UP',18.,.2,'ACTIVE')]],updates=[[1,1000,{}],[2,2000,{}]])
        r=s.run_whole(base());self.assertEqual(r['status'],'UNSUPPORTED_CAPABILITY_STOP')
        self.assertEqual(s.producer.calls,1);self.assertFalse(s.send_calls)

    def test_resource_censor_stops_not_policy_rejection(self):
        s,_=make([[('UP',18.,.2),('DOWN',18.,.2)]],capacity=1,updates=[[1,1000,{}]])
        r=s.run_whole(base());self.assertEqual(r['status'],'RESOURCE_CENSORED_STOP')
        self.assertEqual(r['counts']['resource_censor'],1)
        self.assertFalse(s.gateway.ledger.carriers)

    def test_stale_own_state_returns_newframe_feedback(self):
        s,_=make([[('DOWN',18.,.2)]]);f=frame(s);e=s.producer.produce(f)
        s.gateway.replace_endpoint_authority(EndpointAuthority(-30.,-30.,'CHANGED'))
        r=s.consume(f,e);self.assertEqual(r['reason'],'STALE_NATIVE_OWN_STATE')
        self.assertFalse(s.gateway.ledger.carriers)

    def test_zero_fill_is_partitioned_by_terminal_evidence(self):
        s,_=make([[('DOWN',18.,.2),('UP',18.,.2)]]);take(s)
        s.terminal_at[2000]=[('DOWN_1','CANCELED')];s.process(2000)
        r=owner_census(s)
        self.assertEqual(r['legacy_all_owner_zero'],2)
        self.assertEqual(r['terminal_zero'],1)
        self.assertEqual(r['partitions']['OPEN_ZERO'],1)
        self.assertEqual(r['unresolved_owners'],1)

    def test_subminimum_partial_receipt_is_not_classified_zero(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s);partial(s,'DOWN_1',.01)
        r=owner_census(s);self.assertEqual(r['legacy_all_owner_zero'],0)
        self.assertEqual(r['partitions']['OPEN_PARTIAL'],1)
        self.assertAlmostEqual(s.inv['DOWN'],.01)

    def test_duplicate_unchanged_snapshot_adds_no_receipt(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s);partial(s,'DOWN_1',1.)
        cost=s.cost;s.process(3000);self.assertEqual(s.cost,cost)
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].filled,1.)

    def test_terminal_partial_preserves_economic_work(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s);partial(s,'DOWN_1',15.,terminal='CANCELED')
        r=owner_census(s);self.assertEqual(r['unresolved_owners'],0)
        self.assertEqual(r['partitions']['TERMINAL_PARTIAL'],1)
        self.assertEqual(r['accounts']['1']['repair_remaining'],9.)

    def test_source_end_does_not_create_terminal(self):
        s,_=make([[('DOWN',18.,.2)]],updates=[[1,1000,{}]])
        r=s.run_whole(base());self.assertEqual(r['status'],'SOURCE_ENDED_WITH_UNRESOLVED_OWNERS')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].reserved_qty,18.)
        self.assertFalse(s.cancel_calls)

    def test_zero_terminal_native_status_closes_owner_not_grant(self):
        s,_=make([[('DOWN',18.,.2)],[]],updates=[[1,1000,{}],[2,2000,{}]])
        s.terminal_at[2000]=[('DOWN_1','EXPIRED')]
        r=s.run_whole(base());self.assertEqual(r['status'],'SOURCE_ENDED_RECONCILED')
        self.assertEqual(r['owner_census']['terminal_zero'],1)
        self.assertEqual(r['owner_census']['accounts']['1']['repair_remaining'],24.)

    def test_native_cumulative_disagreement_is_not_silently_zeroed(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s)
        s.native_rows[1].cumExecQty=3.
        r=owner_census(s);self.assertEqual(r['mismatches'][0]['reason'],'NATIVE_GATEWAY_CUMULATIVE_DIFFER')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].filled,0.)

    def test_snapshot_failure_keeps_owner_unresolved(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s)
        def broken(_):raise RuntimeError('INJECTED_SNAPSHOT_QUERY_ERROR')
        s.snap=broken;r=owner_census(s)
        self.assertEqual(r['unresolved_owners'],1)
        self.assertEqual(r['mismatches'][0]['reason'],'NATIVE_SNAPSHOT_UNAVAILABLE')

    def test_postattempt_identity_mismatch_is_unknown_not_unsent(self):
        s,_=make([[('DOWN',18.,.2)]]);s.wrong_identity=True
        with self.assertRaises(ExecutionStopped):take(s)
        self.assertEqual(s.gateway.transport['DOWN_1'],'UNKNOWN')
        self.assertEqual(s.gateway.ledger.carriers['DOWN_1'].reserved_qty,18.)

    def test_failed_presend_journal_releases_only_never_called(self):
        log=[]
        def sink(e):
            if e['kind']=='PHYSICAL_CALL_BEGIN':raise IOError('JOURNAL_FAILURE')
            log.append(e)
        s,_=make([[('DOWN',18.,.2),('UP',18.,.2)]],sink=sink)
        with self.assertRaises(ExecutionStopped):take(s)
        self.assertFalse(s.send_calls)
        self.assertTrue(all(v=='NOT_SENT_TERMINAL' for v in s.gateway.transport.values()))
        self.assertEqual(s.n,3)

    def test_five_passive_owners_and_over100_need_no_old_cap(self):
        specs=[('UP',100.,.2) for _ in range(5)]
        s,_=make([specs],bounds=EndpointAuthority(-200.,-200.,'EXPLICIT_FIVE_OWNER'))
        take(s);self.assertEqual(len(s.send_calls),5)
        self.assertIsNone(s.gateway.ledger.capital)
        self.assertEqual(len(s.gateway.ledger.carriers),5)

    def test_late_market_entry_is_metric_not180_gate(self):
        s,_=make([[('DOWN',18.,.2)]])
        r=take(s,250000);self.assertEqual(r['status'],'COMMITTED_AND_PHYSICAL_CALLS_COMPLETED')
        self.assertEqual(s.late_new_orders,1)

    def test_fullfill_waiting_terminal_is_not_fake_terminal(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s);partial(s,'DOWN_1',18.)
        r=owner_census(s)
        self.assertEqual(r['partitions']['OPEN_FULL'],1)
        self.assertEqual(r['unresolved_owners'],1)

    def test_no_postcommit_id_reuse_after_never_sent_order(self):
        s,_=make([[('DOWN',18.,.2),('UP',18.,.2)]])
        s.fail_send_at=1
        with self.assertRaises(ExecutionStopped):take(s)
        self.assertEqual(s.n,3)
        self.assertIn('UP_2',s.gateway.ledger.carriers)

    def test_complete_plan_trace_contains_external_authority(self):
        s,_=make([[('DOWN',18.,.2)]]);take(s)
        event=s.trace.plans[0]
        self.assertIn('authorized_outcome',event['input_frame']['own_view'])
        self.assertFalse(event['policy_supervision_mask'])
        self.assertIsNone(event['target_expert_policy_label'])


if __name__=='__main__':unittest.main()
