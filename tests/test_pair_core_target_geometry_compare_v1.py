import unittest
from tools.pair_core_target_geometry_compare_v1 import endpoint_at, normalized_distance, target_at


class ComparisonTests(unittest.TestCase):
    def test_native_down_conversion_and_clock(self):
        r=dict(side=-1,price=.3,qty=2,exchange_ts=1000000000,receive_ts=1250000000)
        ex=endpoint_at([r],1100,'exchange_ts');rx=endpoint_at([r],1100,'receive_ts')
        self.assertAlmostEqual(ex['DOWN'],.6);self.assertAlmostEqual(ex['UP'],-1.4)
        self.assertEqual(rx['receipts'],0)

    def test_normalization_not_side_invariant(self):
        a=dict(UP=1,DOWN=-1,cost=2);b=dict(UP=10,DOWN=-10,cost=20)
        self.assertEqual(normalized_distance(a,b),0)
        self.assertEqual(normalized_distance(a,dict(UP=-1,DOWN=1,cost=2)),1)

    def test_zero_activity_not_perfect_similarity(self):
        self.assertIsNone(normalized_distance(dict(cost=0),dict(cost=1)))

    def test_no_future_target_checkpoint(self):
        trace=[dict(eventMs=1200,UP=1,DOWN=-1,buyNotional=2,parents=1)]
        self.assertEqual(target_at(trace,1000)['cost'],0)
        self.assertEqual(target_at(trace,1200)['UP'],1)


if __name__=='__main__':unittest.main()
