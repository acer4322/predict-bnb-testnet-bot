from __future__ import annotations

import time
from pathlib import Path

import pytest

from predict_bot import echtgeld_engine_v1 as engine_mod
from predict_bot import predict_wallet_shadow_observer_v4_23 as producer
from predict_bot import predict_wallet_target_taker_public_side_strategy_v1 as public_side


class _FakeExecutor:
    def __init__(self, config, registry: list["_FakeExecutor"], result: dict | None = None) -> None:
        self.config = config
        self.registry = registry
        self.calls: list[dict] = []
        self.closed = False
        self.result = result or {
            "status": "SUBMITTED",
            "venue": config.venue,
            "executionPrice": 0.61,
            "shares": 2.4,
            "submittedUsdt": 1.5,
            "vendorOrderId": "order-1",
            "completedAtMs": int(time.time() * 1000),
        }
        registry.append(self)

    def close(self) -> None:
        self.closed = True

    def execute(self, **kwargs):
        self.calls.append(kwargs)
        return dict(self.result)

    def available_balance_snapshot(self):
        return {
            "status": "OK",
            "venue": self.config.venue,
            "availableUsdt": 12.5,
            "predictionWalletAddress": "0x1234567890abcdef1234567890abcdef12345678",
        }


class _Factory:
    def __init__(self, result: dict | None = None) -> None:
        self.instances: list[_FakeExecutor] = []
        self.result = result

    def __call__(self, config):
        return _FakeExecutor(config, self.instances, self.result)


def _intent(*, intent_id: str = "signal-1", market_id: int = 700001, created_at_ms: int | None = None) -> dict:
    created = int(created_at_ms or time.time() * 1000)
    return {
        "intentId": intent_id,
        "dedupeKey": f"{public_side.VERSION}:{public_side.SIDE_ONLY_COHORT}:{market_id}",
        "producerVersion": producer.VERSION,
        "strategy": public_side.VERSION,
        "cohort": public_side.SIDE_ONLY_COHORT,
        "marketId": market_id,
        "signalId": intent_id,
        "createdAtMs": created,
        "decision": {
            "decision": "TRADE",
            "reason": "PUBLIC_SIDE_EBM_MATCH",
            "side": "UP",
            "ask": 0.60,
            "secondsLeft": 90.0,
        },
        "snapshot": {
            "market_id": market_id,
            "timestamp_ns": created * 1_000_000,
            "sampled_at_ms": created,
            "bucket_start_sec": (created // 300_000) * 300,
            "window_end_ms": ((created // 300_000) + 1) * 300_000,
            "seconds_left": 90.0,
            "predict_up_ask": 0.60,
            "predict_down_ask": 0.40,
        },
    }


def _make(tmp_path: Path, *, result: dict | None = None, start_worker: bool = True):
    factory = _Factory(result)
    engine = engine_mod.EchtgeldEngine(
        tmp_path / "engine.db",
        executor_factory=factory,
        start_worker=start_worker,
    )
    return engine, factory


def test_engine_always_starts_paused_and_paused_intent_is_never_queued(tmp_path: Path) -> None:
    engine, factory = _make(tmp_path)
    try:
        assert engine.armed is False
        reply = engine.submit_intent(_intent())
        assert reply["accepted"] is False
        assert reply["status"] == "IGNORED_PAUSED"
        assert engine.orders() == []
        assert sum(len(item.calls) for item in factory.instances) == 0
    finally:
        engine.close()


def test_resume_executes_once_after_durable_fence(tmp_path: Path) -> None:
    engine, factory = _make(tmp_path)
    try:
        engine.resume()
        reply = engine.submit_intent(_intent())
        assert reply["queued"] is True
        engine.intent_queue.join()
        orders = engine.orders()
        assert len(orders) == 1
        assert orders[0]["status"] == "SUBMITTED"
        live_instances = [item for item in factory.instances if item.config.mode == "live"]
        assert len(live_instances) == 1
        assert len(live_instances[0].calls) == 1
        assert orders[0]["submitted_usdt"] == pytest.approx(1.5)
    finally:
        engine.close()


def test_second_intent_same_market_hits_live_dedupe_fence(tmp_path: Path) -> None:
    engine, factory = _make(tmp_path)
    try:
        engine.resume()
        engine.submit_intent(_intent(intent_id="signal-1"))
        engine.intent_queue.join()
        engine.submit_intent(_intent(intent_id="signal-2"))
        engine.intent_queue.join()
        orders = engine.orders()
        assert len(orders) == 1
        row = engine.db.execute("SELECT status FROM engine_intents WHERE intent_id='signal-2'").fetchone()
        assert row["status"] == "DUPLICATE_FENCE"
        live_instances = [item for item in factory.instances if item.config.mode == "live"]
        assert sum(len(item.calls) for item in live_instances) == 1
    finally:
        engine.close()


def test_ambiguous_is_persisted_and_never_retried(tmp_path: Path) -> None:
    result = {
        "status": "AMBIGUOUS",
        "venue": "binance",
        "vendorOrderId": "maybe-order",
        "error": "transport returned orderId but reconciliation was inconclusive",
        "completedAtMs": int(time.time() * 1000),
    }
    engine, factory = _make(tmp_path, result=result)
    try:
        engine.update_settings({"venue": "binance", "notionalUsdt": 1.5})
        engine.resume()
        engine.submit_intent(_intent())
        engine.intent_queue.join()
        assert engine.orders()[0]["status"] == "AMBIGUOUS"
        assert sum(len(item.calls) for item in factory.instances if item.config.mode == "live") == 1
        engine.submit_intent(_intent(intent_id="signal-2"))
        engine.intent_queue.join()
        assert len(engine.orders()) == 1
        assert sum(len(item.calls) for item in factory.instances if item.config.mode == "live") == 1
        assert any(row["event_type"] == "ORDER_AMBIGUOUS_NO_RETRY" for row in engine.events())
    finally:
        engine.close()


def test_restart_abandons_queued_intent_and_never_auto_replays(tmp_path: Path) -> None:
    db_path = tmp_path / "restart.db"
    first_factory = _Factory()
    first = engine_mod.EchtgeldEngine(db_path, executor_factory=first_factory, start_worker=False)
    first.resume()
    reply = first.submit_intent(_intent())
    assert reply["queued"] is True
    first.close()

    second_factory = _Factory()
    second = engine_mod.EchtgeldEngine(db_path, executor_factory=second_factory, start_worker=True)
    try:
        assert second.armed is False
        row = second.db.execute("SELECT status FROM engine_intents WHERE intent_id='signal-1'").fetchone()
        assert row["status"] == "ABANDONED_RESTART"
        assert second.orders() == []
        assert sum(len(item.calls) for item in second_factory.instances) == 0
        assert any(row["event_type"] == "INTENT_ABANDONED_RESTART" for row in second.events())
    finally:
        second.close()


def test_settings_require_pause_and_survive_restart_without_arming(tmp_path: Path) -> None:
    db_path = tmp_path / "settings.db"
    engine, _factory = _make(tmp_path)
    try:
        engine.update_settings({
            "venue": "binance",
            "notionalUsdt": 2.25,
            "maxPriceDrift": 0.015,
            "cohort": public_side.HAZARD_SIDE_COHORT,
        })
        engine.resume()
        with pytest.raises(engine_mod.EchtgeldEngineError, match="Pause Echtgeld"):
            engine.update_settings({"notionalUsdt": 3.0})
        engine.pause()
    finally:
        engine.close()

    factory = _Factory()
    restarted = engine_mod.EchtgeldEngine(db_path, executor_factory=factory)
    try:
        assert restarted.armed is False
        assert restarted.config.venue == "binance"
        assert restarted.config.notional_usdt == pytest.approx(2.25)
        assert restarted.config.max_price_drift == pytest.approx(0.015)
        assert restarted.config.cohort == public_side.HAZARD_SIDE_COHORT
    finally:
        restarted.close()


def test_event_ledger_keeps_market_context_and_masks_wallet_address(tmp_path: Path) -> None:
    engine, _factory = _make(tmp_path)
    try:
        engine.resume()
        engine.submit_intent(_intent())
        engine.intent_queue.join()
        attempting = next(row for row in engine.events() if row["event_type"] == "ORDER_ATTEMPTING")
        assert attempting["market_id"] == 700001
        assert attempting["context"]["side"] == "UP"
        assert float(attempting["context"]["signalAsk"]) == pytest.approx(0.60)
        assert attempting["context"]["secondsLeft"] == pytest.approx(90.0)
        state = engine.state()
        assert state["balance"]["predictionWalletAddress"] == "0x1234…5678"
    finally:
        engine.close()


def test_multi_entry_paper_cohort_is_not_live_eligible(tmp_path: Path) -> None:
    engine, _factory = _make(tmp_path)
    payload = _intent()
    payload["cohort"] = "TARGET_TAKER_PUBLIC_SIDE_V1_MULTI_ENTRY_EVERY_SIGNAL"
    payload["dedupeKey"] = "multi-entry"
    try:
        with pytest.raises(engine_mod.EchtgeldEngineError, match="cohort is not live-eligible"):
            engine.submit_intent(payload)
    finally:
        engine.close()


def test_v423_rejects_legacy_embedded_live_control() -> None:
    with pytest.raises(ValueError, match="standalone Echtgeld Engine"):
        producer.WalletShadowObserver.update_target_taker_live_settings(object(), {})
