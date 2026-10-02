from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SUPERVISOR = ROOT / "src" / "predict_bot" / "supervisor.py"
LAUNCHER = ROOT / "src" / "predict_bot" / "cross_oracle_strategy_entry_quote_exit_sim.py"


def test_supervisor_launches_entry_quote_exit_sim_sidecar() -> None:
    source = SUPERVISOR.read_text(encoding="utf-8")
    assert "predict_bot.cross_oracle_strategy_entry_quote_exit_sim" in source


def test_actual_launcher_injects_v5_scenario_summary_canary() -> None:
    source = LAUNCHER.read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        and node.module == "poly_quote_canary_scenario_summary"
        for alias in node.names
    }
    assert "ScenarioSummaryPolyQuoteCanary" in imported
    assert "strategy_module.PolyQuoteCanary = ScenarioSummaryPolyQuoteCanary" in source
    assert "ExitSimulatedPolyQuoteCanary" not in source
