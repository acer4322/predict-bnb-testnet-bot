from __future__ import annotations

from pathlib import Path

from predict_bot import poly_gap_live as base
from predict_bot.poly_gap_live_v10 import ExitOrderReconciledPolyGapLiveEngine
from predict_bot.poly_gap_live_v11 import LegacyAwareExitPolyGapLiveEngine


class FakeHistoryClient:
    def __init__(self, status: str) -> None:
        self.status = status

    def active_orders(self, _wallet: str, *, market_id: int | None = None, limit: int = 100):
        return {"orders": []}

    def order_history(self, _wallet: str, *, limit: int = 100):
        return {"orders": [{"orderId": "sell-legacy", "status": self.status}]}

    def close(self) -> None:
        return


def insert_legacy_settled(
    engine: LegacyAwareExitPolyGapLiveEngine,
    *,
    submitted_shares: float,
    recorded_shares: float = 1.5,
) -> int:
    now = base._now_ms()
    with engine.db_lock:
        cursor = engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                   entry_cost_usdt,shares,exit_signal_at_ms,exit_quote_average,
                   exit_quote_amount_in_wei,exit_order_id,exit_placed_at_ms,
                   exit_sync_started_at_ms,exit_proceeds_usdt,pnl_usdt,
                   official_winner,close_reason,created_at_ms,updated_at_ms
               ) VALUES(10,20,1,'DOWN','token-down','SETTLED',1.0,
                        1.0,?,?,0.30,?,'sell-legacy',?,?,0.30,0.50,
                        'DOWN','OFFICIAL_SETTLEMENT',?,?)""",
            (
                recorded_shares,
                now - 1000,
                base._to_wei(submitted_shares),
                now - 500,
                now - 500,
                now - 2000,
                now,
            ),
        )
        engine.db.commit()
        return int(cursor.lastrowid)


def read_row(engine: LegacyAwareExitPolyGapLiveEngine, round_id: int):
    with engine.db_lock:
        row = engine.db.execute(
            "SELECT * FROM poly_gap_live_rounds WHERE id=?",
            (round_id,),
        ).fetchone()
    assert row is not None
    return dict(row)


def arm_history(engine: LegacyAwareExitPolyGapLiveEngine, status: str) -> None:
    with engine.lock:
        engine.client = FakeHistoryClient(status)  # type: ignore[assignment]
        engine.wallet_address = "wallet"
        engine.wallet_id = "wallet-id"


def test_v11_builds_on_v10() -> None:
    assert issubclass(LegacyAwareExitPolyGapLiveEngine, ExitOrderReconciledPolyGapLiveEngine)


def test_v11_corrects_legacy_settlement_when_full_sell_is_filled(tmp_path: Path) -> None:
    engine = LegacyAwareExitPolyGapLiveEngine(tmp_path / "v11-filled.db")
    try:
        arm_history(engine, "FILLED")
        round_id = insert_legacy_settled(engine, submitted_shares=1.5)
        engine._reconcile_legacy_settled_exits()
        row = read_row(engine, round_id)
        assert row["state"] == "CLOSED"
        assert row["close_reason"] == "LEGACY_EXIT_ORDER_FILLED_RECONCILED"
        assert abs(float(row["pnl_usdt"]) - (-0.70)) < 1e-9
        assert engine.legacy_exit_reconcile_summary["correctedFilled"] == 1
    finally:
        engine.stop()


def test_v11_keeps_settlement_when_sell_was_no_fill(tmp_path: Path) -> None:
    engine = LegacyAwareExitPolyGapLiveEngine(tmp_path / "v11-no-fill.db")
    try:
        arm_history(engine, "EXPIRED")
        round_id = insert_legacy_settled(engine, submitted_shares=1.5)
        engine._reconcile_legacy_settled_exits()
        row = read_row(engine, round_id)
        assert row["state"] == "SETTLED"
        assert row["close_reason"] == "OFFICIAL_SETTLEMENT"
        assert engine.legacy_exit_reconcile_summary["confirmedNoFill"] == 1
    finally:
        engine.stop()


def test_v11_does_not_rewrite_if_filled_sell_was_not_full_position(tmp_path: Path) -> None:
    engine = LegacyAwareExitPolyGapLiveEngine(tmp_path / "v11-partial.db")
    try:
        arm_history(engine, "FILLED")
        round_id = insert_legacy_settled(engine, submitted_shares=1.0, recorded_shares=1.5)
        engine._reconcile_legacy_settled_exits()
        row = read_row(engine, round_id)
        assert row["state"] == "SETTLED"
        assert engine.legacy_exit_reconcile_summary["unresolved"] == 1
    finally:
        engine.stop()


def test_v11_snapshot_exposes_legacy_reconciliation(tmp_path: Path) -> None:
    engine = LegacyAwareExitPolyGapLiveEngine(tmp_path / "v11-snapshot.db")
    try:
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V11"
        legacy = state["legacyExitReconciliation"]
        assert legacy["onlyExplicitFilledMutatesHistoricalPnl"] is True
        assert legacy["requiresFullPositionSizedExit"] is True
    finally:
        engine.stop()
