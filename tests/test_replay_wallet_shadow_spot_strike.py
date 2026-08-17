from __future__ import annotations

from tools.replay_wallet_shadow_spot_strike import (
    MarketSnapshot,
    Policy,
    chronological_split,
    effective_taker_cost,
    evaluate_policy,
    select_asof_book,
    select_asof_observation,
    select_policy,
)


def _snapshot(bucket: int, *, winner: str, displacement: float, ask: float = 0.70) -> MarketSnapshot:
    return MarketSnapshot(
        market_bucket=bucket,
        predict_market_id=bucket,
        binance_market_id=bucket,
        decision_seconds=15,
        sampled_at_ms=bucket * 1000,
        book_age_ms=0,
        observation_timestamp="2026-01-01T00:00:00+00:00",
        observation_lag_seconds=0,
        start_price=100,
        spot_price=100 + displacement / 100,
        displacement_bps=displacement,
        up_ask=ask,
        down_ask=ask,
        winner=winner,
    )


def test_asof_selectors_never_use_future_rows() -> None:
    books = [
        {"sampled_at_ms": 9_999, "receipt_age_ms": 10, "up_ask": 0.5},
        {"sampled_at_ms": 10_001, "receipt_age_ms": 0, "up_ask": 0.1},
    ]
    assert select_asof_book(books, 10_000, 3_000)["up_ask"] == 0.5
    observations = [
        {"seconds_left": 15.2, "start_price": 100, "spot_price": 101, "spot_age_ms": 10},
        {"seconds_left": 14.9, "start_price": 100, "spot_price": 99, "spot_age_ms": 10},
    ]
    assert select_asof_observation(observations, 15, 3)["spot_price"] == 101


def test_asof_book_rejects_repeated_stale_snapshot() -> None:
    rows = [
        {"sampled_at_ms": 9_999, "receipt_age_ms": 3_000, "up_ask": 0.5},
        {"sampled_at_ms": 9_998, "receipt_age_ms": None, "up_ask": 0.4},
    ]
    assert select_asof_book(rows, 10_000, 3_000) is None


def test_fee_is_included_in_roi() -> None:
    assert effective_taker_cost(0.70, 200) == 0.706
    result = evaluate_policy(
        [_snapshot(1, winner="UP", displacement=1, ask=0.70)],
        Policy(15, 0, 0.95),
    )
    assert result["wins"] == 1
    assert result["roi"] == result["pnl"]
    assert 0.41 < result["roi"] < 0.42


def test_chronological_split_is_market_ordered() -> None:
    split = chronological_split(list(range(10)))
    assert split["train"] == set(range(6))
    assert split["validation"] == {6, 7}
    assert split["holdout"] == {8, 9}


def test_policy_selection_does_not_read_holdout() -> None:
    good = Policy(15, 0, 0.95)
    too_strict = Policy(15, 0, 0.60)
    train = [_snapshot(i, winner="UP", displacement=1) for i in range(12)]
    validation = [_snapshot(i + 20, winner="UP", displacement=1) for i in range(12)]
    locked, _, _ = select_policy(
        {"train": train, "validation": validation, "holdout": []},
        [too_strict, good],
    )
    assert locked == good


def test_flip_side_negative_control_reverses_signal() -> None:
    rows = [_snapshot(i, winner="UP", displacement=1) for i in range(12)]
    normal = evaluate_policy(rows, Policy(15, 0, 0.95))
    flipped = evaluate_policy(rows, Policy(15, 0, 0.95), flip_side=True)
    assert normal["winRate"] == 1
    assert flipped["winRate"] == 0
