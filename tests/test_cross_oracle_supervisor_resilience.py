from __future__ import annotations

import inspect

import predict_bot.supervisor as supervisor


def test_supervisor_starts_resilient_cross_oracle_modules() -> None:
    source = inspect.getsource(supervisor)
    assert "predict_bot.cross_oracle_resilient" in source
    assert "predict_bot.cross_oracle_strategy_resilient" in source
    assert '"predict_bot.cross_oracle"]' not in source
    assert '"predict_bot.cross_oracle_strategy_server"]' not in source
