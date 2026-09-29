import unittest
from tools.analyze_pair_core_asset_sizing4_v1 import classify


class VerdictTests(unittest.TestCase):
    def test_parity_has_correct_scope(self):
        self.assertEqual(classify('ETH',{},0,0,True),'ETH_FULL_PARITY')
        self.assertEqual(classify('ETH',{},0,0,False),'CORRECTNESS_STOP')
        self.assertEqual(classify('BTC',{},0,0,True),'NOT_EXERCISED')
    def test_candidate_submit_and_fill_are_distinct(self):
        counts={'earlySelectedSideHasAddedDomain':5}
        self.assertEqual(classify('BTC',counts,0,0,True),'PRICE_DOMAIN_ONLY_NOT_SELECTED')
        counts['candidateInAddedDomain']=1
        self.assertEqual(classify('BTC',counts,0,0,False),'NEW_DOMAIN_CANDIDATE_NOT_ADMITTED')
        self.assertEqual(classify('BTC',counts,1,0,False),'NATIVE_SUBMIT_EXERCISED_ZERO_FILL')
        self.assertEqual(classify('BTC',counts,1,.2,False),'EXECUTION_EXERCISED')


if __name__=='__main__':unittest.main()
