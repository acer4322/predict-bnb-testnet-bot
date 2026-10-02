import itertools
import unittest
from dataclasses import replace
from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant
from tools.pair_core_objective_quantity_planner_v1 import snapshot
from tools.pair_core_authorized_outcome_preview_v1 import (
    InitialPortfolio, EndpointAuthority, ProposedOrder, preview,
    commit_reservations, payoff_projection)


class AuthorizedOutcomeTests(unittest.TestCase):
    def setUp(self):
        grants = [Grant(1,'repair-generation','DOWN',24.,0.,25.,'EXPLICIT_TEST_REPAIR'),
                  Grant(2,'add-generation','UP',0.,18.,20.,'EXPLICIT_TEST_ADD')]
        self.ledger = EconomicGrantLedger(sum(g.cash_limit for g in grants))
        for g in grants: self.ledger.issue(g)
        self.base = InitialPortfolio(120.,80.,85.)
        self.bounds = EndpointAuthority(0.,-6.,'DECLARED_TEST_PAYOFF_ALLOWANCE')
        self.r = ProposedOrder('repair',1,'PASSIVE',24.,.19)
        self.a = ProposedOrder('add',2,'ACTIVE',18.,.62)
        self.args = dict(quote_reference='fixture_quote_v1',now_ms=200000,market_end_ms=300000)

    def inspect(self, orders, **overrides):
        args=dict(self.args);args.update(overrides)
        return preview(self.ledger,self.base,self.bounds,'BTC',orders,**args)

    def commit(self, orders):
        p=self.inspect(orders)
        return commit_reservations(self.ledger,self.base,self.bounds,'BTC',orders,inspected=p,**self.args)

    def partial(self):
        self.commit([self.r])
        self.ledger.confirm_cumulative('repair',15.,2.85)

    def test_full_pair_passes_but_add_first_breaks_declared_endpoint(self):
        p=self.inspect([self.r,self.a])
        self.assertTrue(p['full_fill_within_bounds'])
        self.assertFalse(p['accepted'])
        self.assertAlmostEqual(p['projection']['coordinate_worst']['DOWN'],-16.16)
        self.assertEqual(self.ledger.carriers,{})

    def test_partial_repair_admits_add_before_repair_completion(self):
        self.partial();p=self.inspect([self.a]);self.assertTrue(p['accepted'])
        self.assertAlmostEqual(p['projection']['coordinate_worst']['DOWN'],-4.01)
        self.commit([self.a]);self.assertEqual(self.ledger.account(1)['repair_remaining'],9.)
        self.assertEqual(self.ledger.account(2)['reserved_qty'],18.)
        self.assertEqual(len(self.ledger.grants),2)

    def test_same_progress_different_price_changes_allowed_option(self):
        self.partial();self.assertTrue(self.inspect([self.a])['accepted'])
        bad=self.inspect([replace(self.a,limit_price=.8)])
        self.assertEqual(bad['status'],'DECLARED_ENDPOINT_AUTHORITY_EXCEEDED')
        self.assertAlmostEqual(bad['projection']['coordinate_worst']['DOWN'],-7.25)

    def test_bounds_are_caller_input_not_positive_floor_law(self):
        self.bounds=EndpointAuthority(-50.,-50.,'SEPARATELY_AUTHORIZED_RISK')
        self.assertTrue(self.inspect([self.r,self.a])['accepted'])

    def test_pending_repair_is_not_credit(self):
        self.commit([self.r]);p=self.inspect([self.a])
        self.assertFalse(p['accepted']);self.assertEqual(p['projection']['confirmed']['DOWN'],-5.)

    def test_cancel_request_keeps_quantity_and_payoff_risk(self):
        self.commit([self.r]);before=payoff_projection(self.ledger,self.base)
        self.ledger.request_cancel('repair');after=payoff_projection(self.ledger,self.base)
        self.assertEqual(before['coordinate_worst'],after['coordinate_worst'])
        self.assertEqual(self.ledger.account(1)['reserved_qty'],24.)

    def test_terminal_without_fill_releases_risk_not_creates_credit(self):
        self.commit([self.r]);self.ledger.request_cancel('repair')
        self.ledger.confirm_terminal('repair',filled=0.,payment=0.)
        p=payoff_projection(self.ledger,self.base)
        self.assertEqual(p['confirmed'],dict(UP=35.,DOWN=-5.))
        self.assertEqual(p['coordinate_worst'],p['confirmed'])
        self.assertEqual(self.ledger.account(1)['repair_remaining'],24.)

    def test_partial_size_below_passive_minimum_is_a_valid_receipt(self):
        self.commit([self.r]);self.ledger.confirm_cumulative('repair',1.,.19)
        self.assertEqual(self.ledger.account(1)['repair_paid'],1.)

    def test_duplicate_cumulative_receipt_does_not_repeat_protection(self):
        self.partial();old=snapshot(self.ledger)
        self.ledger.confirm_cumulative('repair',15.,2.85)
        self.assertEqual(snapshot(self.ledger),old)
        self.assertAlmostEqual(payoff_projection(self.ledger,self.base)['confirmed']['DOWN'],7.15)

    def test_terminal_reconciles_late_fill_before_release(self):
        self.partial();self.ledger.request_cancel('repair')
        self.ledger.confirm_terminal('repair',filled=16.,payment=3.04)
        self.assertEqual(self.ledger.account(1)['reserved_qty'],0.)
        self.assertEqual(self.ledger.account(1)['repair_remaining'],8.)
        self.assertAlmostEqual(payoff_projection(self.ledger,self.base)['confirmed']['DOWN'],7.96)

    def test_reservation_atomic_when_second_order_is_invalid(self):
        before=snapshot(self.ledger)
        p=self.inspect([self.r,replace(self.a,parent_id=999)])
        self.assertEqual(p['status'],'NO_EXPLICIT_GRANT');self.assertEqual(snapshot(self.ledger),before)

    def test_stale_receipt_snapshot_blocks_commit(self):
        self.partial();p=self.inspect([self.a]);before=snapshot(self.ledger)
        self.ledger.confirm_cumulative('repair',16.,3.04)
        with self.assertRaises(ValueError):
            commit_reservations(self.ledger,self.base,self.bounds,'BTC',[self.a],inspected=p,**self.args)
        self.assertNotIn('add',self.ledger.carriers)
        self.assertNotEqual(before,snapshot(self.ledger))

    def test_stale_quote_blocks_commit(self):
        self.partial();p=self.inspect([self.a]);args=dict(self.args,quote_reference='changed')
        with self.assertRaises(ValueError):
            commit_reservations(self.ledger,self.base,self.bounds,'BTC',[self.a],inspected=p,**args)

    def test_changed_external_risk_authority_blocks_commit(self):
        self.partial();p=self.inspect([self.a]);new=EndpointAuthority(0.,-10.,'NEW_AUTHORITY')
        with self.assertRaises(ValueError):
            commit_reservations(self.ledger,self.base,new,'BTC',[self.a],inspected=p,**self.args)

    def test_missing_endpoint_authority_is_not_inferred(self):
        with self.assertRaises(ValueError):
            preview(self.ledger,self.base,None,'BTC',[self.r],**self.args)

    def test_two_active_is_capacity_not_target_hold(self):
        p=self.inspect([replace(self.r,route='ACTIVE'),self.a])
        self.assertEqual(p['status'],'EXECUTION_CAPACITY_UNSUPPORTED')
        self.assertEqual(self.ledger.carriers,{})

    def test_sequential_sends_keep_two_economic_work_items(self):
        r=replace(self.r,route='ACTIVE',quantity=2.)
        self.commit([r]);self.ledger.confirm_terminal('repair',filled=2.,payment=.38)
        self.commit([replace(self.a,quantity=1.)])
        self.assertEqual(self.ledger.account(1)['repair_remaining'],22.)
        self.assertEqual(self.ledger.account(2)['reserved_qty'],1.)

    def test_eth_and_btc_passive_minima_remain_distinct(self):
        o=replace(self.r,quantity=12.,limit_price=.20)
        eth=preview(self.ledger,self.base,self.bounds,'ETH',[o],**self.args)
        btc=self.inspect([o])
        self.assertTrue(eth['accepted']);self.assertEqual(btc['status'],'SIZE_SPECIFICATION_REJECTED')

    def test_active_does_not_inherit_passive_size_minimum(self):
        self.assertTrue(self.inspect([replace(self.a,quantity=1.)])['accepted'])

    def test_reserved_sibling_cannot_reuse_same_authority(self):
        self.commit([self.r]);p=self.inspect([ProposedOrder('sibling',1,'ACTIVE',1.,.19)])
        self.assertEqual(p['status'],'RESERVATION_AUTHORITY_REJECTED')

    def test_pending_opposite_order_own_cross_is_separate(self):
        self.partial();p=self.inspect([replace(self.a,limit_price=.81)])
        self.assertEqual(p['status'],'OWN_CROSS_CONFLICT')

    def test_unused_fees_reduce_both_endpoint_allowances(self):
        p=self.inspect([replace(self.a,quantity=1.,fee_cap=.39)])
        self.assertFalse(p['accepted'])
        self.assertAlmostEqual(p['projection']['coordinate_worst']['DOWN'],-6.01)

    def test_market_end_and_no_180_rule_in_component(self):
        self.assertTrue(self.inspect([self.r],now_ms=250000)['accepted'])
        self.assertEqual(self.inspect([self.r],now_ms=300000)['status'],'MARKET_ENDED')

    def test_exact_worst_coordinates_equal_exhaustive_partial_vertices(self):
        self.bounds=EndpointAuthority(-100.,-100.,'EXPLICIT_TEST')
        self.commit([self.r,self.a]);p=payoff_projection(self.ledger,self.base)
        vals=[]
        for fr,fa in itertools.product((0.,.25,.5,1.),repeat=2):
            pay=24*.19*fr+18*.62*fa
            vals.append(dict(UP=35+18*fa-pay,DOWN=-5+24*fr-pay))
        for side in ('UP','DOWN'):
            self.assertAlmostEqual(min(v[side] for v in vals),p['coordinate_worst'][side])

    def test_preview_cannot_create_grants_or_native_orders(self):
        before=snapshot(self.ledger);p=self.inspect([self.r])
        self.assertFalse(p['native_submitted']);self.assertEqual(p['newly_created_grants'],0)
        self.assertEqual(before,snapshot(self.ledger))

    def test_single_pass_order_iterable_cannot_disappear_during_commit(self):
        p=self.inspect([self.r])
        commit_reservations(self.ledger,self.base,self.bounds,'BTC',(o for o in [self.r]),inspected=p,**self.args)
        self.assertEqual(self.ledger.carriers['repair'].qty,24.)


if __name__=='__main__': unittest.main()
