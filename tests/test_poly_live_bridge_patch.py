from __future__ import annotations

from decimal import Decimal

from predict_bot import live_trading as live
from predict_bot.poly_live_bridge_patch import (
    EXIT_STRATEGIES,
    REPRICE_GAP,
    STRATEGIES,
    VERSION,
    _direction,
    _mid,
    _provenance,
)


def test_poly_strategies_are_in_live_whitelist_with_reprice_guard() -> None:
    assert STRATEGIES == (
        "R_POLY_LEAD_ENTRY",
        "R_POLY_LEAD_EXIT",
        "R_POLY_GAP_SCALP",
    )
    for strategy in STRATEGIES:
        assert strategy in live.LIVE_SUPPORTED_STRATEGIES
        assert strategy in live.LIVE_RESEARCH_STRATEGIES
        assert live.LIVE_RESEARCH_REPRICE_GAPS[strategy] == Decimal("0.05")
    assert REPRICE_GAP == Decimal("0.05")


def test_poly_exit_scope_is_only_exit_and_scalp() -> None:
    assert EXIT_STRATEGIES == {"R_POLY_LEAD_EXIT", "R_POLY_GAP_SCALP"}
    assert "R_POLY_LEAD_ENTRY" not in EXIT_STRATEGIES


def test_poly_direction_uses_same_confident_deadband() -> None:
    assert _direction(0.55) == "UP"
    assert _direction(0.45) == "DOWN"
    assert _direction(0.50) is None
    assert _mid({"bestBid": 0.54, "bestAsk": 0.56}) == 0.55


def test_fabricated_poly_live_signal_is_rejected_before_executor() -> None:
    safe, reason = _provenance(
        {
            "strategy": "R_POLY_LEAD_ENTRY",
            "market_id": 123,
            "side": "UP",
            "poly_live_bridge": False,
            "poly_live_version": VERSION,
        }
    )
    assert safe is False
    assert "provenance" in reason.lower()


def test_non_poly_signal_is_not_changed_by_poly_provenance_guard() -> None:
    assert _provenance({"strategy": "R_MICROPRICE"}) == (True, "")


def test_source_keeps_forward_only_and_single_market_live_guards() -> None:
    source = __import__("inspect").getsource(
        __import__("predict_bot.poly_live_bridge_patch", fromlist=["_loop"])
    )
    assert "SELECT COALESCE(MAX(id),0) max_id FROM cross_oracle_strategy_trades" in source
    assert "UNIQUE(strategy, market_id)" in source
    assert "engine.manual_sell(local_id, \"LIMIT\")" in source
    assert "Poly/Binance gap no longer >= 0.03" in source
    assert "BLOCKED_POLY_PROVENANCE" in source
