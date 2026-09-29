import unittest
from tools.hft244_detail_v2_receipts_v1 import attach_receipts,first_divergence

class DetailReceiptTests(unittest.TestCase):
    def test_two_prices_same_owner_stay_separate(self):
        rows=[dict(side='UP',confirmedQty=q,executionPriceFromInheritedSubstrate=p) for q,p in [(1,.4),(2,.41)]]
        receipts=[dict(side=1,qty=q,contractPrice=p,key='UP_1',sequence=i,maker=0) for i,(q,p) in enumerate([(1,.4),(2,.41)],1)]
        attach_receipts(rows,receipts)
        self.assertEqual([r['receiptSequence'] for r in rows],[1,2])
    def test_wrong_owner_clock_coalescing_fails(self):
        with self.assertRaises(AssertionError):attach_receipts([], [dict(key='UP_1')])
    def test_price_mismatch_fails(self):
        with self.assertRaises(AssertionError):attach_receipts([dict(side='UP',confirmedQty=1,executionPriceFromInheritedSubstrate=.4)], [dict(side=1,qty=1,contractPrice=.5)])
    def test_first_action_change_retains_prestate_test(self):
        a=dict(decisionId=1,t=2,preHash='same',actions=[],selected=None,routes=[])
        b=dict(a,actions=[dict(event='cancel')]);self.assertTrue(first_divergence([a],[b])['samePreActionState'])

if __name__=='__main__':unittest.main()
