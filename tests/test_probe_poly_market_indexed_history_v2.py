from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import probe_poly_market_indexed_history_v2 as mod


def _make_dbs(tmp_path: Path) -> tuple[Path, Path]:
    micro = tmp_path / "micro.db"
    cross = tmp_path / "cross.db"

    conn = sqlite3.connect(micro)
    conn.executescript(
        """
        CREATE TABLE microstructure_snapshots(
            id INTEGER PRIMARY KEY,
            timestamp_ns INTEGER NOT NULL,
            market_id INTEGER
        );
        CREATE INDEX micro_snapshots_time_idx
            ON microstructure_snapshots(timestamp_ns);
        """
    )
    start_ns = mod._epoch_ns(mod._parse_iso("2026-08-16T03:40:00+08:00"))
    for market_id, offset_s in [
        (101, 1), (101, 50), (101, 100),
        (102, 301), (102, 350), (102, 400),
    ]:
        conn.execute(
            "INSERT INTO microstructure_snapshots(timestamp_ns, market_id) VALUES (?, ?)",
            (start_ns + offset_s * 1_000_000_000, market_id),
        )
    conn.commit()
    conn.close()

    conn = sqlite3.connect(cross)
    conn.executescript(
        """
        CREATE TABLE poly_binance_lead_samples(
            id INTEGER PRIMARY KEY,
            binance_market_id INTEGER NOT NULL,
            poly_market_slug TEXT NOT NULL,
            observed_at_ms INTEGER NOT NULL,
            poly_received_at_ms INTEGER,
            binance_observed_at_ms INTEGER,
            poly_up_mid REAL NOT NULL,
            binance_up_mid REAL NOT NULL,
            seconds_left_skew REAL,
            binance_book_age_ms REAL,
            binance_book_skew_ms REAL
        );
        CREATE INDEX idx_poly_binance_lead_market_time
            ON poly_binance_lead_samples(binance_market_id, observed_at_ms);
        """
    )
    start_ms = mod._epoch_ms(mod._parse_iso("2026-08-16T03:40:00+08:00"))
    for market_id, offsets in [(101, [2, 30, 60, 99]), (102, [305, 350, 398])]:
        for offset_s in offsets:
            observed = start_ms + offset_s * 1000
            conn.execute(
                """INSERT INTO poly_binance_lead_samples(
                       binance_market_id, poly_market_slug, observed_at_ms,
                       poly_received_at_ms, binance_observed_at_ms,
                       poly_up_mid, binance_up_mid
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    market_id,
                    f"slug-{market_id}",
                    observed,
                    observed - 50,
                    observed,
                    0.55,
                    0.54,
                ),
            )
    conn.commit()
    conn.close()
    return micro, cross


def test_probe_uses_composite_market_time_index_and_preserves_db_bytes(tmp_path: Path) -> None:
    micro, cross = _make_dbs(tmp_path)
    before = (micro.read_bytes(), cross.read_bytes())

    report = mod.probe(
        micro,
        cross,
        start="2026-08-16T03:40:00+08:00",
        end="2026-08-16T03:50:00+08:00",
        count_cap_per_market=100,
    )

    assert report["summary"]["marketsInMicrostructure"] == 2
    assert report["summary"]["marketsWithPolyLeadSamples"] == 2
    assert report["summary"]["marketCoverageRate"] == 1.0
    assert report["markets"][0]["polyLeadFirst"]["poly_up_mid"] == 0.55
    assert report["sources"]["polyLeadIndex"]["columns"][:2] == [
        "binance_market_id",
        "observed_at_ms",
    ]

    after = (micro.read_bytes(), cross.read_bytes())
    assert before == after


def test_probe_refuses_poly_lead_table_without_required_composite_index(tmp_path: Path) -> None:
    micro, cross = _make_dbs(tmp_path)
    conn = sqlite3.connect(cross)
    conn.execute("DROP INDEX idx_poly_binance_lead_market_time")
    conn.commit()
    conn.close()

    try:
        mod.probe(
            micro,
            cross,
            start="2026-08-16T03:40:00+08:00",
            end="2026-08-16T03:50:00+08:00",
        )
    except RuntimeError as exc:
        assert "(binance_market_id, observed_at_ms)" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")
