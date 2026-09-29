from __future__ import annotations

"""Materialize a reusable strict-past feature matrix from Market Capsule V1.

This removes repeated SQLite reads, JSON parsing, bisect/as-of joins and Target
rolling-history reconstruction from downstream teacher/model scripts.

Current Target action columns are label/audit only.  Runtime-safe feature columns
are explicitly listed in the generated manifest.
"""

import argparse
import json
import time
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
VERSION = "BTC5M_DECISION_FEATURE_MATRIX_V1"
DEFAULT_CAPSULE = ROOT / "data" / "research" / "market_capsule_v1" / "benchmark_50_v1"

PUBLIC_FEATURES = [
    "predictUpMid",
    "spotMinusStrikeBps",
    "chainlinkMinusStrikeBps",
    "spotMinusChainlinkBps",
    "directionScore",
    "spotReturn250msBps",
    "spotReturn1sBps",
    "spotReturn3sBps",
    "spotReturn5sBps",
    "futuresReturn250msBps",
    "futuresReturn1sBps",
    "futuresReturn3sBps",
    "futuresReturn5sBps",
    "perpSpotBasisBps",
    "spotQueueImbalance",
    "futuresQueueImbalance",
    "spotTakerImbalance250ms",
    "spotTakerImbalance1s",
    "futuresTakerImbalance250ms",
    "futuresTakerImbalance1s",
    "secondsLeft",
]


def qpath(path: Path) -> str:
    return path.resolve().as_posix().replace("'", "''")


def main() -> int:
    ap = argparse.ArgumentParser(description=VERSION)
    ap.add_argument("--capsule", type=Path, default=DEFAULT_CAPSULE)
    ap.add_argument("--output", type=Path)
    ns = ap.parse_args()

    cap = ns.capsule.resolve()
    seams = cap / "decision_seams.parquet"
    public = cap / "public_snapshots.parquet"
    if not seams.exists() or not public.exists():
        raise FileNotFoundError("capsule requires decision_seams.parquet and public_snapshots.parquet")
    out = (ns.output.resolve() if ns.output else cap / "decision_features_v1.parquet")
    manifest_path = out.with_suffix(".manifest.json")
    out.parent.mkdir(parents=True, exist_ok=True)

    pub_sql = []
    for key in PUBLIC_FEATURES:
        safe = key.replace("'", "''")
        pub_sql.append(
            f"TRY_CAST(json_extract_string(snapshot_json, '$.{safe}') AS DOUBLE) AS public_{key}"
        )

    # RANGE windows exclude the current millisecond with `1 PRECEDING`; this also
    # prevents same-timestamp Target legs from leaking into one another's features.
    sql = f"""
    COPY (
      WITH base AS (
        SELECT
          s.*,
          p.snapshot_json,
          count(*) OVER (PARTITION BY s.market_id) AS market_action_legs,
          count(*) OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 5000 PRECEDING AND 1 PRECEDING
          ) AS hist_events_5s,
          count(*) FILTER (WHERE s.action_role='MAKER') OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 15000 PRECEDING AND 1 PRECEDING
          ) AS hist_maker_events_15s,
          count(*) FILTER (WHERE s.action_role='TAKER') OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 15000 PRECEDING AND 1 PRECEDING
          ) AS hist_taker_events_15s,
          count(*) FILTER (WHERE s.action_side='UP') OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 15000 PRECEDING AND 1 PRECEDING
          ) AS hist_up_events_15s,
          count(*) FILTER (WHERE s.action_side='DOWN') OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 15000 PRECEDING AND 1 PRECEDING
          ) AS hist_down_events_15s,
          coalesce(sum(s.action_shares) FILTER (WHERE s.action_side='UP') OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 15000 PRECEDING AND 1 PRECEDING
          ),0) AS hist_up_shares_15s,
          coalesce(sum(s.action_shares) FILTER (WHERE s.action_side='DOWN') OVER (
            PARTITION BY s.market_id ORDER BY s.action_event_ms
            RANGE BETWEEN 15000 PRECEDING AND 1 PRECEDING
          ),0) AS hist_down_shares_15s
        FROM read_parquet('{qpath(seams)}') s
        LEFT JOIN read_parquet('{qpath(public)}') p
          ON p.market_id=s.market_id AND p.id=s.public_sample_id
      )
      SELECT
        market_id,
        seam_id,
        action_event_ms AS decision_ms,
        seconds_left,

        -- Runtime-safe strict-past portfolio geometry.
        pre_up_shares,
        pre_down_shares,
        pre_net_cost,
        pre_pnl_if_up,
        pre_pnl_if_down,
        pre_floor,
        pre_upside,
        pre_surplus,
        pre_share_gap,
        pre_abs_share_gap,
        target_prior_fill_legs,
        target_prior_parent_count,
        previous_action_age_ms,
        previous_action_role,
        previous_action_side,
        previous_action_quote_type,

        -- Runtime-safe strict-past receipt-frontier book state.
        receipt_strict_book_received_ms,
        receipt_strict_book_received_age_ms,
        receipt_strict_best_bid,
        receipt_strict_best_ask,
        receipt_strict_spread,
        receipt_strict_bid_depth_total,
        receipt_strict_ask_depth_total,
        receipt_strict_order_count,

        -- Runtime-safe rolling Target-history state. These describe only events
        -- strictly earlier than decision_ms and are useful for Target-teacher
        -- research; they are NOT OUR-runtime inputs unless a student analogue is
        -- explicitly defined.
        hist_events_5s AS target_hist_events_5s,
        hist_maker_events_15s AS target_hist_maker_events_15s,
        hist_taker_events_15s AS target_hist_taker_events_15s,
        hist_up_events_15s AS target_hist_up_events_15s,
        hist_down_events_15s AS target_hist_down_events_15s,
        hist_up_shares_15s AS target_hist_up_shares_15s,
        hist_down_shares_15s AS target_hist_down_shares_15s,
        CASE WHEN market_action_legs>1 THEN target_prior_fill_legs::DOUBLE/(market_action_legs-1) ELSE 0 END AS target_event_index_norm,

        public_sample_id,
        public_sampled_at_ms,
        public_age_ms,
        {', '.join(pub_sql)},

        -- Label/audit only. Never feed these columns to a runtime candidate model.
        action_role AS label_action_role,
        action_side AS label_action_side,
        action_quote_type AS label_action_quote_type,
        action_price AS label_action_price,
        action_shares AS label_action_shares,
        action_source_leg_id AS label_action_source_leg_id,
        action_order_hash AS label_action_order_hash
      FROM base
    ) TO '{qpath(out)}' (FORMAT PARQUET, COMPRESSION ZSTD)
    """

    t0 = time.perf_counter()
    con = duckdb.connect(database=":memory:")
    try:
        con.execute(sql)
        n = int(con.execute(f"SELECT count(*) FROM read_parquet('{qpath(out)}')").fetchone()[0])
        leaks = con.execute(f"""
          SELECT
            sum((receipt_strict_book_received_ms>=decision_ms)::INT),
            sum((public_sampled_at_ms>=decision_ms)::INT),
            sum((receipt_strict_book_received_age_ms<0)::INT),
            sum((public_age_ms<0)::INT)
          FROM read_parquet('{qpath(out)}')
        """).fetchone()
        public_cov = float(con.execute(f"SELECT avg((public_sample_id IS NOT NULL)::INT) FROM read_parquet('{qpath(out)}')").fetchone()[0] or 0.0)
        book_cov = float(con.execute(f"SELECT avg((receipt_strict_book_received_ms IS NOT NULL)::INT) FROM read_parquet('{qpath(out)}')").fetchone()[0] or 0.0)
        schema = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qpath(out)}')").fetchall()]
    finally:
        con.close()

    label_cols = [x for x in schema if x.startswith("label_")]
    public_cols = [x for x in schema if x.startswith("public_")]
    teacher_history_cols = [x for x in schema if x.startswith("target_hist_") or x == "target_event_index_norm"]
    identity_cols = {"market_id", "seam_id", "decision_ms", "public_sample_id", "public_sampled_at_ms"}
    runtime_public_book_portfolio = [
        x for x in schema
        if x not in identity_cols
        and x not in label_cols
        and x not in teacher_history_cols
        and not x.startswith("previous_action_")
        and not x.startswith("target_prior_")
    ]
    target_teacher_features = [x for x in schema if x not in identity_cols and x not in label_cols]
    leak_counts = {
        "receiptBookFutureLeak": int(leaks[0] or 0),
        "publicFutureLeak": int(leaks[1] or 0),
        "negativeReceiptAge": int(leaks[2] or 0),
        "negativePublicAge": int(leaks[3] or 0),
    }
    manifest = {
        "version": VERSION,
        "sourceCapsule": str(cap),
        "output": str(out),
        "rows": n,
        "columns": len(schema),
        "elapsedSeconds": round(time.perf_counter()-t0, 4),
        "bytes": out.stat().st_size,
        "coverage": {"strictReceiptBook": book_cov, "strictPublic": public_cov},
        "strictPast": {"pass": all(v == 0 for v in leak_counts.values()), "violations": leak_counts},
        "columnContracts": {
            "identity": sorted(identity_cols),
            "labelOnlyNeverRuntime": label_cols,
            "targetTeacherHistoryStrictPastButNotDirectOurRuntime": teacher_history_cols + ["target_prior_fill_legs", "target_prior_parent_count", "previous_action_age_ms", "previous_action_role", "previous_action_side", "previous_action_quote_type"],
            "publicBookPortfolioRuntimeSafe": runtime_public_book_portfolio,
            "targetTeacherFeatureSet": target_teacher_features,
            "publicFeatures": public_cols,
        },
        "boundaries": [
            "Current Target action is label/audit only and is never used to construct runtime features.",
            "Rolling Target-history windows end at decision_ms-1ms, excluding same-timestamp legs.",
            "Target-history fields are valid for Target-teacher analysis but require an OUR-state analogue before deployment.",
            "Market outcome/winner is not joined into this feature matrix.",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "output": str(out),
        "manifest": str(manifest_path),
        "rows": n,
        "columns": len(schema),
        "bytes": out.stat().st_size,
        "elapsedSeconds": manifest["elapsedSeconds"],
        "coverage": manifest["coverage"],
        "strictPast": manifest["strictPast"],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
