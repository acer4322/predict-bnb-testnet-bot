from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import warnings
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_cap100_closed_loop_v0 as cl
DB = ROOT / "data" / "strategy_target_compare_v1.db"
CSV = ROOT / "data" / "research" / "8784_r2_vs_8786_cap100_fresh_v1_markets.csv"
OUT = ROOT / "data" / "research" / "hftbacktest_execution_shift_v0"
VER = "UNIFIED_PROMOTED_OWNSTATE_V4_R2_CAP100_KEEP18_FORWARD_PAPER"


def market_ids(n: int, start: int = 0) -> list[int]:
    with CSV.open(encoding="utf-8-sig", newline="") as f:
        ids = [int(r["marketId"]) for r in csv.DictReader(f)]
    # Preserve the original frozen-126 boundary even though the CSV is still growing.
    return ids[:126][start:start+n]


def paper_rows(mid: int) -> dict[int, dict]:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """select decision_ms,desired_portfolio_action,execution_choice,primary_reason,payload_json
               from our_decisions where strategy_version=? and market_id=? order by decision_ms""",
            (VER, int(mid)),
        ).fetchall()
    finally:
        con.close()
    out = {}
    for r in rows:
        try:
            payload = json.loads(r["payload_json"] or "{}")
        except Exception:
            payload = {}
        out[int(r["decision_ms"])] = {
            "desired": r["desired_portfolio_action"],
            "execution": r["execution_choice"],
            "reason": r["primary_reason"],
            "episode": payload.get("episode") if isinstance(payload, dict) else None,
            "readiness": payload.get("readiness") if isinstance(payload, dict) else None,
            "actions": payload.get("actions") if isinstance(payload, dict) else [],
            "models": payload.get("models") if isinstance(payload, dict) else {},
        }
    return out


def action_names(xs) -> list[str]:
    return [str(x.get("action")) for x in (xs or []) if isinstance(x, dict) and x.get("action")]


def audit_one(mid: int, args) -> dict:
    r = cl.run_market(
        mid,
        entry_latency_ms=args.entry_latency_ms,
        response_latency_ms=args.response_latency_ms,
        queue_model=args.queue_model,
        trade_offset=args.trade_offset,
        taker_mode=args.taker_mode,
        diagnostic_only=args.diagnostic_only,
    )
    pr = paper_rows(mid)
    cr = {int(x["decisionMs"]): x for x in r["decisionRows"]}
    common = sorted(set(pr) & set(cr))
    first_sem = None
    semantic_agree = 0
    placement_agree = 0
    for t in common:
        p, c = pr[t], cr[t]
        psem = (str(p.get("desired")), str(p.get("reason")))
        csem = (str(c.get("desiredPortfolioAction")), str(c.get("primaryReason")))
        if psem == csem:
            semantic_agree += 1
        elif first_sem is None:
            first_sem = {
                "decisionMs": t,
                "paper": {"desired": p.get("desired"), "reason": p.get("reason"), "episode": p.get("episode"), "readiness": p.get("readiness"), "actions": action_names(p.get("actions"))},
                "closed": {"desired": c.get("desiredPortfolioAction"), "reason": c.get("primaryReason"), "episode": c.get("episode"), "readiness": c.get("readiness"), "actions": action_names(c.get("actions"))},
                "transition": f"{p.get('desired')}->{c.get('desiredPortfolioAction')}",
            }
        if (str(p.get("execution")) == "MAKER") == (str(c.get("executionChoice")) == "MAKER"):
            placement_agree += 1
    ps = r["paper"]
    cs = r["closedLoop"]
    pf = ps.get("firstTaker") or {}
    cf = cs.get("firstTaker") or {}
    return {
        "marketId": mid,
        "matchedDecisions": len(common),
        "semanticAgreementRate": semantic_agree / len(common) if common else None,
        "makerPlacementDecisionAgreementRate": placement_agree / len(common) if common else None,
        "paperMakerOrders": ps.get("makerOrders"),
        "closedMakerPlacements": cs.get("makerPlacements"),
        "closedMakerFilledShares": cs.get("makerFilledShares"),
        "paperTakers": ps.get("takerFills"),
        "closedTakers": cs.get("takerFills"),
        "paperFirstTakerMs": pf.get("filled_at_ms"),
        "closedFirstTakerMs": cf.get("atMs"),
        "firstSemanticDivergence": first_sem,
        "runMetrics": cs.get("runMetrics"),
        "ledger": cs.get("ledger"),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--markets", type=int, default=10)
    p.add_argument("--start-index", type=int, default=0)
    p.add_argument("--entry-latency-ms", type=int, default=1092)
    p.add_argument("--response-latency-ms", type=int, default=273)
    p.add_argument("--queue-model", choices=["risk", "log"], default="risk")
    p.add_argument("--trade-offset", choices=["early", "mid", "late"], default="mid")
    p.add_argument("--taker-mode", choices=["instant", "hft"], default="hft")
    p.add_argument("--diagnostic-only", action="store_true")
    args = p.parse_args()
    warnings.filterwarnings("ignore", message="X does not have valid feature names")
    rows = [audit_one(mid, args) for mid in market_ids(args.markets, args.start_index)]
    trans = Counter((x.get("firstSemanticDivergence") or {}).get("transition") for x in rows if x.get("firstSemanticDivergence"))
    taker_presence_agree = sum((int(x.get("paperTakers") or 0) > 0) == (int(x.get("closedTakers") or 0) > 0) for x in rows)
    both_taker = [x for x in rows if x.get("paperFirstTakerMs") is not None and x.get("closedFirstTakerMs") is not None]
    dt = [int(x["closedFirstTakerMs"]) - int(x["paperFirstTakerMs"]) for x in both_taker]
    closed_pnl=[float((x.get("ledger") or {}).get("realizedPnlUsdt")) for x in rows if (x.get("ledger") or {}).get("realizedPnlUsdt") is not None]
    paper_pnl=[float((x.get("ledger") or {}).get("paperCapPnlUsdt")) for x in rows if (x.get("ledger") or {}).get("paperCapPnlUsdt") is not None]
    cum=0.0; peak=0.0; maxdd=0.0
    for v in closed_pnl:
        cum+=v; peak=max(peak,cum); maxdd=max(maxdd,peak-cum)
    summary = {
        "markets": len(rows),
        "paperPnlUsdt": sum(paper_pnl) if len(paper_pnl)==len(rows) else None,
        "closedLoopPnlUsdt": sum(closed_pnl) if len(closed_pnl)==len(rows) else None,
        "closedLoopPositiveMarkets": sum(v>0 for v in closed_pnl),
        "closedLoopNegativeMarkets": sum(v<0 for v in closed_pnl),
        "closedLoopMaxDrawdownUsdt": maxdd if closed_pnl else None,
        "takerPresenceAgreementRate": taker_presence_agree / len(rows) if rows else None,
        "bothHaveTakerMarkets": len(both_taker),
        "firstTakerDeltaMs": dt,
        "meanSemanticAgreementRate": sum(float(x["semanticAgreementRate"] or 0) for x in rows) / len(rows) if rows else None,
        "meanMakerPlacementDecisionAgreementRate": sum(float(x["makerPlacementDecisionAgreementRate"] or 0) for x in rows) / len(rows) if rows else None,
        "firstSemanticDivergenceTransitions": dict(trans),
        "marketsWithSemanticDivergence": sum(x.get("firstSemanticDivergence") is not None for x in rows),
    }
    report = {
        "version": "HFTBACKTEST_CAP100_CLOSED_LOOP_PILOT_AUDIT_V0",
        "config": vars(args),
        "summary": summary,
        "markets": rows,
        "boundary": "Frozen CAP100 strategy/public-input semantics. Performance-grade default is Stage4B HftBacktest Maker + Taker execution using Predict Execution Tape V1, measured latency and own-state closed-loop feedback. Instant Taker is diagnostic-only and requires explicit override.",
    }
    OUT.mkdir(parents=True, exist_ok=True)
    stage = "stage4b" if args.taker_mode == "hft" else "stage4a"
    path = OUT / f"cap100_closed_loop_pilot_i{args.start_index}_n{args.markets}_{stage}_splitclock_v0.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(path), "summary": summary}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
