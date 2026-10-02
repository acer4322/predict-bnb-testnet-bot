from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hftbacktest_r2_execution_school_v0 import run_market, load_reference_paper

D = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
COHORT = D / "pair_completion_canonical_cohort_v1.json"
PAIR = D / "pair_completion_tradeoff_curriculum_canonical_v4.jsonl"
SEQ = D / "pair_completion_sequential_curriculum_v1.jsonl"
OUT = D / "theory_to_execution_trap_exam_v0.jsonl"
REPORT = D / "theory_to_execution_trap_exam_v0_report.json"
EPS = 1e-9
ACTIVE = {"NEW", "PARTIALLY_FILLED"}


def pair_map() -> dict[int, dict[str, Any]]:
    if not PAIR.exists():
        return {}
    return {int(r["marketId"]): r for r in (json.loads(x) for x in PAIR.read_text(encoding="utf-8").splitlines() if x.strip())}


def seq_map() -> dict[int, dict[int, dict[str, Any]]]:
    out: dict[int, dict[int, dict[str, Any]]] = {}
    if not SEQ.exists():
        return out
    for line in SEQ.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        out.setdefault(int(r["marketId"]), {})[int(r["delayMs"])] = r
    return out


def first_unique(rows: list[dict[str, Any]], keyfn, limit: int = 8) -> list[dict[str, Any]]:
    seen = set(); out = []
    for r in rows:
        k = keyfn(r)
        if k in seen:
            continue
        seen.add(k); out.append(r)
        if len(out) >= limit:
            break
    return out


def analyze(mid: int, pmap: dict[int, dict[str, Any]], smap: dict[int, dict[int, dict[str, Any]]]) -> dict[str, Any]:
    report = run_market(mid)
    ref = load_reference_paper(mid)
    states = list(report.get("orderStateRows") or [])
    fills = list(report.get("makerFillEvents") or [])
    attempts = list(report.get("takerAttempts") or [])
    meta = report.get("orderMeta") or {}

    # A theory action asks to add again while a same-side venue-acknowledged child is still alive.
    dup = []
    for s in states:
        ctx = str(s.get("context") or "")
        if not ctx.startswith("BEFORE_ADD:"):
            continue
        parts = ctx.split(":", 2)
        attempted_side = parts[1] if len(parts) > 1 else ""
        if str(s.get("side")) != attempted_side:
            continue
        if str(s.get("hftStatus")) in ACTIVE and float(s.get("remainingQty") or 0.0) > EPS:
            dup.append({
                "atMs": int(s["checkpointMs"]), "decisionId": s.get("decisionId"),
                "attemptedSide": attempted_side, "attemptReason": parts[2] if len(parts) > 2 else None,
                "existingOrderId": s.get("orderId"), "existingAgeMs": s.get("orderAgeMs"),
                "existingStatus": s.get("hftStatus"), "remainingQty": s.get("remainingQty"),
                "quoteOffsetTicks": s.get("quoteOffsetTicks"),
            })
    dup = first_unique(dup, lambda x: (x.get("decisionId"), x.get("existingOrderId")), 12)

    stale = []
    for s in states:
        if str(s.get("context")) != "POST_DECISION_ACTIVE":
            continue
        if str(s.get("hftStatus")) not in ACTIVE or float(s.get("remainingQty") or 0.0) <= EPS:
            continue
        age = float(s.get("orderAgeMs") or 0.0)
        if age < 5000:
            continue
        stale.append({
            "atMs": int(s["checkpointMs"]), "orderId": s.get("orderId"), "side": s.get("side"),
            "ageMs": age, "status": s.get("hftStatus"), "remainingQty": s.get("remainingQty"),
            "quoteOffsetTicks": s.get("quoteOffsetTicks"),
        })
    stale = first_unique(stale, lambda x: x.get("orderId"), 12)

    partial = []
    for s in states:
        if str(s.get("hftStatus")) == "PARTIALLY_FILLED" and float(s.get("remainingQty") or 0.0) > EPS:
            partial.append({
                "atMs": int(s["checkpointMs"]), "orderId": s.get("orderId"), "side": s.get("side"),
                "ageMs": s.get("orderAgeMs"), "cumExecQty": s.get("cumExecQty"),
                "remainingQty": s.get("remainingQty"), "quoteOffsetTicks": s.get("quoteOffsetTicks"),
            })
    partial = first_unique(partial, lambda x: x.get("orderId"), 12)

    taker_not_complete = [{
        "atMs": int(a.get("atMs") or 0), "side": a.get("side"), "result": a.get("result"),
        "status": a.get("status"), "submitRc": a.get("submitRc"), "decisionId": a.get("decisionId"),
    } for a in attempts if str(a.get("result")) != "FILLED"]

    paper_maker_shares = float(sum(float(x.get("shares") or 0.0) for x in ref["orders"]))
    paper_taker_shares = float(sum(float(x.get("shares") or 0.0) for x in ref["takers"]))
    realized_maker_shares = float(report["studentRollout"].get("makerFilledShares") or 0.0)
    finalp = report["studentRollout"].get("finalPortfolio") or {}
    theory_reasons = sorted({str(x.get("reason")) for x in meta.values() if x.get("reason")})

    pr = pmap.get(mid) or {}
    seq = smap.get(mid) or {}
    persistent8 = seq.get(8000)
    traps: list[str] = []
    if dup: traps.append("DUPLICATE_THEORY_ADD_WHILE_CHILD_LIVE")
    if stale: traps.append("STALE_PASSIVE_CHILD_GE_5S")
    if partial: traps.append("PARTIAL_FILL_LIFECYCLE")
    if taker_not_complete: traps.append("TAKER_ATTEMPT_NOT_COMPLETED")
    if pr.get("hasIntervention"): traps.append("ASYMMETRIC_PAIR_COMPLETION")
    if bool(pr.get("resolvedDuringCancel")): traps.append("CANCEL_RACE_RESOLVED_DURING_CANCEL")
    if persistent8 and persistent8.get("hasCandidate"):
        traps.append("ASYMMETRY_PERSISTS_TO_8S_EXAM")
    if int(report["studentRollout"].get("activeOrdersAtEnd") or 0) > 0:
        traps.append("ACTIVE_CHILD_REMAINS_AT_MARKET_END")

    # This is diagnostic severity, not a reward label and never uses winner/PnL.
    severity = len(traps)
    if dup: severity += 1
    if partial: severity += 1
    if taker_not_complete: severity += 2
    if bool(pr.get("resolvedDuringCancel")): severity += 1

    return {
        "version": "THEORY_TO_EXECUTION_TRAP_EXAM_V0",
        "researchOnly": True,
        "liveTradingChanges": False,
        "marketId": mid,
        "theoryStudent": report.get("student"),
        "theoryQuestionSource": "R2 own-state theory controller decisions replayed under canonical HftBacktest execution physics",
        "theoryActionReasons": theory_reasons,
        "paperTheoryIntent": {
            "makerOrders": len(ref["orders"]), "makerShares": paper_maker_shares,
            "takerFills": len(ref["takers"]), "takerShares": paper_taker_shares,
        },
        "hftRealized": {
            "makerPlacements": int(report["studentRollout"].get("makerPlacements") or 0),
            "makerFillEvents": len(fills), "makerFilledShares": realized_maker_shares,
            "takerAttempts": len(attempts), "takerFills": int(report["studentRollout"].get("takerFills") or 0),
            "activeOrdersAtEnd": int(report["studentRollout"].get("activeOrdersAtEnd") or 0),
            "combinedAbsNetAtEnd": float(finalp.get("combined_abs_net") or 0.0),
            "combinedPairedCoverageAtEnd": float(finalp.get("combined_paired_coverage") or 0.0),
        },
        "pairCompletionTeacherAtFirstAsymmetry": pr.get("paretoLabel"),
        "pairResolvedDuringCancel": bool(pr.get("resolvedDuringCancel")),
        "persistent8sTeacher": persistent8.get("paretoLabel") if persistent8 else None,
        "trapCategories": traps,
        "trapSeverityDiagnostic": int(severity),
        "events": {
            "duplicateAddWhileLiveChild": dup,
            "stalePassiveChild": stale,
            "partialFill": partial,
            "takerNotCompleted": taker_not_complete[:12],
        },
        "examBoundary": "Theory answer is not relabeled wrong. The exam starts after a theory-consistent action meets real venue lifecycle. Pass/fail must judge execution handling, not whether the original strategy intent is changed.",
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    cats: dict[str, int] = {}
    reasons: dict[str, int] = {}
    for r in rows:
        for c in r.get("trapCategories") or []:
            cats[c] = cats.get(c, 0) + 1
        for x in r.get("theoryActionReasons") or []:
            reasons[x] = reasons.get(x, 0) + 1
    hard = sorted(rows, key=lambda r: (-int(r.get("trapSeverityDiagnostic") or 0), int(r["marketId"])))
    return {
        "version": "THEORY_TO_EXECUTION_TRAP_EXAM_V0_REPORT",
        "researchOnly": True,
        "liveTradingChanges": False,
        "markets": len(rows),
        "trapCategoryCounts": cats,
        "theoryReasonCoverage": reasons,
        "marketsWith3PlusTrapCategories": sum(len(r.get("trapCategories") or []) >= 3 for r in rows),
        "marketsWithDuplicateLiveChildTrap": sum("DUPLICATE_THEORY_ADD_WHILE_CHILD_LIVE" in (r.get("trapCategories") or []) for r in rows),
        "marketsWithPartialFillTrap": sum("PARTIAL_FILL_LIFECYCLE" in (r.get("trapCategories") or []) for r in rows),
        "marketsWithTakerCompletionTrap": sum("TAKER_ATTEMPT_NOT_COMPLETED" in (r.get("trapCategories") or []) for r in rows),
        "hardestDevelopmentExamples": [{
            "marketId": r["marketId"], "severity": r.get("trapSeverityDiagnostic"),
            "traps": r.get("trapCategories"), "theoryReasons": r.get("theoryActionReasons"),
        } for r in hard[:20]],
        "antiLeakage": "These opened canonical markets are development/stress curriculum only. Official theory-trap graduation must use future untouched Execution Tape markets after the candidate is frozen.",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--reset", action="store_true")
    args = ap.parse_args()
    mids = [int(x) for x in json.loads(COHORT.read_text(encoding="utf-8"))["marketIds"]]
    if args.reset and OUT.exists(): OUT.unlink()
    done = set()
    if OUT.exists():
        for line in OUT.read_text(encoding="utf-8").splitlines():
            if line.strip(): done.add(int(json.loads(line)["marketId"]))
    todo = [m for m in mids if m not in done][:max(1, args.limit)]
    pmap = pair_map(); smap = seq_map()
    new = []
    with OUT.open("a", encoding="utf-8") as fh:
        for i, mid in enumerate(todo, 1):
            r = analyze(mid, pmap, smap); new.append(r)
            fh.write(json.dumps(r, ensure_ascii=False, allow_nan=True) + "\n"); fh.flush()
            print(json.dumps({
                "progress": i, "marketId": mid, "severity": r["trapSeverityDiagnostic"],
                "traps": r["trapCategories"], "reasons": r["theoryActionReasons"],
                "finalAbsNet": r["hftRealized"]["combinedAbsNetAtEnd"],
            }, ensure_ascii=False), flush=True)
    rows = [json.loads(x) for x in OUT.read_text(encoding="utf-8").splitlines() if x.strip()]
    rep = summarize(rows)
    REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "processed": len(new), "done": len(rows), "total": len(mids), "report": str(REPORT), "summary": rep}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
