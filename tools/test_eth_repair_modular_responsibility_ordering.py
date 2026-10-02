from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT/'tools') not in sys.path:sys.path.insert(0,str(ROOT/'tools'))

from eth_repair_modular.responsibility_frontier import AuthoritativeRepairResponsibilityFrontierV2,RepairResponsibilityFrontierInput
from eth_repair_modular.responsibility_transition import RepairFirstResponsibilityTransitionV1,ResponsibilityTransitionContext
from eth_repair_modular.ownership_transition_guard import ProspectiveOwnershipTransitionGuardV1,ProspectiveOwnershipTransitionContext


def main()->int:
    frontier=AuthoritativeRepairResponsibilityFrontierV2();transition=RepairFirstResponsibilityTransitionV1();guard=ProspectiveOwnershipTransitionGuardV1()

    # Ordinary current DOWN Repair parent must be visible even with no overflow debt.
    f=frontier.evaluate(RepairResponsibilityFrontierInput('DOWN',2.0,0.0,0.0))
    assert f.current_parent_bound and f.debt_down==2.0 and f.debt_up==0.0

    # Prospective DOWN Expand conflicts with live DOWN Repair responsibility.
    td=transition.evaluate(ResponsibilityTransitionContext('DOWN',f.debt_up,f.debt_down))
    gd=guard.evaluate(ProspectiveOwnershipTransitionContext('DOWN',True,td.allow_expand_ownership,td.bind_role,td.reason))
    assert not td.allow_expand_ownership and td.bind_role=='REPAIR'
    assert not gd.allow_thesis_birth

    # Prospective UP Expand remains legal while DOWN Repair debt is live.
    tu=transition.evaluate(ResponsibilityTransitionContext('UP',f.debt_up,f.debt_down))
    gu=guard.evaluate(ProspectiveOwnershipTransitionContext('UP',True,tu.allow_expand_ownership,tu.bind_role,tu.reason))
    assert tu.allow_expand_ownership and gu.allow_thesis_birth

    # Overflow debt remains visible and max-binding avoids double-accounting in the frontier.
    f2=frontier.evaluate(RepairResponsibilityFrontierInput('DOWN',2.0,1.5,3.0))
    assert f2.debt_up==1.5 and f2.debt_down==3.0

    print({'ok':True,'sameSide':{'transition':td.reason,'guard':gd.reason},'oppositeSide':{'transition':tu.reason,'guard':gu.reason},'frontier':{'up':f2.debt_up,'down':f2.debt_down}})
    return 0

if __name__=='__main__':raise SystemExit(main())
