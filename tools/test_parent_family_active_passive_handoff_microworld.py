from __future__ import annotations
import json
from tools.eth_repair_modular.parent_family_active_passive_handoff import FamilyCarrierState,ParentFamilyHandoffContext,ParentFamilyActivePassiveHandoffPolicyV1

def C(k,r,s,a,b,l,c=False):return FamilyCarrierState(k,r,s,a,b,l,c)
def main():
 p=ParentFamilyActivePassiveHandoffPolicyV1(); rows=[]
 def case(name,cap,carriers,expect):
  d=p.evaluate(ParentFamilyHandoffContext(1,cap,tuple(carriers))); ok=bool(d.safe)==bool(expect);rows.append({'name':name,'passed':ok,'expectSafe':expect,'decision':d.__dict__})
 case('one_passive_plus_active_safe',5,[C('P','PASSIVE',2,0,0,True),C('A','ACTIVE',1.8,0,0,True)],True)
 case('1946784_both_fill_safe',5,[C('P','PASSIVE',2.040816,2.040816,0,False),C('A','ACTIVE',1.785714,1.785714,0,False)],True)
 case('two_passive_plus_active_safe',5,[C('P1','PASSIVE',1.5,0,0,True),C('P2','PASSIVE',1.5,0,0,True),C('A','ACTIVE',1.5,0,0,True)],True)
 case('cancel_pending_still_owned',3,[C('P','PASSIVE',2,0,0,False,True),C('A','ACTIVE',1.5,0,0,True)],False)
 case('late_fill_during_cancel_safe',5,[C('P','PASSIVE',2,1,0,False,True),C('A','ACTIVE',2,0,0,True)],True)
 case('post_active_baseline_excludes_old_fill',3,[C('P','PASSIVE',3,2,1,False),C('A','ACTIVE',2,2,0,False)],True)
 case('new_replacement_after_active_safe',4,[C('P2','PASSIVE',1.8,1.8,0,False),C('A','ACTIVE',2,2,0,False)],True)
 case('realized_overfill_detected',3,[C('P','PASSIVE',2,2,0,False),C('A','ACTIVE',2,2,0,False)],False)
 case('worst_case_inflight_overfill_detected',3,[C('P','PASSIVE',2,0,0,True),C('A','ACTIVE',2,0,0,True)],False)
 case('terminal_unfilled_releases_capacity',3,[C('P','PASSIVE',2,0,0,False),C('A','ACTIVE',2,0,0,True)],True)
 case('partial_fill_plus_remaining_exact_cap',5,[C('P','PASSIVE',3,1,0,True),C('A','ACTIVE',2,1,0,True)],True)
 case('baseline_fill_not_double_counted',2,[C('P','PASSIVE',4,3,2,False),C('A','ACTIVE',1,1,0,False)],True)
 out={'version':'PARENT_FAMILY_ACTIVE_PASSIVE_HANDOFF_MICROWORLD_V1','policy':p.name,'passed':sum(x['passed'] for x in rows),'total':len(rows),'allPassed':all(x['passed'] for x in rows),'rows':rows};print(json.dumps(out,indent=2))
if __name__=='__main__':main()
