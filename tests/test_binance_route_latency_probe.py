from predict_bot.binance_route_latency_probe import (
    _market_reference,
    _stats,
    compare_summaries,
)


def test_route_stats_include_tail_percentiles():
    stats = _stats([10.0, 20.0, 30.0, 40.0, 50.0])
    assert stats["count"] == 5
    assert stats["median"] == 30.0
    assert stats["p95"] is not None
    assert stats["p99"] is not None


def test_market_reference_accepts_snake_case_payload():
    payload = {
        "marketReference": {
            "market_id": 123,
            "up_token_id": "up",
            "down_token_id": "down",
        }
    }
    assert _market_reference(payload) == {
        "marketId": 123,
        "upTokenId": "up",
        "downTokenId": "down",
    }


def test_compare_summaries_reports_lower_latency_as_negative_delta():
    baseline = {
        "label": "DIRECT",
        "network": {"coldTcpTlsMs": {"median": 100.0, "p95": 150.0}},
        "http": {
            "publicServerTimeRttMs": {"median": 90.0, "p95": 130.0},
            "predictionOrderbookRttMs": {"median": 110.0, "p95": 160.0},
        },
    }
    current = {
        "label": "TOKYO",
        "network": {"coldTcpTlsMs": {"median": 50.0, "p95": 80.0}},
        "http": {
            "publicServerTimeRttMs": {"median": 45.0, "p95": 70.0},
            "predictionOrderbookRttMs": {"median": 60.0, "p95": 90.0},
        },
    }
    comparison = compare_summaries(current, baseline)
    assert comparison["median"]["predictionOrderbook"]["deltaMs"] == -50.0
    assert comparison["median"]["predictionOrderbook"]["deltaPct"] < 0
