from predict_bot.live_loss_attribution_report_v2 import (
    _late_failure,
    _market_churn_context,
)


def test_late_signal_failure_uses_post_10s_snapshots():
    row = {
        "trajectory": {
            "plus10s": {
                "polySelectedMid": 0.49,
                "binanceSelectedMid": 0.47,
                "polySelectedDeltaFromSignal": -0.12,
                "binanceSelectedDeltaFromSignal": -0.14,
            }
        }
    }
    suspected, evidence = _late_failure(row)
    assert suspected is True
    assert evidence


def test_same_market_churn_requires_repeated_losing_reversal_reentries():
    rows = [
        {
            "roundId": 1,
            "marketId": 99,
            "createdAtMs": 1,
            "side": "UP",
            "exitIntent": "POLY_DIRECTION_FLIP",
            "pnlUsdt": -0.4,
        },
        {
            "roundId": 2,
            "marketId": 99,
            "createdAtMs": 2,
            "side": "DOWN",
            "exitIntent": "POLY_DIRECTION_FLIP",
            "pnlUsdt": -0.3,
        },
        {
            "roundId": 3,
            "marketId": 99,
            "createdAtMs": 3,
            "side": "UP",
            "exitIntent": "POLY_DIRECTION_FLIP",
            "pnlUsdt": 0.1,
        },
    ]
    context = _market_churn_context(rows)[99]
    assert context["sameMarketChopReentrySuspected"] is True
    assert context["rounds"] == 3
    assert context["losses"] == 2
    assert context["reversalExits"] == 3
