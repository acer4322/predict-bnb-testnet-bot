from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPSULE = ROOT / "data" / "research" / "market_capsule_v1" / "benchmark_50_v1" / "decision_seams.parquet"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "market_capsule_v1" / "informative_seams_smoke5_v1.json"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capsule", type=Path, default=DEFAULT_CAPSULE)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--count", type=int, default=5)
    ap.add_argument("--min-seconds-left", type=float, default=180.0)
    ns = ap.parse_args()

    path = ns.capsule.resolve().as_posix().replace("'", "''")
    con = duckdb.connect(database=":memory:")
    try:
        # Score uses strict-past state only. Current Target action fields are kept only as
        # post-selection audit labels and are never used by the fork action generator.
        sql = f"""
        WITH base AS (
          SELECT *,
                 row_number() OVER (
                   PARTITION BY market_id, action_event_ms
                   ORDER BY seam_id
                 ) AS event_rank,
                 least(3.0, coalesce(pre_abs_share_gap,0.0)/10.0) AS gap_component,
                 CASE WHEN coalesce(pre_floor,0.0) < 0 THEN least(2.0, abs(pre_floor)/5.0 + 0.5) ELSE 0.0 END AS floor_component,
                 CASE WHEN coalesce(previous_action_age_ms,999999999) <= 5000 THEN 1.0 ELSE 0.0 END AS continuity_component,
                 CASE WHEN coalesce(receipt_strict_spread,1.0) <= 0.02 THEN 0.5 ELSE 0.0 END AS spread_component,
                 least(1.0,
                   abs(coalesce(receipt_strict_bid_depth_total,0.0)-coalesce(receipt_strict_ask_depth_total,0.0)) /
                   greatest(1.0, coalesce(receipt_strict_bid_depth_total,0.0)+coalesce(receipt_strict_ask_depth_total,0.0)) * 4.0
                 ) AS imbalance_component,
                 CASE WHEN coalesce(receipt_strict_book_received_age_ms,999999999) BETWEEN 0 AND 500 THEN 0.5 ELSE 0.0 END AS freshness_component
          FROM read_parquet('{path}')
          WHERE seconds_left > {float(ns.min_seconds_left)}
            AND receipt_strict_book_received_ms IS NOT NULL
            AND receipt_strict_book_received_ms < action_event_ms
            AND public_sampled_at_ms IS NOT NULL
            AND public_sampled_at_ms < action_event_ms
            AND receipt_strict_best_bid IS NOT NULL
            AND receipt_strict_best_ask IS NOT NULL
            AND receipt_strict_best_bid > 0
            AND receipt_strict_best_ask < 1
        ), ranked AS (
          SELECT *,
                 gap_component + floor_component + continuity_component + spread_component + imbalance_component + freshness_component AS information_score,
                 row_number() OVER (
                   PARTITION BY market_id
                   ORDER BY gap_component + floor_component + continuity_component + spread_component + imbalance_component + freshness_component DESC,
                            action_event_ms DESC,
                            seam_id
                 ) AS market_rank
          FROM base
          WHERE event_rank=1
        )
        SELECT *
        FROM ranked
        WHERE market_rank=1
        ORDER BY information_score DESC, market_id DESC
        LIMIT {max(1, int(ns.count))}
        """
        df = con.execute(sql).fetchdf()
    finally:
        con.close()

    if len(df) < max(1, int(ns.count)):
        raise RuntimeError(f"only {len(df)} informative seams available; requested {ns.count}")

    rows = []
    for rec in df.to_dict(orient="records"):
        clean = {}
        for k, v in rec.items():
            if hasattr(v, "item"):
                try:
                    v = v.item()
                except Exception:
                    pass
            if isinstance(v, float) and not math.isfinite(v):
                v = None
            clean[k] = v
        rows.append(clean)

    payload = {
        "version": "BTC5M_INFORMATIVE_DECISION_SEAMS_V1",
        "researchOnly": True,
        "source": str(ns.capsule.resolve()),
        "count": len(rows),
        "selection": {
            "minimumSecondsLeft": float(ns.min_seconds_left),
            "oneRepresentativePerTargetEventTimestamp": True,
            "oneSeamPerMarket": True,
            "scoreInputs": [
                "pre_abs_share_gap",
                "pre_floor",
                "previous_action_age_ms",
                "receipt_strict_spread",
                "receipt_strict_bid_depth_total/ask_depth_total",
                "receipt_strict_book_received_age_ms",
            ],
            "usesCurrentTargetActionForScore": False,
            "targetActionFieldsKeptForAuditOnly": True,
        },
        "rows": rows,
    }
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "output": str(ns.output.resolve()),
        "count": len(rows),
        "selected": [
            {
                "marketId": int(r["market_id"]),
                "seamId": r["seam_id"],
                "score": round(float(r["information_score"]), 4),
                "secondsLeft": round(float(r["seconds_left"]), 3),
                "preFloor": round(float(r.get("pre_floor") or 0.0), 4),
                "preAbsGap": round(float(r.get("pre_abs_share_gap") or 0.0), 4),
                "targetAuditRole": r.get("action_role"),
                "targetAuditSide": r.get("action_side"),
            }
            for r in rows
        ],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
