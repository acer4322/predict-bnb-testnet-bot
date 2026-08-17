from __future__ import annotations

import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
import audit_target_controller_prediction_independent_windows_v275 as mod


def test_history3_requires_current_fresh_and_two_second_span():
    rows=[
        {'market_id':1,'sample_ms':1000,'fresh_2s':True},
        {'market_id':1,'sample_ms':2000,'fresh_2s':False},
        {'market_id':1,'sample_ms':3000,'fresh_2s':True},
        {'market_id':1,'sample_ms':4000,'fresh_2s':True},
    ]
    got=mod.history3(rows)
    assert got[(1,1000)] is False
    assert got[(1,3000)] is True
    assert got[(1,4000)] is True


def test_decision_requires_four_markets_and_300_rows():
    base={'eligible':True,'rows':80}
    assert mod.decide([dict(base) for _ in range(4)])['status']=='READY_FOR_INDEPENDENT_REPAIR_VALIDATION'
    assert mod.decide([dict(base) for _ in range(3)])['status']=='PARTIAL_INDEPENDENT_COVERAGE_DO_NOT_MODEL_YET'


def test_development_market_set_is_frozen():
    assert mod.DEV=={1396279,1396303,1396309,1396369}
