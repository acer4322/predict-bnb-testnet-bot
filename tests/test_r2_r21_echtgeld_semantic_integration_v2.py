from __future__ import annotations

import threading
from types import SimpleNamespace

from predict_bot import echtgeld_engine_v23 as v23
from predict_bot import echtgeld_engine_v27 as v27
from predict_bot import unified_controller_cap100_shadow_v1 as cap100_base
from predict_bot import unified_controller_paper_v2 as canonical_r2
from predict_bot import unified_controller_paper_v1 as base
from predict_bot.r21_echtgeld_state_bridge_v1 import R21EchtgeldStateBridgeV1
from predict_bot.unified_controller_r2_r21_echtgeld_v1 import (
    ENTRY_SOURCE,
    EngineDeterministicReject,
    VERSION,
    UnifiedControllerR2R21EchtgeldV1,
)


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config

    def close(self) -> None:
        pass

    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}


def event(
    seq: int,
    cid: str,
    event_type: str,
    state: str,
    *,
    side: str = "UP",
    role: str = "MAKER",
    requested: float = 10.0,
    filled: float = 0.0,
    delta: float = 0.0,
    source: str = ENTRY_SOURCE,
    cancel_requested_at_ms: int | None = None,
) -> dict:
    return {
        "seq": seq,
        "occurred_at_ms": 10_000 + seq * 100,
        "event_type": event_type,
        "client_order_id": cid,
        "source_id": source,
        "source_market_id": 1001,
        "role": role,
        "side": side,
        "state": state,
        "requested_shares": requested,
        "filled_share_qty": filled,
        "delta_shares": delta,
        "cancel_requested_at_ms": cancel_requested_at_ms,
        "created_at_ms": 1_000,
    }


def test_r21_aggregates_multiple_children_and_keeps_terminal_remainder() -> None:
    bridge = R21EchtgeldStateBridgeV1(ENTRY_SOURCE)
    bridge.reset_market(1001)
    bridge.register_intent(
        client_order_id=f"{VERSION}:A",
        market_id=1001,
        role="MAKER",
        side="UP",
        requested_shares=10,
        created_at_ms=1_000,
    )
    bridge.register_intent(
        client_order_id=f"{VERSION}:B",
        market_id=1001,
        role="MAKER",
        side="DOWN",
        requested_shares=10,
        created_at_ms=1_100,
    )
    bridge.observe_event(event(1, f"{VERSION}:A", "ORDER_RESTING", "RESTING"))
    bridge.observe_event(event(2, f"{VERSION}:B", "ORDER_REJECTED", "REJECTED", side="DOWN"))

    inbox = bridge.snapshot(
        at_ms=20_000,
        actual_inventory={"UP": 0, "DOWN": 0},
        pending_cancels=set(),
        orphan_count=0,
    )
    assert inbox["actionAuthority"] is False
    assert inbox["orderMutationAuthority"] is False
    assert inbox["desiredPortfolioMutationAuthority"] is False
    assert inbox["ownershipState"] == "CURRENT_CHILD"
    assert inbox["terminalCertainty"] is False
    assert inbox["remainingObligation"] == {"UP": 0.0, "DOWN": 10.0}
    assert any(row["incidentType"] == "SUBMIT_REJECT_CONFIRMED" for row in inbox["incidents"])


def test_r21_confirmed_maker_fill_allocates_fault_residual_fifo() -> None:
    bridge = R21EchtgeldStateBridgeV1(ENTRY_SOURCE)
    bridge.reset_market(1001)
    failed = f"{VERSION}:FAILED"
    repair = f"{VERSION}:REPAIR"
    bridge.register_intent(
        client_order_id=failed,
        market_id=1001,
        role="MAKER",
        side="UP",
        requested_shares=10,
        created_at_ms=1_000,
    )
    bridge.observe_event(event(1, failed, "ORDER_REJECTED", "REJECTED"))
    bridge.register_intent(
        client_order_id=repair,
        market_id=1001,
        role="MAKER",
        side="UP",
        requested_shares=10,
        created_at_ms=11_000,
    )
    bridge.observe_event(event(2, repair, "FILL_DELTA", "PARTIAL_FILL", filled=4, delta=4))

    inbox = bridge.snapshot(
        at_ms=20_000,
        actual_inventory={"UP": 4, "DOWN": 0},
        pending_cancels=set(),
        orphan_count=0,
    )
    assert inbox["actualConfirmedInventory"]["UP"] == 4
    assert inbox["remainingObligation"]["UP"] == 6
    obligation = inbox["obligationResidualBeliefs"]["obligations"][0]
    assert obligation["progressQty"] == 4
    assert obligation["residualQty"] == 6
    assert obligation["actionRecommendation"] is None


def test_r21_filters_foreign_source_and_detects_fill_during_cancel() -> None:
    bridge = R21EchtgeldStateBridgeV1(ENTRY_SOURCE)
    bridge.reset_market(1001)
    cid = f"{VERSION}:CANCEL"
    bridge.register_intent(
        client_order_id=cid,
        market_id=1001,
        role="MAKER",
        side="UP",
        requested_shares=10,
        created_at_ms=1_000,
    )
    assert bridge.observe_event(event(1, "CAP100_OLD", "FILL_DELTA", "FILLED", source=v23.CAP100_SOURCE, filled=10, delta=10)) is False
    assert bridge.observe_event(event(2, cid, "ORDER_CANCEL_PENDING", "CANCEL_PENDING")) is True
    assert bridge.observe_event(
        event(3, cid, "FILL_DELTA", "PARTIAL_FILL", filled=3, delta=3, cancel_requested_at_ms=10_150)
    ) is True
    inbox = bridge.snapshot(
        at_ms=20_000,
        actual_inventory={"UP": 3, "DOWN": 0},
        pending_cancels={cid},
        orphan_count=0,
    )
    assert inbox["filteredForeignEventCount"] == 1
    assert any(row["incidentType"] == "FILL_DURING_CANCEL" for row in inbox["incidents"])


def test_r21_source_is_durable_and_maker_minimum_is_rejected_before_quote(tmp_path, monkeypatch) -> None:
    engine = v23.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "settlement.db",
        target_official_db_path=tmp_path / "official.db",
    )
    try:
        engine.select_entry_source(v23.R2_R21_SOURCE)
        engine.armed = True
        monkeypatch.setattr(engine, "_cap100_market", lambda payload: {
            "source_market_id": int(payload["marketId"]),
            "market_id": 7001,
            "bucket_start_sec": int(payload["bucketStartSec"]),
            "end_ms": int(payload["windowEndMs"]),
            "up_token_id": "UPTOKEN",
            "down_token_id": "DOWNTOKEN",
            "fee_rate_bps": 200,
        })
        monkeypatch.setattr(
            engine,
            "_poly_ensure_client",
            lambda: (_ for _ in ()).throw(AssertionError("quote/client must not be touched below $1")),
        )
        result = engine.submit_cap100_maker({
            "entrySource": v23.R2_R21_SOURCE,
            "clientOrderId": f"{VERSION}:MIN",
            "controllerVersion": VERSION,
            "marketId": 1001,
            "bucketStartSec": 2_000,
            "windowEndMs": 2_300_000,
            "asset": "BTC",
            "side": "UP",
            "price": 0.06,
            "shares": 10.0,
        })
        assert result["ok"] is False
        assert result["preVenueRejected"] is True
        assert result["order"]["state"] == "REJECTED"
        assert result["order"]["source_id"] == v23.R2_R21_SOURCE
        assert result["order"]["strategy"] == v23.R2_R21_STRATEGY
        state = engine.cap100_state()
        assert state["sourceId"] == v23.R2_R21_SOURCE
        assert state["strategy"] == v23.R2_R21_STRATEGY
        assert v23.R2_R21_SOURCE in state["allowedSources"]
        rows = engine.cap100_events()
        rejection = next(row for row in rows if row["event_type"] == "ORDER_REJECTED")
        assert rejection["source_id"] == v23.R2_R21_SOURCE
        assert rejection["requested_shares"] == 10.0
    finally:
        engine.close()


def test_controller_blocks_active_completion_until_cancel_ack_or_after_active_fault() -> None:
    controller = object.__new__(UnifiedControllerR2R21EchtgeldV1)
    controller._deployment_live_ready = lambda: True
    controller.live_metrics = {"r21CancelAckBlocks": 0, "r21PostActiveFaultWaitBlocks": 0}
    controller.pending_cancels = {"CID"}
    controller.r21_active_fault_wait = False
    result = controller._record_taker("UP", 0.5, 1, "D", {}, {}, 0, 0, 0, "")
    assert result is False
    assert controller.live_metrics["r21CancelAckBlocks"] == 1

    controller.pending_cancels = set()
    controller.r21_active_fault_wait = True
    result = controller._record_taker("UP", 0.5, 2, "D", {}, {}, 0, 0, 0, "")
    assert result is False
    assert controller.live_metrics["r21PostActiveFaultWaitBlocks"] == 1


def test_background_lifecycle_poll_delivers_terminal_without_public_strategy_snapshot() -> None:
    controller = object.__new__(UnifiedControllerR2R21EchtgeldV1)
    cid = f"{VERSION}:1001:TAKER:UP:10000:D"
    controller.lock = threading.RLock()
    controller.orders = {}
    controller.orphan_orders = {}
    controller.pending_cancels = {cid}
    controller.taker_pending = {
        cid: {"filledShares": 0.0, "filledUsdt": 0.0, "terminalState": None}
    }
    controller.current_market_id = 1001
    controller.inventory = SimpleNamespace(maker_up=0.0, maker_down=0.0, taker_up=0.0, taker_down=0.0)
    controller.engine_state = {}
    controller.r21_bridge = R21EchtgeldStateBridgeV1(ENTRY_SOURCE)
    controller.r21_bridge.reset_market(1001)
    controller.r21_bridge.register_intent(
        client_order_id=cid,
        market_id=1001,
        role="TAKER",
        side="UP",
        requested_shares=10.0,
        created_at_ms=10_000,
        reason="R2_AUTHORIZED_ACTIVE_INTERVENTION",
    )
    controller.r21_state = controller._r21_inbox(10_000)
    controller.r21_active_fault_wait = False
    controller.r21_active_fault_reason = None
    controller.active_intervention_required = True
    controller.active_intervention_reason = "UNRESOLVED_PASSIVE_REPAIR_15S"
    controller.live_metrics = {
        "foreignEngineEventsFiltered": 0,
        "r21Reassessments": 0,
        "backgroundLifecyclePolls": 0,
        "backgroundLifecycleEventsApplied": 0,
    }
    terminal = event(9, cid, "ORDER_CANCELED", "CANCELED", role="TAKER")

    def poll(*, force: bool = False):
        assert force is True
        assert controller._apply_engine_event(terminal) is True
        return [terminal]

    controller._poll_engine_events = poll
    assert controller._poll_lifecycle_without_snapshot() == 1
    assert controller.taker_pending[cid]["terminalState"] == "CANCELED"
    assert cid not in controller.pending_cancels
    assert controller.r21_state["ownershipState"] == "NO_CHILD"
    assert controller.r21_state["terminalCertainty"] is True
    assert any(row["incidentType"] == "TERMINAL_ZERO_FILL_CONFIRMED" for row in controller.r21_state["incidents"])
    assert controller.active_intervention_required is False
    assert controller.live_metrics["backgroundLifecyclePolls"] == 1
    assert controller.live_metrics["backgroundLifecycleEventsApplied"] == 1


def test_live_decision_snapshot_uses_v33_quantity_policy_labels() -> None:
    controller = object.__new__(UnifiedControllerR2R21EchtgeldV1)
    controller.last_decision = {"decisionId": "D", "capitalPolicy": "CAP100_KEEP18_MAKER80_TAKER20"}
    controller._deployment_live_ready = lambda: False
    # Stop after the in-memory trace rewrite; the durable recorder branch is
    # separately exercised by live-replay/source tests.
    controller.last_decision["decisionId"] = ""
    controller._rewrite_live_decision_trace()
    assert controller.last_decision["capitalPolicy"] == "R2_R21_10SHARE_NO_NOTIONAL_CAP"
    assert controller.last_decision["configuredShares"] == 10.0
    assert controller.last_decision["notionalCapEnabled"] is False


def test_r2_r21_inherits_canonical_frozen_r2_not_cap100_branch() -> None:
    assert issubclass(UnifiedControllerR2R21EchtgeldV1, canonical_r2.UnifiedControllerPaperV2)
    assert not issubclass(UnifiedControllerR2R21EchtgeldV1, cap100_base.UnifiedControllerCap100ShadowV1)


def test_deterministic_engine_rejection_does_not_create_local_unknown_or_orphan(monkeypatch) -> None:
    controller = object.__new__(UnifiedControllerR2R21EchtgeldV1)
    controller.orders = {}
    controller.last_closed = {}
    controller.current_market_id = 1002
    controller.current_metrics = {"makerPlacements": 0}
    controller.run_metrics = {"makerPlacements": 0}
    controller.live_metrics = {"makerSubmitRejected": 0, "makerUnknown": 0, "engineHttpErrors": 0}
    controller.entry_freeze = False
    controller.entry_freeze_detail = None
    controller.execution_ready = True
    controller.execution_block_reason = None
    controller.last_error = None
    controller.r21_bridge = SimpleNamespace(register_intent=lambda **_kwargs: None)
    controller.recorder = SimpleNamespace(record_order_cancel=lambda **_kwargs: None)
    controller._deployment_live_ready = lambda: True

    def plan(self, side, now, snapshot_ns, decision_id, reason, p, snapshot, allow_stack=True, bypass_guard=False):
        key = (side, 46)
        self.orders[key] = canonical_r2.PaperOrder(
            f"{VERSION}:1002:MAKER:{side}:46:{now}:1",
            side,
            46,
            0.46,
            10.0,
            now,
            snapshot_ns,
            0.0,
            "bids",
            0.46,
            False,
        )
        self.current_metrics["makerPlacements"] += 1
        self.run_metrics["makerPlacements"] += 1
        return True

    monkeypatch.setattr(canonical_r2.UnifiedControllerPaperV2, "_add_order", plan)
    controller._engine_post = lambda *_args, **_kwargs: (_ for _ in ()).throw(
        EngineDeterministicReject({"ok": False, "error": "one-market session is COMPLETING"}, 400)
    )
    made = controller._add_order(
        "UP",
        10_000,
        10_000_000,
        "D",
        "MAKER_HAZARD",
        0.5,
        {"marketId": 1002, "bucketStartSec": 2_300, "windowEndMs": 2_600_000},
    )
    assert made is False
    assert controller.orders == {}
    assert controller.live_metrics["makerSubmitRejected"] == 1
    assert controller.live_metrics["makerUnknown"] == 0
    assert controller.entry_freeze is False


def test_live_replay_preserves_r2_r21_source_attribution(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(v27, "REPLAY_DB", tmp_path / "replay.db")
    monkeypatch.setattr(v27, "CONTROLLER_DB", tmp_path / "controller.db")
    monkeypatch.setattr(v27, "R2_R21_CONTROLLER_DB", tmp_path / "r2-r21-controller.db")
    engine = v27.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "settlement.db",
        target_official_db_path=tmp_path / "official.db",
    )
    try:
        engine.select_entry_source(v23.R2_R21_SOURCE)
        engine.cap100_heartbeat({
            "entrySource": v23.R2_R21_SOURCE,
            "controllerVersion": VERSION,
            "marketId": 1001,
        })
        heartbeat = next(row for row in reversed(engine.replay_events()) if row["event_type"] == "CONTROLLER_HEARTBEAT")
        assert heartbeat["source_id"] == v23.R2_R21_SOURCE
        assert heartbeat["selected_source"] == v23.R2_R21_SOURCE
    finally:
        engine.close()


def test_v33_controller_cancel_routes_to_8781_and_preserves_pending_until_terminal_ack(tmp_path, monkeypatch) -> None:
    class Client:
        def __init__(self) -> None:
            self.cancel_calls: list[list[str]] = []

        def orderbook(self, *_args, **_kwargs):
            return {}

        def server_timestamp_ms(self):
            return 2_000_000

        def get_quote(self, **_kwargs):
            return {"quoteId": "Q-R21"}

        def place_limit_order(self, **_kwargs):
            return {"orderId": "O-R21", "status": "NEW"}

        def batch_cancel_orders_raw(self, **kwargs):
            self.cancel_calls.append(list(kwargs["order_ids"]))
            return {"ok": True}

    engine = v23.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "settlement.db",
        target_official_db_path=tmp_path / "official.db",
    )
    client = Client()
    try:
        engine.select_entry_source(v23.R2_R21_SOURCE)
        engine.armed = True
        monkeypatch.setattr(engine, "_poly_ensure_client", lambda: (client, {"walletAddress": "W", "walletId": "ID"}))
        monkeypatch.setattr(engine, "_cap100_market", lambda payload: {
            "source_market_id": int(payload["marketId"]),
            "market_id": 7001,
            "bucket_start_sec": int(payload["bucketStartSec"]),
            "end_ms": int(payload["windowEndMs"]),
            "up_token_id": "UPTOKEN",
            "down_token_id": "DOWNTOKEN",
            "fee_rate_bps": 200,
        })
        monkeypatch.setattr(v23, "binance_top_of_book", lambda *_a, **_k: SimpleNamespace(up_ask=0.60, down_ask=0.60))
        cid = f"{VERSION}:1001:MAKER:UP:40:10000:1"
        placed = engine.submit_cap100_maker({
            "entrySource": ENTRY_SOURCE,
            "clientOrderId": cid,
            "controllerVersion": VERSION,
            "marketId": 1001,
            "bucketStartSec": 2_000,
            "windowEndMs": 2_300_000,
            "asset": "BTC",
            "side": "UP",
            "price": 0.40,
            "shares": 10.0,
        })
        assert placed["order"]["state"] == "RESTING"
        assert placed["order"]["source_id"] == ENTRY_SOURCE

        controller = object.__new__(UnifiedControllerR2R21EchtgeldV1)
        key = ("UP", 40)
        controller.orders = {
            key: base.PaperOrder(cid, "UP", 40, 0.40, 10.0, 10_000, 10_000_000, 0.0, "BID", 0.40, False)
        }
        controller.pending_cancels = set()
        controller.live_metrics = {"makerCancelRequested": 0, "engineHttpErrors": 0}
        controller.entry_freeze = False
        controller.entry_freeze_detail = None
        controller.last_error = None
        controller._engine_post = lambda path, payload: (
            engine.cancel_cap100_order(payload)
            if path == "/cap100/cancel"
            else (_ for _ in ()).throw(AssertionError(path))
        )
        controller._cancel_order(key, 11_000, "MARKET_ROLLOVER")

        assert client.cancel_calls == [["O-R21"]]
        assert cid in controller.pending_cancels
        pending = next(row for row in engine._cap100_rows(active_only=False) if row["client_order_id"] == cid)
        assert pending["state"] == "CANCEL_PENDING"
        assert pending["source_id"] == ENTRY_SOURCE
        assert any(
            row["event_type"] == "CANCEL_REQUEST_ACCEPTED" and row["source_id"] == ENTRY_SOURCE
            for row in engine.cap100_events()
        )
        terminal = engine._apply_remote(
            pending,
            {"orderId": "O-R21", "status": "CANCELLED", "filledShareQty": 0, "filledUsdtAmount": 0},
        )
        assert terminal["state"] == "CANCELED"
    finally:
        engine.close()
