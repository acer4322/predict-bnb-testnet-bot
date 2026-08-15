from predict_bot.binance_feed_latency_probe import _collector_observation, _state_key


def test_state_key_prefers_explicit_book_versions() -> None:
    key = _state_key(
        {
            "marketId": 123,
            "upBookTimestampMs": 1000,
            "downBookTimestampMs": 1002,
            "upBid": 0.2,
            "upAsk": 0.21,
            "downBid": 0.79,
            "downAsk": 0.8,
        }
    )
    assert key == ("V", 123, 1000, 1002)


def test_state_key_falls_back_to_exact_top_of_book_signature() -> None:
    key = _state_key(
        {
            "marketId": 123,
            "upBid": 0.2,
            "upAsk": 0.21,
            "downBid": 0.79,
            "downAsk": 0.8,
        }
    )
    assert key == ("Q", 123, 0.2, 0.21, 0.79, 0.8)


def test_collector_observation_accepts_existing_realtime_snake_case_shape() -> None:
    observation = _collector_observation(
        {
            "market_id": 456,
            "up_bid": 0.12,
            "up_ask": 0.13,
            "down_bid": 0.87,
            "down_ask": 0.88,
            "up_book_timestamp_ms": 2000,
            "down_book_timestamp_ms": 2001,
            "book_age_ms": 44.0,
        },
        3000,
    )
    assert observation is not None
    assert observation["marketId"] == 456
    assert observation["receivedAtMs"] == 3000
    assert _state_key(observation) == ("V", 456, 2000, 2001)
