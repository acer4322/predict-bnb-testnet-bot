from __future__ import annotations

import threading
import time
import types

from predict_bot.r31_echtgeld_state_bridge_v1 import R31EchtgeldStateBridgeV1
from predict_bot.unified_controller_r3s_r31_echtgeld_v1 import (
    ENTRY_SOURCE,
    UnifiedControllerR3SR31EchtgeldV1,
)
from predict_bot.unified_controller_r2_r21_echtgeld_v1 import (
    ENTRY_SOURCE as R2_ENTRY_SOURCE,
    UnifiedControllerR2R21EchtgeldV1,
)


class _HeartbeatResponse:
    status_code = 200
    content = b"{}"

    def json(self):
        return {"ok": True}


class _HeartbeatClient:
    def __init__(self):
        self.calls = 0

    def post(self, *_args, **_kwargs):
        self.calls += 1
        return _HeartbeatResponse()


def _minimal_runtime():
    runtime = UnifiedControllerR3SR31EchtgeldV1.__new__(UnifiedControllerR3SR31EchtgeldV1)
    runtime.current_market_id = 123
    runtime.last_market_bucket_start_sec = 100
    runtime.last_market_window_end_ms = 200000
    runtime.last_heartbeat_ms = None
    runtime.last_heartbeat_attempt_ms = None
    runtime.last_heartbeat_error = None
    runtime.max_heartbeat_gap_ms = 0
    runtime.live_metrics = {
        "heartbeatFailures": 0,
        "heartbeatSendFailures": 0,
        "preVenueGhostsRetired": 0,
    }
    runtime.heartbeat_http = _HeartbeatClient()
    runtime.lock = threading.Lock()
    return runtime


def test_heartbeat_is_independent_of_controller_lock():
    runtime = _minimal_runtime()
    runtime.lock.acquire()
    result = {}

    thread = threading.Thread(target=lambda: result.setdefault("ok", runtime._send_heartbeat_once()))
    started = time.monotonic()
    thread.start()
    thread.join(timeout=0.25)
    elapsed = time.monotonic() - started
    runtime.lock.release()

    assert not thread.is_alive(), "heartbeat must not wait on controller self.lock"
    assert result.get("ok") is True
    assert runtime.heartbeat_http.calls == 1
    assert elapsed < 0.25


def test_prevenue_reject_terminalizes_planned_child_without_ghost():
    runtime = _minimal_runtime()
    runtime.r21_bridge = R31EchtgeldStateBridgeV1(ENTRY_SOURCE)
    runtime.r21_bridge.reset_market(runtime.current_market_id)
    runtime._r21_inbox = types.MethodType(lambda self, at_ms: {}, runtime)
    cid = "R3S_TEST:123:MAKER:UP:1"
    runtime.r21_bridge.register_intent(
        client_order_id=cid,
        market_id=runtime.current_market_id,
        role="MAKER",
        side="UP",
        requested_shares=10.0,
        created_at_ms=1000,
        reason="TEST",
        requested_price=0.4,
    )
    assert runtime.r21_bridge.children[cid]["state"] == "PLANNED"
    assert runtime.r21_bridge.children[cid]["terminal"] is False

    runtime._bridge_confirm_prevenue_reject(
        cid=cid, role="MAKER", side="UP", at_ms=1100, reason="source gate rejected before venue write"
    )

    child = runtime.r21_bridge.children[cid]
    assert child["state"] == "REJECTED"
    assert child["terminal"] is True
    assert runtime.live_metrics["preVenueGhostsRetired"] == 1
    # Preserve the existing R2.1/R3.1 terminal residual contract rather than
    # silently deleting the failed responsibility.
    assert cid in runtime.r21_bridge.obligations


def test_prevenue_taker_reject_terminalizes_without_residual_obligation():
    runtime = _minimal_runtime()
    runtime.r21_bridge = R31EchtgeldStateBridgeV1(ENTRY_SOURCE)
    runtime.r21_bridge.reset_market(runtime.current_market_id)
    runtime._r21_inbox = types.MethodType(lambda self, at_ms: {}, runtime)
    cid = "R3S_TEST:123:TAKER:DOWN:1"
    runtime.r21_bridge.register_intent(
        client_order_id=cid,
        market_id=runtime.current_market_id,
        role="TAKER",
        side="DOWN",
        requested_shares=12.0,
        created_at_ms=1000,
        reason="TEST",
        requested_price=0.5,
    )

    runtime._bridge_confirm_prevenue_reject(
        cid=cid, role="TAKER", side="DOWN", at_ms=1100, reason="price cap pre-venue reject"
    )

    child = runtime.r21_bridge.children[cid]
    assert child["terminal"] is True
    assert child["state"] == "REJECTED"
    assert cid not in runtime.r21_bridge.obligations


def test_r2_heartbeat_is_also_independent_of_controller_lock():
    runtime = UnifiedControllerR2R21EchtgeldV1.__new__(UnifiedControllerR2R21EchtgeldV1)
    runtime.current_market_id = 123
    runtime.last_market_bucket_start_sec = 100
    runtime.last_market_window_end_ms = 200000
    runtime.last_heartbeat_ms = None
    runtime.last_heartbeat_attempt_ms = None
    runtime.last_heartbeat_error = None
    runtime.max_heartbeat_gap_ms = 0
    runtime.live_metrics = {"heartbeatFailures": 0, "heartbeatSendFailures": 0, "preVenueGhostsRetired": 0}
    runtime.heartbeat_http = _HeartbeatClient()
    runtime.lock = threading.Lock()
    runtime.lock.acquire()
    result = {}
    thread = threading.Thread(target=lambda: result.setdefault("ok", runtime._send_heartbeat_once()))
    thread.start()
    thread.join(timeout=0.25)
    runtime.lock.release()
    assert not thread.is_alive()
    assert result.get("ok") is True


def test_r2_prevenue_reject_does_not_leave_planned_ghost():
    runtime = UnifiedControllerR2R21EchtgeldV1.__new__(UnifiedControllerR2R21EchtgeldV1)
    runtime.current_market_id = 123
    runtime.live_metrics = {"preVenueGhostsRetired": 0}
    runtime.r21_bridge = R31EchtgeldStateBridgeV1(R2_ENTRY_SOURCE)
    runtime.r21_bridge.reset_market(runtime.current_market_id)
    runtime._r21_inbox = types.MethodType(lambda self, at_ms: {}, runtime)
    cid = "R2_TEST:123:MAKER:UP:1"
    runtime.r21_bridge.register_intent(
        client_order_id=cid, market_id=123, role="MAKER", side="UP", requested_shares=10.0,
        created_at_ms=1000, reason="TEST", requested_price=0.4,
    )
    runtime._bridge_confirm_prevenue_reject(cid=cid, role="MAKER", side="UP", at_ms=1100, reason="pre-venue gate")
    assert runtime.r21_bridge.children[cid]["terminal"] is True
    assert runtime.r21_bridge.children[cid]["state"] == "REJECTED"
    assert runtime.live_metrics["preVenueGhostsRetired"] == 1
