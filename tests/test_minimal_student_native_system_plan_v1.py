"""Pure interface checks before native whole-plan migration smoke. No HFT."""
import unittest
from copy import deepcopy
from dataclasses import replace
from collections import deque

from tools.pair_core_economic_grant_ledger_v1 import EconomicGrantLedger,Grant
from tools.minimal_student_system_plan_v1 import SystemPlanGateway,PlanAction,PlanRejected
from tools.minimal_student_native_system_plan_v1 import (
    Envelope,frame_id,complete_envelope,check_envelope,CausalWholePlanProbe,
)


class NativePlanContractTests(unittest.TestCase):
    def setup_frame(self,live=False):
        l=EconomicGrantLedger(100.)
        for i,s in [(1,'UP'),(2,'DOWN')]:l.issue(Grant(i,'fixture',s,0.,110.,50.,'TEST_ONLY'))
        p=CausalWholePlanProbe(30.)
        g=SystemPlanGateway(l,asset='BTC',policy_id=p.policy_id,capabilities=('PASSIVE',))
        if live:
            a=g.propose('prior',[PlanAction('NEW','UP_1',1,'PASSIVE',.4,30.)],p.continuation_id)
            g.commit(a,now_ms=1,market_end_ms=300000);g.record_send('UP_1','SENT',evidence='TEST')
        f=dict(t=1000,end=300000,index=1,book={'bids':{.4:100.},'asks':{.42:100.}},
            previous_book={'bids':{},'asks':{}},quotes={'imb':.1},
            own_view=dict(inv={'UP':0.,'DOWN':0.},cost=0.,un={'UP':deque(),'DOWN':deque()},
                orders={} if not live else {'UP_1':dict(n=1,side='UP',qty=30.,cum=0.,price=.4,placed=1,status='NEW')},
                slot_key={} if not live else {1:'UP_1'},key_role={} if not live else {'UP_1':'SEED'},
                n=1 if not live else 2,max_slots=4),snapshots={},cancellable={} if not live else {'UP_1':True},
            ledger=deepcopy(g.ledger),gateway_state_id=g.snapshot_id())
        return f,p,g
    def test_complete_bilateral_plan_without_native_access(self):
        f,p,g=self.setup_frame();e=p.produce(f)
        self.assertEqual(len([a for a in e.plan.actions if a.kind=='NEW']),2)
        self.assertTrue(check_envelope(f,e,p.policy_id,p.continuation_id))
        g.commit(e.plan,now_ms=f['t'],market_end_ms=f['end'])
        self.assertEqual(len(g.ledger.carriers),2)
    def test_frame_has_no_engine_future_or_target(self):
        f,_,_=self.setup_frame()
        self.assertTrue(set(f).isdisjoint({'bt','payload','updates','winner','target','future'}))
    def test_changed_book_invalidates_plan(self):
        f,p,_=self.setup_frame();e=p.produce(f);f['book']['bids'][.4]=99.
        with self.assertRaisesRegex(PlanRejected,'STALE_MARKET'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_changed_clock_invalidates_plan(self):
        f,p,_=self.setup_frame();e=p.produce(f);f['t']+=1
        with self.assertRaises(PlanRejected):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_missing_live_maintenance_rejected(self):
        f,p,_=self.setup_frame(True);e=complete_envelope(f,p,[]);e.plan=replace(e.plan,actions=())
        with self.assertRaisesRegex(PlanRejected,'WHOLE_LIVE'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_keep_plan_is_explicit_and_legal(self):
        f,p,_=self.setup_frame(True);e=complete_envelope(f,p,[])
        self.assertEqual(e.plan.actions[0].kind,'KEEP')
        self.assertTrue(check_envelope(f,e,p.policy_id,p.continuation_id))
    def test_policy_version_mixing_rejected(self):
        f,p,_=self.setup_frame();e=p.produce(f);e.plan=replace(e.plan,policy_id='OTHER')
        with self.assertRaisesRegex(PlanRejected,'UNDECLARED'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_continuation_mixing_rejected(self):
        f,p,_=self.setup_frame();e=p.produce(f);e.plan=replace(e.plan,continuation_id='OLD')
        with self.assertRaises(PlanRejected):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_quantity_metadata_drift_rejected(self):
        f,p,_=self.setup_frame();e=p.produce(f);e.operations[0]['qty']=18.
        with self.assertRaisesRegex(PlanRejected,'METADATA_MISMATCH'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_native_active_rejected_as_unsupported(self):
        f,p,_=self.setup_frame();e=p.produce(f);a=e.plan.actions[0]
        e.plan=replace(e.plan,actions=(replace(a,route='ACTIVE'),)+e.plan.actions[1:])
        with self.assertRaisesRegex(PlanRejected,'ACTIVE_UNSUPPORTED'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_hidden_new_operation_rejected(self):
        f,p,_=self.setup_frame();e=p.produce(f);e.operations=[]
        with self.assertRaisesRegex(PlanRejected,'UNREPRESENTED'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_stale_gateway_rejected(self):
        f,p,_=self.setup_frame();e=p.produce(f);e.plan=replace(e.plan,state_id='STALE')
        with self.assertRaisesRegex(PlanRejected,'STALE_GATEWAY'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_pending_cancel_does_not_free_slot(self):
        f,p,_=self.setup_frame(True);f['own_view']['max_slots']=1
        ops=[dict(kind='CANCEL',key='UP_1',origin='WHOLE_POLICY',reason='TEST',slot=1),
             dict(kind='NEW',key='DOWN_2',parent_id=2,side='DOWN',price=.58,qty=30.,role='TEST')]
        e=complete_envelope(f,p,ops)
        with self.assertRaisesRegex(PlanRejected,'SLOT_CAP'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_180s_fence_preserved(self):
        f,p,_=self.setup_frame();e=p.produce(f);f['t']=120000;e.frame_id=frame_id(f)
        with self.assertRaisesRegex(PlanRejected,'180S'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_nonvisible_price_rejected(self):
        f,p,_=self.setup_frame();e=p.produce(f);ops=e.operations;ops[0]['price']=.39
        e=complete_envelope(f,p,ops)
        with self.assertRaisesRegex(PlanRejected,'VISIBLE'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_pair_economics_preserved(self):
        f,p,_=self.setup_frame();e=p.produce(f);f['own_view']['un']['DOWN'].append((30.,.7));e.frame_id=frame_id(f)
        with self.assertRaisesRegex(PlanRejected,'PAIR_GATE'):check_envelope(f,e,p.policy_id,p.continuation_id)
    def test_own_fill_changes_continuation_plan(self):
        f,p,g=self.setup_frame(True);e0=p.produce(deepcopy(f))
        self.assertEqual([a.kind for a in e0.plan.actions],['KEEP'])
        f['own_view']['inv']['UP']=5.;f['own_view']['un']['UP'].append((5.,.4))
        e1=p.produce(deepcopy(f))
        self.assertIn('CANCEL',[a.kind for a in e1.plan.actions])
        self.assertIn('NEW',[a.kind for a in e1.plan.actions])
        self.assertEqual(e1.plan.continuation_id,e0.plan.continuation_id)


if __name__=='__main__':unittest.main()
