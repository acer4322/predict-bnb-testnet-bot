"""Rule-separation regression tests. Synthetic states are explicit, not Target labels."""
import unittest
from dataclasses import replace
from collections import deque
from copy import deepcopy

from tools.pair_core_economic_grant_ledger_v1 import Grant, EconomicGrantLedger
from tools.minimal_student_system_plan_v1 import SystemPlanGateway, PlanAction, PlanRejected
from tools.minimal_student_training_world_v2 import (
    WorldProfile, TrainingGrantLedger, TrainingPlanGateway, TrainingPlan,
    BudgetAllocation, envelope, validate_envelope, training_frame_id, full_input, verified_market_window,
)


class Producer:
    policy_id='TEST_COMPLETE_POLICY';continuation_id='TEST_ALL_STEPS';provenance='SYNTHETIC_FIXTURE_ONLY'


class TrainingWorldV2Tests(unittest.TestCase):
    def fixture(self,limit=32):
        profile=WorldProfile(max_live_owners=limit)
        ledger=TrainingGrantLedger(100.,profile)
        for pid,side in [(1,'UP'),(2,'DOWN')]:ledger.issue(Grant(pid,'same-generation',side,0.,110.,50.,'TEST_INITIAL_AUTHORITY'))
        gw=TrainingPlanGateway(ledger,asset='BTC',policy_id=Producer.policy_id,capabilities=('PASSIVE',))
        return gw
    def frame(self,gw,t=120000):
        return dict(t=t,start=0,end=300000,index=t,world_profile=replace(gw.ledger.profile).__dict__.copy(),
            book={'bids':{.5:100.},'asks':{.54:100.}},previous_book={'bids':{},'asks':{}},quotes={},
            own_view=dict(inv={'UP':0.,'DOWN':0.},cost=0.,un={'UP':deque(),'DOWN':deque()},
                orders={},slot_key={},key_role={},n=1,max_slots=gw.ledger.profile.max_live_owners),
            snapshots={},cancellable={},ledger=deepcopy(gw.ledger),gateway_state_id=gw.snapshot_id())
    def op(self,key='UP_1',parent=1,side='UP',price=.1,qty=18.):
        return dict(kind='NEW',key=key,parent_id=parent,side=side,price=price,qty=qty,role='TEST_ECONOMIC_DECISION')
    def commit(self,gw,f,ops,alloc=()):
        env=envelope(f,Producer,ops,alloc)
        validate_envelope(f,env,Producer.policy_id,Producer.continuation_id)
        gw.commit(env.plan,now_ms=f['t'],market_end_ms=f['end'])
        return env
    def reserve_sent(self,gw,key='UP_1',q=18.,p=.1):
        pl=TrainingPlan('seed'+key,gw.snapshot_id(),Producer.policy_id,Producer.continuation_id,(PlanAction('NEW',key,1,'PASSIVE',p,q),))
        gw.commit(pl,now_ms=1000,market_end_ms=300000);gw.record_send(key,'SENT',evidence='UNIT_ONLY')
    def test_old_180_before_at_after_and_final_instant_all_reachable(self):
        for t in (119999,120000,120001,240000,299999):
            gw=self.fixture();f=self.frame(gw,t);self.commit(gw,f,[self.op()]);self.assertEqual(gw.ledger.carriers['UP_1'].qty,18.)
    def test_old_gateway_still_rejects_late_and_remains_frozen(self):
        gw=self.fixture();old=SystemPlanGateway(gw.ledger,asset='BTC',policy_id='OLD',capabilities=('PASSIVE',))
        p=old.propose('late',[PlanAction('NEW','x',1,'PASSIVE',.1,18.)],'OLD')
        with self.assertRaisesRegex(PlanRejected,'AFTER180'):old.commit(p,now_ms=120001,market_end_ms=300000)
    def test_actual_market_end_blocks(self):
        for t in (300000,300001):
            gw=self.fixture();f=self.frame(gw,t)
            with self.assertRaisesRegex(PlanRejected,'MARKET_NOT_OPEN'):self.commit(gw,f,[self.op()])
    def test_before_market_start_blocks(self):
        gw=self.fixture();f=self.frame(gw,999);f['start']=1000
        with self.assertRaisesRegex(PlanRejected,'MARKET_NOT_OPEN'):self.commit(gw,f,[self.op()])
    def test_costly_opposite_pair_is_not_a_world_veto(self):
        gw=self.fixture();f=self.frame(gw)
        f['own_view']['inv']['UP']=30.;f['own_view']['cost']=24.;f['own_view']['un']['UP'].append((30.,.8))
        self.commit(gw,f,[self.op(key='DOWN_1',parent=2,side='DOWN',price=.4)])
        self.assertIn('DOWN_1',gw.ledger.carriers) # .8+.4>1: expensive does not mean illegal.
    def test_negative_floor_does_not_force_balance_or_block_add(self):
        gw=self.fixture();f=self.frame(gw);f['own_view']['inv']['UP']=5.;f['own_view']['cost']=4.
        f['own_view']['un']['UP'].append((5.,.8))
        self.commit(gw,f,[self.op()]);self.assertIn('UP_1',gw.ledger.carriers)
    def test_non_displayed_passive_quote_allowed(self):
        gw=self.fixture();self.commit(gw,self.frame(gw),[self.op(price=.1)])
    def test_improve_bid_without_existing_queue_allowed(self):
        gw=self.fixture();self.commit(gw,self.frame(gw),[self.op(price=.52)])
    def test_marketable_passive_rejected_not_converted_to_active(self):
        for price in (.54,.55):
            gw=self.fixture()
            with self.assertRaisesRegex(PlanRejected,'POSTONLY'):self.commit(gw,self.frame(gw),[self.op(price=price)])
    def test_missing_book_support_censored(self):
        gw=self.fixture();f=self.frame(gw);f['book']['asks']={}
        with self.assertRaisesRegex(PlanRejected,'BOOK_SUPPORT_UNKNOWN_NOT_HOLD'):self.commit(gw,f,[self.op()])
    def test_five_parallel_same_side_and_same_tick_orders_allowed(self):
        gw=self.fixture();ops=[self.op(key=f'UP_{i}') for i in range(1,6)]
        self.commit(gw,self.frame(gw),ops);self.assertEqual(len(gw.ledger.carriers),5)
    def test_old_ledger_four_pool_is_not_changed(self):
        old=EconomicGrantLedger(100.);old.issue(Grant(1,'old','UP',0.,110.,100.,'OLD'))
        for i in range(4):old.reserve(str(i),1,'PASSIVE',18.,.1,0.,now_ms=1,market_end_ms=300000)
        with self.assertRaisesRegex(ValueError,'pool full'):old.reserve('fifth',1,'PASSIVE',18.,.1,0.,now_ms=1,market_end_ms=300000)
    def test_engineering_resource_bound_is_explicit_censor(self):
        gw=self.fixture(4);ops=[self.op(key=f'UP_{i}') for i in range(1,6)]
        with self.assertRaisesRegex(PlanRejected,'RESOURCE_CENSOR'):self.commit(gw,self.frame(gw),ops)
        self.assertEqual(len(gw.ledger.carriers),0)
    def test_nondefault_resource_profile_in_snapshot(self):
        self.assertNotEqual(self.fixture(4).snapshot_id(),self.fixture(32).snapshot_id())
    def test_two_active_reservations_not_artificial_single_pool(self):
        ledger=self.fixture().ledger
        for i in (1,2):ledger.reserve(f'a{i}',1,'ACTIVE',2.,.1,0.,now_ms=200000,market_end_ms=300000)
        self.assertEqual(len(ledger.carriers),2) # Ledger only. Native Active still unsupported.
    def test_native_active_is_unsupported_not_hold(self):
        gw=self.fixture();f=self.frame(gw);o=self.op(qty=2.);o['route']='ACTIVE'
        with self.assertRaisesRegex(PlanRejected,'ACTIVE_UNSUPPORTED_NOT_HOLD'):self.commit(gw,f,[o])
    def test_minimum_is_not_fixed_or_cap(self):
        for q in (18.,30.,55.,100.):
            gw=self.fixture();self.commit(gw,self.frame(gw),[self.op(qty=q)]);self.assertEqual(gw.ledger.carriers['UP_1'].qty,q)
    def test_new_order_minima_still_declared(self):
        for q,p in ((17.,.1),(18.,.05)):
            gw=self.fixture()
            with self.assertRaises(PlanRejected):self.commit(gw,self.frame(gw),[self.op(qty=q,price=p)])
    def test_partial_fill_below_original_minimum_preserved(self):
        gw=self.fixture();self.reserve_sent(gw);gw.receipt('UP_1',filled=.5,payment=.05,evidence='NATIVE_FIXTURE')
        self.assertEqual(gw.own_state()['inventory']['UP'],.5)
    def test_original_qty_not_clipped_to_cash(self):
        gw=self.fixture()
        with self.assertRaises(PlanRejected):self.commit(gw,self.frame(gw),[self.op(qty=100.,price=.52)])
        self.assertFalse(gw.ledger.carriers)
    def test_explicit_cash_reallocation_under_same_cap(self):
        gw=self.fixture();u=(BudgetAllocation(1,65.,130.,'EXPLICIT_PLAN'),BudgetAllocation(2,35.,110.,'EXPLICIT_PLAN'))
        self.commit(gw,self.frame(gw),[],u)
        self.assertEqual(gw.ledger.capital,100.)
        self.assertEqual(gw.ledger.grants[1].cash_limit,65.)
        self.assertEqual(gw.ledger.grants[1].generation,'same-generation')
    def test_budget_updates_and_new_orders_atomic(self):
        gw=self.fixture();before=gw.snapshot_id();u=(BudgetAllocation(1,65.,110.,'PLAN'),BudgetAllocation(2,35.,110.,'PLAN'))
        with self.assertRaises(PlanRejected):self.commit(gw,self.frame(gw),[self.op(qty=500.)],u)
        self.assertEqual(gw.snapshot_id(),before)
    def test_total_cap_cannot_be_increased(self):
        gw=self.fixture();u=(BudgetAllocation(1,70.,110.,'PLAN'),BudgetAllocation(2,40.,110.,'PLAN'))
        with self.assertRaisesRegex(PlanRejected,'total capital'):self.commit(gw,self.frame(gw),[],u)
    def test_pending_cash_cannot_be_reallocated(self):
        gw=self.fixture();self.reserve_sent(gw,q=30.,p=.5)
        before=gw.snapshot_id()
        with self.assertRaisesRegex(ValueError,'pending cash'):gw.ledger.reallocate((BudgetAllocation(1,14.,110.,'PLAN'),BudgetAllocation(2,86.,110.,'PLAN')))
        self.assertEqual(gw.snapshot_id(),before)
    def test_spent_cash_and_acquired_quantity_not_recycled(self):
        gw=self.fixture();self.reserve_sent(gw,q=30.,p=.5)
        gw.receipt('UP_1',filled=30.,payment=15.,terminal=True,evidence='FINAL')
        with self.assertRaisesRegex(ValueError,'pending cash'):gw.ledger.reallocate((BudgetAllocation(1,14.,110.,'PLAN'),))
        with self.assertRaisesRegex(ValueError,'quantity'):gw.ledger.reallocate((BudgetAllocation(1,50.,29.,'PLAN'),))
    def test_cash_budget_can_move_after_confirmed_zero_terminal(self):
        gw=self.fixture();self.reserve_sent(gw,q=30.,p=.5)
        gw.receipt('UP_1',filled=0.,payment=0.,terminal=True,evidence='CANCELLED_NATIVE')
        gw.ledger.reallocate((BudgetAllocation(1,10.,110.,'PLAN'),BudgetAllocation(2,90.,110.,'PLAN')))
        self.assertEqual(gw.ledger.grants[2].cash_limit,90.)
    def test_cancel_pending_retains_capacity_until_late_fill_terminal(self):
        gw=self.fixture();self.reserve_sent(gw,q=30.,p=.5);gw.ledger.request_cancel('UP_1')
        self.assertEqual(gw.ledger.account(1)['reserved_cash'],15.)
        gw.receipt('UP_1',filled=1.,payment=.5,evidence='LATE_CANONICAL')
        self.assertEqual(gw.ledger.account(1)['reserved_cash'],14.5)
        gw.receipt('UP_1',filled=1.,payment=.5,terminal=True,evidence='TERMINAL_CANONICAL')
        self.assertEqual(gw.ledger.account(1)['reserved_cash'],0.)
    def test_duplicate_canonical_fill_does_not_double_credit(self):
        gw=self.fixture();self.reserve_sent(gw);gw.receipt('UP_1',filled=1.,payment=.1,evidence='ONE')
        before=gw.own_state();gw.receipt('UP_1',filled=1.,payment=.1,evidence='SAME')
        self.assertEqual(gw.own_state(),before)
    def test_unowned_receipt_rejected(self):
        gw=self.fixture()
        with self.assertRaises(ValueError):gw.receipt('ghost',filled=1.,payment=.1,evidence='NO_OWNER')
    def test_self_cross_including_cancel_pending_retained(self):
        gw=self.fixture();self.reserve_sent(gw,q=30.,p=.5);gw.ledger.request_cancel('UP_1')
        with self.assertRaisesRegex(ValueError,'own cross'):gw.ledger.reserve('DOWN_2',2,'PASSIVE',18.,.51,0.,now_ms=200000,market_end_ms=300000)
    def test_stale_world_profile_rejected(self):
        gw=self.fixture();f=self.frame(gw);e=envelope(f,Producer,[self.op()]);f['world_profile']['max_live_owners']=4
        with self.assertRaisesRegex(PlanRejected,'STALE_WORLD'):validate_envelope(f,e,Producer.policy_id,Producer.continuation_id)
    def test_continuation_cannot_switch_unannounced(self):
        gw=self.fixture();f=self.frame(gw);e=envelope(f,Producer,[self.op()]);e.plan=replace(e.plan,continuation_id='OLD_FRAGMENT')
        with self.assertRaisesRegex(PlanRejected,'CONTINUATION'):validate_envelope(f,e,Producer.policy_id,Producer.continuation_id)
    def test_native_owner_must_be_maintained_explicitly(self):
        gw=self.fixture();self.reserve_sent(gw);f=self.frame(gw);e=envelope(f,Producer,[]);e.plan=replace(e.plan,actions=())
        with self.assertRaisesRegex(PlanRejected,'WHOLE_LIVE_OWNER'):validate_envelope(f,e,Producer.policy_id,Producer.continuation_id)
    def test_order_age_does_not_force_cancel(self):
        gw=self.fixture();self.reserve_sent(gw);f=self.frame(gw,290000);e=envelope(f,Producer,[])
        self.assertEqual(e.plan.actions[0].kind,'KEEP');validate_envelope(f,e,Producer.policy_id,Producer.continuation_id)
    def test_disappeared_book_level_does_not_force_cancel(self):
        gw=self.fixture();self.reserve_sent(gw,p=.1);f=self.frame(gw);self.assertNotIn(.1,f['book']['bids'])
        e=envelope(f,Producer,[]);validate_envelope(f,e,Producer.policy_id,Producer.continuation_id)
        self.assertEqual(e.plan.actions[0].kind,'KEEP')
    def test_hidden_ttl_origin_rejected(self):
        gw=self.fixture();self.reserve_sent(gw);f=self.frame(gw);f['cancellable']['UP_1']=True
        e=envelope(f,Producer,[dict(kind='CANCEL',key='UP_1',origin='TTL')])
        with self.assertRaisesRegex(PlanRejected,'HIDDEN_LEGACY'):validate_envelope(f,e,Producer.policy_id,Producer.continuation_id)
    def test_frame_export_is_serializable_and_has_full_own_authority(self):
        import json
        gw=self.fixture();f=self.frame(gw);export=full_input(f)
        json.dumps(export,allow_nan=False)
        self.assertIn('own_view',export);self.assertIn('authority',export);self.assertIn('snapshots',export)
        self.assertNotIn('winner',export);self.assertNotIn('target',export);self.assertNotIn('updates',export)
    def test_off_tick_rejected_instead_of_rewritten(self):
        gw=self.fixture()
        with self.assertRaisesRegex(PlanRejected,'GRID'):self.commit(gw,self.frame(gw),[self.op(price=.105)])
    def test_saved_market_window_can_supply_missing_native_start(self):
        self.assertEqual(verified_market_window({'window_end_ms':300000},[0,300000],'a'*64),(0,300000))
    def test_disagreeing_native_end_or_missing_provenance_rejected(self):
        with self.assertRaises(ValueError):verified_market_window({'window_end_ms':299999},[0,300000],'a'*64)
        with self.assertRaises(ValueError):verified_market_window({'window_end_ms':300000},[0,300000],'')
    def test_same_policy_frame_snapshot_does_not_reuse_old_capacity(self):
        gw=self.fixture();f=self.frame(gw);e=envelope(f,Producer,[self.op()]);gw.ledger.reallocate((BudgetAllocation(1,40.,110.,'EXPLICIT'),))
        with self.assertRaisesRegex(PlanRejected,'STALE_OWN'):gw.commit(e.plan,now_ms=f['t'],market_end_ms=f['end'])


if __name__=='__main__':unittest.main()
