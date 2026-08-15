import pytest

from tools.analyze_wallet_maker_batch_pairing import (
    _pair_summary,
    pair_net_batches_fifo,
    pair_parent_fifo,
)


def parent(market: int, side: str, at: int, price: float, shares: float, parent_id: str):
    return {
        "market_id": market,
        "side": side,
        "first_event_ms": at,
        "average_price": price,
        "shares": shares,
        "notional_usdt": shares * price,
        "parent_id": parent_id,
    }


def test_net_batch_pairs_same_second_before_carrying_residual_fifo() -> None:
    parents = [
        parent(1, "UP", 1_000, 0.40, 18, "u1"),
        parent(1, "DOWN", 2_000, 0.50, 36, "d1"),
        parent(1, "UP", 4_000, 0.45, 18, "u2"),
        parent(1, "DOWN", 4_000, 0.48, 9, "d2"),
    ]
    pairs, trajectory = pair_net_batches_fifo(parents, {1: 10_000})
    assert sum(row["pairedShares"] for row in pairs) == 36
    assert pairs[0]["lagMs"] == 1_000
    assert pairs[0]["priceSum"] == pytest.approx(0.90)
    same_batch = next(row for row in pairs if row["withinSameSecondBatch"])
    assert same_batch["pairedShares"] == 9
    assert same_batch["priceSum"] == pytest.approx(0.93)
    assert trajectory[-1]["inventoryDeltaShares"] == -9
    assert trajectory[-1]["pairedCoverage"] == pytest.approx(1 - 9 / 81)


def test_parent_fifo_preserves_total_pairable_shares() -> None:
    parents = [
        parent(1, "UP", 1_000, 0.40, 10, "u1"),
        parent(1, "UP", 1_000, 0.41, 8, "u2"),
        parent(1, "DOWN", 2_000, 0.50, 18, "d1"),
    ]
    pairs = pair_parent_fifo(parents)
    assert sum(row["pairedShares"] for row in pairs) == 18
    assert len(pairs) == 2


def test_pair_summary_is_share_weighted() -> None:
    pairs = [
        {"pairedShares": 90, "lagMs": 0, "priceSum": 0.98, "grossLockedEdgeUsdt": 1.8, "withinSameSecondBatch": True},
        {"pairedShares": 10, "lagMs": 10_000, "priceSum": 1.02, "grossLockedEdgeUsdt": -0.2, "withinSameSecondBatch": False},
    ]
    result = _pair_summary(pairs)
    assert result["lagMsShareWeighted"]["median"] == 0
    assert result["priceSumShareWeighted"]["median"] == 0.98
    assert result["priceSumBelow1Share"] == 0.9
    assert result["grossLockedEdgeUsdt"] == pytest.approx(1.6)
