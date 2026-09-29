"""Exact-size interface tests. All quantities/grants below are disclosed fixtures."""
import unittest
from dataclasses import replace

from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant
from tools.pair_core_objective_quantity_planner_v1 import commit_reservation, snapshot
from tools.minimal_student_quantity_seam_v1 import (
    QuantityIntent, VenueGrid, raw_passive_levels, prepare_exact,
)

GRID=VenueGrid(.01,.01,.01,0.,'FROZEN_RESEARCH_BACKEND_GRID_NOT_LIVE_CERTIFICATION')


class ExactQuantityTests(unittest.TestCase):
    def ledger(self,qty=110.,cash=50.,side='UP'):
        x=EconomicGrantLedger(100.)
        x.issue(Grant(1,'fixture',side,0.,qty,cash,'EXPLICIT_COMPONENT_FIXTURE_NOT_TARGET'))
        return x

    def plan(self,x,p=.5,q=30.,route='PASSIVE',asset='BTC',side='UP',key='p',grid=GRID):
        return prepare_exact(x,asset,side,p,QuantityIntent(1,q,route,'TEST_ONLY'),
            key=key,quote_reference='q1',now_ms=1,market_end_ms=300000,grid=grid)

    def commit(self,x,r):
        self.assertIsNotNone(r.plan,r.reason)
        commit_reservation(x,r.plan,quote_reference='q1',now_ms=1)

    def test_price_domain_not_filtered_by_ticket_or_12cap(self):
        b={'bids':{.02:5.,.04:8.,.06:10.,.5:1.,.01:0.},'asks':{.98:4.,.96:7.}}
        self.assertEqual(raw_passive_levels(b,'UP'),[.5,.06,.04,.02])
        self.assertEqual(raw_passive_levels(b,'DOWN'),[.04,.02])

    def test_qty18_price_floor_tick06(self):
        self.assertIsNone(self.plan(self.ledger(),p=.05,q=18.).plan)
        self.assertEqual(self.plan(self.ledger(),p=.06,q=18.).plan.quantity,18.)

    def test_qty30_price_floor_tick04(self):
        self.assertIsNone(self.plan(self.ledger(),p=.03,q=30.).plan)
        self.assertEqual(self.plan(self.ledger(),p=.04,q=30.).plan.quantity,30.)

    def test_qty55_price_floor_tick02(self):
        self.assertIsNone(self.plan(self.ledger(),p=.01,q=55.).plan)
        self.assertEqual(self.plan(self.ledger(),p=.02,q=55.).plan.quantity,55.)

    def test_identical_price_different_size_is_different_action(self):
        self.assertIsNone(self.plan(self.ledger(),p=.02,q=30.).plan)
        self.assertIsNotNone(self.plan(self.ledger(),p=.02,q=55.).plan)

    def test_high_size_not_implicitly_capped_at18(self):
        for q in (30.,55.,100.):
            self.assertEqual(self.plan(self.ledger(),q=q).plan.quantity,q)

    def test_insufficient_cash_never_clips(self):
        x=self.ledger(cash=10.)
        before=snapshot(x);r=self.plan(x,q=30.)
        self.assertEqual(r.reason,'EXACT_REQUEST_UNAFFORDABLE_NOT_RESIZED')
        self.assertEqual(snapshot(x),before)

    def test_insufficient_qty_never_clips(self):
        self.assertEqual(self.plan(self.ledger(qty=40.),q=55.).reason,
                         'EXACT_REQUEST_UNAFFORDABLE_NOT_RESIZED')

    def test_no_grant_is_not_inferred_from_book_or_target(self):
        self.assertEqual(self.plan(EconomicGrantLedger(100.)).reason,'NO_EXPLICIT_GRANT')

    def test_missing_intent_not_filled_with_default18(self):
        x=self.ledger()
        r=prepare_exact(x,'BTC','UP',.5,None,key='p',quote_reference='q1',now_ms=1,
            market_end_ms=300000,grid=GRID)
        self.assertEqual(r.reason,'MISSING_EXPLICIT_QUANTITY_INTENT')

    def test_asset_minima_are_distinct(self):
        self.assertIsNotNone(self.plan(self.ledger(),asset='ETH',q=12.).plan)
        self.assertIsNone(self.plan(self.ledger(),asset='BTC',q=12.).plan)

    def test_active_unsupported_is_not_hold(self):
        self.assertEqual(self.plan(self.ledger(),route='ACTIVE',q=2.).reason,'UNSUPPORTED_CHANNEL')

    def test_future_active_route_does_not_inherit_passive_minima(self):
        x=self.ledger()
        r=prepare_exact(x,'BTC','UP',.02,QuantityIntent(1,2.,'ACTIVE','COMPONENT_ONLY'),
            key='a',quote_reference='q1',now_ms=1,market_end_ms=300000,
            grid=GRID,allowed_routes=('ACTIVE',))
        self.assertEqual(r.plan.quantity,2.)

    def test_wrong_side_authority_rejected(self):
        self.assertEqual(self.plan(self.ledger(side='DOWN')).reason,'GRANT_SIDE_MISMATCH')

    def test_grid_rejects_instead_of_rounding_price_or_qty(self):
        self.assertIsNone(self.plan(self.ledger(),p=.025,q=55.).plan)
        self.assertIsNone(self.plan(self.ledger(),q=30.001).plan)

    def test_invalid_grid(self):
        for g in (replace(GRID,tick=0.),replace(GRID,quantity_step=0.),replace(GRID,provenance='')):
            self.assertEqual(self.plan(self.ledger(),grid=g).reason,'INVALID_DECLARED_VENUE_GRID')

    def test_nonfinite_and_boolean_qty_rejected(self):
        for q in (0.,-1.,True,float('nan'),float('inf')):
            self.assertIsNone(self.plan(self.ledger(),q=q).plan)

    def test_pending_reservation_prevents_second_copy(self):
        x=self.ledger(qty=55.);r=self.plan(x,q=55.);self.commit(x,r)
        self.assertIsNone(self.plan(x,q=55.,key='p2').plan)
        self.assertEqual(x.account(1)['reserved_qty'],55.)

    def test_partial_fill_below_new_order_minimum_is_valid(self):
        x=self.ledger(qty=55.);self.commit(x,self.plan(x,q=55.,p=.02))
        x.confirm_cumulative('p',.5,.01)
        self.assertEqual(x.account(1)['add_filled'],.5)
        self.assertEqual(x.account(1)['reserved_qty'],54.5)
        self.assertIsNone(self.plan(x,q=18.,key='p2').plan)

    def test_cancel_intent_keeps_claim_until_final_receipt(self):
        x=self.ledger(qty=55.);self.commit(x,self.plan(x,q=55.,p=.02))
        x.request_cancel('p');self.assertEqual(x.account(1)['reserved_qty'],55.)
        x.confirm_terminal('p',filled=.5,payment=.01)
        self.assertEqual(x.account(1)['reserved_qty'],0.)
        self.assertEqual(self.plan(x,q=54.5,p=.02,key='p2').plan.quantity,54.5)

    def test_duplicate_receipt_does_not_create_capacity(self):
        x=self.ledger(qty=55.);self.commit(x,self.plan(x,q=55.,p=.02))
        x.confirm_cumulative('p',2.,.04);before=snapshot(x)
        x.confirm_cumulative('p',2.,.04);self.assertEqual(snapshot(x),before)

    def test_late_after_reconciled_terminal_is_error_not_double_fill(self):
        x=self.ledger();self.commit(x,self.plan(x));x.confirm_terminal('p',filled=1.,payment=.5)
        with self.assertRaises(ValueError):x.confirm_cumulative('p',2.,1.)

    def test_stale_plan_rejected(self):
        x=self.ledger();r=self.plan(x);self.commit(x,self.plan(x,key='other'))
        with self.assertRaisesRegex(ValueError,'snapshot changed'):self.commit(x,r)

    def test_pool_cap_not_bypassed(self):
        x=self.ledger(qty=150.,cash=50.)
        for i in range(4):self.commit(x,self.plan(x,p=.04,q=30.,key=str(i)))
        self.assertIn('pool full',self.plan(x,p=.04,q=30.,key='fifth').reason)

    def test_pending_self_cross_not_bypassed(self):
        x=self.ledger(qty=30.,cash=30.)
        self.commit(x,self.plan(x,p=.4,q=30.));x.request_cancel('p')
        x.issue(Grant(2,'second','DOWN',0.,30.,30.,'EXPLICIT_FIXTURE'))
        r=prepare_exact(x,'BTC','DOWN',.7,QuantityIntent(2,30.,'PASSIVE','FIXTURE'),
            key='opposite',quote_reference='q2',now_ms=1,market_end_ms=300000,grid=GRID)
        self.assertIn('potential own cross',r.reason)


if __name__=='__main__':unittest.main()
