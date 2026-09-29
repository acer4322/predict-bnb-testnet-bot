import math
import unittest

from tools.eth_repair_modular.persistent_execution_roles import (
    CORE, SATELLITE, PersistentExecutionRoles,
)
from tools.eth_repair_modular.parent_execution_occupancy import ParentRepairExecutionOccupancyV1


class RoleMicroworld(unittest.TestCase):
    def setUp(self):
        self.policy = PersistentExecutionRoles()
        self.policy.bind('c', 1, 'UP', CORE, lease_price=.54)
        self.policy.bind('s', 1, 'UP', SATELLITE)
        self.rows = [dict(key='c', parentId=1, side='UP', price=.54),
                     dict(key='s', parentId=1, side='UP', price=.58)]

    def test_negative_control_selects_core_but_roles_preserve_it(self):
        self.assertEqual(min(self.rows, key=lambda r: r['price'])['key'], 'c')
        for reason in ['FRONTIER_REANCHOR', 'CAPACITY_CONTRACTION']:
            self.assertEqual(self.policy.select(self.rows, reason)['key'], 's')
        self.assertEqual(self.rows[0]['key'], 'c')

    def test_core_is_queue_lease_not_universal_never_cancel(self):
        self.assertIsNone(self.policy.select(self.rows[:1], 'FRONTIER_REANCHOR'))
        self.assertEqual(self.policy.select(self.rows[:1], 'CAPACITY_CONTRACTION')['key'], 'c')

    def test_parent_identity_and_terminal_are_required(self):
        with self.assertRaises(ValueError):
            self.policy.inherit('s', 's2', 1, 'UP', terminal_confirmed=False)
        with self.assertRaises(ValueError):
            self.policy.inherit('s', 's2', 2, 'UP', terminal_confirmed=True)
        with self.assertRaises(ValueError):
            self.policy.select([self.rows[0], {**self.rows[1], 'parentId': 2}], 'FRONTIER_REANCHOR')
        with self.assertRaises(ValueError):
            self.policy.bind('c', 1, 'UP', SATELLITE)

    def test_core_requires_finite_approved_lease(self):
        for price in [None, math.nan, math.inf, 0, 1]:
            with self.assertRaises(ValueError):
                self.policy.bind('bad', 1, 'UP', CORE, lease_price=price)

    def test_cancel_partial_late_fill_then_replacement_preserves_quota(self):
        occ = ParentRepairExecutionOccupancyV1()
        occ.reserve(key='c', parent_id=1, route='PASSIVE', qty=2)
        occ.reserve(key='s', parent_id=1, route='PASSIVE', qty=2)
        debt = 5.0
        occ.mark_cancel_pending('s')
        self.assertEqual(occ.available(1, debt), 1)
        occ.observe_fill('s', .4)
        debt -= .4
        self.assertAlmostEqual(occ.available(1, debt), 1)
        self.assertFalse(occ.can_reserve(1, debt, 1.25))
        occ.confirm_terminal('s', cumulative_fill=.6)
        debt -= .2
        self.assertAlmostEqual(occ.available(1, debt), 2.4)
        self.assertTrue(occ.can_reserve(1, debt, 1.25))
        self.policy.inherit('s', 's2', 1, 'UP', terminal_confirmed=True)
        occ.reserve(key='s2', parent_id=1, route='PASSIVE', qty=1.25)
        self.assertEqual(self.policy.entries['s2'].root_key, 's')
        self.assertEqual(self.policy.entries['s2'].role, SATELLITE)
        occ.observe_fill('s', .6)  # Repeat notification neither releases nor adds quota.
        self.assertAlmostEqual(occ.parent_reserved(1), 3.25)
        self.assertLessEqual(occ.parent_reserved(1), debt)
        self.assertEqual(self.policy.role('c'), CORE)

    def test_partial_core_and_satellite_contraction(self):
        occ = ParentRepairExecutionOccupancyV1()
        occ.reserve(key='c', parent_id=1, route='PASSIVE', qty=2)
        occ.reserve(key='s', parent_id=1, route='PASSIVE', qty=2)
        occ.observe_fill('c', 1.9)
        debt = 3.1
        chosen = self.policy.select(self.rows, 'CAPACITY_CONTRACTION')
        occ.mark_cancel_pending(chosen['key'])
        self.assertAlmostEqual(occ.parent_reserved(1), 2.1)
        self.assertLessEqual(occ.parent_reserved(1), debt)
        self.assertFalse(occ.carriers['c'].cancel_pending)


if __name__ == '__main__':
    unittest.main()
