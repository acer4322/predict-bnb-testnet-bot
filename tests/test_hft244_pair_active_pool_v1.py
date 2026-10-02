from collections import Counter
from types import SimpleNamespace
import unittest
from tools.hft244_pair_active_pool_v1 import make_sim,pool_counts


class ActivePoolTests(unittest.TestCase):
    def test_four_passive_plus_one_active(self):
        self.assertEqual(pool_counts({1:'u',2:'v',3:'w',4:'x',5:'a'},'a'),(4,1))
        self.assertEqual(pool_counts({1:'u',2:'v'},'a'),(2,0))

    def test_wrong_pool_or_duplicate_owner_fails(self):
        for slots in ({1:'a'},{5:'passive'},{1:'a',5:'a'},{1:'u',2:'v',3:'w',4:'x',6:'y'}):
            with self.assertRaises(ValueError):pool_counts(slots,'a')

    def fake(self):
        Sim=make_sim(SimpleNamespace(MinimalPairRoleSim=object,v2=SimpleNamespace()))
        s=object.__new__(Sim);s.slot_key={1:'u',2:'v',3:'w',5:'a'};s._probe_key='a'
        s._probe_arm='T';s._probe_stage='ACTIVE_OWNER_PENDING';s.veto=Counter()
        s._probe_cross_seen=0;s._probe_cross_blocks=0;s.serialize_same_side=False
        s._probe_pool_extra_admissions=0
        return s

    def test_active_still_blocks_self_cross(self):
        s=self.fake();s._reservations=lambda:[dict(key='a',side='DOWN',price=.75)]
        self.assertFalse(s._submit_role(1,'UP','SATELLITE_EXPAND',.3,1/.3,None,'fixture'))
        self.assertEqual(s._probe_cross_blocks,1)

    def test_fifth_passive_is_rejected_before_submit(self):
        s=self.fake();s.slot_key[4]='fourth'
        s._reservations=lambda:self.fail('full passive capacity should reject first')
        self.assertFalse(s._submit_role(1,'UP','SATELLITE_EXPAND',.3,1/.3,None,'fixture'))

    def test_fourth_passive_can_submit_without_hiding_active(self):
        s=self.fake();s._reservations=lambda:[dict(key='a',side='UP',price=.3)]
        s.n=10;s.key_role={};s.role_submits=Counter();s.marginal_pair_credit=Counter();s.marginal_pair_risk_spend=Counter()
        s._pair_edge=lambda *a:(0.,0.);s._state=lambda:('TWO_SIDED',None,'DOWN');s._physical_floor=lambda:-1
        s.slot_history=[];observed=[]
        s.submit=lambda *a:observed.append(dict(s.slot_key))
        self.assertTrue(s._submit_role(1,'UP','SATELLITE_EXPAND',.2,5,None,'fixture'))
        self.assertEqual(observed[0][5],'a')
        self.assertEqual(s.slot_key[4],'UP_10')
        self.assertEqual(s._probe_pool_extra_admissions,1)

    def test_cancel_pending_active_not_released_by_pool_counter(self):
        # Pool counting never interprets a cancel request as terminal release.
        slots={1:'passive',5:'cancel_pending_active'}
        self.assertEqual(pool_counts(slots,'cancel_pending_active'),(1,1))
        self.assertIn(5,slots)


if __name__=='__main__':unittest.main()
