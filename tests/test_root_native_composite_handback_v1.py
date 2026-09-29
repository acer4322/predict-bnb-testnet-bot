import pytest
from tools.run_root_native_composite_handback_v1 import is_repair_proposal


@pytest.mark.parametrize('repair,overflow,role,side,reached,expected',[
    (2,0,'ECONOMIC_CORE','UP',True,True),
    (2,.08,'ECONOMIC_CORE','UP',True,True),
    (0,2,'SATELLITE_EXPAND','UP',True,False),
    (2,0,'SATELLITE_REPAIR','DOWN',True,False),
    (2,0,'SATELLITE_REPAIR','UP',False,False),
])
def test_composite_allowed_but_not_other_objective(repair,overflow,role,side,reached,expected):
    p=dict(transportReached=reached,candidate=dict(side=side,role=role,
        split=dict(repairQty=repair,overflowQty=overflow)))
    assert is_repair_proposal(p,'UP') == expected
