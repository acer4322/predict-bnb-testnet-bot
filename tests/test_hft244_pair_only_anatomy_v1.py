import unittest
from tools.hft244_pair_only_anatomy_v1 import quote_frontier,receipt_anatomy

class AnatomyTests(unittest.TestCase):
    def test_missing_ask_is_not_feasible(self):
        r=quote_frontier({'UP':0,'DOWN':0},{'UP':[],'DOWN':[]},{},'UP','PROBE_CORE')
        self.assertFalse(r['askPairPass'])

    def test_surplus_not_command_to_repair(self):
        r=quote_frontier({'UP':8,'DOWN':2},{'UP':[(6,.2)],'DOWN':[]},
                         {'DOWN':{'bid':.79,'ask':.81}},'DOWN','ECONOMIC_CORE')
        self.assertFalse(r['askPairPass'])
        self.assertNotIn('repairRequired',r)

    def test_no_opposite_lot_does_not_predict_value(self):
        r=quote_frontier({'UP':1,'DOWN':0},{'UP':[(1,.9)],'DOWN':[]},
                         {'UP':{'bid':.9,'ask':.91}},'UP','SATELLITE_EXPAND')
        self.assertTrue(r['askPairPass'])
        self.assertIsNone(r['pairAskExcess'])

    def test_positive_pair_and_lossy_residual(self):
        receipts=[dict(sequence=1,side=1,price=.4,qty=2),dict(sequence=2,side=-1,price=.5,qty=1)]
        r=receipt_anatomy(receipts,{'cost':1.3,'UP':.7,'DOWN':-.3})
        self.assertAlmostEqual(r['pairedMargin'],.1)
        self.assertAlmostEqual(r['residualCost'],.4)

    def test_negative_pair_is_not_residual_loss(self):
        receipts=[dict(sequence=1,side=1,price=.6,qty=1),dict(sequence=2,side=-1,price=.5,qty=1)]
        r=receipt_anatomy(receipts,{'cost':1.1,'UP':-.1,'DOWN':-.1})
        self.assertAlmostEqual(r['negativePairMargin'],-.1)
        self.assertEqual(r['residualCost'],0)

if __name__=='__main__':unittest.main()
