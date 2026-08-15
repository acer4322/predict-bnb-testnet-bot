from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from predict_bot.target_taker_behavior_v1 import RAW_PUBLIC_FEATURES
from predict_bot.target_taker_reentry_v1_dataset import (
    REENTRY_MODEL_FEATURES,
    build_target_taker_reentry_v1_dataset,
)


def _official_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE target_parent_orders (
            parent_id TEXT PRIMARY KEY, wallet TEXT, asset TEXT, market_id INTEGER,
            role TEXT, side TEXT, quote_type TEXT, order_hash TEXT,
            first_event_ms INTEGER, last_event_ms INTEGER,
            average_price REAL, shares REAL, fill_legs INTEGER, updated_at_ms INTEGER
        )"""
    )
    rows = [
        ("official-a", "w", "BTC", 101, "TAKER", "UP", "BID", "0xA", 10_000, 10_000, 0.40, 5.0, 1, 10_000),
        ("official-b", "w", "BTC", 101, "TAKER", "UP", "BID", "0xB", 12_500, 12_500, 0.43, 5.0, 1, 12_500),
        ("official-c", "w", "BTC", 101, "TAKER", "DOWN", "BID", "0xC", 16_000, 16_000, 0.45, 5.0, 1, 16_000),
        ("official-exit", "w", "BTC", 101, "TAKER", "UP", "ASK", "0xEXIT", 18_000, 18_000, 0.60, 2.0, 1, 18_000),
    ]
    db.executemany(
        "INSERT INTO target_parent_orders VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    db.commit()
    db.close()


def _legacy_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.execute(
        """CREATE TABLE wallet_target_taker_mirror_parents (
            cohort TEXT, parent_id TEXT, market_id INTEGER, order_hash TEXT, side TEXT,
            target_event_ms INTEGER, target_last_event_ms INTEGER,
            target_average_price REAL, target_latest_shares REAL, target_fill_legs INTEGER
        )"""
    )
    # Same order hash as official-a: the clean Official parent must override it.
    db.execute(
        "INSERT INTO wallet_target_taker_mirror_parents VALUES (?,?,?,?,?,?,?,?,?,?)",
        ("TARGET_TAKER_MIRROR_AUDIT_V1", "legacy-a", 101, "0xA", "UP", 9_000, 9_000, 0.39, 5.0, 1),
    )
    db.commit()
    db.close()


def _signal_db(path: Path) -> None:
    db = sqlite3.connect(path)
    public_columns = ",".join(f"{name} REAL" for name in RAW_PUBLIC_FEATURES)
    db.execute(
        f"""CREATE TABLE wallet_taker_signal_snapshots (
            timestamp_ns INTEGER, market_id INTEGER, sampled_at_ms INTEGER,
            {public_columns}
        )"""
    )
    times = [9_000, 10_500, 11_250, 12_000, 12_500, 12_750, 15_500, 16_000, 17_250]
    base = {name: 0.1 for name in RAW_PUBLIC_FEATURES}
    base.update(
        {
            "seconds_left": 100.0,
            "strike_price": 100.0,
            "predict_up_bid": 0.40,
            "predict_up_ask": 0.42,
            "predict_up_mid": 0.41,
            "predict_down_bid": 0.58,
            "predict_down_ask": 0.60,
            "predict_down_mid": 0.59,
            "spot_price": 100.1,
            "spot_microprice": 100.1,
            "futures_price": 100.2,
            "futures_microprice": 100.2,
            "chainlink_price": 100.05,
            "spot_minus_strike_bps": 10.0,
            "chainlink_minus_strike_bps": 5.0,
            "spot_minus_chainlink_bps": 5.0,
            "direction_score": 0.2,
        }
    )
    columns = ["timestamp_ns", "market_id", "sampled_at_ms"] + list(RAW_PUBLIC_FEATURES)
    placeholders = ",".join("?" for _ in columns)
    for index, sampled in enumerate(times):
        values = [sampled * 1_000_000, 101, sampled]
        row = dict(base)
        row["direction_score"] = 0.20 + index * 0.01
        row["spot_minus_strike_bps"] = 10.0 + index
        values.extend(row[name] for name in RAW_PUBLIC_FEATURES)
        db.execute(
            f"INSERT INTO wallet_taker_signal_snapshots ({','.join(columns)}) VALUES ({placeholders})",
            values,
        )
    db.commit()
    db.close()


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def test_reentry_v1_builds_strict_causal_multi_horizon_risk_set(tmp_path: Path) -> None:
    official = tmp_path / "official.db"
    legacy = tmp_path / "legacy.db"
    signal = tmp_path / "signal.db"
    output = tmp_path / "reentry.csv"
    meta = tmp_path / "reentry.meta.json"
    _official_db(official)
    _legacy_db(legacy)
    _signal_db(signal)

    report = build_target_taker_reentry_v1_dataset(
        official_db_path=official,
        legacy_db_path=legacy,
        signal_db_path=signal,
        output_path=output,
        meta_output_path=meta,
        min_snapshot_step_ms=0,
    )
    rows = _rows(output)
    by_time = {int(row["decision_sampled_at_ms"]): row for row in rows}

    # Whole-second Target buckets are never used as decision observations.
    assert 10_500 not in by_time
    assert 16_000 not in by_time
    # Exact millisecond action timestamps are also excluded at the action instant.
    assert 12_500 not in by_time

    first_risk = by_time[11_250]
    assert first_risk["actor_prev_parent_id"] == "official-a"
    assert first_risk["actor_prev_parent_source"] == "TARGET_WALLET_OFFICIAL"
    assert int(first_risk["actor_entry_count_so_far"]) == 1
    assert int(first_risk["label_any_reentry_within_1000ms"]) == 0
    assert int(first_risk["label_any_reentry_within_2000ms"]) == 1
    assert int(first_risk["label_same_side_reentry_within_2000ms"]) == 1
    assert int(first_risk["label_opposite_side_entry_within_2000ms"]) == 0

    at_12000 = by_time[12_000]
    assert int(at_12000["label_any_reentry_within_500ms"]) == 1
    assert int(at_12000["label_same_side_reentry_within_500ms"]) == 1

    after_second = by_time[12_750]
    assert int(after_second["actor_entry_count_so_far"]) == 2
    assert after_second["actor_prev_parent_id"] == "official-b"
    assert int(after_second["label_any_reentry_within_2000ms"]) == 0
    assert int(after_second["label_any_reentry_within_5000ms"]) == 1
    assert int(after_second["label_opposite_side_entry_within_5000ms"]) == 1

    # The next event is a whole-second bucket [16000,16999]. At 15500 the
    # 500ms horizon is ambiguous, while 2000ms is definitely positive.
    ambiguous = by_time[15_500]
    assert ambiguous["label_any_reentry_within_500ms"] == ""
    assert ambiguous["label_opposite_side_entry_within_500ms"] == ""
    assert int(ambiguous["label_any_reentry_within_2000ms"]) == 1
    assert int(ambiguous["label_opposite_side_entry_within_2000ms"]) == 1
    assert int(ambiguous["label_same_side_reentry_within_2000ms"]) == 0

    after_last = by_time[17_250]
    assert int(after_last["actor_entry_count_so_far"]) == 3
    assert int(after_last["label_any_reentry_within_5000ms"]) == 0

    assert report["sourceParents"]["official"]["excludedAskParents"] == 1
    assert report["sourceParents"]["officialOverridesLegacyByOrderHash"] == 1
    assert report["sequence"]["reentryPairs"] == 2
    assert report["sequence"]["sameSidePairs"] == 1
    assert report["sequence"]["oppositeSidePairs"] == 1
    assert report["riskSet"]["rowsFrozen16Complete"] == len(rows)
    assert meta.exists()


def test_reentry_model_feature_contract_has_no_future_target_or_label_columns() -> None:
    assert REENTRY_MODEL_FEATURES
    assert all(not name.startswith("label_") for name in REENTRY_MODEL_FEATURES)
    assert all(not name.startswith("audit_") for name in REENTRY_MODEL_FEATURES)
    assert all(not name.startswith("target_") for name in REENTRY_MODEL_FEATURES)
    assert "actor_entry_count_so_far" in REENTRY_MODEL_FEATURES
    assert "actor_ms_since_prev_entry_min" in REENTRY_MODEL_FEATURES
    assert "delta_direction_score_since_prev_pre_entry" in REENTRY_MODEL_FEATURES
