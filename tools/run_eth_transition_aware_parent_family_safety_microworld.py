from __future__ import annotations
import json
from tools.eth_repair_modular.transition_aware_parent_family_safety import TransitionAwareParentFamilySafetyContext,TransitionAwareParentFamilySafetyPolicyV2

P=TransitionAwareParentFamilySafetyPolicyV2(); rows=[]
def case(name,expect,**kw):
 d=P.evaluate(TransitionAwareParentFamilySafetyContext(**kw)); rows.append({'name':name,'pass':bool(d.safe)==bool(expect),'safe':d.safe,'decision':d.__dict__})
base=dict(initial_parent_debt=5.0,repair_paid=5.0,remaining_parent_debt=0.0,occupancy_over_reserved=0.0,overflow_born=0.0,transition_overflow=0.0,overflow_birth_observed=False,overflow_visible_to_transition_frontier=False,pre_birth_leak=0.0,duplicate_debt=0.0)
case('exact_old_parent_payoff',True,**base)
x=dict(base);x.update(repair_paid=3.826530612244898,remaining_parent_debt=1.173469387755102);case('1946784_family_partial_pay',True,**x)
x=dict(base);x.update(overflow_born=2.4390243902439033,transition_overflow=2.4390243902439033,overflow_birth_observed=True,overflow_visible_to_transition_frontier=True);case('1946653_exact_composite_overflow',True,**x)
x=dict(base);x.update(repair_paid=5.1,remaining_parent_debt=0.0);case('repair_overpay_rejected',False,**x)
x=dict(base);x.update(remaining_parent_debt=-0.1);case('negative_remaining_rejected',False,**x)
x=dict(base);x.update(occupancy_over_reserved=0.2);case('concurrent_occupancy_overreserve_rejected',False,**x)
x=dict(base);x.update(overflow_born=2.0,transition_overflow=1.5,overflow_birth_observed=True,overflow_visible_to_transition_frontier=True);case('overflow_bucket_mismatch_rejected',False,**x)
x=dict(base);x.update(overflow_born=2.0,transition_overflow=2.0,overflow_birth_observed=False,overflow_visible_to_transition_frontier=True);case('missing_overflow_birth_rejected',False,**x)
x=dict(base);x.update(overflow_born=2.0,transition_overflow=2.0,overflow_birth_observed=True,overflow_visible_to_transition_frontier=False);case('overflow_not_visible_to_frontier_rejected',False,**x)
x=dict(base);x.update(overflow_born=2.0,transition_overflow=2.0,overflow_birth_observed=True,overflow_visible_to_transition_frontier=True,pre_birth_leak=.1);case('prebirth_leak_rejected',False,**x)
x=dict(base);x.update(overflow_born=2.0,transition_overflow=2.0,overflow_birth_observed=True,overflow_visible_to_transition_frontier=True,duplicate_debt=.1);case('duplicate_debt_rejected',False,**x)
x=dict(base);x.update(repair_paid=0.0,remaining_parent_debt=5.0);case('no_fill_no_overflow_safe',True,**x)
out={'version':'TRANSITION_AWARE_PARENT_FAMILY_SAFETY_MICROWORLD_V1','policy':P.name,'passed':sum(r['pass'] for r in rows),'total':len(rows),'allPass':all(r['pass'] for r in rows),'rows':rows};print(json.dumps(out,ensure_ascii=False,indent=2))
if not out['allPass']:raise SystemExit(1)
