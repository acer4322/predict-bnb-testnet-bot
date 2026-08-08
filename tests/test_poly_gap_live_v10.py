from __future__ import annotations

from pathlib import Path

from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v9 import OrderReconciledPolyGapLiveEngine
from predict_bot.poly_gap_live_v10 import ExitOrderReconciledPolyGapLiveEngine


class FakeExitClient:
    def __init__(self, *, order_status: str, total: float = 1.5, available: float = 1.5) -> None:
        self.order_status = order_status
        self.total = total
        self.available = available

    def position_by_token(self, _wallet: str, _token: str):
        return {
            "shares": self.total,
            "availableShares": self.available,
        }

    def active_orders(self, _wallet: str, *, market_id: int | None = None, limit: int = 100):
        if self.order_status in {"NEW", "OPEN", "PENDING", "PROCESSING"}:
            return {
                "orders": [
                    {
                        "orderId": "sell-1",
                        "status": self.order_status,
                    }
                ]
            }
        return {"orders": []}

    def order_history(self, _wallet: str, *, limit: int = 100):
        if self.order_status in {"NEW", "OPEN", "PENDING", "PROCESSING"}:
            return {"orders": []}
        return {
            "orders": [
                {
                    "orderId": "sell-1",
                    "status": self.order_status,
                }
            ]
        }


def insert_exit_sync_round(engine: ExitOrderReconciledPolyGapLiveEngine) -> int:
    now = base._now_ms()
    with engine.db_lock:
        cursor = engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                   entry_cost_usdt,shares,exit_signal_at_ms,exit_quote_average,
                   exit_order_id,exit_placed_at_ms,exit_sync_started_at_ms,
                   exit_proceeds_usdt,created_at_ms,updated_at_ms
               ) VALUES(10,20,1,'DOWN','token-down','EXIT_SYNC',1.0,
                        1.0,1.5,?,0.30,'sell-1',?,?,0.30,?,?)""",
            (now - 1000, now - 500, now - 500, now - 2000, now),
        )
        engine.db.commit()
        return int(cursor.lastrowid)


def row_for(engine: ExitOrderReconciledPolyGapLiveEngine, round_id: int):
    with engine.db_lock:
        row = engine.db.execute(
            "SELECT * FROM poly_gap_live_rounds WHERE id=?",
            (round_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def arm_fake(engine: ExitOrderReconciledPolyGapLiveEngine, client: FakeExitClient) -> None:
    with engine.lock:
        engine.client = client  # type: ignore[assignment]
        engine.wallet_address = "wallet"
        engine.wallet_id = "wallet-id"


def test_v10_builds_on_v9() -> None:
    assert issubclass(ExitOrderReconciledPolyGapLiveEngine, OrderReconciledPolyGapLiveEngine)


def test_expired_sell_is_definite_no_fill_and_returns_open(tmp_path: Path) -> None:
    engine = ExitOrderReconciledPolyGapLiveEngine(tmp_path / "v10-no-fill.db")
    try:
        arm_fake(engine, FakeExitClient(order_status="EXPIRED", total=1.5, available=1.5))
        round_id = insert_exit_sync_round(engine)
        engine._sync_exit(row_for(engine, round_id))
        row = row_for(engine, round_id)
        assert row["state"] == "OPEN"
        assert row["exit_order_id"] is None
        assert row["error_kind"] == "EXIT_ORDER_NOT_FILLED"
        assert engine.halted_market_id is None
    finally:
        engine.stop()


def test_filled_sell_closes_even_if_position_endpoint_still_lags(tmp_path: Path) -> None:
    engine = ExitOrderReconciledPolyGapLiveEngine(tmp_path / "v10-filled.db")
    try:
        # available=0 can mean the SELL reserved the shares while the total-position
        # endpoint has not propagated yet. FILLED order history is authoritative.
        arm_fake(engine, FakeExitClient(order_status="FILLED", total=1.5, available=0.0))
        round_id = insert_exit_sync_round(engine)
        engine.entry_signal_latch = (10, "DOWN")
        engine._sync_exit(row_for(engine, round_id))
        row = row_for(engine, round_id)
        assert row["state"] == "CLOSED"
        assert row["close_reason"] == "EXIT_ORDER_FILLED"
        assert abs(float(row["pnl_usdt"]) - (-0.70)) < 1e-9
        assert engine.entry_signal_latch is None
        assert engine.last_rearm_reason == "confirmed flat after EXIT_ORDER_FILLED"
        assert engine.halted_market_id is None
    finally:
        engine.stop()


def test_active_sell_with_reserved_shares_stays_exit_sync(tmp_path: Path) -> None:
    engine = ExitOrderReconciledPolyGapLiveEngine(tmp_path / "v10-active.db")
    try:
        arm_fake(engine, FakeExitClient(order_status="NEW", total=1.5, available=0.0))
        round_id = insert_exit_sync_round(engine)
        engine._sync_exit(row_for(engine, round_id))
        row = row_for(engine, round_id)
        assert row["state"] == "EXIT_SYNC"
        assert row["exit_order_id"] == "sell-1"
        assert engine.status == "EXIT_SHARES_RESERVED_WAITING_ORDER"
        assert engine.halted_market_id is None
    finally:
        engine.stop()


def test_unresolved_exit_order_blocks_hold_to_settlement_accounting(tmp_path: Path) -> None:
    engine = ExitOrderReconciledPolyGapLiveEngine(tmp_path / "v10-settlement.db")
    try:
        arm_fake(engine, FakeExitClient(order_status="NEW", total=1.5, available=0.0))
        round_id = insert_exit_sync_round(engine)
        engine._settle_hold(row_for(engine, round_id))
        row = row_for(engine, round_id)
        assert row["state"] == "EXIT_SYNC"
        assert row["pnl_usdt"] is None
        assert engine.status == "SETTLEMENT_WAITING_EXIT_RECONCILIATION"
    finally:
        engine.stop()


def test_v10_snapshot_exposes_exit_reconciliation(tmp_path: Path) -> None:
    engine = ExitOrderReconciledPolyGapLiveEngine(tmp_path / "v10-snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V10"
        reconciliation = state["exitReconciliation"]
        assert reconciliation["usesOrderHistory"] is True
        assert reconciliation["unresolvedOrderBlocksDuplicateSell"] is True
        assert reconciliation["unresolvedOrderBlocksOfficialSettlement"] is True
        assert reconciliation["usesAvailableSharesAsFlatProof"] is False
    finally:
        engine.stop()


def test_supervisor_runs_v10_entrypoint() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v10" in source
    assert "predict_bot.poly_gap_live_v9" not in source
