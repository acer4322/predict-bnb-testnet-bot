import unittest
from tools.pair_core_payoff_path_metrics_v1 import distance,path_metrics


class PayoffMetricTests(unittest.TestCase):
    def test_identical_trajectory(self):
        r=[dict(exchange_ts=500000000,side=1,price=.5,qty=2)]
        t=[dict(eventMs=500,UP=1,DOWN=-1)]
        m=path_metrics(r,t,0,1000,1,1)
        self.assertEqual(m['meanDistance'],0);self.assertEqual(m['terminalDistance'],0)
        self.assertEqual(m['zeroPositionMeanDistance'],.5)
    def test_prefix_state_and_time_weight(self):
        r=[dict(exchange_ts=-100000000,side=1,price=.5,qty=2)]
        self.assertEqual(path_metrics(r,[],0,1000,1,1)['meanDistance'],1)
    def test_same_scale_not_candidate_turnover(self):
        p=dict(UP=1,DOWN=-1);q=dict(UP=2,DOWN=-2)
        self.assertEqual(distance(p,q,1,2),0)
        self.assertEqual(distance(q,q,1,2),1)
    def test_rescale_and_side_flip(self):
        p=dict(UP=1,DOWN=-1);q=dict(UP=2,DOWN=-2)
        self.assertEqual(distance(p,q,1,2),distance(q,q,2,2))
        self.assertGreater(distance(p,dict(UP=-2,DOWN=2),1,2),0)
    def test_zero_scale_invalid(self):
        with self.assertRaises(ValueError):distance({}, {},0,1)


if __name__=='__main__':unittest.main()
