from copy import deepcopy
from decimal import Decimal
import unittest
from tools.pair_core_asset_route_sizing_v2 import (
    minimum_passive_quantity, validate_size, reserve_authorized, sizing_declaration)
from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant


class PassiveLowerBoundTests(unittest.TestCase):
    def minimum(self, asset, price):
        return minimum_passive_quantity(asset, price, quantity_step=.01)

    def ledger(self, qty=100., cash=100.):
        ledger = EconomicGrantLedger(cash)
        ledger.issue(Grant(1, 'explicit', 'UP', qty, 0., cash, 'UNIT_FIXTURE_NOT_TARGET'))
        return ledger

    def reserve(self, ledger, route, qty, price, asset='ETH', key='o'):
        return reserve_authorized(ledger, asset, key, 1, route, qty, price, 0.,
                                  now_ms=1000, market_end_ms=300000, quantity_step=.01)

    def test_eth_minimum_shares_at_mid_price(self):
        self.assertEqual(self.minimum('ETH', .2), 12.)
        self.assertIsNone(validate_size('ETH', 'PASSIVE', .2, 12.))

    def test_btc_minimum_shares_at_mid_price(self):
        self.assertEqual(self.minimum('BTC', .2), 18.)
        with self.assertRaises(ValueError):
            validate_size('BTC', 'PASSIVE', .2, 12.)

    def test_low_price_requires_notional_and_ceil(self):
        self.assertEqual(self.minimum('ETH', .07), 14.29)
        self.assertEqual(self.minimum('BTC', .07), 18.)
        self.assertEqual(self.minimum('ETH', .05), 20.)
        self.assertEqual(self.minimum('BTC', .05), 20.)
        self.assertEqual(self.minimum('BTC', .03), 33.34)

    def test_larger_passive_order_not_forbidden_by_12_18(self):
        for asset in ('ETH', 'BTC'):
            self.assertIsNone(validate_size(asset, 'PASSIVE', .5, 100.))

    def test_each_passive_minimum_independently_required(self):
        with self.assertRaises(ValueError):validate_size('ETH', 'PASSIVE', .9, 11.)
        with self.assertRaises(ValueError):validate_size('ETH', 'PASSIVE', .07, 12.)
        with self.assertRaises(ValueError):validate_size('BTC', 'PASSIVE', .05, 18.)

    def test_amount1_is_minimum_not_exact_ticket(self):
        self.assertIsNone(validate_size('ETH', 'PASSIVE', .2, 12.))
        self.assertIsNone(validate_size('BTC', 'PASSIVE', .2, 18.))
        self.assertGreater(12*.2, 1)

    def test_active_small_orders_do_not_inherit_passive_minima(self):
        for asset in ('ETH', 'BTC'):
            self.assertIsNone(validate_size(asset, 'ACTIVE', .02, .5, quantity_step=.01))

    def test_active_large_orders_have_no_inherited_cap(self):
        for asset in ('ETH', 'BTC'):
            self.assertIsNone(validate_size(asset, 'ACTIVE', .5, 50.))

    def test_active_still_needs_cash_and_quantity_authority(self):
        l = self.ledger(qty=10, cash=1)
        with self.assertRaises(ValueError):self.reserve(l, 'ACTIVE', 20, .5)
        with self.assertRaises(ValueError):self.reserve(l, 'ACTIVE', 4, .5)
        self.assertEqual(l.carriers, {})

    def test_minimum_cannot_inflate_insufficient_grant(self):
        l = self.ledger(qty=1, cash=10)
        with self.assertRaises(ValueError):self.reserve(l, 'PASSIVE', 12, .2)
        self.assertEqual(l.account(1)['repair_remaining'], 1)
        self.assertEqual(l.carriers, {})

    def test_cash1_not_implicitly_raised_to_meet_share_minimum(self):
        l = self.ledger(qty=12, cash=1)
        with self.assertRaises(ValueError):self.reserve(l, 'PASSIVE', 12, .2)
        self.assertEqual(l.carriers, {})

    def test_partial_fill_below_minimum_is_valid_receipt(self):
        l = self.ledger(qty=12, cash=3)
        self.reserve(l, 'PASSIVE', 12, .2)
        l.confirm_cumulative('o', 1, .2)
        self.assertEqual(l.account(1)['repair_paid'], 1)
        self.assertEqual(l.account(1)['reserved_qty'], 11)
        self.assertTrue(l.invariants())

    def test_cancel_keeps_claim_until_reconciled_terminal(self):
        l = self.ledger(qty=12, cash=3)
        self.reserve(l, 'PASSIVE', 12, .2)
        l.request_cancel('o')
        with self.assertRaises(ValueError):self.reserve(l, 'ACTIVE', 1, .2, key='a')
        l.confirm_terminal('o', filled=1, payment=.2)
        self.reserve(l, 'ACTIVE', 1, .2, key='a')
        self.assertTrue(l.invariants())

    def test_grid_and_invalid_inputs_do_not_silently_coerce(self):
        with self.assertRaises(ValueError):validate_size('ETH', 'ACTIVE', .2, 1.001, quantity_step=.01)
        for value in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError):self.minimum('ETH', value)
        with self.assertRaises(ValueError):self.minimum('BNB', .2)
        with self.assertRaises(ValueError):validate_size('ETH', 'unknown', .2, 12)

    def test_every_cent_meets_both_minima_without_cap(self):
        for asset, minimum in [('ETH', 12), ('BTC', 18)]:
            for cents in range(1, 100):
                p=Decimal(cents)/100
                q=Decimal(str(self.minimum(asset,float(p))))
                self.assertGreaterEqual(q,minimum)
                self.assertGreaterEqual(q*p,1)
                self.assertEqual(q%Decimal('.01'),0)
                validate_size(asset,'PASSIVE',p,q,quantity_step=Decimal('.01'))

    def test_declaration_labels_target_evidence_unknown(self):
        for asset in ('ETH','BTC'):
            d=sizing_declaration(asset,'ACTIVE')
            self.assertIsNone(d['passiveMinShares'])
            self.assertIsNone(d['inheritedSizeUpperBound'])
            self.assertFalse(d['nativeIntegration'])


if __name__=='__main__':unittest.main()
