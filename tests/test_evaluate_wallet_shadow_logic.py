from __future__ import annotations

from tools.evaluate_wallet_shadow_logic import maker_to_taker_rate, residual_side, stream_fingerprint


def row(side: str, at_ms: int, shares: float = 18.0, price: float = 0.5) -> dict:
    return {"side": side, "at_ms": at_ms, "shares": shares, "price": price}


def test_fingerprint_reports_dual_side_cent_grid_and_unit_size() -> None:
    result = stream_fingerprint([[row("UP", 1), row("DOWN", 2)]])

    assert result["exact18Share"] == 1.0
    assert result["centGrid"] == 1.0
    assert result["bothSidesMarketRate"] == 1.0
    assert result["upShareFraction"] == 0.5


def test_role_transition_is_causal_and_same_side() -> None:
    makers = [row("UP", 1_000), row("DOWN", 2_000)]
    takers = [row("UP", 5_000), row("DOWN", 1_500), row("DOWN", 8_000)]

    assert maker_to_taker_rate(makers, takers) == 1 / 3


def test_residual_side_uses_share_inventory() -> None:
    assert residual_side([row("UP", 1, 20), row("DOWN", 2, 10)]) == "UP"
