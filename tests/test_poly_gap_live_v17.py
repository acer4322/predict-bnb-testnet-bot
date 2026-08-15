from __future__ import annotations

from pathlib import Path

from predict_bot.poly_gap_live_v16 import TimeSyncHardenedPolyGapLiveEngine
from predict_bot.poly_gap_live_v17 import SettlementRecoveryPolyGapLiveEngine


def _insert_round(
    engine: SettlementRecoveryPolyGapLiveEngine,
    *,
    state: str,
    market_id: int = 101,
    topic_id: int = 201,
    side: str = "UP",
    shares: float = 2.0,
    entry_cost: float = 1.0,
    end_ms: int | None = None,
    exit_order_id: str | None = None,
) -> int:
    now = 1_800_000_000_000
    with engine.db_lock:
        cursor = engine.db.execute(
            """INSERT INTO poly_gap_live_rounds(
                   market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                   entry_signal_at_ms,entry_cost_usdt,shares,exit_order_id,
                   created_at_ms,updated_at_ms,market_end_ms
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                market_id,
                topic_id,
                1,
                side,
                "token",
                state,
                1.0,
                now,
                entry_cost,
                shares,
                exit_order_id,
                now,
                now,
                end_ms,
            ),
        )
        engine.db.commit()
        return int(cursor.lastrowid)


def test_v17_builds_on_v16() -> None:
    assert issubclass(SettlementRecoveryPolyGapLiveEngine, TimeSyncHardenedPolyGapLiveEngine)


def test_expired_open_round_moves_to_background_waiting_settlement(
    tmp_path: Path, monkeypatch
) -> None:
    engine = SettlementRecoveryPolyGapLiveEngine(tmp_path / "v17-detach.db")
    try:
        end_ms = 1_800_000_100_000
        round_id = _insert_round(engine, state="OPEN", end_ms=end_ms)
        monkeypatch.setattr("predict_bot.poly_gap_live_v17.base._now_ms", lambda: end_ms + 10_000)
        monkeypatch.setattr(engine, "_official_winner", lambda _market_id: None)

        row = engine._round_state(round_id)
        assert row is not None
        assert engine._handle_expired_active(row, end_ms) is True

        after = engine._round_state(round_id)
        assert after is not None
        assert after["state"] == "WAITING_SETTLEMENT"
        assert engine._current_active_round() is None
        assert engine.settlement_recovery_stats["expiredDetached"] == 1
    finally:
        engine.stop()


def test_unresolved_exit_order_remains_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    engine = SettlementRecoveryPolyGapLiveEngine(tmp_path / "v17-exit.db")
    try:
        end_ms = 1_800_000_100_000
        round_id = _insert_round(
            engine,
            state="EXIT_SYNC",
            end_ms=end_ms,
            exit_order_id="sell-123",
        )
        monkeypatch.setattr("predict_bot.poly_gap_live_v17.base._now_ms", lambda: end_ms + 10_000)
        calls: list[int] = []
        monkeypatch.setattr(engine, "_settle_hold", lambda row: calls.append(int(row["id"])))

        row = engine._round_state(round_id)
        assert row is not None
        assert engine._handle_expired_active(row, end_ms) is True
        assert calls == [round_id]

        after = engine._round_state(round_id)
        assert after is not None
        assert after["state"] == "EXIT_SYNC"
        assert engine._current_active_round() is not None
        assert engine.settlement_recovery_stats["lastBlockedRoundId"] == round_id
        assert "SELL order sell-123" in str(
            engine.settlement_recovery_stats["lastBlockedReason"]
        )
    finally:
        engine.stop()


def test_background_waiting_round_settles_only_when_official_winner_exists(
    tmp_path: Path, monkeypatch
) -> None:
    engine = SettlementRecoveryPolyGapLiveEngine(tmp_path / "v17-background.db")
    try:
        end_ms = 1_800_000_100_000
        round_id = _insert_round(engine, state="WAITING_SETTLEMENT", end_ms=end_ms)
        monkeypatch.setattr(engine, "_official_winner", lambda _market_id: "UP")
        engine._next_pending_settlement_scan_mono = 0.0
        engine._recover_pending_settlements()

        after = engine._round_state(round_id)
        assert after is not None
        assert after["state"] == "SETTLED"
        assert after["official_winner"] == "UP"
        assert after["pnl_usdt"] == 1.0
        assert engine.settlement_recovery_stats["backgroundSettled"] == 1
    finally:
        engine.stop()


def test_legacy_round_end_can_be_inferred_without_current_market_cache(tmp_path: Path, monkeypatch) -> None:
    engine = SettlementRecoveryPolyGapLiveEngine(tmp_path / "v17-end.db")
    try:
        signal_ms = 1_800_000_123_456
        with engine.db_lock:
            cursor = engine.db.execute(
                """INSERT INTO poly_gap_live_rounds(
                       market_id,topic_id,round_no,side,token_id,state,stake_usdt,
                       entry_signal_at_ms,entry_cost_usdt,shares,
                       created_at_ms,updated_at_ms,market_end_ms
                   ) VALUES(101,201,1,'UP','token','OPEN',1.0,?,1.0,2.0,?,?,NULL)""",
                (signal_ms, signal_ms, signal_ms),
            )
            engine.db.commit()
            round_id = int(cursor.lastrowid)
        monkeypatch.setattr(engine, "_recover_round_end_from_binance", lambda _row: None)

        row = engine._round_state(round_id)
        assert row is not None
        inferred = engine._round_end_ms(row)
        assert inferred == ((signal_ms // 300_000) + 1) * 300_000

        after = engine._round_state(round_id)
        assert after is not None
        assert after["market_end_ms"] == inferred
        assert engine.settlement_recovery_stats["legacyEndInferredFromEntryBucket"] == 1
    finally:
        engine.stop()


def test_v17_snapshot_declares_pending_settlement_non_blocking(tmp_path: Path) -> None:
    engine = SettlementRecoveryPolyGapLiveEngine(tmp_path / "v17-snapshot.db")
    try:
        _insert_round(engine, state="WAITING_SETTLEMENT", end_ms=1_800_000_100_000)
        state = engine.snapshot()
        assert state["version"] == "POLY_GAP_DEDICATED_LIVE_V17"
        recovery = state["settlementRecovery"]
        assert recovery["pendingCount"] == 1
        assert recovery["waitingSettlementBlocksNewMarket"] is False
        assert recovery["unresolvedExecutionStillBlocksNewMarket"] is True
        assert recovery["officialWinnerRequiredForLivePnl"] is True
        assert recovery["proxySettlementNeverUsedForLivePnl"] is True
    finally:
        engine.stop()


def test_supervisor_launches_v17() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.poly_gap_live_v17" in source
