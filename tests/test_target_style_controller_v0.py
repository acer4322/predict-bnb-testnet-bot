from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from predict_bot.target_style_controller_v0 import (
    DOWN,
    NEUTRAL,
    UP,
    ControllerV0Config,
    MarketSignal,
    PortfolioState,
    TargetStyleControllerV0,
    evaluate_opportunity,
)


def _signal(
    *,
    spot: float = 100.04,
    start: float = 100.0,
    up_bid: float = 0.59,
    up_ask: float = 0.61,
    down_bid: float = 0.39,
    down_ask: float = 0.41,
    seconds_left: float = 200.0,
) -> MarketSignal:
    return MarketSignal(
        market_id=1,
        seconds_left=seconds_left,
        start_price=start,
        spot_price=spot,
        up_bid=up_bid,
        up_ask=up_ask,
        down_bid=down_bid,
        down_ask=down_ask,
    )


def test_opportunity_requires_spot_and_poly_agreement() -> None:
    cfg = ControllerV0Config()
    yes = evaluate_opportunity(_signal(), cfg)
    assert yes.status == "CONFIRMED"
    assert yes.side == UP

    conflict = evaluate_opportunity(
        _signal(up_bid=0.39, up_ask=0.41, down_bid=0.59, down_ask=0.61), cfg
    )
    assert conflict.status == "CONFLICT"
    assert conflict.side == NEUTRAL


def test_layered_controller_builds_fixed_own_inventory_target() -> None:
    cfg = ControllerV0Config(target_net_shares=4.0, gross_cap_shares=8.0)
    controller = TargetStyleControllerV0(cfg)
    portfolio = PortfolioState()
    trace = controller.step(_signal(), portfolio, now_ts=100.0)
    assert trace.desired.side == UP
    assert trace.desired.target_net_shares == 4.0
    assert trace.intent.action == "TAKER_ADD"
    assert trace.intent.side == UP
    assert trace.intent.shares == 4.0


def test_opposite_confirmed_state_repairs_only_to_neutral() -> None:
    cfg = ControllerV0Config(target_net_shares=4.0, gross_cap_shares=8.0)
    controller = TargetStyleControllerV0(cfg)
    portfolio = PortfolioState(up_shares=4.0, down_shares=0.0, last_action_ts=90.0)
    down = _signal(
        spot=99.96,
        up_bid=0.39,
        up_ask=0.41,
        down_bid=0.59,
        down_ask=0.61,
    )
    trace = controller.step(down, portfolio, now_ts=100.0)
    assert trace.risk.repair_needed is True
    assert trace.intent.action == "TAKER_REPAIR"
    assert trace.intent.side == DOWN
    assert trace.intent.shares == 4.0


def test_gross_cap_blocks_more_trading_after_full_hedge() -> None:
    cfg = ControllerV0Config(target_net_shares=4.0, gross_cap_shares=8.0)
    controller = TargetStyleControllerV0(cfg)
    portfolio = PortfolioState(up_shares=4.0, down_shares=4.0, last_action_ts=90.0)
    trace = controller.step(_signal(), portfolio, now_ts=100.0)
    assert trace.risk.gross_remaining == 0.0
    assert trace.intent.action == "HOLD"


def test_price_cap_and_time_guard_are_fail_closed() -> None:
    cfg = ControllerV0Config(max_entry_ask=0.75)
    controller = TargetStyleControllerV0(cfg)
    expensive = _signal(up_bid=0.78, up_ask=0.80, down_bid=0.20, down_ask=0.22)
    trace = controller.step(expensive, PortfolioState(), now_ts=100.0)
    assert trace.opportunity.status == "CONFIRMED"
    assert trace.intent.action == "HOLD"

    too_early = evaluate_opportunity(_signal(seconds_left=295.0), cfg)
    assert too_early.status == "TIME_GUARD"
