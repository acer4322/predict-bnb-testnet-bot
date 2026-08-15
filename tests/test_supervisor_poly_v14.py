from __future__ import annotations

from pathlib import Path


def test_supervisor_runs_trade_ready_collector_and_live_v14() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "predict_bot"
        / "supervisor.py"
    ).read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_trade_readiness" in source
    assert "predict_bot.poly_gap_live_v14" in source
    assert "predict_bot.cross_oracle_event_identity_recovery\"]" not in source
    assert "predict_bot.poly_gap_live_v13\"]" not in source
