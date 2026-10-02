from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CAPSULE = ROOT / "data" / "research" / "market_capsule_v1" / "benchmark_50_v1" / "decision_seams.parquet"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "market_capsule_v1" / "informative_seams_stratified12_v2.json"
CATEGORIES = ("REPAIR_CRITICAL", "NEAR_BALANCED", "NEAR_FLOOR", "DENSE_CONTINUATION")


def clean_record(rec: dict) -> dict:
    out = {}
    for k, v in rec.items():
        if hasattr(v, "item"):
            try:
                v = v.item()
            except Exception:
                pass
        if isinstance(v, float) and not math.isfinite(v):
            v = None
        out[k] = v
    return out


def category(row: dict) -> str | None:
    gap = float(row.get("pre_abs_share_gap") or 0.0)
    floor = float(row.get("pre_floor") or 0.0)
    prev = row.get("previous_action_age_ms")
    prev = int(prev) if prev is not None else 10**12
    if floor < -20 and gap >= 50:
        return "REPAIR_CRITICAL"
    if gap <= 20:
        return "NEAR_BALANCED"
    if -5 <= floor <= 5:
        return "NEAR_FLOOR"
    if prev <= 3000:
        return "DENSE_CONTINUATION"
    return None


def category_score(row: dict, cat: str) -> float:
    gap = float(row.get("pre_abs_share_gap") or 0.0)
    floor = float(row.get("pre_floor") or 0.0)
    prev = row.get("previous_action_age_ms")
    prev = float(prev) if prev is not None else 1e12
    spread = float(row.get("receipt_strict_spread") or 1.0)
    age = float(row.get("receipt_strict_book_received_age_ms") or 1e12)
    freshness = 1.0 / (1.0 + max(0.0, age) / 250.0)
    tight = 1.0 if spread <= 0.02 else 0.0
    if cat == "REPAIR_CRITICAL":
        return min(abs(floor), 200.0) / 40.0 + min(gap, 400.0) / 80.0 + freshness + tight
    if cat == "NEAR_BALANCED":
        return (20.0 - min(gap, 20.0)) / 5.0 + freshness + tight + (1.0 if prev <= 5000 else 0.0)
    if cat == "NEAR_FLOOR":
        return (5.0 - min(abs(floor), 5.0)) + freshness + tight + (1.0 if prev <= 5000 else 0.0)
    if cat == "DENSE_CONTINUATION":
        return 3.0 / (1.0 + max(prev, 0.0) / 1000.0) + freshness + tight + min(gap, 50.0) / 50.0
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capsule", type=Path, default=DEFAULT_CAPSULE)
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    ap.add_argument("--per-category", type=int, default=3)
    ap.add_argument("--min-seconds-left", type=float, default=180.0)
    ns = ap.parse_args()

    path = ns.capsule.resolve().as_posix().replace("'", "''")
    con = duckdb.connect(database=":memory:")
    try:
        df = con.execute(f"""
            WITH x AS (
              SELECT *, row_number() OVER (
                PARTITION BY market_id, action_event_ms ORDER BY seam_id
              ) event_rank
              FROM read_parquet('{path}')
              WHERE seconds_left > {float(ns.min_seconds_left)}
                AND receipt_strict_book_received_ms IS NOT NULL
                AND receipt_strict_book_received_ms < action_event_ms
                AND public_sampled_at_ms IS NOT NULL
                AND public_sampled_at_ms < action_event_ms
                AND receipt_strict_best_bid > 0
                AND receipt_strict_best_ask < 1
            )
            SELECT * FROM x WHERE event_rank=1
        """).fetchdf()
    finally:
        con.close()

    pools = {c: [] for c in CATEGORIES}
    for raw in df.to_dict(orient="records"):
        row = clean_record(raw)
        cat = category(row)
        if cat is None:
            continue
        row["sampling_category"] = cat
        row["category_score"] = category_score(row, cat)
        pools[cat].append(row)
    for cat in CATEGORIES:
        pools[cat].sort(key=lambda r: (-float(r["category_score"]), -int(r["action_event_ms"]), int(r["market_id"]), str(r["seam_id"])))

    selected = []
    used_markets: set[int] = set()
    used_seams: set[str] = set()
    need = max(1, int(ns.per_category))
    for cat in CATEGORIES:
        count = 0
        # First pass: require a new market to maximize market diversity.
        for row in pools[cat]:
            mid = int(row["market_id"])
            sid = str(row["seam_id"])
            if mid in used_markets or sid in used_seams:
                continue
            selected.append(row); used_markets.add(mid); used_seams.add(sid); count += 1
            if count >= need:
                break
        # Second pass: if a category is scarce across new markets, allow a reused market but never reused seam.
        if count < need:
            for row in pools[cat]:
                sid = str(row["seam_id"])
                if sid in used_seams:
                    continue
                selected.append(row); used_markets.add(int(row["market_id"])); used_seams.add(sid); count += 1
                if count >= need:
                    break
        if count < need:
            raise RuntimeError(f"category {cat} has only {count} selectable seams; need {need}")

    payload = {
        "version": "BTC5M_STRATIFIED_INFORMATIVE_DECISION_SEAMS_V2",
        "researchOnly": True,
        "source": str(ns.capsule.resolve()),
        "selection": {
            "categories": list(CATEGORIES),
            "perCategory": need,
            "minimumSecondsLeft": float(ns.min_seconds_left),
            "oneRepresentativePerTargetEventTimestamp": True,
            "newMarketPreferredAcrossCategories": True,
            "currentTargetActionUsedForScore": False,
            "currentTargetActionFieldsAuditOnly": True,
        },
        "categoryPoolCounts": {k: len(v) for k, v in pools.items()},
        "count": len(selected),
        "rows": selected,
    }
    ns.output.parent.mkdir(parents=True, exist_ok=True)
    ns.output.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({
        "ok": True,
        "output": str(ns.output.resolve()),
        "count": len(selected),
        "categoryPoolCounts": payload["categoryPoolCounts"],
        "selected": [
            {
                "category": r["sampling_category"],
                "marketId": int(r["market_id"]),
                "score": round(float(r["category_score"]), 4),
                "secondsLeft": round(float(r["seconds_left"]), 3),
                "preFloor": round(float(r.get("pre_floor") or 0.0), 4),
                "preAbsGap": round(float(r.get("pre_abs_share_gap") or 0.0), 4),
                "previousActionAgeMs": r.get("previous_action_age_ms"),
            }
            for r in selected
        ],
    }, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
