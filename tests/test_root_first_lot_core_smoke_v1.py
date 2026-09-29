import unittest
from tools.run_root_first_lot_core_smoke_v1 import eligible_quantity


class FirstLotTests(unittest.TestCase):
    def test_valid_whole_original_lot(self):
        self.assertTrue(eligible_quantity(1/0.51,1/0.47))
    def test_no_below_minimum_or_invented_overflow(self):
        self.assertFalse(eligible_quantity(2,1.9))
        self.assertFalse(eligible_quantity(2,2))
    def test_keep_existing_maximum_and_finite_values(self):
        self.assertFalse(eligible_quantity(2,12.1))
        self.assertFalse(eligible_quantity(2,float('nan')))
        self.assertFalse(eligible_quantity(-1,2))


if __name__=='__main__':unittest.main()
