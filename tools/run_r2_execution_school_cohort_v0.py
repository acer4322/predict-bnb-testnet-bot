from __future__ import annotations

import argparse
import json
import warnings
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
INTEGRITY_DIR = ROOT / "data" / "research" / "hftbacktest_execution_shift_v0"

from tools.hftbacktest_r2_execution_school_v0 import run_market


def frozen126() -> list[int]:
    mids: list[int] = []
    for p in sorted(INTEGRITY_DIR.glob("execution_tape_frozen126_integrity_i*_n21_v1.json")):
        d = json.loads(p.read_text(encoding="utf-8"))
        mids.extend(int(x["marketId"]) for x in d.get("markets", []))
    return sorted(set(mids))


def target_covered(markets: list[int]) -> set[int]:
    import sqlite3
    db = sqlite3.connect(ROOT / "data" / "target_wallet_official_v1.db")
    try:
        got = {int(r[0]) for r in db.execute("SELECT DISTINCT market_id FROM wallet_shadow_target_events WHERE asset='BTC'")}
    finally:
        db.close()
    return set(markets) & got


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target-covered-only", action="store_true")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--start-index", type=int, default=0)
    p.add_argument("--entry-latency-ms", type=int, default=1092)
    p.add_argument("--response-latency-ms", type=int, default=273)
    p.add_argument("--queue-model", choices=["risk", "log"], default="risk")
    p.add_argument("--trade-offset", choices=["early", "mid", "late"], default="mid")
    p.add_argument("--taker-confirm-ms", type=int, default=2200)
    p.add_argument("--overwrite", action="store_true")
    args = p.parse_args()

    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    markets = frozen126()
    if args.target_covered_only:
        covered = target_covered(markets)
        markets = [m for m in markets if m in covered]
    markets = markets[max(0, args.start_index):]
    if args.limit > 0:
        markets = markets[:args.limit]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    completed: list[dict] = []
    errors: list[dict] = []
    for i, mid in enumerate(markets, 1):
        out = OUT_DIR / f"r2_execution_school_market{mid}_mid_risk_v0.json"
        try:
            if out.exists() and not args.overwrite:
                rep = json.loads(out.read_text(encoding="utf-8"))
                status = "EXISTING"
            else:
                rep = run_market(
                    mid,
                    entry_latency_ms=args.entry_latency_ms,
                    response_latency_ms=args.response_latency_ms,
                    queue_model=args.queue_model,
                    trade_offset=args.trade_offset,
                    taker_confirm_ms=args.taker_confirm_ms,
                )
                out.write_text(json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
                status = "RAN"
            s = rep.get("studentRollout", {})
            completed.append({
                "marketId": mid, "status": status, "decisions": s.get("decisions"),
                "makerPlacements": s.get("makerPlacements"), "makerFillEvents": s.get("makerFillEvents"),
                "makerFilledShares": s.get("makerFilledShares"), "takerFills": s.get("takerFills"),
                "orderStateRows": len(rep.get("orderStateRows", [])),
            })
        except Exception as exc:
            errors.append({"marketId": mid, "error": f"{type(exc).__name__}: {exc}"})
        if i % 5 == 0 or i == len(markets):
            print(json.dumps({"progress": i, "requested": len(markets), "completed": len(completed), "errors": len(errors), "lastMarket": mid}), flush=True)

    summary = {
        "version": "R2_EXECUTION_SCHOOL_COHORT_V0",
        "researchOnly": True,
        "student": "UNIFIED_PROMOTED_OWNSTATE_V4_R2_RESIDUAL_FORWARD_PAPER",
        "studentScale": "PRE_CAP100_ORIGINAL_R2",
        "dreamFillAllowed": False,
        "targetCoveredOnly": bool(args.target_covered_only),
        "requestedMarkets": len(markets),
        "completedMarkets": len(completed),
        "errors": errors,
        "totals": {
            "decisions": sum(int(x.get("decisions") or 0) for x in completed),
            "makerPlacements": sum(int(x.get("makerPlacements") or 0) for x in completed),
            "makerFillEvents": sum(int(x.get("makerFillEvents") or 0) for x in completed),
            "makerFilledShares": sum(float(x.get("makerFilledShares") or 0.0) for x in completed),
            "takerFills": sum(int(x.get("takerFills") or 0) for x in completed),
            "orderStateRows": sum(int(x.get("orderStateRows") or 0) for x in completed),
        },
        "markets": completed,
        "boundary": "All Student fills are HftBacktest/Execution Tape V1 only. Target coverage is only a cohort-selection flag; Target data is not read during rollout.",
    }
    name = "r2_execution_school_target112_cohort_v0.json" if args.target_covered_only and args.start_index == 0 and args.limit == 0 else f"r2_execution_school_cohort_i{args.start_index}_n{len(markets)}_v0.json"
    path = OUT_DIR / name
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": not errors, "path": str(path), "summary": summary["totals"], "completedMarkets": len(completed), "errors": errors}, ensure_ascii=False))
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
