from __future__ import annotations
import json
from tools.eth_repair_modular.parent_family_transition_aware_safety import ParentFamilyTransitionSafetyContext,ParentFamilyTransitionAwareSafetyPolicyV2
P=ParentFamilyTransitionAwareSafetyPolicyV2();rows=[]
def case(name,ctx,expect,reason=None):
 d=P.evaluate(ctx);ok=(d.safe==expect and (reason is None or d.reason==reason));rows.append({'name':name,'passed':ok,'expectSafe':expect,'decision':d.__dict__})
case('1946784_no_overflow',ParentFamilyTransitionSafetyContext(5,1.1734693878,3.8265306122,0,0,3.8265306122),True)
case('1946653_exact_transition_overflow',ParentFamilyTransitionSafetyContext(5,0,5,2.4390243902,2.4390243902,7.4390243902),True)
case('physical_allocation_mismatch',ParentFamilyTransitionSafetyContext(5,0,5,2,2,8),False,'PHYSICAL_ALLOCATION_MISMATCH')
case('repair_overpay',ParentFamilyTransitionSafetyContext(5,0,5.1,0,0,5.1),False,'REPAIR_OVERPAY')
case('overflow_bucket_mismatch',ParentFamilyTransitionSafetyContext(5,0,5,2,1.5,7),False,'OVERFLOW_BUCKET_MISMATCH')
case('overflow_not_visible',ParentFamilyTransitionSafetyContext(5,0,5,2,2,7,overflow_visible_to_transition_frontier=False),False,'OVERFLOW_NOT_VISIBLE_TO_TRANSITION_FRONTIER')
case('prebirth_leak',ParentFamilyTransitionSafetyContext(5,0,5,2,2,7,zero_prebirth_leak=False),False,'PREBIRTH_PAYMENT_LEAK')
case('duplicate_debt',ParentFamilyTransitionSafetyContext(5,0,5,2,2,7,zero_duplicate_debt=False),False,'DUPLICATE_DEBT')
case('inflight_within_remaining_debt',ParentFamilyTransitionSafetyContext(5,2,3,0,0,3,1.5,prospective_overflow_authorized=False,joint_recoverable=False),True)
case('prospective_overflow_authorized',ParentFamilyTransitionSafetyContext(5,0.35,4.65,0,0,4.65,2.79,prospective_overflow_authorized=True,joint_recoverable=True),True)
case('prospective_overflow_not_authorized',ParentFamilyTransitionSafetyContext(5,0.35,4.65,0,0,4.65,2.79,prospective_overflow_authorized=False,joint_recoverable=True),False,'UNAUTHORIZED_OR_UNRECOVERABLE_PROSPECTIVE_OVERFLOW')
case('prospective_overflow_not_recoverable',ParentFamilyTransitionSafetyContext(5,0.35,4.65,0,0,4.65,2.79,prospective_overflow_authorized=True,joint_recoverable=False),False,'UNAUTHORIZED_OR_UNRECOVERABLE_PROSPECTIVE_OVERFLOW')
out={'version':'PARENT_FAMILY_TRANSITION_AWARE_SAFETY_MICROWORLD_V1','policy':P.name,'passed':sum(x['passed'] for x in rows),'total':len(rows),'allPassed':all(x['passed'] for x in rows),'rows':rows};print(json.dumps(out,indent=2));raise SystemExit(0 if out['allPassed'] else 1)
