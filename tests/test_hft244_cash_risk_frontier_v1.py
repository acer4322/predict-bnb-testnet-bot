from fractions import Fraction as F
from itertools import product
import unittest
from tools.hft244_cash_risk_frontier_v1 import requirements


class RiskGeometryTest(unittest.TestCase):
    def test_no_trade(self):
        r=requirements(2,1,2,[])
        self.assertEqual(r['requiredSettlementLossBudget'],1)
        self.assertEqual(r['additionalCashNeeded'],0)

    def test_paid_cash_never_refunded_by_repair(self):
        r=requirements(2,F(50,37),2,[dict(side='DOWN',price=F(37,50),qty=F(50,37))])
        self.assertEqual(r['minimumInitialCashWithoutProceeds'],3)
        self.assertEqual(r['requiredSettlementLossBudget'],1)
        self.assertEqual(r['addedSettlementLossBudget'],F(13,37))

    def test_balanced_pair_not_dream_fill(self):
        legs=[dict(side='UP',price=F(2,5),qty=1),dict(side='DOWN',price=F(2,5),qty=1)]
        r=requirements(0,0,0,legs)
        self.assertEqual(r['fullFillEndpoints'],{'UP':F(1,5),'DOWN':F(1,5)})
        self.assertEqual(r['requiredSettlementLossBudget'],F(2,5))

    def test_formula_matches_all_corners(self):
        legs=[dict(side='UP',price=F(1,4),qty=2),dict(side='DOWN',price=F(3,4),qty=1)]
        r=requirements(2,F(3,2),2,legs)
        states=[]
        for filled in product([0,F(1,2),1],repeat=2):
            up,down,cost=F(2),F(3,2),F(2)
            for x,l in zip(filled,legs):
                q=x*l['qty']; cost+=q*l['price']
                if l['side']=='UP':up+=q
                else:down+=q
            states.append({'UP':up-cost,'DOWN':down-cost})
        self.assertEqual(r['independentFillWorstEndpoints'],{s:min(x[s] for x in states) for s in ['UP','DOWN']})

    def test_cancel_pending_still_counts_when_supplied(self):
        order=dict(side='UP',price=F(1,2),qty=2)
        self.assertEqual(requirements(0,0,0,[order])['requiredSettlementLossBudget'],1)

    def test_invalid_binary_price(self):
        with self.assertRaises(AssertionError):requirements(0,0,0,[dict(side='UP',price=1,qty=1)])


if __name__=='__main__':unittest.main()
