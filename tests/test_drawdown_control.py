import pytest

from predict_bot.drawdown_control import MarketRegimeDrawdownController


def _ready_controller(*, net_return_bps: float = 20.0) -> MarketRegimeDrawdownController:
    controller = MarketRegimeDrawdownController()
    per_market = net_return_bps / 6.0
    for market_id in range(1, 7):
        controller.record_completed_market(
            market_id,
            start_price=100.0,
            end_price=100.0 * (1.0 + per_market / 10_000.0),
        )
    return controller


def test_controller_fails_closed_until_completed_market_history_is_ready():
    controller = MarketRegimeDrawdownController()
    for market_id in range(1, 6):
        assert controller.record_completed_market(
            market_id, start_price=100.0, end_price=100.1
        )

    decision = controller.evaluate(side="UP", start_price=100.0, spot_price=100.1)

    assert decision.allowed is False
    assert decision.status == "BLOCK_NOT_READY"
    assert decision.prior_market_count == 5


def test_controller_blocks_opposed_signal_at_strong_prior_move_boundary():
    controller = _ready_controller(net_return_bps=20.01)

    decision = controller.evaluate(
        side="UP",
        start_price=100.0,
        spot_price=100.0 * (1.0 - 3.01 / 10_000.0),
    )

    assert decision.allowed is False
    assert decision.status == "BLOCK_REGIME_REVERSAL"
    assert decision.prior_net_return_bps == pytest.approx(20.01)
    assert decision.side_alignment_bps == pytest.approx(-3.01)


def test_exact_three_bps_opposition_is_allowed():
    controller = _ready_controller(net_return_bps=25.0)

    decision = controller.evaluate(
        side="DOWN",
        start_price=100.0,
        spot_price=100.0 * (1.0 + 3.0 / 10_000.0),
    )

    assert decision.allowed is True
    assert decision.side_alignment_bps == pytest.approx(-3.0)


def test_weak_prior_move_does_not_block_material_opposition():
    controller = _ready_controller(net_return_bps=19.0)

    decision = controller.evaluate(
        side="UP", start_price=100.0, spot_price=99.9
    )

    assert decision.allowed is True
    assert decision.prior_net_return_bps == pytest.approx(19.0)


def test_duplicate_completed_market_is_ignored():
    controller = MarketRegimeDrawdownController(lookback_markets=2)

    assert controller.record_completed_market(1, start_price=100.0, end_price=101.0)
    assert not controller.record_completed_market(1, start_price=100.0, end_price=99.0)
    assert controller.state()["completedMarkets"] == [
        {"marketId": 1, "returnBps": pytest.approx(100.0)}
    ]
