from collections import Counter
from types import SimpleNamespace
import unittest
from tools.pair_core_full_horizon_v1 import make_sim


class Dummy:
    def __init__(self,tape,n,serial):
        self.inv={'UP':1.,'DOWN':0.};self.cost=.5;self.veto=Counter();self.max_slots=4
        self.role_budget_blocks=Counter();self.submits=0;self.calls=[];self.rows=[];self.cand=(.5,2,None)
    def _open_one_option(self,t,qv,end):self.calls.append('original')
    def _role_decision(self,qv):self.calls.append('role');return 'UP','SATELLITE_EXPAND',True,False
    def _live_role_rows(self,side):return self.rows
    def _candidate_from_levels(self,side,pair,budget):self.calls.append(('candidate',pair,budget));return self.cand
    def _submit_role(self,*args):self.submits+=1;self.calls.append('submit')


class HorizonTests(unittest.TestCase):
    def sim(self,full):
        s=make_sim(SimpleNamespace(MinimalPairRoleSim=Dummy,v2=SimpleNamespace(NO_NEW_EXPOSURE_MS=180000)))('tape',full)
        s._probe_prefix=lambda:'frozen';return s
    def test_original_always_delegates(self):
        s=self.sim(False);s._open_one_option(200000,{},300000);self.assertEqual(s.calls,['original'])
    def test_preboundary_unchanged(self):
        s=self.sim(True);s._open_one_option(119999,{},300000);self.assertEqual(s.calls,['original']);self.assertIsNone(s._probe_boundary)
    def test_late_expand_not_repair_only(self):
        s=self.sim(True);s._open_one_option(120000,{},300000)
        self.assertEqual(s.submits,1);self.assertEqual(s._probe_late_admissions,1);self.assertIn(('candidate',True,False),s.calls)
    def test_closed_market_no_new_request(self):
        s=self.sim(True);s._open_one_option(300000,{},300000);self.assertEqual(s.submits,0);self.assertEqual(s.veto['MARKET_ENDED_NO_NEW_REQUEST'],1)
    def test_side_capacity_preserved(self):
        s=self.sim(True);s.rows=[1]*4;s._open_one_option(120000,{},300000);self.assertEqual(s.submits,0);self.assertEqual(s.veto['SIDE_SLOT_CAP_FULL'],1)
    def test_no_candidate_no_substitution(self):
        s=self.sim(True);s.cand=None;s._open_one_option(120000,{},300000);self.assertEqual(s.submits,0)


if __name__=='__main__':unittest.main()
