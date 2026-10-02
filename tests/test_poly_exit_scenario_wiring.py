from __future__ import annotations

from predict_bot import cross_oracle_strategy_quote_sized as wrapper
from predict_bot import cross_oracle_strategy_resilient as strategy_module
from predict_bot.poly_quote_canary_exit_sim_v4 import (
    SafeDurableExitSimulatedPolyQuoteCanary,
)


def test_strategy_wrapper_installs_exit_scenario_canary() -> None:
    assert wrapper.SafeDurableExitSimulatedPolyQuoteCanary is SafeDurableExitSimulatedPolyQuoteCanary
    assert strategy_module.PolyQuoteCanary is SafeDurableExitSimulatedPolyQuoteCanary


def test_legacy_migration_scope_is_only_available_share_rejects() -> None:
    check = SafeDurableExitSimulatedPolyQuoteCanary._is_invalid_paper_sell_reject
    assert check({"status": "QUOTE_REJECTED", "reason": "HTTP 400 code -9000 available shares"})
    assert check({"status": "QUOTE_REJECTED", "reason": "You have exceeded your available shares."})
    assert not check({"status": "QUOTE_REJECTED", "reason": "rate limit"})
    assert not check({"status": "PASS_SIMULATED_PLACE", "reason": "ok"})
