from __future__ import annotations

"""Explicit provenance-only label export for OUR directional-alpha identification.

This script intentionally performs one bounded SQLite read for settlement winner
labels because winner is not materialized in ResearchFastPath our-features.
It reads exact market_ids discovered from the fast feature cache and writes only
market_id/winner. It is NOT a runtime feature source.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.research_fast_path_v1 import ResearchFastPath


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", required=True)
    ns = ap.parse_args()

    with ResearchFastPath() as fp:
        rel = fp.rows(
            "our-features",
            columns=["market_id", "decision_ms", "state_seconds_left"],
            where="state_seconds_left > 180",
            include_labels=False,
        )
        df = rel.df()

    if df.empty:
        raise RuntimeError("no eligible OUR feature rows >180s")

    ids = sorted(int(x) for x in df["market_id"].unique())
    q = ",".join("?" for _ in ids)
    db_path = ROOT / "data" / "echtgeld_engine_v1.db"
    con = sqlite3.connect(str(db_path))
    try:
        rows = con.execute(
            f"select market_id, upper(winner) from engine_settlements where market_id in ({q})",
            ids,
        ).fetchall()
    finally:
        con.close()

    labels = {int(mid): str(w).upper() for mid, w in rows if str(w).upper() in {"UP", "DOWN"}}
    missing = [mid for mid in ids if mid not in labels]
    out = {
        "version": "OUR_DIRECTIONAL_WINNER_LABELS_PROVENANCE_V1",
        "date": "2026-09-09",
        "researchOnly": True,
        "runtimeAuthority": False,
        "featureSource": "ResearchFastPath:our-features state_seconds_left>180",
        "labelSource": "data/echtgeld_engine_v1.db:engine_settlements exact market_id set",
        "eligibleMarkets": len(ids),
        "winnerLabels": len(labels),
        "missingMarkets": missing,
        "labels": [{"market_id": mid, "winner": labels[mid]} for mid in ids if mid in labels],
    }
    Path(ns.output).write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({k: out[k] for k in ["eligibleMarkets", "winnerLabels", "missingMarkets"]}, indent=2))
    return 0 if not missing else 3


if __name__ == "__main__":
    raise SystemExit(main())
