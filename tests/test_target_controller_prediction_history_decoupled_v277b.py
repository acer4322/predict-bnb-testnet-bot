from __future__ import annotations
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))

import audit_target_controller_prediction_history_decoupled_v277b as mod


def test_prediction_history_uses_full_state_timeline():
    states=[{'market_id':1,'sample_ms':t} for t in (1000,2000,3000,4000)]
    pred={
        (1,1000):{'fresh_2s':True,'prediction_mid':0.40,'latest_age_ms':10},
        (1,2000):{'fresh_2s':True,'prediction_mid':0.45,'latest_age_ms':10},
        (1,3000):{'fresh_2s':True,'prediction_mid':0.50,'latest_age_ms':10},
        (1,4000):{'fresh_2s':True,'prediction_mid':0.55,'latest_age_ms':10},
    }
    fmap=mod._prediction_feature_map(states,pred)
    row=fmap[(1,4000)]
    assert abs(row['micro_prediction_delta_3s']-0.15)<1e-12
    assert abs(row['micro_prediction_range_3s']-0.15)<1e-12


def test_apply_decoupled_prediction_recomputes_pressure_alignment():
    joined=[{'market_id':1,'sample_ms':4000,'micro_book_pressure_mean_3s':-0.25}]
    fmap={(1,4000):{
        'micro_prediction_up_mid':0.55,
        'micro_prediction_distance_05':0.05,
        'micro_prediction_delta_3s':0.15,
        'micro_prediction_range_3s':0.15,
    }}
    row=mod._apply_decoupled_prediction(joined,fmap)[0]
    assert abs(row['micro_prediction_pressure_aligned_delta_3s']+0.15)<1e-12
