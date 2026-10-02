import unittest
from types import SimpleNamespace
from tools.hft244_pair_confirmed_handoff_v1 import ready_reason,make_sim

class HandoffTests(unittest.TestCase):
    def reason(self,**changes):
        args=dict(remaining_conflicts=[],ask=.69,limit=.69,available=3.125,qty=1/.69,occupied=2,late=False)
        args.update(changes);return ready_reason(**args)
    def test_ready_after_ack(self):self.assertEqual(self.reason(),'READY')
    def test_cancel_request_does_not_release(self):self.assertEqual(self.reason(remaining_conflicts=['DOWN_12']),'WAIT_TERMINAL_ACK')
    def test_no_repricing_to_chase(self):self.assertEqual(self.reason(ask=.70),'ABORT_NO_CHASE_PRICE')
    def test_liability_paid_elsewhere_aborts(self):self.assertEqual(self.reason(available=.5),'ABORT_LIABILITY_OR_RESERVATION_CHANGED')
    def test_no_dust_resize(self):self.assertEqual(self.reason(available=1.4),'ABORT_LIABILITY_OR_RESERVATION_CHANGED')
    def test_capacity_and_existing_fence(self):
        self.assertEqual(self.reason(occupied=4),'ABORT_CAPACITY_CHANGED')
        self.assertEqual(self.reason(late=True,remaining_conflicts=['pending']),'ABORT_INHERITED_BOUNDARY')
    def fake(self,arm='A',stage='UNSELECTED'):
        class Minimal:
            def _submit_role(self,*args):return 'ORIGINAL_PATH'
        Sim=make_sim(SimpleNamespace(MinimalPairRoleSim=Minimal))
        s=object.__new__(Sim);s.slot_key={};s.max_slots=4
        s._probe_arm=arm;s._probe_stage=stage;s._probe_cross_seen=0;s._probe_cross_blocks=0
        s._reservations=lambda:[dict(key='DOWN_1',side='DOWN',price=.31)]
        return s
    def submit(self,s):return s._submit_role(1,'UP','ECONOMIC_CORE',.69,1/.69,None,'fixture')
    def test_full_capacity_never_reaches_cross_check(self):
        s=self.fake();s.slot_key={i:str(i) for i in range(4)}
        s._reservations=lambda:self.fail('capacity-rejected candidate was examined')
        self.assertEqual(self.submit(s),'ORIGINAL_PATH')
    def test_baseline_and_pretreatment_shadow_only(self):
        for arm,stage in [('A','A_OBSERVED'),('C','UNSELECTED'),('T','UNSELECTED')]:
            s=self.fake(arm,stage);self.assertEqual(self.submit(s),'ORIGINAL_PATH')
            self.assertEqual(s._probe_cross_blocks,0)
    def test_guard_is_part_of_postselection_protocol(self):
        s=self.fake('T','ACTIVE_OWNER_PENDING');self.assertFalse(self.submit(s))
        self.assertEqual(s._probe_cross_blocks,1)

if __name__=='__main__':unittest.main()
