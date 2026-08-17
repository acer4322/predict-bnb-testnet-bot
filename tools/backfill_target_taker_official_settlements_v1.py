from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any

import httpx

import analyze_target_taker_ordinary_paper_pnl_v1 as pnl

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OOF = ROOT / "data" / "research" / "target_taker_trigger_conditioned_action_v1_oof.csv"
DEFAULT_SIM_DB = ROOT / "data" / "simulation.db"
DEFAULT_OUTPUT_DB = ROOT / "data" / "research" / "target_taker_official_settlements_v1.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_official_settlements_v1_report.json"
PREDICT_API_BASE = os.environ.get("PREDICT_FUN_API_BASE", "https://api.predict.fun").rstrip("/")
VERSION = "TARGET_TAKER_OFFICIAL_SETTLEMENT_BACKFILL_V1"


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return None
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _normalize_detail(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    return data if isinstance(data, dict) else payload


def _parse_official_detail(payload: Any) -> dict[str, Any] | None:
    detail = _normalize_detail(payload)
    variant = detail.get("variantData")
    if not isinstance(variant, dict):
        return None
    try:
        start = float(variant.get("startPrice"))
        end = float(variant.get("endPrice"))
    except (TypeError, ValueError):
        return None
    if not start > 0 or not end > 0:
        return None
    return {
        "start_price": start,
        "official_end_price": end,
        "official_winner": "UP" if end > start else "DOWN",
    }


def _create_output(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(resolved, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    db.execute(
        """CREATE TABLE IF NOT EXISTS market_settlements(
            market_id INTEGER PRIMARY KEY,
            topic_id INTEGER,
            start_price REAL,
            official_winner TEXT,
            official_end_price REAL,
            status TEXT NOT NULL,
            source TEXT NOT NULL,
            fetched_at_ms INTEGER NOT NULL
        )"""
    )
    db.commit()
    return db


def _simulation_lookup(db: sqlite3.Connection | None, market_id: int) -> dict[str, Any] | None:
    if db is None:
        return None
    cols = {str(row[1]) for row in db.execute("PRAGMA table_info(market_settlements)")}
    if not {"market_id", "status", "official_winner"}.issubset(cols):
        return None
    select_cols = ["market_id", "status", "official_winner"]
    for name in ("topic_id", "start_price", "official_end_price"):
        if name in cols:
            select_cols.append(name)
    row = db.execute(
        f"SELECT {','.join(select_cols)} FROM market_settlements WHERE market_id=? LIMIT 1",
        (int(market_id),),
    ).fetchone()
    match_mode = "market_id"
    if row is None and "topic_id" in cols:
        matches = db.execute(
            f"SELECT {','.join(select_cols)} FROM market_settlements WHERE topic_id=? LIMIT 2",
            (int(market_id),),
        ).fetchall()
        if len(matches) == 1:
            row = matches[0]
            match_mode = "topic_id"
    if row is None or str(row["status"] or "").upper() != "OFFICIAL":
        return None
    winner = pnl._normalize_winner(row["official_winner"])
    if winner is None:
        return None
    result = {
        "market_id": int(market_id),
        "topic_id": int(row["topic_id"]) if "topic_id" in row.keys() and row["topic_id"] is not None else None,
        "start_price": float(row["start_price"]) if "start_price" in row.keys() and row["start_price"] is not None else None,
        "official_end_price": float(row["official_end_price"]) if "official_end_price" in row.keys() and row["official_end_price"] is not None else None,
        "official_winner": winner,
        "source": f"SIMULATION_DB_{match_mode.upper()}",
    }
    return result


def _upsert(db: sqlite3.Connection, row: dict[str, Any]) -> None:
    db.execute(
        """INSERT INTO market_settlements(
            market_id,topic_id,start_price,official_winner,official_end_price,status,source,fetched_at_ms
        ) VALUES(?,?,?,?,?,'OFFICIAL',?,?)
        ON CONFLICT(market_id) DO UPDATE SET
            topic_id=excluded.topic_id,
            start_price=excluded.start_price,
            official_winner=excluded.official_winner,
            official_end_price=excluded.official_end_price,
            status='OFFICIAL',
            source=excluded.source,
            fetched_at_ms=excluded.fetched_at_ms""",
        (
            int(row["market_id"]), row.get("topic_id"), row.get("start_price"),
            row["official_winner"], row.get("official_end_price"), row["source"], int(time.time() * 1000),
        ),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill OFFICIAL Predict settlements for frozen Target-like OOF markets.")
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--simulation-db", type=Path, default=DEFAULT_SIM_DB)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--window-rows", type=int, default=300)
    parser.add_argument("--min-history-rows", type=int, default=40)
    args = parser.parse_args()

    selected, coverage = pnl._selected_rows(
        args.oof,
        window_rows=max(80, int(args.window_rows)),
        min_history_rows=max(20, int(args.min_history_rows)),
    )
    market_ids = sorted({int(row["market_id"]) for row in selected})
    if not market_ids:
        raise SystemExit("no selected OOF markets")

    sim = _connect_ro(args.simulation_db)
    out = _create_output(args.output_db)
    headers = {"Accept": "application/json", "User-Agent": "Target-Taker-Settlement-Backfill/1.0"}
    if os.environ.get("PREDICT_FUN_API_KEY"):
        headers["x-api-key"] = os.environ["PREDICT_FUN_API_KEY"]
    client = httpx.Client(timeout=httpx.Timeout(8.0, connect=2.0), trust_env=False, headers=headers)

    counts = {"requestedMarkets": len(market_ids), "existingOutput": 0, "simulationDb": 0, "predictApi": 0, "unresolved": 0, "apiErrors": 0}
    unresolved: list[int] = []
    try:
        for index, market_id in enumerate(market_ids, 1):
            existing = out.execute(
                "SELECT status,official_winner FROM market_settlements WHERE market_id=? LIMIT 1", (market_id,)
            ).fetchone()
            if existing is not None and str(existing["status"]).upper() == "OFFICIAL" and pnl._normalize_winner(existing["official_winner"]):
                counts["existingOutput"] += 1
                continue

            copied = _simulation_lookup(sim, market_id)
            if copied is not None:
                _upsert(out, copied)
                counts["simulationDb"] += 1
                continue

            resolved = None
            try:
                response = client.get(f"{PREDICT_API_BASE}/v1/markets/{market_id}")
                response.raise_for_status()
                parsed = _parse_official_detail(response.json())
                if parsed is not None:
                    resolved = {"market_id": market_id, "topic_id": None, **parsed, "source": "PREDICT_MARKET_DETAIL_API"}
            except Exception as exc:
                counts["apiErrors"] += 1
                if len(unresolved) < 20:
                    print(f"API error market {market_id}: {type(exc).__name__}: {exc}", flush=True)

            if resolved is not None:
                _upsert(out, resolved)
                counts["predictApi"] += 1
            else:
                counts["unresolved"] += 1
                unresolved.append(market_id)

            if index % 25 == 0 or index == len(market_ids):
                out.commit()
                print(
                    f"settlements {index}/{len(market_ids)} | sim={counts['simulationDb']} api={counts['predictApi']} unresolved={counts['unresolved']}",
                    flush=True,
                )
        out.commit()
    finally:
        client.close()
        out.close()
        if sim is not None:
            sim.close()

    resolved_count = counts["existingOutput"] + counts["simulationDb"] + counts["predictApi"]
    report = {
        "reportVersion": VERSION,
        "paperResearchOnly": True,
        "sourcePolicy": "simulation.db OFFICIAL first; otherwise Predict /v1/markets/{market_id} variantData.startPrice/endPrice",
        "winnerRule": "UP iff official endPrice > startPrice, else DOWN; identical to simulation settlement logic",
        "selectionCoverage": coverage,
        "counts": counts,
        "resolvedCoverage": resolved_count / len(market_ids),
        "unresolvedMarketIdsFirst50": unresolved[:50],
        "outputDb": str(args.output_db.expanduser().resolve()),
    }
    resolved_report = args.report.expanduser().resolve()
    resolved_report.parent.mkdir(parents=True, exist_ok=True)
    resolved_report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Resolved official settlements: {resolved_count}/{len(market_ids)} ({resolved_count / len(market_ids):.1%})", flush=True)
    print(f"Report: {resolved_report}", flush=True)
    return 0 if resolved_count else 2


if __name__ == "__main__":
    raise SystemExit(main())
