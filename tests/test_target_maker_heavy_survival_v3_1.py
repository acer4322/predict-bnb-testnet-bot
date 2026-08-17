from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import analyze_target_maker_heavy_survival_v3_1 as mod
import backfill_target_maker_survival_settlements_v3 as backfill


def test_coverage_guard_requires_each_regime_independently():
    cohort = {
        1: "ORDINARY_PRE_SPECIAL",
        2: "ORDINARY_PRE_SPECIAL",
        3: "SPECIAL",
        4: "SPECIAL",
    }
    complete = mod._coverage_guard(
        cohort,
        {1: "UP", 2: "DOWN", 3: "UP", 4: "DOWN"},
        min_ordinary=1.0,
        min_special=1.0,
    )
    assert complete["passed"] is True

    missing_ordinary = mod._coverage_guard(
        cohort,
        {1: "UP", 3: "UP", 4: "DOWN"},
        min_ordinary=0.75,
        min_special=1.0,
    )
    assert missing_ordinary["ORDINARY_PRE_SPECIAL"]["coverage"] == 0.5
    assert missing_ordinary["ORDINARY_PRE_SPECIAL"]["passed"] is False
    assert missing_ordinary["SPECIAL"]["passed"] is True
    assert missing_ordinary["passed"] is False


def test_backfill_coverage_payload_requires_both_regimes():
    cohort = {
        10: "ORDINARY_PRE_SPECIAL",
        11: "ORDINARY_PRE_SPECIAL",
        20: "SPECIAL",
    }
    payload = backfill._coverage_payload(
        cohort,
        {10, 11},
        min_ordinary=1.0,
        min_special=1.0,
    )
    assert payload["ORDINARY_PRE_SPECIAL"]["passed"] is True
    assert payload["SPECIAL"]["passed"] is False
    assert payload["passed"] is False


def test_winner_concordance_survives_heavy_side_flip_when_outcome_is_consistent():
    risk_rows = [
        {
            "market_id": 101,
            "maker_heavy_side": "UP",
            "heavy_side_won": 1,
        },
        {
            "market_id": 101,
            "maker_heavy_side": "DOWN",
            "heavy_side_won": 0,
        },
    ]
    audit = mod._winner_concordance(risk_rows, {101: "UP"})
    assert audit["overlapMarkets"] == 1
    assert audit["matchedWinnerMarkets"] == 1
    assert audit["mismatchedWinnerMarkets"] == 0
    assert audit["conflictingRiskWinnerMarkets"] == 0
    assert audit["passed"] is True


def test_winner_concordance_rejects_mismatch():
    risk_rows = [
        {
            "market_id": 202,
            "maker_heavy_side": "UP",
            "heavy_side_won": 1,
        }
    ]
    audit = mod._winner_concordance(risk_rows, {202: "DOWN"})
    assert audit["mismatchedWinnerMarkets"] == 1
    assert audit["mismatchMarketIdsFirst50"] == [202]
    assert audit["passed"] is False


def test_market_blocked_probability_metrics_weight_markets_equally():
    rows = [
        {"market_id": 1, "label": 1, "p": 1.0},
        {"market_id": 1, "label": 1, "p": 1.0},
        {"market_id": 1, "label": 1, "p": 1.0},
        {"market_id": 2, "label": 0, "p": 1.0},
    ]
    metrics = mod._blocked_metrics(rows, label_key="label", probability_key="p")
    assert metrics["markets"] == 2
    assert abs(float(metrics["brier"]) - 0.5) < 1e-5


def test_repair_audit_uses_exact_timestamp_not_same_second_bucket():
    risk_rows = [
        {
            "market_id": 7,
            "sampled_ms": 1_001,
            "regime": "ORDINARY_PRE_SPECIAL",
            "phase": "MID",
            "lifecycle_state": "POST_FIRST_TAKER",
            "maker_heavy_side": "UP",
            "maker_abs_delta": 10.0,
            "predict_heavy_probability": 0.60,
            "heavy_side_won": 1,
            "repair_taker_5s": 0,
            "repair_shares_5s": 0.0,
        },
        {
            "market_id": 7,
            "sampled_ms": 1_999,
            "regime": "ORDINARY_PRE_SPECIAL",
            "phase": "MID",
            "lifecycle_state": "POST_FIRST_TAKER",
            "maker_heavy_side": "UP",
            "maker_abs_delta": 11.0,
            "predict_heavy_probability": 0.61,
            "heavy_side_won": 1,
            "repair_taker_5s": 1,
            "repair_shares_5s": 5.0,
        },
    ]
    scores = {
        "TEST": [
            {
                "market_id": 7,
                "sampled_ms": 1_001,
                "model_up_probability": 0.70,
                "calibrated_predict_up_probability": 0.65,
            }
        ]
    }
    audit = mod._repair_audit_exact_v31(risk_rows, scores)["TEST"]["ORDINARY_PRE_SPECIAL"]
    assert audit["riskRowsInScoredMarkets"] == 2
    assert audit["exactJoinedRows"] == 1
    assert audit["missingExactRows"] == 1
    assert audit["exactJoinCount"] == 1
    assert audit["futureJoinCount"] == 0
    assert audit["pastJoinCount"] == 0
