from copy import deepcopy
from dataclasses import replace
import unittest

from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant
from tools.pair_core_objective_quantity_planner_v1 import (
    ExecutionLimits, prepare, commit_reservation, snapshot,
)

# Disclosed diagnostic fixture, not a venue specification or trained parameters.
LIMITS = ExecutionLimits(.01, .01, .01, 0., 12.)


class ObjectiveQuantityTests(unittest.TestCase):
    def ledger(self, repair=12., add=0., cash=1., side='UP'):
        x=EconomicGrantLedger(20.)
        x.issue(Grant(1,'g1',side,repair,add,cash,'EXPLICIT_TEST_INTENT_NOT_MODEL'))
        return x

    def plan(self, x, p=.4, route='PASSIVE', key='child', limits=LIMITS,
             parent=1, fee=0., now=290000):
        return prepare(x,parent,route,p,key=key,quote_reference='quote-v1',
            now_ms=now,market_end_ms=300000,limits=limits,fee_cap=fee)

    def commit(self, x, result):
        self.assertIsNotNone(result.plan, result.reason)
        commit_reservation(x,result.plan,quote_reference='quote-v1',now_ms=result.plan.now_ms)

    def test_all_four_channels_use_explicit_grant(self):
        for repair,add in [(3.,0.),(0.,3.)]:
            for route in ('PASSIVE','ACTIVE'):
                x=self.ledger(repair,add,cash=2.)
                r=self.plan(x,p=.4,route=route);self.assertEqual(r.plan.quantity,3.)
                self.commit(x,r);a=x.confirm_terminal('child',filled=3.,payment=1.2)
                self.assertEqual(a.repair_increment,3. if repair else 0.)
                self.assertEqual(a.overflow_increment,3. if add else 0.)
                self.assertTrue(x.invariants())

    def test_low_price_keeps_same_cash_and_child_caps(self):
        x=self.ledger();r=self.plan(x,p=.07)
        self.assertGreater(1/.07,12.)
        self.assertEqual(r.plan.quantity,12.)
        self.assertAlmostEqual(r.plan.reserved_child_cost,.84)
        self.commit(x,r);self.assertEqual(x.account(1)['repair_paid'],0.)

    def test_small_objective_does_not_force_whole_ticket_spend(self):
        x=self.ledger(repair=1.);r=self.plan(x,p=.2)
        self.assertEqual(r.plan.quantity,1.)
        self.assertEqual(r.plan.reserved_child_cost,.2)

    def test_cash_ceiling_changes_quantity_for_more_expensive_route(self):
        x=self.ledger();p=self.plan(x,p=.4);a=self.plan(x,p=.55,route='ACTIVE')
        self.assertEqual(p.plan.quantity,2.5);self.assertEqual(a.plan.quantity,1.81)
        self.assertLessEqual(a.plan.reserved_child_cost,1.)
        self.assertEqual(x.carriers,{})

    def test_shared_reservations_reduce_both_free_cash_and_qty(self):
        x=self.ledger();x.reserve('p',1,'PASSIVE',4.,.1,0.,now_ms=1,market_end_ms=300000)
        r=self.plan(x,p=.05,route='ACTIVE')
        self.assertEqual(r.plan.authorized_free_quantity,8.)
        self.assertEqual(r.plan.quantity,8.)
        self.assertAlmostEqual(r.plan.free_cash_before_child_fee,.6)

    def test_partial_fill_does_not_reissue_already_claimed_shares(self):
        x=self.ledger();x.reserve('p',1,'PASSIVE',4.,.1,0.,now_ms=1,market_end_ms=300000)
        x.confirm_cumulative('p',2.,.2)
        r=self.plan(x,p=.05,route='ACTIVE')
        self.assertEqual(r.plan.quantity,8.)
        self.assertEqual(x.account(1)['repair_remaining'],10.)

    def test_cancel_intent_never_releases_sibling_claim(self):
        x=self.ledger();x.reserve('p',1,'PASSIVE',4.,.1,0.,now_ms=1,market_end_ms=300000)
        x.request_cancel('p');self.assertEqual(self.plan(x,p=.05).plan.quantity,8.)
        x.confirm_terminal('p',filled=1.,payment=.1)
        self.assertEqual(self.plan(x,p=.05).plan.quantity,11.)

    def test_full_service_does_not_create_fresh_grant(self):
        x=self.ledger(repair=2.,cash=1.);r=self.plan(x,p=.4);self.commit(x,r)
        x.confirm_terminal('child',filled=2.,payment=.8)
        self.assertEqual(self.plan(x,p=.05,key='next').reason,'NO_UNRESERVED_QUANTITY_AUTHORITY')

    def test_missing_authority_is_not_inferred(self):
        x=EconomicGrantLedger(20.)
        self.assertEqual(self.plan(x).reason,'NO_EXPLICIT_GRANT')

    def test_two_alternative_plans_cannot_double_commit(self):
        x=self.ledger();p=self.plan(x,key='p');a=self.plan(x,p=.5,route='ACTIVE',key='a')
        self.commit(x,p);old=snapshot(x)
        with self.assertRaisesRegex(ValueError,'snapshot changed'):self.commit(x,a)
        self.assertEqual(snapshot(x),old)

    def test_receipt_makes_saved_plan_stale(self):
        x=self.ledger();x.reserve('p',1,'PASSIVE',1.,.1,0.,now_ms=1,market_end_ms=300000)
        r=self.plan(x,p=.2,route='ACTIVE');x.confirm_cumulative('p',.5,.05)
        with self.assertRaisesRegex(ValueError,'snapshot changed'):self.commit(x,r)

    def test_quote_or_clock_change_requires_new_plan(self):
        x=self.ledger();p=self.plan(x).plan
        for quote,now in [('q2',p.now_ms),('quote-v1',p.now_ms+1)]:
            with self.assertRaisesRegex(ValueError,'quote/decision'):
                commit_reservation(x,p,quote_reference=quote,now_ms=now)
        self.assertEqual(x.carriers,{})

    def test_caller_cannot_enlarge_prepared_quantity(self):
        x=self.ledger();p=self.plan(x).plan
        with self.assertRaisesRegex(ValueError,'current authorized'):
            commit_reservation(x,replace(p,quantity=p.quantity+1.),quote_reference='quote-v1',now_ms=p.now_ms)
        self.assertEqual(x.carriers,{})

    def test_fee_reserve_not_spendable(self):
        x=self.ledger();p=self.plan(x,p=.5,fee=.1).plan
        self.assertEqual(p.quantity,1.8);self.assertAlmostEqual(p.reserved_child_cost,1.)

    def test_actual_min_notional_can_still_block_low_price(self):
        x=self.ledger();r=self.plan(x,p=.07,limits=replace(LIMITS,min_notional=1.))
        self.assertEqual(r.reason,'BELOW_DECLARED_MIN_NOTIONAL')

    def test_quantity_rounds_down_not_price(self):
        x=self.ledger();p=self.plan(x,p=.3).plan
        self.assertEqual(p.quantity,3.33);self.assertLess(p.reserved_child_cost,1.)
        self.assertEqual(self.plan(x,p=.305).reason,'PRICE_OFF_DECLARED_TICK')

    def test_end_prohibits_new_claim_not_arbitrary_180(self):
        x=self.ledger();self.assertIsNotNone(self.plan(x,now=299999).plan)
        self.assertEqual(self.plan(x,now=300000).reason,'MARKET_ENDED')

    def test_unrelated_repair_debt_does_not_veto_explicit_add(self):
        x=self.ledger(repair=3.,cash=2.)
        x.reserve('p',1,'PASSIVE',1.,.2,0.,now_ms=1,market_end_ms=300000)
        x.issue(Grant(2,'g2','DOWN',0.,2.,2.,'SEPARATE_EXPLICIT_ADD'))
        r=self.plan(x,p=.7,route='ACTIVE',parent=2);self.commit(x,r)
        self.assertEqual(x.account(1)['repair_remaining'],3.)
        self.assertEqual(x.account(2)['reserved_qty'],2.)

    def test_pending_opposite_self_cross_remains_blocked(self):
        x=self.ledger(repair=3.,cash=2.)
        x.reserve('p',1,'PASSIVE',1.,.4,0.,now_ms=1,market_end_ms=300000)
        x.request_cancel('p');x.issue(Grant(2,'g2','DOWN',0.,2.,2.,'ADD'))
        r=self.plan(x,p=.7,route='ACTIVE',parent=2)
        self.assertIn('potential own cross',r.reason)

    def test_pool_capacity_is_not_bypassed(self):
        x=self.ledger(repair=12.,cash=10.)
        for i in range(4):x.reserve('p'+str(i),1,'PASSIVE',1.,.2,0.,now_ms=1,market_end_ms=300000)
        self.assertIn('pool full',self.plan(x,p=.2).reason)
        self.assertIsNotNone(self.plan(x,p=.3,route='ACTIVE').plan)

    def test_planning_is_inert_and_repeatable(self):
        x=self.ledger();before=snapshot(x);r=self.plan(x)
        self.assertEqual(r,self.plan(x));self.assertEqual(before,snapshot(x))
        self.assertEqual(x.carriers,{})

    def test_composite_grant_keeps_confirmed_allocation_separate(self):
        x=self.ledger(repair=1.,add=2.,cash=2.)
        r=self.plan(x,p=.5,route='ACTIVE');self.assertEqual(r.plan.quantity,3.)
        self.commit(x,r);self.assertEqual(x.account(1)['repair_paid'],0.)
        a=x.confirm_terminal('child',filled=3.,payment=1.5)
        self.assertEqual((a.repair_increment,a.overflow_increment),(1.,2.))

    def test_previous_valid_whole_ticket_cases_preserved(self):
        for p,q in [(.1,10.),(.2,5.),(.25,4.),(.5,2.)]:
            self.assertEqual(self.plan(self.ledger(),p=p).plan.quantity,q)

    def test_invalid_input_and_unaffordable_fees_block(self):
        x=self.ledger()
        for p in [0.,1.,float('nan'),True]:self.assertIsNone(self.plan(x,p=p).plan)
        self.assertEqual(self.plan(x,fee=1.).reason,'NO_UNRESERVED_CASH_AUTHORITY')
        self.assertIsNone(self.plan(x,limits=replace(LIMITS,quantity_step=0.)).plan)


if __name__=='__main__':unittest.main()
