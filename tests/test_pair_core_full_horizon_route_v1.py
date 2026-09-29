from collections import Counter
from types import SimpleNamespace
from unittest.mock import patch
import unittest
from tools.pair_core_full_horizon_route_v1 import full_horizon_view, make_sim


class DummyParent:
    def __init__(self, tape, arm):
        self.inv={'UP':1.,'DOWN':0.};self.cost=.5;self.veto=Counter()
        self._probe_stage='UNSELECTED';self.submits=0;self.calls=[];self.events=[]
        self.slot_key={1:'pending'}
    def _probe_prefix(self):return 'same'
    def _open_one_option(self, t, qv, end):
        self.calls.append((t,end));self.submits+=1
    def _event(self,t,event):self.events.append((t,event))


class FullHorizonRouteTests(unittest.TestCase):
    def minimal(self):
        return SimpleNamespace(MinimalPairRoleSim=object,
                               v2=SimpleNamespace(NO_NEW_EXPOSURE_MS=180000, sentinel=object()))
    def sim(self,arm='A'):
        with patch('tools.pair_core_full_horizon_route_v1.pool_factory', return_value=DummyParent):
            return make_sim(self.minimal())('tape',arm)
    def test_module_view_never_mutates_shared_constant(self):
        original=self.minimal();view=full_horizon_view(original)
        self.assertEqual(view.v2.NO_NEW_EXPOSURE_MS,0)
        self.assertEqual(original.v2.NO_NEW_EXPOSURE_MS,180000)
        self.assertIs(view.v2.sentinel,original.v2.sentinel)
    def test_all_arms_retain_preboundary_and_late_continuation(self):
        for arm in ('A','C','T'):
            s=self.sim(arm);s._open_one_option(1,{},300000)
            self.assertIsNone(s._probe_boundary)
            s._open_one_option(120000,{},300000)
            self.assertEqual(s.submits,2);self.assertEqual(s._probe_late_admissions,1)
            self.assertEqual(s._probe_boundary['prefix'],'same')
    def test_end_and_postend_never_submit_or_erase_owner(self):
        for t in (300000,300001):
            s=self.sim('T');s._probe_stage='WAIT_ACK';s._open_one_option(t,{},300000)
            self.assertEqual(s.submits,0);self.assertEqual(s.slot_key,{1:'pending'})
            self.assertEqual(s._probe_stage,'ABORT_MARKET_ENDED')
    def test_unknown_arm_rejected(self):
        with self.assertRaises(ValueError):self.sim('POLICY_SEARCH')


if __name__=='__main__':unittest.main()
