import pytest

from tools.analyze_wallet_maker_strategy_forward import _lifetime_profile, _pair_opposite_within, _structural_profile


def parent(*, market: int, side: str, at: int, price: float, shares: float = 18.0, order: str) -> dict:
    return {
        "market_id": market,
        "side": side,
        "first_event_ms": at,
        "last_event_ms": at,
        "average_price": price,
        "shares": shares,
        "notional_usdt": shares * price,
        "fill_legs": 1,
        "order_hash": order,
    }


def test_pair_proxy_is_one_to_one_and_measures_complement_edge() -> None:
    parents = [
        parent(market=1, side="UP", at=1_000, price=0.45, order="u1"),
        parent(market=1, side="DOWN", at=2_000, price=0.50, order="d1"),
        parent(market=1, side="UP", at=2_500, price=0.46, order="u2"),
    ]
    pairs = _pair_opposite_within(parents)
    assert len(pairs) == 1
    assert pairs[0]["priceSum"] == 0.95
    assert pairs[0]["grossLockedEdgeUsdt"] == pytest.approx(0.9)


def test_structure_distinguishes_soft_inventory_from_strict_alternation() -> None:
    parents = [
        parent(market=1, side="UP", at=1_000, price=0.45, order="u1"),
        parent(market=1, side="UP", at=2_000, price=0.44, order="u2"),
        parent(market=1, side="DOWN", at=3_000, price=0.50, order="d1"),
        parent(market=1, side="DOWN", at=4_000, price=0.49, order="d2"),
    ]
    result = _structural_profile(parents)
    assert result["observedParentSize"]["exact18Share"] == 1.0
    assert result["centGrid"]["integerCentPriceShare"] == 1.0
    assert result["refillAndRuns"]["sameSideRunLengthMedian"] == 2.0
    assert result["softInventory"]["finalPairedShareCoverageMedian"] == 1.0


def test_lifetime_profile_freezes_last_30_second_cutoff() -> None:
    parents = [
        parent(market=1, side="UP", at=1_000, price=0.45, order="u1"),
        parent(market=1, side="UP", at=46_000, price=0.44, order="u2"),
        parent(market=1, side="DOWN", at=76_000, price=0.50, order="d1"),
    ]
    result = _lifetime_profile(parents, {1: 91_000})
    assert result["frozenActiveCutoffSecondsLeft"] == 30
    assert result["last30SecondsParents"] == 1
    assert result["prior30SecondsParents"] == 1
