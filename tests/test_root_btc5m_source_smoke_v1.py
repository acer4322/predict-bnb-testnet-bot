import unittest
from collections import deque

from tools.run_root_btc5m_source_smoke_v1 import (
    cash_check, fingerprint, latest_public, native_quantities,
)


class SourceSmokeTests(unittest.TestCase):
    def test_strict_past_excludes_equal_and_future(self):
        rows = [{'availableMs': 10}, {'availableMs': 20}]
        self.assertIsNone(latest_public(rows, [10, 20], 10))
        self.assertEqual(latest_public(rows, [10, 20], 20), rows[0])
        self.assertEqual(latest_public(rows, [10, 20], 21), rows[1])

    def test_buy_up_mapping(self):
        self.assertEqual(native_quantities(2, 2, -.8), {'UP': 2, 'DOWN': 0, 'cost': .8})

    def test_buy_down_mapping(self):
        q = native_quantities(-2, 2, 1.2)
        self.assertEqual(q['DOWN'], 2)
        self.assertAlmostEqual(q['cost'], .8)

    def test_joint_mapping_and_cash_negative_control(self):
        n = {'position': 1, 'trading_volume': 5, 'balance': -.1}
        self.assertTrue(cash_check({'UP': 3, 'DOWN': 2}, 2.1, n)['pass'])
        self.assertFalse(cash_check({'UP': 3, 'DOWN': 2}, 2.2, n)['pass'])

    def test_fingerprints_cover_late_history(self):
        a = {'fills': list(range(3000))}
        b = {'fills': list(range(3000)) + [3000]}
        self.assertNotEqual(fingerprint(a), fingerprint(b))

    def test_fingerprints_ignore_dict_and_set_iteration_order(self):
        self.assertEqual(fingerprint({'a': {2, 1}, 3: 'x'}), fingerprint({3: 'x', 'a': {1, 2}}))

    def test_deque_is_not_silently_omitted(self):
        self.assertEqual(fingerprint(deque([1, 2])), fingerprint([1, 2]))


if __name__ == '__main__':
    unittest.main()
