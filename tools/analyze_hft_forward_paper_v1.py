from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "hft_forward_paper_v1.db"
OUT_JSON = ROOT / "data" / "research" / "hft_forward_paper_v1_report.json"
OUT_CSV = ROOT / "data" / "research" / "hft_forward_paper_v1_markets.csv"


def _stats(xs: list[float]) -> dict:
    ys = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {
        "n": len(ys),
        "sum": sum(ys) if ys else 0.0,
        "mean": statistics.mean(ys) if ys else None,
        "median": statistics.median(ys) if ys else None,
        "min": min(ys) if ys else None,
        "max": max(ys) if ys else None,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=0.0, help="0 means all official HFT forward markets")
    args = ap.parse_args()
    if not DB.exists():
        raise RuntimeError(f"official HFT forward PAPER ledger not found: {DB}")
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        cutoff = int(time.time() * 1000 - args.hours * 3600_000) if args.hours > 0 else 0
        rows = [dict(r) for r in con.execute(
            """SELECT * FROM hft_forward_runs_v1
               WHERE status='COMPLETE' AND winner IN ('UP','DOWN')
                 AND COALESCE(window_end_ms,0)>=?
               ORDER BY market_id,strategy_key""",
            (cutoff,),
        )]
        boundary = con.execute("SELECT value FROM hft_forward_meta_v1 WHERE key='activation_after_window_end_ms'").fetchone()
    finally:
        con.close()
    by_market: dict[int, dict[str, dict]] = {}
    for r in rows:
        by_market.setdefault(int(r["market_id"]), {})[str(r["strategy_key"])] = r
    matched = []
    for mid in sorted(by_market):
        z = by_market[mid]
        if "R2" not in z or "CAP100" not in z:
            continue
        r2, cap = z["R2"], z["CAP100"]
        matched.append({
            "marketId": mid,
            "winner": r2.get("winner") or cap.get("winner"),
            "r2Pnl": float(r2["realized_pnl_usdt"]),
            "cap100Pnl": float(cap["realized_pnl_usdt"]),
            "deltaCapMinusR2": float(cap["realized_pnl_usdt"]) - float(r2["realized_pnl_usdt"]),
            "r2Capital": float(r2["total_cost_usdt"] or 0.0),
            "cap100Capital": float(cap["total_cost_usdt"] or 0.0),
            "r2MakerFilledShares": float(r2["maker_filled_shares"] or 0.0),
            "cap100MakerFilledShares": float(cap["maker_filled_shares"] or 0.0),
            "r2TakerFills": int(r2["taker_fills"] or 0),
            "cap100TakerFills": int(cap["taker_fills"] or 0),
            "r2FinalAbsNet": float(r2["final_abs_net"] or 0.0),
            "cap100FinalAbsNet": float(cap["final_abs_net"] or 0.0),
            "r2FinalMakerNet": float(r2["final_maker_net"] or 0.0),
            "cap100FinalMakerNet": float(cap["final_maker_net"] or 0.0),
            "windowEndMs": int(r2["window_end_ms"] or cap["window_end_ms"] or 0),
        })
    r2p = [x["r2Pnl"] for x in matched]
    capp = [x["cap100Pnl"] for x in matched]
    delta = [x["deltaCapMinusR2"] for x in matched]
    report = {
        "version": "HFT_FORWARD_PAPER_ANALYZER_V1",
        "officialPerformanceEvidence": True,
        "executionEvidenceLabel": "HFTBACKTEST_PREDICT_TAPE_CLOSED_LOOP_FORWARD_PAPER",
        "dreamFillUsed": False,
        "database": str(DB),
        "activationAfterWindowEndMs": int(boundary[0]) if boundary else None,
        "hours": args.hours,
        "matchedSettledMarkets": len(matched),
        "r2": {
            "pnlUsdt": sum(r2p),
            "positiveMarkets": sum(x > 0 for x in r2p),
            "positiveRate": sum(x > 0 for x in r2p) / len(r2p) if r2p else None,
            "capital": _stats([x["r2Capital"] for x in matched]),
            "finalAbsNet": _stats([x["r2FinalAbsNet"] for x in matched]),
            "makerFilledShares": _stats([x["r2MakerFilledShares"] for x in matched]),
        },
        "cap100": {
            "pnlUsdt": sum(capp),
            "positiveMarkets": sum(x > 0 for x in capp),
            "positiveRate": sum(x > 0 for x in capp) / len(capp) if capp else None,
            "capital": _stats([x["cap100Capital"] for x in matched]),
            "finalAbsNet": _stats([x["cap100FinalAbsNet"] for x in matched]),
            "makerFilledShares": _stats([x["cap100MakerFilledShares"] for x in matched]),
        },
        "matchedComparison": {
            "capMinusR2PnlUsdt": sum(delta),
            "betterMarkets": sum(x > 0 for x in delta),
            "worseMarkets": sum(x < 0 for x in delta),
            "flatMarkets": sum(abs(x) <= 1e-12 for x in delta),
            "delta": _stats(delta),
        },
        "boundary": "Only post-cutover markets with COMPLETE_FORWARD_V1 Predict tape and completed HftBacktest own-state closed-loop for BOTH R2 and CAP100 are included. Legacy 8784/8786 QUEUECLEAR_PASS fills are excluded.",
        "markets": matched,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    fields = list(matched[0]) if matched else ["marketId","winner","r2Pnl","cap100Pnl","deltaCapMinusR2"]
    with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields); w.writeheader(); w.writerows(matched)
    print(json.dumps({"ok": True, "report": str(OUT_JSON), "marketsCsv": str(OUT_CSV), "matchedSettledMarkets": len(matched), "r2Pnl": sum(r2p), "cap100Pnl": sum(capp), "capMinusR2": sum(delta)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
