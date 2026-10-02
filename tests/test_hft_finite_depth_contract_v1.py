from decimal import Decimal as D
import random
import unittest

from tools.hft_finite_depth_contract_v1 import DepthBank, ResponsibilityBook, TradeBudget, TradeBank


class FiniteDepthContractTests(unittest.TestCase):
    def test_take_then_rest_and_cancel_retains_consumption(self):
        bank = DepthBank(); orders = ResponsibilityBook(); level = ('ASK', 75)
        bank.observe(level, 1, '.6'); orders.register((1, 0), 'repair-A', '1.35')
        filled = bank.allocate(level, '1.35', 'fill-1')
        orders.fill((1, 0), 'fill-1', filled, '.75')
        order = orders.orders[(1, 0)]
        self.assertEqual((filled, order.leaves, order.payment), (D('.6'), D('.75'), D('.45')))
        orders.request_cancel((1, 0)); self.assertEqual(order.outstanding, D('.75'))
        orders.acknowledge_cancel((1, 0)); self.assertEqual(order.outstanding, 0)
        self.assertEqual(bank.available(level), 0)

    def test_competing_slots_cannot_reuse_same_level(self):
        bank = DepthBank(); level = ('ASK', 75); bank.observe(level, 1, '1')
        self.assertEqual(bank.allocate(level, '.6', 'A'), D('.6'))
        self.assertEqual(bank.allocate(level, '.6', 'B'), D('.4'))
        self.assertEqual(bank.allocate(level, '1', 'C'), 0)

    def test_unchanged_snapshot_does_not_replenish(self):
        bank = DepthBank(); level = ('BID', 25); bank.observe(level, 1, '.6')
        bank.allocate(level, '.6', 'A'); bank.observe(level, 2, '.6')
        bank.observe(level, 2, '.6')
        self.assertEqual(bank.available(level), 0)

    def test_reduction_retires_debt_without_adding_credit(self):
        bank = DepthBank(); level = ('ASK', 75); bank.observe(level, 1, '1')
        bank.allocate(level, '.6', 'A'); bank.observe(level, 2, '.7')
        self.assertEqual(bank.available(level), D('.4'))
        bank.observe(level, 3, '.8'); self.assertEqual(bank.available(level), D('.5'))
        bank.observe(level, 4, '0'); bank.observe(level, 5, '.2')
        self.assertEqual(bank.available(level), D('.2'))

    def test_invalid_or_conflicting_observation_stops(self):
        bank = DepthBank(); bank.observe(('ASK', 75), 2, '1')
        for seq, value in [(1, '1'), (2, '2'), (3, '-1'), (3, 'NaN')]:
            with self.assertRaises(ValueError): bank.observe(('ASK', 75), seq, value)
        self.assertEqual(bank.available(('ASK', 75)), 1)

    def test_allocation_id_cannot_be_reused(self):
        bank = DepthBank(); bank.observe(('ASK', 75), 1, '2')
        bank.allocate(('ASK', 75), '.6', 'A')
        with self.assertRaises(ValueError): bank.allocate(('ASK', 75), '.6', 'A')
        self.assertEqual(bank.available(('ASK', 75)), D('1.4'))

    def test_multilevel_payment_and_receipt_dedup(self):
        orders = ResponsibilityBook(); key = (1, 0); orders.register(key, 'repair', '1.35')
        orders.fill(key, 'first', '.6', '.75'); orders.fill(key, 'second', '.75', '.76')
        self.assertEqual(orders.orders[key].payment, D('1.02'))
        self.assertFalse(orders.fill(key, 'second', '.75', '.76'))
        self.assertEqual(orders.orders[key].payment, D('1.02'))
        with self.assertRaises(ValueError): orders.fill(key, 'second', '.75', '.75')

    def test_partial_fill_while_cancel_pending(self):
        orders = ResponsibilityBook(); key = (1, 0); orders.register(key, 'repair', '1.35')
        orders.fill(key, 'take', '.6', '.75'); orders.request_cancel(key)
        orders.fill(key, 'late-passive', '.25', '.76')
        self.assertEqual(orders.orders[key].outstanding, D('.5'))
        orders.acknowledge_cancel(key)
        self.assertEqual((orders.orders[key].filled, orders.orders[key].leaves), (D('.85'), D('.5')))

    def test_full_fill_wins_cancel_race(self):
        orders = ResponsibilityBook(); key = (1, 0); orders.register(key, 'repair', '1.35')
        orders.request_cancel(key); orders.fill(key, 'fill', '1.35', '.76')
        orders.acknowledge_cancel(key)
        self.assertEqual(orders.orders[key].terminal, 'FILLED')

    def test_residual_precision_and_generation(self):
        orders = ResponsibilityBook(); orders.register((1, 0), 'A', '1.35135135')
        orders.fill((1, 0), 'fill', '.6', '.75')
        self.assertEqual(orders.orders[(1, 0)].leaves, D('.75135135'))
        with self.assertRaises(ValueError): orders.register((1, 1), 'B', '2')
        orders.request_cancel((1, 0)); orders.acknowledge_cancel((1, 0))
        orders.register((1, 1), 'B', '2')
        self.assertEqual(orders.orders[(1, 1)].filled, 0)

    def test_one_print_budget_shared_by_slots(self):
        budget = TradeBudget('.6')
        fills = [budget.allocate('.4'), budget.allocate('.4'), budget.allocate('1')]
        self.assertEqual(fills, [D('.4'), D('.2'), D('0')])

    def test_duplicate_print_reuses_remaining_budget(self):
        bank = TradeBank(); first = bank.begin('physical-leg-A', '.6')
        self.assertEqual(first.allocate('.4'), D('.4'))
        again = bank.begin('physical-leg-A', '.6')
        self.assertIs(first, again)
        self.assertEqual(again.allocate('.4'), D('.2'))
        with self.assertRaises(ValueError): bank.begin('physical-leg-A', '1')

    def test_bounded_random_conservation(self):
        rng = random.Random(1203); bank = DepthBank(); level = ('ASK', 75)
        for seq in range(1, 1001):
            old = bank.available(level); observed = bank.levels.get(level)
            old_raw = observed.observed if observed else D(0)
            new = D(rng.randrange(0, 100))/10
            bank.observe(level, seq, new)
            self.assertLessEqual(bank.available(level), old + max(new-old_raw, D(0)))
            before = bank.available(level)
            got = bank.allocate(level, D(rng.randrange(0, 150))/10, str(seq))
            self.assertLessEqual(got, before)
            self.assertEqual(bank.available(level), before-got)


if __name__ == '__main__': unittest.main()
