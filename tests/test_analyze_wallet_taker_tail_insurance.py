import json
import sqlite3
from pathlib import Path

from tools.analyze_wallet_taker_tail_insurance import build_report


def _raw(end_at: str) -> str:
    return json.dumps({"market": {"boostEndsAt": end_at}})


def test_tail_insurance_episode_uses_strictly_prior_capital_state(tmp_path: Path) -> None:
    db_path = tmp_path / "wallet.db"
    db = sqlite3.connect(db_path)
    db.executescript(
        """
        CREATE TABLE wallet_shadow_target_events (
            leg_id TEXT PRIMARY KEY, wallet TEXT, market_id INTEGER, role TEXT, side TEXT,
            quote_type TEXT, order_hash TEXT, event_ms INTEGER, price REAL, shares REAL, raw_json TEXT
        );
        CREATE TABLE wallet_shadow_target_market_results (
            market_id INTEGER PRIMARY KEY, winner TEXT
        );
        """
    )
    end_at = "2026-08-14T00:05:00.000Z"
    end_ms = 1_786_665_900_000
    rows = [
        ("m1", "w", 1, "MAKER", "UP", "BID", "maker-up", end_ms - 120_000, 0.80, 100.0, _raw(end_at)),
        ("m2", "w", 1, "MAKER", "DOWN", "BID", "maker-down", end_ms - 110_000, 0.20, 20.0, _raw(end_at)),
        ("t1", "w", 1, "TAKER", "DOWN", "BID", "hedge-1", end_ms - 30_000, 0.05, 20.0, _raw(end_at)),
        ("t2", "w", 1, "TAKER", "DOWN", "BID", "hedge-2", end_ms - 20_000, 0.05, 20.0, _raw(end_at)),
        ("t3", "w", 1, "TAKER", "UP", "BID", "same-side-cheap", end_ms - 10_000, 0.05, 20.0, _raw(end_at)),
    ]
    db.executemany("INSERT INTO wallet_shadow_target_events VALUES (?,?,?,?,?,?,?,?,?,?,?)", rows)
    db.execute("INSERT INTO wallet_shadow_target_market_results VALUES (1,'UP')")
    db.commit()
    db.close()

    report = build_report(db_path)
    assert report["summary"]["candidateParents"] == 2
    assert report["summary"]["episodes"] == 1
    episode = report["episodes"][0]
    assert episode["observedInsuranceShares"] == 40.0
    assert episode["observedInsuranceCostUsdt"] == 2.0
    assert episode["mainSharesBefore"] == 100.0
    assert episode["mainCapitalBeforeUsdt"] == 80.0
    assert episode["totalCapitalBeforeUsdt"] == 84.0
    assert episode["insuranceSharesToMainShares"] == 0.4
    assert episode["insuranceWon"] is False
