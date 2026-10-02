from __future__ import annotations

from types import SimpleNamespace

import pytest

from predict_bot import echtgeld_engine_v23 as v23
from predict_bot.core import ApiTransportError


class FakeExecutor:
    def __init__(self, config) -> None:
        self.config = config
    def close(self) -> None:
        pass
    def available_balance_snapshot(self) -> dict:
        return {"status": "OK", "availableUsdt": 100.0}
    def execute(self, **_kwargs):
        raise AssertionError("generic executor must not be used by CAP100 tests")


class FakeClient:
    def __init__(self) -> None:
        self.place_result = {"orderId": "O1", "status": "NEW"}
        self.place_exc: Exception | None = None
        self.cancel_calls: list[list[str]] = []
        self.cancel_exc: Exception | None = None
        self.history: list[dict] = []
    def orderbook(self, *_args, **_kwargs):
        return {}
    def server_timestamp_ms(self):
        return 2_000_000
    def get_quote(self, **_kwargs):
        return {"quoteId": "Q1"}
    def place_limit_order(self, **_kwargs):
        if self.place_exc:
            raise self.place_exc
        return dict(self.place_result)
    def place_market_order(self, **_kwargs):
        if self.place_exc:
            raise self.place_exc
        return dict(self.place_result)
    def batch_cancel_orders_raw(self, **kwargs):
        self.cancel_calls.append(list(kwargs["order_ids"]))
        if self.cancel_exc:
            raise self.cancel_exc
        return {"ok": True}
    def order_history(self, *_args, **_kwargs):
        return list(self.history)
    def active_orders(self, *_args, **_kwargs):
        return []


def make_engine(tmp_path, monkeypatch):
    engine = v23.EchtgeldEngine(
        tmp_path / "echtgeld.db",
        executor_factory=FakeExecutor,
        start_worker=False,
        settlement_db_path=tmp_path / "none.db",
        target_official_db_path=tmp_path / "none2.db",
    )
    client = FakeClient()
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
    engine.select_entry_source(v23.CAP100_SOURCE)
    engine.armed = True
    return engine, client


def maker_payload(cid="M1"):
    return {
        "entrySource": v23.CAP100_SOURCE,
        "clientOrderId": cid,
        "marketId": 1510001,
        "bucketStartSec": 2_000,
        "windowEndMs": 2_300_000,
        "asset": "BTC",
        "side": "UP",
        "price": 0.40,
        "shares": 18.0,
    }


def r2_r21_taker_payload(cid="T-R21"):
    return {
        "entrySource": v23.R2_R21_SOURCE,
        "clientOrderId": cid,
        "controllerVersion": "UNIFIED_R2_R21_V33_INFORMATION_ONLY_ECHTGELD_10SHARE_V3",
        "marketId": 1510001,
        "bucketStartSec": 2_000,
        "windowEndMs": 2_300_000,
        "asset": "BTC",
        "side": "UP",
        "price": 0.60,
        "maxPrice": 0.62,
        "shares": 10.0,
    }


def test_maker_ack_is_resting_and_fill_deltas_are_incremental(tmp_path, monkeypatch):
    engine, _client = make_engine(tmp_path, monkeypatch)
    try:
        result = engine.submit_cap100_maker(maker_payload())
        assert result["ok"] is True
        assert result["order"]["state"] == "RESTING"
        assert result["order"]["filled_share_qty"] == 0

        row = result["order"]
        row = engine._apply_remote(row, {
            "orderId": "O1", "status": "PARTIALLY_FILLED", "fillPercentage": 0.5,
            "filledShareQty": 9.0, "filledUsdtAmount": 3.6,
        })
        assert row["state"] == "PARTIAL_FILL"
        assert row["filled_share_qty"] == pytest.approx(9.0)

        row = engine._apply_remote(row, {
            "orderId": "O1", "status": "FILLED", "fillPercentage": 1.0,
            "filledShareQty": 18.0, "filledUsdtAmount": 7.2,
        })
        assert row["state"] == "FILLED"
        fills = [e for e in engine.cap100_events() if e["event_type"] == "FILL_DELTA"]
        assert [e["delta_shares"] for e in fills] == pytest.approx([9.0, 9.0])
        assert [e["delta_usdt"] for e in fills] == pytest.approx([3.6, 3.6])
    finally:
        engine.close()


def test_cancel_ack_is_not_terminal_until_reconciled(tmp_path, monkeypatch):
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        result = engine.submit_cap100_maker(maker_payload())
        cancel = engine.cancel_cap100_order({"entrySource": v23.CAP100_SOURCE, "clientOrderId": "M1"})
        assert cancel["ok"] is True
        assert cancel["order"]["state"] == "CANCEL_PENDING"
        assert client.cancel_calls == [["O1"]]

        row = engine._apply_remote(cancel["order"], {"orderId": "O1", "status": "CANCELLED", "filledShareQty": 0, "filledUsdtAmount": 0})
        assert row["state"] == "CANCELED"
    finally:
        engine.close()


def test_cancel_pending_is_idempotent_across_controller_and_engine_pause(tmp_path, monkeypatch):
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        engine.submit_cap100_maker(maker_payload())
        first = engine.cancel_cap100_order({"entrySource": v23.CAP100_SOURCE, "clientOrderId": "M1"})
        duplicate = engine.cancel_cap100_order({"entrySource": v23.CAP100_SOURCE, "clientOrderId": "M1"})
        engine.pause("market rollover")
        assert first["order"]["state"] == "CANCEL_PENDING"
        assert duplicate["alreadyPending"] is True
        assert client.cancel_calls == [["O1"]]
        row = engine._cap100_rows(active_only=True)[0]
        assert row["state"] == "CANCEL_PENDING"
    finally:
        engine.close()


def test_r2_r21_taker_timeout_cancels_once_and_waits_for_terminal_reconciliation(tmp_path, monkeypatch):
    clock = [1_000_000]
    monkeypatch.setattr(v23, "_now_ms", lambda: clock[0])
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        engine.armed = False
        engine.select_entry_source(v23.R2_R21_SOURCE)
        engine.armed = True
        client.place_result = {"orderId": "O-TAKER", "status": "NEW"}
        client.history = [{
            "orderId": "O-TAKER",
            "status": "NEW",
            "filledShareQty": 0,
            "filledUsdtAmount": 0,
        }]
        placed = engine.submit_cap100_taker(r2_r21_taker_payload())
        assert placed["order"]["state"] == "RESTING"

        clock[0] += v23.R2_R21_TAKER_CONFIRM_TIMEOUT_MS - 1
        engine._cap100_reconcile_once()
        assert client.cancel_calls == []

        clock[0] += 1
        engine._cap100_reconcile_once()
        assert client.cancel_calls == [["O-TAKER"]]
        pending = engine._cap100_rows(active_only=True)[0]
        assert pending["state"] == "CANCEL_PENDING"

        # A stale NEW/PARTIALLY_FILLED history row after cancel acceptance must
        # retain cancel ownership and must never emit a duplicate venue cancel.
        client.history = [{
            "orderId": "O-TAKER",
            "status": "PARTIALLY_FILLED",
            "filledShareQty": 4,
            "filledUsdtAmount": 2.4,
        }]
        clock[0] += 500
        engine._cap100_reconcile_once()
        pending = engine._cap100_rows(active_only=True)[0]
        assert pending["state"] == "CANCEL_PENDING"
        assert pending["filled_share_qty"] == pytest.approx(4.0)
        assert client.cancel_calls == [["O-TAKER"]]

        client.history = [{
            "orderId": "O-TAKER",
            "status": "CANCELLED",
            "filledShareQty": 4,
            "filledUsdtAmount": 2.4,
        }]
        clock[0] += 500
        engine._cap100_reconcile_once()
        terminal = engine._cap100_rows(active_only=False)[0]
        assert terminal["state"] == "CANCELED"
        assert terminal["filled_share_qty"] == pytest.approx(4.0)
        assert client.cancel_calls == [["O-TAKER"]]
        events = engine.cap100_events()
        assert any(
            row["event_type"] == "CANCEL_REQUEST_ACCEPTED"
            and "R2_R21_TAKER_CONFIRM_TIMEOUT_2200MS" in str(row.get("detail") or "")
            for row in events
        )
        assert any(row["event_type"] == "FILL_DELTA" and row["delta_shares"] == pytest.approx(4.0) for row in events)
        assert any(row["event_type"] == "ORDER_CANCELED" for row in events)
    finally:
        engine.close()


def test_transport_unknown_freezes_all_following_cap100_entries(tmp_path, monkeypatch):
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        client.place_exc = ApiTransportError("timeout after write")
        result = engine.submit_cap100_maker(maker_payload("M-UNKNOWN"))
        assert result["uncertain"] is True
        assert result["order"]["state"] == "UNKNOWN_SUBMISSION"

        client.place_exc = None
        with pytest.raises(v23.v1.EchtgeldEngineError, match="frozen"):
            engine.submit_cap100_maker(maker_payload("M2"))
    finally:
        engine.close()


def test_unknown_submission_can_be_adopted_only_by_unique_history_fingerprint(tmp_path, monkeypatch):
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        client.place_exc = ApiTransportError("timeout")
        result = engine.submit_cap100_maker(maker_payload("M-UNKNOWN"))
        row = result["order"]
        client.place_exc = None
        client.history = [{
            "orderId": "O-RECOVERED", "tokenId": "UPTOKEN", "side": "BUY", "orderType": "LIMIT",
            "price": 0.40, "status": "FILLED", "filledShareQty": 18.0, "filledUsdtAmount": 7.2,
        }]
        engine._cap100_reconcile_once()
        recovered = engine._cap100_rows(active_only=False)[0]
        assert recovered["order_id"] == "O-RECOVERED"
        assert recovered["state"] == "FILLED"
        assert engine._cap100_unknown_write() is None
    finally:
        engine.close()


def test_pause_requests_cancel_but_preserves_risk_until_confirmation(tmp_path, monkeypatch):
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        engine.submit_cap100_maker(maker_payload())
        engine.pause("test")
        row = engine._cap100_rows(active_only=True)[0]
        assert row["state"] == "CANCEL_PENDING"
        assert client.cancel_calls == [["O1"]]
        assert engine.armed is False
    finally:
        engine.close()


def test_cancel_unknown_is_engine_level_fail_closed(tmp_path, monkeypatch):
    engine, client = make_engine(tmp_path, monkeypatch)
    try:
        engine.submit_cap100_maker(maker_payload())
        client.cancel_exc = RuntimeError("cancel API implementation failure")
        result = engine.cancel_cap100_order({"entrySource": v23.CAP100_SOURCE, "clientOrderId": "M1"})
        assert result["uncertain"] is True
        assert result["order"]["state"] == "CANCEL_UNKNOWN"
        assert engine._cap100_unknown_write() is not None
        with pytest.raises(v23.v1.EchtgeldEngineError, match="frozen"):
            engine.submit_cap100_maker(maker_payload("M2"))
    finally:
        engine.close()
