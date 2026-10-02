from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
import audit_target_controller_prediction_joint_support_v275b as mod

def test_required_prediction_is_frozen_current_plus_3s():
    assert mod.REQUIRED_PREDICTION==['micro_prediction_up_mid','micro_prediction_distance_05','micro_prediction_delta_3s','micro_prediction_range_3s','micro_prediction_pressure_aligned_delta_3s']

def test_market_gate_requires_joint_support():
    states=[{'market_id':1,'regime':'STRESS_2026_08_16'} for _ in range(40)]+[{'market_id':2,'regime':'ORDINARY_2026_08_17'} for _ in range(40)]
    joined=[{'market_id':1} for _ in range(35)]+[{'market_id':2} for _ in range(29)]
    complete=[{'market_id':1} for _ in range(30)]+[{'market_id':2} for _ in range(29)]
    by_id={r['marketId']:r for r in mod._market_report(states,joined,complete)}
    assert by_id[1]['jointEligible'] is True
    assert by_id[2]['jointEligible'] is False

def test_decision_requires_both_regimes():
    markets=[{'marketId':i,'regime':'STRESS_2026_08_16','jointEligible':True,'jointCompleteRows':100} for i in range(4)]
    markets += [{'marketId':i,'regime':'ORDINARY_2026_08_17','jointEligible':True,'jointCompleteRows':100} for i in range(4,8)]
    assert mod._decision(markets)['status']=='READY_FOR_V276_INDEPENDENT_REPAIR_AB'
    markets[-1]['jointEligible']=False
    assert mod._decision(markets)['status']=='INSUFFICIENT_JOINT_SUPPORT_DO_NOT_FIT_V276'
