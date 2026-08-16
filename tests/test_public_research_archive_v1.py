from __future__ import annotations

import sqlite3
from pathlib import Path

from predict_bot import public_research_archive_v1 as archive_mod


ROOT = Path(__file__).resolve().parents[1]


def test_archive_schema_is_legacy_ebm_compatible_and_target_blind(tmp_path: Path) -> None:
    archive = archive_mod.PublicResearchArchive(tmp_path / "public.db")
    try:
        with sqlite3.connect(archive.db_path) as db:
            columns = {
                row[1]
                for row in db.execute("PRAGMA table_info(wallet_taker_signal_snapshots)")
            }
        assert "market_id" in columns
        assert set(archive_mod.LEGACY_SIGNAL_COLUMNS).issubset(columns)
        assert {
            "predict_receipt_age_ms",
            "chainlink_receipt_age_ms",
            "spot_trade_stream_status",
            "micro_writer_lag_ms",
            "missing_feature_count",
            "feature_completeness_ratio",
        }.issubset(columns)
        state = archive.state()
        assert state["targetBlind"] is True
        assert state["targetWalletInputs"] is False
        assert state["officialTruthInputs"] is False
        assert state["strategyOutputsUsed"] is False
    finally:
        archive.stop()


def test_missing_flow_feature_remains_sql_null_not_neutral_zero(tmp_path: Path) -> None:
    archive = archive_mod.PublicResearchArchive(tmp_path / "public.db")
    try:
        snapshot = {column: 1.0 for column in archive_mod.ALL_INSERT_COLUMNS}
        snapshot.update(
            market_id=123,
            sampled_at_ms=1_800_000_000_000,
            spot_taker_imbalance_1s=None,
            missing_feature_count=1,
            feature_completeness_ratio=0.9,
        )
        assert archive._write_snapshot(snapshot) is True
        with sqlite3.connect(archive.db_path) as db:
            row = db.execute(
                "SELECT spot_taker_imbalance_1s,missing_feature_count "
                "FROM wallet_taker_signal_snapshots WHERE market_id=123"
            ).fetchone()
        assert row == (None, 1)
    finally:
        archive.stop()


def test_public_archive_port_and_launcher_are_reserved() -> None:
    assert archive_mod.PORT == 8783
    launcher = (ROOT / "start-public-research-archive-v1.ps1").read_text(encoding="utf-8")
    assert "predict_bot.public_research_archive_v1" in launcher
    assert "127.0.0.1:8783/health" in launcher
    assert "127.0.0.1:8771/state" in launcher
    assert "Target Wallet / 8776 inputs = none" in launcher
