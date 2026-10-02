import sys
from pathlib import Path
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from export_graduation_local_inputs import authoritative_label, btc5m_title, depth


class GraduationEvidenceTests(unittest.TestCase):
    def test_nonbinary_or_unresolved_markets_cannot_become_binary_labels(self):
        m={'status':'RESOLVED','outcomes':[{'name':'Up','status':'WON'},{'name':'Down','status':'WON'}]}
        self.assertIsNone(authoritative_label(m))
        m['outcomes'][1]['status']='LOST';self.assertEqual(authoritative_label(m),'UP')
        m['status']='ACTIVE';self.assertIsNone(authoritative_label(m))

    def test_title_filter_handles_noon_and_midnight_but_excludes_other_durations(self):
        for t in ['11:55AM-12PM','11:55PM-12AM','3:55PM-4PM','12:55PM-1PM']:
            self.assertTrue(btc5m_title('Bitcoin Up or Down - September 27, '+t+' ET'))
        self.assertFalse(btc5m_title('Bitcoin Up or Down - September 27, 3:45PM-4PM ET'))
        self.assertFalse(btc5m_title('Ethereum Up or Down - September 27, 3:55PM-4PM ET'))

    def test_down_depth_preserves_own_prices_and_insufficient_liquidity(self):
        b={'source_ms':1000,'received_ms':1800,'chain_received_max_ms':1900,
           'bids':[[.6,10],[.5,20]],'asks':[[.7,5]]}
        d=depth(b,'DOWN',40,1500)
        self.assertEqual(d['opposite_asks_top3'],[[.4,10],[.5,20]])
        self.assertAlmostEqual(d['opposite_bid'],.3)
        self.assertFalse(d['available_by_decision'])
        self.assertFalse(d['observed_depth_covers_qty']);self.assertIsNone(d['hedge_vwap'])
        self.assertEqual(d['hedge_uncovered_qty'],10)
        d=depth(b,'DOWN',30,2000)
        self.assertTrue(d['available_by_decision']);self.assertAlmostEqual(d['hedge_vwap'],14/30)


if __name__=='__main__':unittest.main()
