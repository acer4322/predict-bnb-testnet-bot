import copy
import json
from pathlib import Path
import unittest
from tools.hft244_pair_continuation_value_audit_v1 import audit, geometry, receipt_value, close


class ContinuationValueAuditTests(unittest.TestCase):
    def test_direct_vs_whole_can_reverse(self):
        self.assertAlmostEqual(geometry(-1, 1/3)['zeroAtP_UP'], .25)
        self.assertIsNone(geometry(-.796479, -.795117)['positiveMixture'])

    def test_tradeoffs(self):
        self.assertEqual(geometry(2, -8)['positiveMixture'], 'p_UP > root')
        self.assertAlmostEqual(geometry(2, -8)['zeroAtP_UP'], .8)
        self.assertEqual(geometry(-8, 2)['positiveMixture'], 'p_UP < root')

    def test_dominance_and_neutral(self):
        self.assertEqual(geometry(1, 2)['positiveMixture'], 'ALL')
        self.assertEqual(geometry(0, 0)['ordering'], 'NEUTRAL')
        self.assertEqual(geometry(0, -1)['zeroAtP_UP'], 1)

    def test_native_down_price_mapping(self):
        v = receipt_value([dict(side=-1, qty=4., price=.75, fee=0.)])
        self.assertEqual(v['cost'], 1)
        self.assertEqual(v['endpoints'], {'UP': -1, 'DOWN': 3})

    def test_no_silent_net_cost_claim(self):
        with self.assertRaises(ValueError):
            receipt_value([dict(side=1, qty=1., price=.5, fee=.01)])

    def test_reject_invalid_data(self):
        for side, qty, price in [(0, 1, .5), (1, 0, .5), (1, 1, 2)]:
            with self.assertRaises(ValueError):
                receipt_value([dict(side=side, qty=qty, price=price, fee=0)])
        with self.assertRaises(ValueError):
            geometry(float('nan'), 0)
        with self.assertRaises(ValueError):
            close(1, 2)


class FrozenCompactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / 'data/research/lan_worker_returns/hft244-pair-confirmed-handoff-20260910-v2/COMPACT.json'
        cls.doc = json.loads(path.read_text(encoding='utf-8'))

    def test_nine_arms_reconcile(self):
        result = audit(self.doc)
        self.assertEqual(len(result['markets']), 3)
        r = next(x for x in result['markets'] if x['marketId']==2023609)
        self.assertAlmostEqual(r['deltaCash'], 1.72)
        self.assertAlmostEqual(r['fifoDelta']['residualQty']['UP'], -.0013617055870582817)
        self.assertFalse(result['expectedValueIdentified'])

    def test_prefix_tamper_rejected(self):
        doc = copy.deepcopy(self.doc)
        doc['rows'][-1]['ready']['prefix'] = 'different'
        with self.assertRaisesRegex(ValueError, 'prefix/intent'):
            audit(doc)

    def test_direct_lineage_tamper_rejected(self):
        doc = copy.deepcopy(self.doc)
        doc['rows'][-1]['directReceipts'][0]['order_id'] = -1
        with self.assertRaisesRegex(ValueError, 'lineage'):
            audit(doc)


if __name__ == '__main__':
    unittest.main()
