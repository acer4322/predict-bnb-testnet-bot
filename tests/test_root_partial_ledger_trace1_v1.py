from types import SimpleNamespace

import pytest

from tools.run_root_partial_ledger_trace1_v1 import native_order_fields


@pytest.mark.parametrize('status,valid',[(1,False),(3,True),(4,False),(5,True)])
def test_snapshot_does_not_require_unexposed_maker(status,valid):
    raw=SimpleNamespace(qty=1.35,leaves_qty=.35,exec_qty=1.,exec_price=.26,
                        exch_timestamp=2000,local_timestamp=1100,status=status,req=0)
    result=native_order_fields(raw)
    assert result['maker'] is None
    assert result['makerMissingReason']=='NOT_EXPOSED_BY_PYTHON_ORDER_API'
    assert result['executionFieldsValid'] is valid
    assert result['leaves_qty']==.35
    assert result['exec_qty']==1.
