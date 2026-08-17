from __future__ import annotations
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
TOOLS=ROOT/'tools'
if str(TOOLS) not in sys.path: sys.path.insert(0,str(TOOLS))
import audit_target_controller_prediction_clock_history_v277c as mod


def _pred(mid: float | None):
    return {
        'fresh_2s': mid is not None,
        'prediction_mid': mid,
        'latest_age_ms': 100 if mid is not None else None,
        'reconstructable': mid is not None,
        'valid_mid': mid is not None,
        'latest_update_id': 1,
        'checkpoint_update_id': 1,
    }


def test_clock_history_uses_independent_anchors_not_target_rows():
    row={
        'market_id':1,'sample_ms':10000,
        'micro_book_pressure_mean_3s':0.5,
    }
    audited={
        (1,7000):_pred(0.40),
        (1,8000):_pred(0.45),
        (1,9000):_pred(0.50),
        (1,10000):_pred(0.55),
    }
    out=mod._clock_features_for_row(row,audited)
    assert abs(out['micro_prediction_delta_3s']-0.15)<1e-12
    assert abs(out['micro_prediction_range_3s']-0.15)<1e-12
    assert abs(out['micro_prediction_pressure_aligned_delta_3s']-0.15)<1e-12


def test_clock_history_preserves_65pct_span_rule():
    row={'market_id':1,'sample_ms':10000,'micro_book_pressure_mean_3s':-1.0}
    audited={
        (1,7000):_pred(None),
        (1,8000):_pred(None),
        (1,9000):_pred(0.50),
        (1,10000):_pred(0.55),
    }
    out=mod._clock_features_for_row(row,audited)
    assert out['micro_prediction_delta_3s'] is None
    assert out['micro_prediction_range_3s'] is None
    assert out['micro_prediction_pressure_aligned_delta_3s'] is None
