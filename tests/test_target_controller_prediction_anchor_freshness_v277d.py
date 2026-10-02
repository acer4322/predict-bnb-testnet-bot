from __future__ import annotations
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / 'tools'
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import audit_target_controller_prediction_anchor_freshness_v277d as mod


def _pred(*, fresh: bool, age: int, mid: float = 0.5):
    return {
        'fresh_2s': fresh,
        'prediction_mid': mid if fresh else None,
        'latest_age_ms': age,
        'reconstructable': True,
        'valid_mid': True,
    }


def test_quantile_interpolates():
    assert mod._quantile([0, 10], 0.5) == 5.0
    assert mod._quantile([], 0.5) is None


def test_market_row_reports_anchor_freshness_and_span():
    meta = {'marketId': 7, 'bucketStartMs': 1000, 'bucketEndMs': 301000, 'stateRows': 2}
    rows = [
        {'market_id': 7, 'sample_ms': 10000},
        {'market_id': 7, 'sample_ms': 11000},
    ]
    audited = {
        (7, 7000): _pred(fresh=True, age=100, mid=0.40),
        (7, 8000): _pred(fresh=True, age=100, mid=0.45),
        (7, 9000): _pred(fresh=True, age=100, mid=0.50),
        (7, 10000): _pred(fresh=True, age=100, mid=0.55),
        (7, 11000): _pred(fresh=False, age=2500),
    }
    out = mod._market_row(meta, rows, audited, 1200)
    assert out['anchorFreshness']['0']['requested'] == 2
    assert out['anchorFreshness']['0']['fresh2s'] == 1
    assert out['anchorFreshness']['0']['fresh2sRate'] == 0.5
    assert out['clock3sSpanSupportedRows'] == 1
    assert out['clock3sSpanSupportedRateOnMicroRows'] == 0.5
