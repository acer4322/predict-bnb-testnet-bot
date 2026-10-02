from __future__ import annotations

"""Materialize current-V3B pre-action research rows as a compact Parquet capsule.

Column contract intentionally separates:
- state_*   : pure pre-action controller / responsibility / market state
- context_* : current state projected onto the candidate side (still causal/current)
- action_*  : candidate action context
- label_*   : future physical execution outcomes; never runtime features by default

Canonical truth remains the V3B HFT run + exact-FIFO ledger artifacts.
"""

import argparse
import hashlib
import json
from pathlib import Path

import duckdb


ID_MAP = {
    "marketId": "market_id",
    "t": "decision_ms",
    "key": "carrier_key",
    "windowEndMs": "window_end_ms",
    "stateTiming": "state_timing",
    "candidateAlreadyInState": "candidate_already_in_state",
}

STATE_MAP = {
    "secondsLeft": "state_seconds_left",
    "upQty": "state_up_qty",
    "downQty": "state_down_qty",
    "cost": "state_cost",
    "floor": "state_floor",
    "best": "state_best",
    "absNet": "state_abs_net",
    "coverage": "state_coverage",
    "gross": "state_gross",
    "dominantSide": "state_dominant_side",
    "debtUp": "state_debt_up",
    "debtDown": "state_debt_down",
    "totalDebt": "state_total_debt",
    "oldestRepairProgress": "state_oldest_repair_progress",
    "oldestRepairAgeMs": "state_oldest_repair_age_ms",
    "responsibilityCount": "state_responsibility_count",
    "liveSlots": "state_live_slots",
    "repairLiveSlots": "state_repair_family_live_slots",
    "expandLiveSlots": "state_satellite_expand_live_slots",
    "pendingCancelCount": "state_pending_cancel_count",
    "bookImbalance": "state_book_imbalance",
    "spread": "state_spread",
    "dominantMid": "state_dominant_mid",
    "qLadderLive": "state_q_ladder_live",
    "qPendingActive": "state_q_pending_active",
}

CONTEXT_MAP = {
    "targetDebtForActionSide": "context_target_debt_for_action_side",
    "sideLiveSlots": "context_side_live_slots",
    "sideBid": "context_side_bid",
    "sideAsk": "context_side_ask",
    "sideMid": "context_side_mid",
    "sideIsDominant": "context_side_is_dominant",
    "sideIsWeak": "context_side_is_weak",
}

ACTION_MAP = {
    "side": "action_side",
    "role": "action_role",
    "route": "action_route",
    "source": "action_source",
    "price": "action_price",
    "qty": "action_qty",
    "priceToBid": "action_price_to_bid",
    "askToPrice": "action_ask_to_price",
    "pairLegal": "action_pair_legal",
    "isRepairRole": "action_is_repair_role",
    "isExpandRole": "action_is_expand_role",
}

LABEL_MAP = {
    "fillQty3s": "label_fill_qty_3s",
    "anyFill3s": "label_any_fill_3s",
    "repairPayQty3s": "label_repair_pay_qty_3s",
    "overflowQty3s": "label_overflow_qty_3s",
    "cancelReq3s": "label_cancel_req_3s",
    "terminal3s": "label_terminal_3s",
    "fillQty5s": "label_fill_qty_5s",
    "anyFill5s": "label_any_fill_5s",
    "repairPayQty5s": "label_repair_pay_qty_5s",
    "overflowQty5s": "label_overflow_qty_5s",
    "cancelReq5s": "label_cancel_req_5s",
    "terminal5s": "label_terminal_5s",
    "finalCum": "label_final_cum",
    "eventualFill": "label_eventual_fill",
}

GROUPS = {
    "identifiers": list(ID_MAP.values()),
    "runtimeState": list(STATE_MAP.values()),
    "candidateContext": list(CONTEXT_MAP.values()),
    "candidateAction": list(ACTION_MAP.values()),
    "physicalLabels": list(LABEL_MAP.values()),
}


def qident(x: str) -> str:
    return '"' + str(x).replace('"', '""') + '"'


def qpath(p: Path) -> str:
    return p.resolve().as_posix().replace("'", "''")


def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-jsonl", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    a = ap.parse_args()

    src = a.source_jsonl.resolve()
    outdir = a.output_dir.resolve()
    outdir.mkdir(parents=True, exist_ok=True)
    parquet = outdir / "our_decision_features_v1.parquet"
    manifest_path = outdir / "our_decision_features_v1.manifest.json"

    con = duckdb.connect(database=":memory:")
    con.execute(
        f"CREATE VIEW src AS SELECT * FROM read_json_auto('{qpath(src)}', format='newline_delimited')"
    )
    available = [r[0] for r in con.execute("DESCRIBE SELECT * FROM src").fetchall()]
    mapping = {}
    for group in (ID_MAP, STATE_MAP, CONTEXT_MAP, ACTION_MAP, LABEL_MAP):
        mapping.update(group)
    missing = [c for c in mapping if c not in available]
    if missing:
        raise SystemExit(f"source missing required columns: {missing}")

    select_sql = ",\n  ".join(f"{qident(src_col)} AS {qident(dst_col)}" for src_col, dst_col in mapping.items())
    con.execute(
        f"COPY (SELECT\n  {select_sql}\n FROM src ORDER BY marketId,t,key) "
        f"TO '{qpath(parquet)}' (FORMAT PARQUET, COMPRESSION ZSTD)"
    )

    qp = qpath(parquet)
    row_count = int(con.execute(f"SELECT count(*) FROM read_parquet('{qp}')").fetchone()[0])
    market_count = int(con.execute(f"SELECT count(DISTINCT market_id) FROM read_parquet('{qp}')").fetchone()[0])
    duplicate_keys = int(con.execute(
        f"SELECT count(*) FROM (SELECT market_id,decision_ms,carrier_key,count(*) n FROM read_parquet('{qp}') GROUP BY ALL HAVING n>1)"
    ).fetchone()[0])
    timing_bad = int(con.execute(
        f"SELECT count(*) FROM read_parquet('{qp}') WHERE state_timing!='PRE_SUBMIT_DECISION' OR candidate_already_in_state!=0"
    ).fetchone()[0])
    after_end = int(con.execute(
        f"SELECT count(*) FROM read_parquet('{qp}') WHERE decision_ms>window_end_ms"
    ).fetchone()[0])
    negative_slots = int(con.execute(
        f"SELECT count(*) FROM read_parquet('{qp}') WHERE state_live_slots<0 OR context_side_live_slots<0 OR state_repair_family_live_slots<0 OR state_satellite_expand_live_slots<0"
    ).fetchone()[0])
    label_cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qp}')").fetchall() if r[0].startswith("label_")]
    all_cols = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM read_parquet('{qp}')").fetchall()]
    forbidden_names = [c for c in all_cols if "winner" in c.lower() or "target" in c.lower() and c != "context_target_debt_for_action_side"]

    checks = {
        "uniqueDecisionCarrierKey": duplicate_keys == 0,
        "allPreSubmitState": timing_bad == 0,
        "decisionNotAfterWindowEnd": after_end == 0,
        "nonnegativeOccupancy": negative_slots == 0,
        "physicalLabelsPrefixed": set(label_cols) == set(LABEL_MAP.values()),
        "noWinnerOrTargetTeacherRuntimeColumns": len(forbidden_names) == 0,
    }
    manifest = {
        "version": "OUR_DECISION_RESPONSIBILITY_CAPSULE_V1",
        "date": "2026-09-07",
        "researchOnly": True,
        "actionAuthority": False,
        "source": str(src),
        "sourceSha256": sha256_file(src),
        "output": str(parquet),
        "rows": row_count,
        "markets": market_count,
        "columns": all_cols,
        "groups": GROUPS,
        "stateSemantics": {
            "timing": "PRE_SUBMIT_DECISION",
            "candidateCarrierAlreadyInState": False,
            "runtimeState": "action-independent current V3B controller/responsibility/book state",
            "candidateContext": "current state projected onto candidate side; causal but candidate-relative",
            "candidateAction": "candidate role/route/side/price/qty",
            "roleOccupancyTaxonomy": "state_repair_family_live_slots = ECONOMIC_CORE + SATELLITE_REPAIR; state_satellite_expand_live_slots = SATELLITE_EXPAND only; PROBE_CORE live occupancy is not present in V1 source trace",
            "physicalLabels": "future HFT outcomes; forbidden from default runtime feature queries",
        },
        "checks": checks,
        "promotionGate": {
            "pass": all(checks.values()),
            "required": list(checks),
        },
        "boundaries": [
            "derived analytical cache only; canonical truth remains realistic HFT + exact FIFO ledger",
            "no winner/Target future runtime features",
            "3s/5s horizons are physical outcome labels, not fixed-second strategy rules",
            "no dream fill",
            "no 8781",
        ],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "rows": row_count,
        "markets": market_count,
        "parquetBytes": parquet.stat().st_size,
        "checks": checks,
        "promotionPass": manifest["promotionGate"]["pass"],
        "output": str(parquet),
        "manifest": str(manifest_path),
    }, indent=2, ensure_ascii=False))
    con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
