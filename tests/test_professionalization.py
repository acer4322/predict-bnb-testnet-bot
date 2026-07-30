from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from predict_bot.professionalization import (
    FROZEN_COMPOSITES,
    ProfessionalizationLedger,
    advantage_fingerprints,
    apply_two_loss_skip_one,
    calibration_report,
    connect_read_only,
    data_coverage_audit,
    execution_stress_records,
    infer_selected_side_probability,
    platform_risk_registry,
    selected_composite_trades,
    sha256_normalized_text,
    select_development_exit_candidate,
    strategy_local_sizing_research,
    wilson_interval,
)


def trade(
    observation_id: int,
    *,
    strategy: str = "R_MICROPRICE",
    observer_version: str = "V6+DD20",
    pnl: float = 1.0,
    split: str = "development",
) -> dict[str, object]:
    winner = "UP" if pnl > 0 else "DOWN"
    return {
        "strategy": strategy,
        "observer_version": observer_version,
        "split": split,
        "market_id": observation_id,
        "observation_id": observation_id,
        "timestamp": f"2026-07-24T00:00:{observation_id:02d}+00:00",
        "side": "UP",
        "winner": winner,
        "entry_price": 0.402,
        "raw_ask": 0.4,
        "visible_ask_size": 100.0,
        "stake": 2.0,
        "shares": 4.975,
        "fee": 0.04,
        "pnl": pnl,
        "signal": 0.08,
        "book_age_ms": 100.0,
        "book_skew_ms": 0.0,
    }


def test_frozen_composites_keep_observer_and_local_controls() -> None:
    assert [item.composite_id for item in FROZEN_COMPOSITES] == [
        "R_FUTURES_LEAD+V2",
        "R_CALIBRATED_VALUE+V6+DD20",
        "R_MICROPRICE+V6+DD20+2L1",
    ]
    assert [item.observer_version for item in FROZEN_COMPOSITES] == [
        "V2",
        "V6",
        "V6",
    ]


def test_two_loss_skip_one_only_changes_microprice() -> None:
    values = [
        trade(1, pnl=-2),
        trade(2, pnl=-2),
        trade(3, pnl=5),
        trade(4, pnl=-2),
        trade(5, strategy="R_CALIBRATED_VALUE", pnl=-2),
    ]
    kept, blocked = apply_two_loss_skip_one(values)
    assert [value["observation_id"] for value in kept] == [1, 2, 4, 5]
    assert [value["observation_id"] for value in blocked] == [3]


def test_selected_composite_trades_filters_versions_and_applies_cooldown() -> None:
    report = {
        "trades": [
            trade(1, pnl=-2),
            trade(2, pnl=-2),
            trade(3, pnl=5),
            trade(4, observer_version="V2", pnl=5),
            trade(
                5,
                strategy="R_FUTURES_LEAD",
                observer_version="V2",
                pnl=5,
            ),
        ]
    }
    assert [value["observation_id"] for value in selected_composite_trades(report)] == [
        1,
        2,
        5,
    ]


def test_calibrated_value_probability_is_reconstructed() -> None:
    value = trade(
        1,
        strategy="R_CALIBRATED_VALUE",
        observer_version="V6+DD20",
    )
    assert infer_selected_side_probability(value) == pytest.approx(
        0.08 + 0.402 + 0.04 / 4.975
    )
    assert infer_selected_side_probability(trade(2)) is None


def test_calibration_report_and_unavailable_state() -> None:
    unavailable = calibration_report([])
    assert unavailable["status"] == "UNAVAILABLE"
    report = calibration_report(
        [
            {"model_probability": 0.8, "outcome": 1},
            {"model_probability": 0.2, "outcome": 0},
        ]
    )
    assert report["status"] == "READY"
    assert report["samples"] == 2
    assert report["brierScore"] == pytest.approx(0.04)
    assert report["logLoss"] == pytest.approx(-__import__("math").log(0.8))


def test_wilson_interval_contains_observed_rate() -> None:
    lower, upper = wilson_interval(7, 10)
    assert lower is not None and upper is not None
    assert lower < 0.7 < upper
    assert wilson_interval(0, 0) == (None, None)


def test_read_only_connector_rejects_write(tmp_path: Path) -> None:
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE values_table(value INTEGER)")
    with connect_read_only(path) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO values_table VALUES(1)")


def test_source_hash_is_independent_of_line_endings(tmp_path: Path) -> None:
    lf = tmp_path / "lf.py"
    crlf = tmp_path / "crlf.py"
    lf.write_bytes(b"value = 1\nprint(value)\n")
    crlf.write_bytes(b"value = 1\r\nprint(value)\r\n")
    assert sha256_normalized_text(lf) == sha256_normalized_text(crlf)


def test_coverage_audit_uses_fixed_inclusive_end(tmp_path: Path) -> None:
    path = tmp_path / "coverage.db"
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE observations(
                market_id INTEGER, timestamp TEXT, book_age_ms REAL,
                book_skew_ms REAL, up_ask REAL, up_bid REAL,
                down_ask REAL, down_bid REAL
            );
            CREATE TABLE market_settlements(
                official_winner TEXT, official_settled_at TEXT
            );
            INSERT INTO observations VALUES
                (1, '2026-07-01T00:00:00+00:00', 10, 0, .5, .49, .5, .49),
                (2, '2026-08-01T00:00:00+00:00', 10, 0, .5, .49, .5, .49);
            INSERT INTO market_settlements VALUES
                ('UP', '2026-07-02T00:00:00+00:00'),
                ('DOWN', '2026-08-02T00:00:00+00:00');
            """
        )
    report = data_coverage_audit(
        path, end_at="2026-07-31T23:59:59+00:00"
    )
    assert report["observationRows"] == 1
    assert report["distinctMarkets"] == 1
    assert report["officialMarkets"] == 1
    assert report["lastObservation"] == "2026-07-01T00:00:00+00:00"


def test_research_ledger_is_deterministic_and_separate(tmp_path: Path) -> None:
    path = tmp_path / "research" / "professionalization.db"
    with ProfessionalizationLedger(path) as ledger:
        first = ledger.register_experiment(
            dataset_sha256="abc", config={"window": "fixed"}, prior_holdout_reused=True
        )
        second = ledger.register_experiment(
            dataset_sha256="abc", config={"window": "fixed"}, prior_holdout_reused=True
        )
        inserted = ledger.insert_platform_risks(first, platform_risk_registry())
        assert first == second
        assert inserted == 5
        assert ledger.counts(first)["platform_risks"] == 5
        row = ledger.db.execute(
            "SELECT promotion_status FROM experiments WHERE experiment_id=?", (first,)
        ).fetchone()
        assert row[0] == "RESEARCH_ONLY_NEEDS_NEW_FORWARD"
    assert path.exists()


def test_execution_stress_preserves_recorded_share_quantity() -> None:
    value = trade(1)
    value["visible_ask_size"] = 4.975
    records = execution_stress_records([value])
    baseline = next(row for row in records if row["scenario"] == "recorded_50bps")
    assert baseline["fill_ratio"] == pytest.approx(1.0)


def test_fingerprints_and_exit_candidate_fail_closed_on_small_samples() -> None:
    fingerprints = advantage_fingerprints([trade(index) for index in range(1, 6)])
    assert (
        fingerprints["R_MICROPRICE+V6+DD20+2L1"]["status"]
        == "INSUFFICIENT_DEVELOPMENT_TRADES"
    )
    candidate = select_development_exit_candidate({}, "R_FUTURES_LEAD+V2")
    assert candidate["status"] == "INSUFFICIENT_DEVELOPMENT_EXIT_EVIDENCE"


def test_exit_candidate_rejects_least_bad_but_still_negative_horizon() -> None:
    summary = {
        "R_FUTURES_LEAD+V2": {
            "development": {
                "3": {
                    "fullDepthExecutable": 35,
                    "coverageRate": 0.7,
                    "pnlDeltaVsCoveredSettlement": -1.0,
                },
                "10": {
                    "fullDepthExecutable": 40,
                    "coverageRate": 0.8,
                    "pnlDeltaVsCoveredSettlement": -0.1,
                },
            }
        }
    }
    candidate = select_development_exit_candidate(summary, "R_FUTURES_LEAD+V2")
    assert candidate["status"] == "NO_BENEFICIAL_DEVELOPMENT_EXIT_CANDIDATE"
    assert candidate["selectedHorizonSeconds"] is None
    assert candidate["bestObservedHorizonSeconds"] == 10


def test_strategy_local_sizing_is_research_only_and_causal() -> None:
    values = [trade(1, pnl=-2), trade(2, pnl=2), trade(3, pnl=2)]
    report = strategy_local_sizing_research(values)
    microprice = report["R_MICROPRICE+V6+DD20+2L1"]
    rolling = microprice["scenarios"]["rolling1To4Start2"]["all"]
    assert microprice["status"] == "RESEARCH_ONLY_NO_SELECTION_NO_LIVE_FORWARDING"
    assert rolling["totalStake"] == pytest.approx(5.0)
    assert rolling["realizedPnl"] == pytest.approx(1.0)


def test_plan_document_freezes_scope() -> None:
    plan = Path("docs/PROFESSIONALIZATION_PLAN_V1.md").read_text(encoding="utf-8")
    assert "No Observer ranking" in plan
    assert "account-wide portfolio risk engine" in plan
    assert "separate approvals" in plan
    json.dumps([item.composite_id for item in FROZEN_COMPOSITES])
