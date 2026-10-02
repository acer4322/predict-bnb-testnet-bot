import unittest
from tools.hft244_pair_paid_probe_v1 import candidate,digest

class PaidOptionTests(unittest.TestCase):
    def option(self,**kw):
        args=dict(side='UP',role='ECONOMIC_CORE',ask=.7,average=.4,unmatched=5.,pending=0.,occupied=3,used_prices=[])
        args.update(kw);return candidate(**args)
    def test_one_existing_ticket_not_gap_closing(self):
        r=self.option();self.assertAlmostEqual(r['qty']*r['price'],1);self.assertLess(r['qty'],5)
    def test_cancel_pending_still_blocks(self):self.assertIsNone(self.option(pending=4.))
    def test_submitted_occupancy_not_free(self):self.assertIsNone(self.option(occupied=4))
    def test_no_scaling_down_to_fit_dust(self):self.assertIsNone(self.option(unmatched=.1))
    def test_not_cheap_pair_relabel(self):self.assertIsNone(self.option(average=.2))
    def test_not_expand_relabel(self):self.assertIsNone(self.option(role='SATELLITE_EXPAND'))
    def test_missing_or_duplicate_quote(self):
        self.assertIsNone(self.option(ask=None));self.assertIsNone(self.option(used_prices=[.7]))
    def test_snapshot_detects_owner_and_pending_changes(self):
        self.assertNotEqual(digest({'owner':'a','pending':1}),digest({'owner':'b','pending':1}))
        self.assertNotEqual(digest({'owner':'a','pending':1}),digest({'owner':'a','pending':0}))
    def test_unknown_state_fails_closed(self):
        with self.assertRaises(TypeError):digest(object())

if __name__=='__main__':unittest.main()
