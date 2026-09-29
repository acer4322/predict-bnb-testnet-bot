from __future__ import annotations

from predict_bot.cap100_venue_execution_simulator_v1 import (
    Cap100VenueExecutionSimulatorV1,
    OrderState,
    QueueMode,
    VenueBookEvent,
)


def test_latency_prevents_pre_rest_fill():
    sim = Cap100VenueExecutionSimulatorV1(queue_mode=QueueMode.STRICT_RISK_AVERSE, entry_latency_ms=500, ack_latency_ms=500)
    o = sim.submit_maker(order_id="m1", market_id=1, side="UP", price=.42, shares=18,
                         submit_ms=1000, initial_visible_depth=5, native_side="bids", native_price=.42)
    assert sim.apply_event(VenueBookEvent(1500,"bids",.42,executed_qty=20)) == []
    assert o.filled_shares == 0
    fills = sim.apply_event(VenueBookEvent(2100,"bids",.42,executed_qty=20))
    assert fills and fills[0].shares == 15
    assert o.state == OrderState.PARTIAL_FILL


def test_queue_ahead_is_consumed_before_our_fill():
    sim = Cap100VenueExecutionSimulatorV1(queue_mode=QueueMode.STRICT_RISK_AVERSE)
    o = sim.submit_maker(order_id="m1", market_id=1, side="UP", price=.42, shares=18,
                         submit_ms=1000, initial_visible_depth=12, native_side="bids", native_price=.42)
    assert sim.apply_event(VenueBookEvent(1001,"bids",.42,executed_qty=7)) == []
    assert o.queue_ahead == 5
    f = sim.apply_event(VenueBookEvent(1002,"bids",.42,executed_qty=9))[0]
    assert f.shares == 4
    assert o.queue_ahead == 0
    assert o.remaining_shares == 14


def test_partial_then_full_fill():
    sim = Cap100VenueExecutionSimulatorV1(queue_mode=QueueMode.STRICT_RISK_AVERSE)
    o = sim.submit_maker(order_id="m1", market_id=1, side="DOWN", price=.53, shares=18,
                         submit_ms=0, initial_visible_depth=2, native_side="asks", native_price=.47)
    f1 = sim.apply_event(VenueBookEvent(10,"asks",.47,executed_qty=8))[0]
    assert f1.shares == 6
    assert o.state == OrderState.PARTIAL_FILL
    f2 = sim.apply_event(VenueBookEvent(20,"asks",.47,executed_qty=20))[0]
    assert f2.shares == 12
    assert o.state == OrderState.FILLED
    assert o.filled_shares == 18


def test_strict_mode_does_not_treat_l2_cancel_as_trade():
    sim = Cap100VenueExecutionSimulatorV1(queue_mode=QueueMode.STRICT_RISK_AVERSE)
    o = sim.submit_maker(order_id="m1", market_id=1, side="UP", price=.42, shares=18,
                         submit_ms=0, initial_visible_depth=10, native_side="bids", native_price=.42)
    assert sim.apply_event(VenueBookEvent(1,"bids",.42,depth_before=10,depth_after=0)) == []
    assert o.queue_ahead == 10
    assert o.filled_shares == 0


def test_l2_approx_can_use_negative_depletion():
    sim = Cap100VenueExecutionSimulatorV1(queue_mode=QueueMode.L2_DEPLETION_APPROX)
    o = sim.submit_maker(order_id="m1", market_id=1, side="UP", price=.42, shares=18,
                         submit_ms=0, initial_visible_depth=5, native_side="bids", native_price=.42)
    f = sim.apply_event(VenueBookEvent(1,"bids",.42,depth_before=20,depth_after=7))[0]
    assert f.shares == 8
    assert f.source == "L2_NEGATIVE_DEPLETION_APPROX"


def test_cancel_pending_can_still_fill_before_cancel_confirmation():
    sim = Cap100VenueExecutionSimulatorV1(queue_mode=QueueMode.STRICT_RISK_AVERSE)
    o = sim.submit_maker(order_id="m1", market_id=1, side="UP", price=.42, shares=18,
                         submit_ms=0, initial_visible_depth=0, native_side="bids", native_price=.42)
    sim.request_cancel("m1", 5)
    fills = sim.apply_event(VenueBookEvent(6,"bids",.42,executed_qty=7))
    assert fills and fills[0].shares == 7
    assert o.filled_shares == 7
    assert o.remaining_shares == 11
    sim.confirm_cancel("m1", 10)
    assert o.state == OrderState.CANCELED
    assert o.filled_shares == 7
