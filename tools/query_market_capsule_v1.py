from __future__ import annotations

"""Fast query helper for Market Capsule / Decision Seam V1 Parquet datasets."""

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPSULE = ROOT / "data" / "research" / "market_capsule_v1" / "benchmark_50_v1"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capsule", type=Path, default=DEFAULT_CAPSULE)
    ap.add_argument("--market-id", type=int)
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--sql", help="custom DuckDB SQL; use {capsule} as a directory placeholder")
    ns = ap.parse_args()
    try:
        import duckdb  # type: ignore
    except ImportError as exc:
        raise SystemExit("duckdb is required; install the project research extras: pip install -e '.[research]'") from exc

    cap = ns.capsule.resolve()
    con = duckdb.connect(database=":memory:")
    try:
        if ns.sql:
            sql = ns.sql.replace("{capsule}", cap.as_posix())
            rows = con.execute(sql).fetchdf()
            print(rows.to_string(index=False))
            return 0
        seams = (cap / "decision_seams.parquet").as_posix()
        markets = (cap / "markets.parquet").as_posix()
        if ns.market_id is not None:
            rows = con.execute(
                f"""SELECT action_event_ms,seconds_left,action_role,action_side,action_quote_type,
                            action_price,action_shares,pre_floor,pre_upside,pre_share_gap,
                            source_strict_best_bid,source_strict_best_ask,source_strict_book_age_ms
                       FROM read_parquet('{seams}')
                      WHERE market_id=? ORDER BY action_event_ms LIMIT ?""",
                [int(ns.market_id), max(1, int(ns.limit))],
            ).fetchdf()
            print(rows.to_string(index=False))
            return 0
        summary = {
            "markets": con.execute(f"SELECT count(*) FROM read_parquet('{markets}')").fetchone()[0],
            "decisionSeams": con.execute(f"SELECT count(*) FROM read_parquet('{seams}')").fetchone()[0],
            "strictBookCoverage": con.execute(
                f"SELECT avg((source_strict_book_source_ms IS NOT NULL)::INT) FROM read_parquet('{seams}')"
            ).fetchone()[0],
            "roles": [
                {"role": r[0], "side": r[1], "n": r[2]}
                for r in con.execute(
                    f"SELECT action_role,action_side,count(*) FROM read_parquet('{seams}') GROUP BY 1,2 ORDER BY 1,2"
                ).fetchall()
            ],
        }
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
