from predict_bot.poly_route_latency_probe import compare_summaries


def test_compare_poly_route_latency_metrics():
    baseline = {
        "label": "DIRECT",
        "wsSourceAgeMs": {"median": 700.0, "p95": 900.0},
        "restSourceAgeMs": {"median": 710.0, "p95": 950.0},
        "restRttMs": {"median": 275.0, "p95": 315.0},
        "restMinusWsMs": {"median": 3.0, "p95": 40.0},
    }
    current = {
        "label": "WARP",
        "wsSourceAgeMs": {"median": 680.0, "p95": 850.0},
        "restSourceAgeMs": {"median": 690.0, "p95": 900.0},
        "restRttMs": {"median": 240.0, "p95": 280.0},
        "restMinusWsMs": {"median": 8.0, "p95": 45.0},
    }
    comparison = compare_summaries(current, baseline)
    assert comparison["median"]["wsSourceAge"]["deltaMs"] == -20.0
    assert comparison["median"]["restRtt"]["deltaMs"] == -35.0
    assert comparison["median"]["restMinusWs"]["deltaMs"] == 5.0
    assert comparison["lowerIsBetter"]["wsSourceAge"] is True
    assert comparison["higherMeansWsObservedSameHashEarlier"]["restMinusWs"] is True
