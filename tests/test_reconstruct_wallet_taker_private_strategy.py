from tools.reconstruct_wallet_taker_private_strategy import _asof_snapshot, _match_batches, _split_markets


def test_asof_snapshot_is_strictly_before_guard() -> None:
    rows = [
        {"sampled_at_ms": 1_500, "value": "old"},
        {"sampled_at_ms": 1_800, "value": "too-new"},
    ]
    index = {1: ([1_500, 1_800], rows)}
    assert _asof_snapshot(index, 1, 2_000, guard_ms=250)["value"] == "old"


def test_market_split_is_chronological() -> None:
    result = _split_markets({market_id: market_id * 1_000 for market_id in range(1, 11)})
    assert [result[index] for index in range(1, 11)] == [
        "TRAIN", "TRAIN", "TRAIN", "TRAIN", "TRAIN", "TRAIN",
        "VALIDATION", "VALIDATION", "TEST", "TEST",
    ]


def test_batch_matching_is_one_to_one() -> None:
    actual = [
        {"market_id": 1, "decision_ms": 10_000},
        {"market_id": 1, "decision_ms": 11_000},
    ]
    predicted = [
        {"market_id": 1, "decision_ms": 10_100},
        {"market_id": 1, "decision_ms": 10_200},
    ]
    matches = _match_batches(actual, predicted, 1_000)
    assert len(matches) == 2
    assert len({actual_index for _, actual_index in matches}) == 2
