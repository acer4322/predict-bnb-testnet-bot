from predict_bot.live_loss_attribution_report import (
    _market_lead_context,
    _round_attribution,
)


def _attempt(action: str, attempt_no: int, **values):
    row = {
        "id": attempt_no,
        "round_id": 1,
        "market_id": 123,
        "side": "DOWN",
        "action": action,
        "attempt_no": attempt_no,
        "created_at_ms": 1_000 + attempt_no * 100,
        "coverage_ratio": 10.0,
        "expected_vwap": None,
        "quote_average": None,
        "outcome": "SUBMITTED",
        "place_rtt_ms": 120.0,
        "quote_response_to_place_start_ms": 3,
        "quote_response_to_order_response_ms": 123,
        "place_response_at_ms": 1_100 + attempt_no * 100,
    }
    row.update(values)
    return row


def test_exit_no_fill_and_quote_collapse_is_exit_execution_delay():
    round_row = {
        "id": 1,
        "market_id": 123,
        "round_no": 1,
        "side": "DOWN",
        "state": "CLOSED",
        "stake_usdt": 2.0,
        "shares": 3.58,
        "pnl_usdt": -0.91,
        "entry_signal_at_ms": 500,
        "entry_binance_ask": 0.52,
        "entry_quote_average": 0.52,
        "exit_signal_at_ms": 1_000,
        "exit_quote_average": 0.31,
        "created_at_ms": 500,
        "updated_at_ms": 2_000,
        "exit_intent": "POLY_DIRECTION_FLIP",
        "close_reason": "POLY_DIRECTION_FLIP",
    }
    attempts = [
        _attempt("BUY", 1, expected_vwap=0.52, quote_average=0.52),
        _attempt("SELL", 1, quote_average=0.43, outcome="NO_FILL", order_status="FAILED"),
        _attempt(
            "SELL",
            2,
            quote_average=0.31,
            outcome="FILLED",
            created_at_ms=2_000,
            place_response_at_ms=2_200,
        ),
    ]
    result = _round_attribution(
        round_row,
        attempts,
        [],
        {"marketLeader": "INSUFFICIENT", "samples": 0},
    )
    assert result["exit"]["execution"]["noFills"] == 1
    assert abs(result["exit"]["firstToFinalQuoteDrop"] - 0.12) < 1e-12
    assert result["attribution"]["exitExecutionDelaySuspected"] is True
    assert result["attribution"]["primarySuspectedCause"] == "EXIT_EXECUTION_DELAY"


def test_three_second_probability_reversal_marks_signal_suspicion():
    round_row = {
        "id": 1,
        "market_id": 123,
        "round_no": 1,
        "side": "UP",
        "state": "CLOSED",
        "stake_usdt": 2.0,
        "shares": 4.0,
        "pnl_usdt": -0.50,
        "entry_signal_at_ms": 10_000,
        "entry_binance_ask": 0.50,
        "entry_quote_average": 0.50,
        "created_at_ms": 10_000,
        "updated_at_ms": 20_000,
        "close_reason": "POLY_DIRECTION_FLIP",
    }
    attempts = [_attempt("BUY", 1, expected_vwap=0.50, quote_average=0.50)]
    samples = [
        {
            "observed_at_ms": 10_000,
            "poly_received_at_ms": 10_000,
            "binance_observed_at_ms": 10_000,
            "poly_up_mid": 0.70,
            "binance_up_mid": 0.65,
        },
        {
            "observed_at_ms": 13_000,
            "poly_received_at_ms": 13_000,
            "binance_observed_at_ms": 13_000,
            "poly_up_mid": 0.55,
            "binance_up_mid": 0.52,
        },
    ]
    result = _round_attribution(
        round_row,
        attempts,
        samples,
        {"marketLeader": "TIE_MIXED", "samples": 2},
    )
    assert result["trajectory"]["plus3s"]["polySelectedDeltaFromSignal"] < -0.08
    assert result["attribution"]["signalReversalSuspected"] is True
    assert "SIGNAL_REVERSAL" in result["attribution"]["suspectedCauses"]


def test_market_lead_context_recovers_poly_first_milestone():
    rows = [
        {
            "observed_at_ms": 1_000,
            "poly_received_at_ms": 1_000,
            "binance_observed_at_ms": 1_000,
            "poly_up_mid": 0.60,
            "binance_up_mid": 0.60,
        },
        {
            "observed_at_ms": 2_000,
            "poly_received_at_ms": 2_000,
            "binance_observed_at_ms": 2_700,
            "poly_up_mid": 0.72,
            "binance_up_mid": 0.72,
        },
        {
            "observed_at_ms": 3_000,
            "poly_received_at_ms": 3_000,
            "binance_observed_at_ms": 3_700,
            "poly_up_mid": 0.82,
            "binance_up_mid": 0.82,
        },
    ]
    context = _market_lead_context(rows)
    assert context["matchedMilestoneEvents"] >= 2
    assert context["polyFirstEvents"] >= 2
    assert context["marketLeader"] == "POLY"
