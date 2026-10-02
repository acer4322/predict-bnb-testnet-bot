"""Zero-engine checks for reporting math, not native replay validation."""
import unittest
from tools.run_hft244_corrected_route_fork_2023609_worker import classify, fifo


class ReportingTest(unittest.TestCase):
    def row(self, up=0., down=0., qty=2.):
        return dict(UP=up, DOWN=down, fills=2, submits=2, qty=qty, cost=1.,
                    scopeCompletions=0, scopeFlips=0, peakAbsNet=1., netIntegralShareMs=1.)

    def test_no_change(self):
        self.assertEqual(classify(self.row(),self.row())['verdict'],'NO_TERMINAL_ROUTE_EFFECT')

    def test_tradeoff(self):
        self.assertEqual(classify(self.row(),self.row(1.,-1.))['verdict'],'ROUTE_ENDPOINT_TRADEOFF')

    def test_dominance_symmetric(self):
        self.assertEqual(classify(self.row(),self.row(1.,1.))['dominanceDirection'],'P')
        self.assertEqual(classify(self.row(1.,1.),self.row())['dominanceDirection'],'S')

    def test_activity_loss_not_promoted(self):
        result=classify(self.row(),self.row(1.,1.,qty=1.))
        self.assertEqual(result['verdict'],'ROUTE_DOMINANCE_WITH_ACTIVITY_REDUCTION')

    def test_cost_pair_and_residual(self):
        receipts=[dict(sequence=1,side=1,price=.5,qty=1.),
                  dict(sequence=2,side=-1,price=.2,qty=1.5)]
        result=fifo(receipts,{1:dict(key='UP_1'),2:dict(key='DOWN_2')},
                    dict(cost=1.7,UP=-.7,DOWN=-.2))
        self.assertAlmostEqual(result['matchedMargin'],-.3)
        self.assertAlmostEqual(result['remainingCost'],.4)

    def test_bad_accounting_stops(self):
        with self.assertRaises(AssertionError):
            fifo([dict(sequence=1,side=1,price=.5,qty=1.)],{1:dict(key='UP_1')},
                 dict(cost=0.,UP=.5,DOWN=-.5))


if __name__ == '__main__': unittest.main()
