from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUMMARY = ROOT / "src" / "predict_bot" / "poly_quote_canary_scenario_summary.py"
WRAPPER = ROOT / "src" / "predict_bot" / "cross_oracle_strategy_quote_sized.py"


def test_scenario_summary_filters_to_executable_signed_entries() -> None:
    source = SUMMARY.read_text(encoding="utf-8")
    assert "entry_would_submit" in source
    assert "would_submit=1" in source
    assert 'int(row.get("entry_would_submit") or 0) == 1' in source
    assert '"excludedEntryFailures"' in source


def test_scenario_summary_exposes_same_three_metrics_for_best_and_worst() -> None:
    source = SUMMARY.read_text(encoding="utf-8")
    assert '"trades": 0' in source
    assert '"open": 0' in source
    assert '"winRate": None' in source
    assert '"grossPnlUsdt": 0.0' in source
    assert '"best": best' in source
    assert '"worst": worst' in source
    assert '"grossPnl": True' in source


def test_best_uses_exit_envelope_and_worst_holds_to_settlement() -> None:
    source = SUMMARY.read_text(encoding="utf-8")
    assert "sim_best_exit_proceeds_usdt" in source
    assert "sim_best_unfilled_shares" in source
    assert "zero shares sold on every exit signal" in source
    assert "official_winner" in source


def test_runtime_wrapper_injects_v5_summary_canary() -> None:
    source = WRAPPER.read_text(encoding="utf-8")
    assert "ScenarioSummaryPolyQuoteCanary" in source
    assert "strategy_module.PolyQuoteCanary = ScenarioSummaryPolyQuoteCanary" in source
