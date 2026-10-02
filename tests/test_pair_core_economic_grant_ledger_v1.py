from copy import deepcopy
import unittest
from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger,Grant


class GrantLedgerTests(unittest.TestCase):
    def ledger(self,repair=3,add=2,cash=4,side='UP'):
        l=EconomicGrantLedger(20);l.issue(Grant(1,'generation1',side,repair,add,cash,'test_explicit_grant'))
        return l
    def reserve(self,l,key='p',route='PASSIVE',qty=2,limit=.4,pid=1,fee=0):
        l.reserve(key,pid,route,qty,limit,fee,now_ms=290000,market_end_ms=300000)
    def test_four_channels_include_pure_active_add(self):
        for repair,add in ((3,0),(0,3)):
            for route in ('PASSIVE','ACTIVE'):
                l=self.ledger(repair,add);self.reserve(l,route=route)
                r=l.confirm_cumulative('p',2,.8)
                self.assertEqual(r.repair_increment,2 if repair else 0)
                self.assertEqual(r.overflow_increment,0 if repair else 2)
                self.assertTrue(l.invariants())
    def test_composite_parallel_fills_share_debt_once(self):
        l=self.ledger();self.reserve(l);self.reserve(l,'a','ACTIVE',3,.5)
        x=l.confirm_cumulative('a',2,1);self.assertEqual(x.repair_increment,2)
        y=l.confirm_cumulative('p',2,.8)
        self.assertEqual((y.repair_increment,y.overflow_increment),(1,1))
        z=l.confirm_cumulative('a',3,1.5);self.assertEqual(z.overflow_increment,1)
        self.assertTrue(l.invariants())
    def test_pending_and_cancel_pending_are_not_paid_or_released(self):
        l=self.ledger(3,0);self.reserve(l,qty=3);l.request_cancel('p')
        self.assertEqual(l.account(1)['repair_remaining'],3)
        with self.assertRaises(ValueError):self.reserve(l,'a','ACTIVE',1)
        l.confirm_terminal('p',filled=1,payment=.4)
        self.reserve(l,'a','ACTIVE',2);self.assertTrue(l.invariants())
    def test_shared_cash_not_independent_by_route(self):
        l=self.ledger(10,0,1);self.reserve(l,qty=2)
        with self.assertRaises(ValueError):self.reserve(l,'a','ACTIVE',1,.4)
    def test_capital_not_double_granted(self):
        l=EconomicGrantLedger(1);l.issue(Grant(1,'g','UP',1,0,1,'manual'))
        with self.assertRaises(ValueError):l.issue(Grant(2,'g','DOWN',1,0,1,'manual'))
    def test_active_excluded_from_passive_pool_but_not_cash(self):
        l=self.ledger(10,0,10)
        for i in range(4):self.reserve(l,str(i),'PASSIVE',1,.4)
        self.reserve(l,'a','ACTIVE',1,.5)
        with self.assertRaises(ValueError):self.reserve(l,'p5','PASSIVE',1)
        with self.assertRaises(ValueError):self.reserve(l,'a2','ACTIVE',1)
        self.assertAlmostEqual(l.account(1)['reserved_cash'],2.1)
    def test_cancel_pending_blocks_cross(self):
        l=self.ledger();self.reserve(l);l.request_cancel('p')
        l.issue(Grant(2,'g','DOWN',0,2,2,'manual'))
        with self.assertRaises(ValueError):self.reserve(l,'a','ACTIVE',1,.7,2)
    def test_duplicate_cumulative_and_terminal_are_idempotent(self):
        l=self.ledger();self.reserve(l);l.confirm_terminal('p',filled=1,payment=.4)
        self.assertIsNone(l.confirm_terminal('p',filled=1,payment=.4))
        self.assertEqual(l.account(1)['repair_paid'],1)
    def test_invalid_confirmation_cannot_mutate_or_release(self):
        l=self.ledger();self.reserve(l);saved=deepcopy(vars(l.carriers['p']))
        with self.assertRaises(ValueError):l.confirm_terminal('p',filled=1,payment=.5)
        self.assertEqual(vars(l.carriers['p']),saved)
        self.assertEqual(l.account(1)['repair_paid'],0)
    def test_grant_and_carrier_cannot_be_rebound(self):
        l=self.ledger();self.reserve(l)
        with self.assertRaises(ValueError):l.issue(l.grants[1])
        with self.assertRaises(ValueError):self.reserve(l)
    def test_no_unauthorized_overflow_or_synthetic_protection(self):
        l=self.ledger(1,0)
        with self.assertRaises(ValueError):self.reserve(l,qty=2)
        self.assertEqual(l.account(1)['repair_paid'],0)
    def test_end_boundary_not_180_and_missing_grant_rejected(self):
        l=self.ledger();self.reserve(l) # remaining10s allowed with explicit grant
        with self.assertRaises(ValueError):
            l.reserve('late',1,'ACTIVE',1,.5,0,now_ms=300000,market_end_ms=300000)
        with self.assertRaises(ValueError):self.reserve(l,pid=7)
    def test_fee_reservation_and_actual_improved_price(self):
        l=self.ledger();self.reserve(l,fee=.1)
        l.confirm_cumulative('p',1,.3,.04)
        self.assertAlmostEqual(l.account(1)['spent'],.34)
        self.assertAlmostEqual(l.account(1)['reserved_cash'],.46)
        self.assertTrue(l.invariants())
    def test_unreconciled_late_execution_fails_loudly(self):
        l=self.ledger();self.reserve(l);l.confirm_terminal('p',filled=1,payment=.4)
        with self.assertRaises(ValueError):l.confirm_cumulative('p',2,.8)
    def test_partial_price_improvement_cannot_hide_later_limit_violation(self):
        l=self.ledger();self.reserve(l);l.confirm_cumulative('p',1,.1)
        with self.assertRaises(ValueError):l.confirm_cumulative('p',2,.6)
    def test_add_need_not_wait_for_other_repair_debt_to_clear(self):
        l=self.ledger(3,0,3,'UP');self.reserve(l,qty=1,limit=.2)
        l.issue(Grant(2,'g2','DOWN',0,2,2,'independent_add_grant'))
        self.reserve(l,'add','ACTIVE',1,.7,2)
        l.confirm_terminal('add',filled=1,payment=.7)
        self.assertEqual(l.account(1)['repair_remaining'],3)
        self.assertEqual(l.account(2)['add_filled'],1)
        self.assertTrue(l.invariants())
    def test_repair_completion_does_not_rearm_qty_or_cash(self):
        l=self.ledger(2,0,1);self.reserve(l,qty=2,limit=.4)
        l.confirm_terminal('p',filled=2,payment=.8)
        with self.assertRaises(ValueError):self.reserve(l,'again','ACTIVE',1,.1)
        self.assertEqual(l.account(1)['spent'],.8)
    def test_all_two_route_partial_fill_interleavings_conserve_grant(self):
        from itertools import combinations
        # Every interleaving of two3-part receipt streams (20), including repeats
        # and a pending cancel, must preserve one shared Repair/ADD accounting.
        for positions in combinations(range(6),3):
            l=self.ledger(3,3,4);self.reserve(l,'p','PASSIVE',3,.4)
            self.reserve(l,'a','ACTIVE',3,.5);l.request_cancel('p')
            counts={'p':0,'a':0}
            for i in range(6):
                key='p' if i in positions else 'a';counts[key]+=1
                q=counts[key];cash=q*(.4 if key=='p' else .5)
                l.confirm_cumulative(key,q,cash)
                self.assertIsNone(l.confirm_cumulative(key,q,cash))
                self.assertTrue(l.invariants())
            self.assertEqual(l.account(1)['repair_paid'],3)
            self.assertEqual(l.account(1)['add_filled'],3)


if __name__=='__main__':unittest.main()
