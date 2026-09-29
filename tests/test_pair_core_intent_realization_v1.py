import unittest
from tools.pair_core_intent_realization_v1 import summarize


def frame(side='UP',ok=True,oid=1):
    m={s:dict(first=[.5,2,None],hasPhysicalSlot=True,potentialCrossOwners=[]) for s in ('UP','DOWN')}
    return dict(t=1000,outcome='ADMITTED' if ok else 'SUBMIT_REJECTED',decisions=[[side,'CORE',True,False]],menu=m,
                submits=[dict(ok=ok,orderId=oid if ok else None,side=side,price=.5,qty=2)])


class IntentTests(unittest.TestCase):
    def test_zero_fill_owners_retained(self):
        s=summarize([frame()],[])['sideTotals']['UP']
        self.assertEqual(s['zeroFillOwners'],1);self.assertEqual(s['shareRealizationRate'],0)

    def test_partial_down_receipt(self):
        s=summarize([frame('DOWN')],[dict(order_id=1,side=-1,qty=1,price=.6)])['sideTotals']['DOWN']
        self.assertAlmostEqual(s['paid'],.4);self.assertAlmostEqual(s['shareRealizationRate'],.5)

    def test_orphan_stops(self):
        with self.assertRaises(AssertionError):summarize([], [dict(order_id=99,side=1,qty=1,price=.5)])

    def test_quote_not_authority(self):
        f=frame(ok=False);f['menu']['UP']['first']=None;f['menu']['DOWN']['hasPhysicalSlot']=False
        s=summarize([f],[])
        self.assertEqual(s['quoteAvailability']['selectedMissingOtherQuoteExists'],1)
        self.assertNotIn('selectedMissingOtherQuoteSlotAndNoPotentialCross',s['quoteAvailability'])


if __name__=='__main__':unittest.main()
