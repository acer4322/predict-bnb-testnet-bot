from predict_bot import poly_binance_opening_leader_research_v2 as research


def _row(market_id: int, later: str, *, coverage: bool = True, opening: str = "POLY", correct: bool = True):
    return {
        "marketId": market_id,
        "marketStartMs": market_id * 300_000,
        "coverageQualified": coverage,
        "laterEvaluable": coverage and later in {"POLY", "BINANCE"},
        "laterLeader": later,
        "openingEvaluable": coverage and opening in {"POLY", "BINANCE"},
        "openingLeader": opening,
        "predictionEvaluable": coverage and later in {"POLY", "BINANCE"} and opening in {"POLY", "BINANCE"},
        "openingPredictsLater": correct,
    }


def test_tie_and_insufficient_markets_consume_prior_10_slots():
    rows = [
        _row(1, "POLY"),
        _row(2, "POLY"),
        _row(3, "POLY"),
        _row(4, "POLY"),
        _row(5, "POLY"),
        _row(6, "POLY"),
        _row(7, "POLY"),
        _row(8, "TIE_MIXED", opening="TIE_MIXED"),
        _row(9, "INSUFFICIENT", opening="INSUFFICIENT"),
        _row(10, "BINANCE", opening="BINANCE"),
        _row(11, "POLY"),
    ]
    report = research._simulate_confidence_policy(rows)
    decision = next(item for item in report["decisions"] if item["marketId"] == 11)
    assert decision["prior10PolyCount"] == 7
    assert decision["prior10OtherOrUnusableCount"] == 2
    assert decision["consensusConfidenceMode"] is False


def test_literal_eight_of_previous_ten_arms_confidence():
    rows = [_row(i, "POLY") for i in range(1, 9)]
    rows += [_row(9, "BINANCE", opening="BINANCE", correct=True), _row(10, "TIE_MIXED", opening="TIE_MIXED")]
    rows.append(_row(11, "POLY"))
    report = research._simulate_confidence_policy(rows)
    decision = next(item for item in report["decisions"] if item["marketId"] == 11)
    assert decision["prior10PolyCount"] == 8
    assert decision["prior10LeaderConsensus"] == "POLY"
    assert decision["consensusConfidenceMode"] is True
    assert decision["consensusPolicyMode"] == "CONFIDENCE_SKIP_OPENING"
