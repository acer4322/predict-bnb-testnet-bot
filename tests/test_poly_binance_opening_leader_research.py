from predict_bot.poly_binance_opening_leader_research import _simulate_confidence_policy


def _market(market_id: int, later: str, opening: str, correct: bool = True):
    return {
        "marketId": market_id,
        "marketStartMs": market_id * 300_000,
        "openingLeader": opening,
        "laterLeader": later,
        "openingEvaluable": True,
        "laterEvaluable": True,
        "predictionEvaluable": True,
        "openingPredictsLater": correct,
    }


def test_strict_8_of_10_poly_confidence_skips_eleventh_opening_window():
    rows = [_market(i, "POLY", "POLY", True) for i in range(1, 11)]
    rows.append(_market(11, "POLY", "BINANCE", False))
    report = _simulate_confidence_policy(rows)
    decision = report["decisions"][-1]
    assert decision["prior10LeaderConsensus"] == "POLY"
    assert decision["prior10LeaderConsensusCount"] == 10
    assert decision["prior10OpeningCorrect"] == 10
    assert decision["strictConfidenceMode"] is True
    assert decision["strictPolicyMode"] == "CONFIDENCE_SKIP_OPENING"
    assert decision["strictPolicyPrediction"] == "POLY"
    assert decision["strictPolicyCorrect"] is True


def test_no_8_of_10_consensus_keeps_opening_window_required():
    rows = []
    for i in range(1, 11):
        leader = "POLY" if i <= 6 else "BINANCE"
        rows.append(_market(i, leader, leader, True))
    rows.append(_market(11, "POLY", "POLY", True))
    report = _simulate_confidence_policy(rows)
    decision = report["decisions"][-1]
    assert decision["consensusConfidenceMode"] is False
    assert decision["strictConfidenceMode"] is False
    assert decision["strictPolicyMode"] == "OPENING_WINDOW_REQUIRED"
    assert decision["strictPolicyPrediction"] == "POLY"
