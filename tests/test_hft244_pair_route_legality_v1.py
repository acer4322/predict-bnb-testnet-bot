import unittest
from tools.hft244_pair_route_legality_v1 import crossing_owners

class NativeCrossTests(unittest.TestCase):
    def test_observed_paid_seam_crosses_down12(self):
        rows=[dict(key='DOWN_10',side='DOWN',price=.30),dict(key='DOWN_11',side='DOWN',price=.29),dict(key='DOWN_12',side='DOWN',price=.31)]
        self.assertEqual(crossing_owners('UP',.69,rows),['DOWN_12'])
    def test_cancel_pending_and_none_not_free(self):
        for status in ('NONE','NEW'):
            self.assertEqual(crossing_owners('DOWN',.31,[dict(key='UP_2',side='UP',price=.69,status=status,cancelRequested=True)]),['UP_2'])
    def test_non_crossing_and_same_native_side(self):
        self.assertEqual(crossing_owners('UP',.68,[dict(key='DOWN_3',side='DOWN',price=.31),dict(key='UP_1',side='UP',price=.8)]),[])
    def test_released_owner_absent(self):self.assertEqual(crossing_owners('UP',.69,[]),[])

if __name__=='__main__':unittest.main()
