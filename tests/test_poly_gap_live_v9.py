from __future__ import annotations

from pathlib import Path

from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v9 import (
    FILLED_POSITION_GRACE_MS,
    OrderReconciledPolyGapLiveEngine,
    _find_order,
)


class _FakeOrderClient:
    def __init__(self, *, active=None, history=None) -> None:
        self._active = active if active is not None else {"orders": []}
        self._history = history if history is not None else {"orders": []}

    def active_orders(self, wallet_address: str, *, market_id: int | None = None, limit: int = 100):
        return self._active

    def order_history(self, wallet_address: str, *, limit: int = 100):
        return self._history


def _insert_entry_sync(engine: OrderReconciledPolyGapLiveEngine, *, order_id: str, started_ms: int):
    now = base._now_ms()
    with engine.db_lock:
        cur = engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                   entry_signal_at_ms,entry_order_id,entry_sync_started_at_ms,
                   created_at_ms,updated_at_ms
               ) VALUES(101,202,1,'DOWN','token-down','ENTRY_SYNC',1.0,?,?,?,?,?)""",
            (now - 1000, order_id, started_ms, now - 1000, now),
        )
        engine.db.commit()
        row = engine.db.execute(
            "SELECT * FROM poly_gap_live_rounds WHERE id=?", (cur.lastrowid,)
        ).fetchone()
    return dict(row)


def test_find_order_matches_nested_binance_order_id() -> None:
    payload = {
        "data": {
            "orders": [
                {"orderId": "other", "status": "FILLED"},
                {"orderId": "target", "status": "CANCELED"},
            ]
        }
    }
    assert _find_order(payload, "target") == {"orderId": "target", "status": "CANCELED"}


def test_definite_fok_no_fill_rearms_instead_of_halting(tmp_path: Path) -> None:
    engine = OrderReconciledPolyGapLiveEngine(tmp_path / "v9-no-fill.db")
    try:
        engine.client = _FakeOrderClient(
            history={"orders": [{"orderId": "order-1", "status": "CANCELED"}]}
        )
        engine.wallet_address = "wallet"
        engine._position_shares = lambda token_id: 0.0  # type: ignore[method-assign]
        row = _insert_entry_sync(
            engine, order_id="order-1", started_ms=base._now_ms() - 1000
        )

        engine._sync_entry(row)

        refreshed = engine._round_state(int(row["id"]))
        assert refreshed is not None
        assert refreshed["state"] == "REJECTED"
        assert refreshed["error_kind"] == "ENTRY_ORDER_NOT_FILLED"
        assert engine.halted_market_id is None
        assert engine.status == "ENTRY_RETRY_COOLDOWN"
        assert engine.entry_signal_latch is None
        assert engine._retry_remaining_ms((101, "DOWN")) > 0
    finally:
        engine.stop()


def test_filled_order_gets_longer_position_grace(tmp_path: Path) -> None:
    engine = OrderReconciledPolyGapLiveEngine(tmp_path / "v9-filled-grace.db")
    try:
        engine.client = _FakeOrderClient(
            history={"orders": [{"orderId": "order-2", "status": "FILLED"}]}
        )
        engine.wallet_address = "wallet"
        engine._position_shares = lambda token_id: 0.0  # type: ignore[method-assign]
        row = _insert_entry_sync(
            engine, order_id="order-2", started_ms=base._now_ms() - 1000
        )

        engine._sync_entry(row)

        refreshed = engine._round_state(int(row["id"]))
        assert refreshed is not None
        assert refreshed["state"] == "ENTRY_SYNC"
        assert engine.status == "ENTRY_FILLED_WAITING_POSITION"
        assert engine.halted_market_id is None
        assert FILLED_POSITION_GRACE_MS >= base.POSITION_SYNC_TIMEOUT_MS
    finally:
        engine.stop()


def test_unknown_order_outcome_eventually_remains_fail_closed(tmp_path: Path) -> None:
    engine = OrderReconciledPolyGapLiveEngine(tmp_path / "v9-unknown.db")
    try:
        engine.client = _FakeOrderClient()
        engine.wallet_address = "wallet"
        engine._position_shares = lambda token_id: 0.0  # type: ignore[method-assign]
        row = _insert_entry_sync(
            engine,
            order_id="missing-order",
            started_ms=base._now_ms() - FILLED_POSITION_GRACE_MS - 1000,
        )

        engine._sync_entry(row)

        refreshed = engine._round_state(int(row["id"]))
        assert refreshed is not None
        assert refreshed["state"] == "AMBIGUOUS"
        assert refreshed["error_kind"] == "ENTRY_POSITION_AND_ORDER_UNCONFIRMED"
        assert engine.halted_market_id == 101
    finally:
        engine.stop()


def test_v9_snapshot_exposes_order_reconciliation(tmp_path: Path) -> None:
    engine = OrderReconciledPolyGapLiveEngine(tmp_path / "v9-snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V9"
        assert state["entryReconciliation"]["usesActiveOrders"] is True
        assert state["entryReconciliation"]["usesOrderHistory"] is True
        assert state["entryReconciliation"]["definiteFokNoFillRearms"] is True
        assert state["entryReconciliation"]["unknownOutcomeStillHalts"] is True
    finally:
        engine.stop()
