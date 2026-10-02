import ast
from collections import Counter
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import unittest

from tools.pair_core_asset_route_sizing_v1 import (
    make_passive_sim, passive_quantity, reserve_authorized, validate_size)
from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger, Grant


def source_method(filename, class_name, method):
    # Test the actual pure legacy methods without importing its native runtime or
    # predict_bot initializer. Parse only two small named source files.
    path = Path(__file__).resolve().parents[1] / 'tools' / filename
    tree = ast.parse(path.read_text(encoding='utf-8-sig'))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == class_name)
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
    module = ast.Module(body=[fn], type_ignores=[])
    # Verified V2 kprice is round(float(p), 10), NOT native tick rounding.
    kprice = lambda p: round(float(p), 10)
    scope = {'EPS': 1e-9, 'kprice': kprice, 'v2': SimpleNamespace(kprice=kprice)}
    import math
    scope['math'] = math
    exec(compile(module, str(path), 'exec'), scope)
    return scope[method]


class SizingTests(unittest.TestCase):
    def test_asset_boundaries_and_no_clamping(self):
        self.assertIsNone(passive_quantity('ETH', .08))
        self.assertAlmostEqual(passive_quantity('ETH', .09), 1/.09)
        self.assertIsNone(passive_quantity('BTC', .05))
        self.assertAlmostEqual(passive_quantity('BTC', .06), 1/.06)
        self.assertAlmostEqual(passive_quantity('ETH', 1/12), 12)
        self.assertAlmostEqual(passive_quantity('BTC', 1/18), 18)
        for asset, price, clamped in [('ETH', .07, 12), ('BTC', .04, 18)]:
            with self.assertRaises(ValueError): validate_size(asset, 'PASSIVE', price, clamped)

    def test_all_cent_prices_have_correct_domain_and_ticket(self):
        for asset, first in [('ETH', 9), ('BTC', 6)]:
            for cent in range(1, 100):
                q = passive_quantity(asset, cent/100)
                if cent < first:
                    self.assertIsNone(q)
                else:
                    self.assertAlmostEqual(q*cent/100, 1)
                    validate_size(asset, 'PASSIVE', cent/100, q)

    def test_no_asset_guess_no_unknown_route_and_no_invalid_number(self):
        for asset in ('', 'eth', 'SOL', None):
            with self.assertRaises(ValueError): passive_quantity(asset, .5)
        for price in (0, 1, -.1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError): passive_quantity('ETH', price)
        for qty in (0, -1, float('nan'), float('inf'), True):
            with self.assertRaises(ValueError): validate_size('BTC', 'ACTIVE', .1, qty)
        with self.assertRaises(ValueError): validate_size('BTC', 'TAKER', .1, 3)

    def test_active_has_neither_passive_fixed_cash_nor_passive_share_cap(self):
        for asset in ('ETH', 'BTC'):
            for q in (2., 25., 40.):
                validate_size(asset, 'ACTIVE', .04, q)

    def test_passive_smaller_order_does_not_masquerade_as_fixed_cash(self):
        with self.assertRaises(ValueError): validate_size('BTC', 'PASSIVE', .5, 1)

    def ledger(self, repair=0., add=40., cash=3.):
        ledger = EconomicGrantLedger(10)
        ledger.issue(Grant(1, 'g', 'UP', repair, add, cash, 'SYNTHETIC_EXPLICIT_AUTHORITY'))
        return ledger

    def reserve(self, ledger, route='ACTIVE', qty=25., price=.04, key='a', asset='BTC'):
        reserve_authorized(ledger, asset, key, 1, route, qty, price, 0.,
                           now_ms=295000, market_end_ms=300000)

    def test_active_over18_still_requires_and_consumes_shared_grant(self):
        l = self.ledger(); self.reserve(l)
        self.assertAlmostEqual(l.account(1)['reserved_cash'], 1)
        result = l.confirm_terminal('a', filled=25, payment=1)
        self.assertEqual(result.overflow_increment, 25)
        self.assertTrue(l.invariants())
        with self.assertRaises(ValueError): self.reserve(l, key='b', qty=20)
        with self.assertRaises(ValueError): self.reserve(EconomicGrantLedger(10))

    def test_active_cash_notional_not_forced_to_one(self):
        l = self.ledger(); self.reserve(l, qty=30)
        self.assertAlmostEqual(l.account(1)['reserved_cash'], 1.2)
        with self.assertRaises(ValueError): self.reserve(self.ledger(cash=.8))

    def test_pending_cancel_not_quantity_recycling(self):
        l = self.ledger(); self.reserve(l); l.request_cancel('a')
        with self.assertRaises(ValueError): self.reserve(l, key='b', qty=10)
        l.confirm_terminal('a', filled=5, payment=.2)
        self.reserve(l, key='b', qty=30)
        self.assertEqual(l.account(1)['add_filled'], 5)
        self.assertTrue(l.invariants())

    def test_four_channels_and_composite_allocation(self):
        for asset in ('ETH', 'BTC'):
            for route in ('PASSIVE', 'ACTIVE'):
                for repair, add in ((5, 0), (0, 5), (1, 4)):
                    l = self.ledger(repair, add)
                    self.reserve(l, route=route, qty=2, price=.5, asset=asset)
                    r = l.confirm_terminal('a', filled=2, payment=1)
                    self.assertEqual(r.repair_increment, min(repair, 2))
                    self.assertEqual(r.overflow_increment, max(0, 2-repair))
                    self.assertTrue(l.invariants())

    def test_failed_passive_validation_is_behavior_inert(self):
        l = self.ledger(); saved = deepcopy(l.__dict__)
        with self.assertRaises(ValueError): self.reserve(l, route='PASSIVE', qty=12, price=.07, asset='ETH')
        self.assertEqual(l.grants, saved['grants'])
        self.assertEqual(l.carriers, saved['carriers'])
        self.assertEqual(l.allocation.parents, saved['allocation'].parents)


class FactoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.legacy_levels = staticmethod(source_method('run_eth_target_grounded_distinct_multislot_v2_smoke.py',
                                         'TargetGroundedDistinctSlotSim', '_live_price_levels'))
        cls.real_candidate = staticmethod(source_method('run_eth_role_separated_minimal_pair_safety_smoke.py',
                                         'MinimalPairRoleSim', '_candidate_from_levels'))

    def make(self, asset):
        class Stub:
            def _candidate_from_levels(s, *a): return self.real_candidate(s, *a)
            def _used_prices(s, side): return s.used
            def _pair_ok(s, side, p): return p <= s.pair_max
            def _submit_role(s, *a): s.submitted.append(a); return True
        module = SimpleNamespace(MinimalPairRoleSim=Stub,
                                 v2=SimpleNamespace(kprice=lambda p: round(float(p), 10)))
        sim = make_passive_sim(module, asset)()
        sim.book = {'bids': {}, 'asks': {}}
        sim.used=set(); sim.pair_max=1; sim.veto=Counter()
        sim.minimal_pair_checks=sim.minimal_pair_blocks=0; sim.submitted=[]
        return sim, module

    def test_eth_exact_legacy_levels_for_both_sides_and_all_cent_prices(self):
        s, _ = self.make('ETH')
        s.book = {'bids': {x/100: 1 for x in range(101)}, 'asks': {x/100: 1 for x in range(101)}}
        for side in ('UP', 'DOWN'):
            self.assertEqual(s._live_price_levels(side), self.legacy_levels(s, side))

    def test_btc_recovers_06_07_08_before_not_after_legacy_filter(self):
        s, _ = self.make('BTC')
        s.book = {'bids': {.09: 1, .08: 1, .07: 1, .06: 1, .05: 1},
                  'asks': {.91: 1, .92: 1, .93: 1, .94: 1, .95: 1}}
        for side in ('UP', 'DOWN'):
            self.assertEqual(s._live_price_levels(side), [.09, .08, .07, .06])
            self.assertEqual(self.legacy_levels(s, side), [.09])

    def test_subcent_input_is_not_silently_quantized_to_native_ticks(self):
        s, _ = self.make('ETH')
        s.book={'bids': {.0834: 1, .091234567891: 1},
                'asks': {.9166: 1, .908765432109: 1}}
        for side in ('UP', 'DOWN'):
            self.assertEqual(s._live_price_levels(side), self.legacy_levels(s, side))
            self.assertIn(.0834, s._live_price_levels(side))

    def test_actual_minimal_candidate_retains_pair_and_used_price_checks(self):
        s, _ = self.make('BTC'); s.book['bids']={.08: 1, .07: 1, .06: 1}
        s.pair_max=.07; s.used={.07}
        self.assertEqual(s._candidate_from_levels('UP'), (.06, 1/.06, None))
        self.assertEqual(s.veto['MINIMAL_PAIR_ECONOMICS'], 1)
        s.pair_max=.05
        self.assertIsNone(s._candidate_from_levels('UP'))

    def test_asset_isolation_and_no_legacy_module_mutation(self):
        e, module = self.make('ETH'); before = dict(module.__dict__)
        b = make_passive_sim(module, 'BTC')(); b.book={'bids': {.07: 1}, 'asks': {}}
        e.book=b.book
        self.assertEqual(e._live_price_levels('UP'), [])
        self.assertEqual(b._live_price_levels('UP'), [.07])
        self.assertEqual(module.__dict__, before)
        self.assertFalse(b.sizing_declaration()['activeManager'])

    def test_submit_validates_before_inherited_action(self):
        s, _ = self.make('BTC')
        with self.assertRaises(ValueError): s._submit_role(1,'UP','PROBE_CORE',.07,12,None,'TEST')
        self.assertEqual(s.submitted, [])
        self.assertTrue(s._submit_role(1,'UP','PROBE_CORE',.07,1/.07,None,'TEST'))
        self.assertEqual(len(s.submitted), 1)


if __name__ == '__main__':
    unittest.main()
