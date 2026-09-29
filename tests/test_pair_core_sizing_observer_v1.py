from collections import Counter
from copy import deepcopy
from types import SimpleNamespace
import unittest
from tools.pair_core_sizing_observer_v1 import make_observed
from tools.pair_core_research_scope_preflight_v1 import validate,PROFILE_DEVIATIONS,RUNTIME_CHECKS,NATIVE


class ObserverTests(unittest.TestCase):
    def make(self):
        class Parent:
            def __init__(s,*args):
                s.book={'bids':{.07:1,.09:1},'asks':{.93:1,.91:1}}
                s.slot_key={};s.veto=Counter();s.n=1
            def _open_one_option(s,t,qv,end):
                if end-t<=180000:s.veto['LATE_180S']+=1;return
                side,role,pair,budget=s._role_decision(qv)
                p,q,proj=s._candidate_from_levels(side,pair,budget)
                return s._submit_role(t,side,role,p,q,proj,'TEST')
            def _role_decision(s,qv):return 'UP','PROBE_CORE',True,False
            def _candidate_from_levels(s,*a):return .09,1/.09,None
            def _submit_role(s,*a):s.n+=1;return True
        return make_observed(Parent,SimpleNamespace(v2=SimpleNamespace(kprice=lambda p:round(p,10))))(None)

    def test_before_role_block_keeps_domain_not_fabricated_role(self):
        s=self.make();s._open_one_option(200000,{},300000)
        f=s._sz_frames[0]
        self.assertEqual(f['roles'],[]);self.assertEqual(f['outcome'],'BLOCK_BEFORE_ROLE')
        self.assertEqual(f['domain']['UP']['added18'],[.07])
        self.assertEqual(f['domain']['DOWN']['added18'],[.07])
        self.assertEqual(f['veto'],{'LATE_180S':1})

    def test_early_actual_role_candidate_submit_once(self):
        s=self.make();self.assertTrue(s._open_one_option(1000,{},300000))
        f=s._sz_frames[0]
        self.assertEqual(len(f['roles']),1);self.assertEqual(len(f['candidates']),1)
        self.assertEqual(f['submits'][0]['orderId'],1)
        self.assertEqual(f['outcome'],'ADMITTED');self.assertIsNone(s._sz_current)


class SizingScopeTests(unittest.TestCase):
    def declaration(self):
        return dict(profile='S_ASSET_SIZING_PASSIVE',
            acknowledged_deviations=sorted(PROFILE_DEVIATIONS['S_ASSET_SIZING_PASSIVE']),
            claims=['MECHANISM_ONLY'],native_sha256=NATIVE,required_runtime_checks=sorted(RUNTIME_CHECKS),
            target_runtime_inputs=False,preserve_original_artifacts=True,frozen_sources={'x.py':'0'*64},
            asset_route_sizing={'ETH':{'PASSIVE':[1,12]},'BTC':{'PASSIVE':[1,18]}},
            scope_audit='PAIR_CORE_ASSET_SIZING_SMOKE4_PREREG_20260910.md')
    def test_explicit_reviewed_scope_passes_only_metadata(self):self.assertEqual(validate(self.declaration()),[])
    def test_asset_or_route_scope_cannot_drift(self):
        d=self.declaration()
        for wrong in ({'ETH':{'PASSIVE':[1,12]},'BTC':{'PASSIVE':[1,12]}},
                      {'ETH':{'PASSIVE':[1,12]},'BTC':{'ACTIVE':[1,18]}}):
            d['asset_route_sizing']=wrong;self.assertIn('ASSET_ROUTE_SIZING_CONTRACT_MISMATCH',validate(d))
    def test_missing_audit_and_full_manager_claim_rejected(self):
        d=self.declaration();del d['scope_audit'];self.assertTrue(validate(d))
        d=self.declaration();d['claims']=['WHOLE_LIFECYCLE_READINESS'];self.assertTrue(validate(d))


if __name__=='__main__':unittest.main()
