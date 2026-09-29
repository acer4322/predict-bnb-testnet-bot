from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import sys
import warnings
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import hftbacktest_r2_execution_graduation_candidate_v1 as candidate
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners
from tools.strategy_book_receipt_frontier_replay_v1 import ReceiptFrontierBookTailer
from tools.strategy_input_snapshot_replay_v1 import assess_strategy_input_snapshots, load_strategy_input_snapshots

D = ROOT / "data/research/execution_aware_fill_lifecycle_v0"
FREEZE = D / "r2_execution_graduation_candidate_freeze_v1.json"
REGISTRY = D / "r2_execution_graduation_exam_registry_v1.json"
EPS = 1e-9


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_candidate_hashes(freeze: dict[str, Any]) -> dict[str, Any]:
    bad = []
    for rel, expected in freeze["hashes"].items():
        path = ROOT / rel
        got = sha256(path) if path.exists() else None
        if got != expected:
            bad.append({"path": rel, "expected": expected, "got": got})
    return {"ok": not bad, "filesChecked": len(freeze["hashes"]), "bad": bad}


def archive_markets_after(freeze_ms: int) -> list[dict[str, Any]]:
    db = ROOT / "data/strategy_input_snapshot_archive_v1.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            """select market_id,count(*) n,min(sampled_at_ms) first_ms,max(sampled_at_ms) last_ms
                 from strategy_input_snapshots_v1
                where controller_version=? and sampled_at_ms>?
                group by market_id order by first_ms,market_id""",
            (mod.VERSION, int(freeze_ms)),
        ).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def receipt_quality(mid: int) -> dict[str, Any]:
    b = ReceiptFrontierBookTailer(mid, mod.VERSION, strict_hash=False)
    try:
        for snap in load_strategy_input_snapshots(mid, mod.VERSION):
            b.advance(mid, int(snap["sampledAtMs"]))
        return b.quality()
    finally:
        b.close()


def settlement(mid: int) -> str | None:
    w = winners([mid]).get(mid)
    if w in {"UP", "DOWN"}:
        return str(w)
    db = ROOT / "data/echtgeld_engine_v1.db"
    if not db.exists():
        return None
    con = sqlite3.connect(db)
    try:
        row = con.execute(
            "select winner from engine_settlements where market_id=? order by synced_at_ms desc limit 1",
            (int(mid),),
        ).fetchone()
    finally:
        con.close()
    if row and str(row[0]).upper() in {"UP", "DOWN"}:
        return str(row[0]).upper()
    return None


def quality_check(mid: int) -> dict[str, Any]:
    iq = assess_strategy_input_snapshots(mid, mod.VERSION)
    if not bool(iq.get("complete")):
        tail_proof = terminal_one_sided_tail_proof(mid, iq)
        if not bool(tail_proof.get("proven")):
            return {"eligible": False, "stage": "STRATEGY_INPUT", "input": iq, "terminalTailProof": tail_proof}
        iq = dict(iq)
        iq["rawStatus"] = iq.get("status")
        iq["status"] = "COMPLETE_FAIL_CLOSED_ONE_SIDED_TAIL_V1"
        iq["completeRuntimeTrace"] = True
        iq["terminalTailProof"] = tail_proof
    rq = receipt_quality(mid)
    if int(rq.get("hashMismatchCount") or 0) != 0 or float(rq.get("hashMatchRate") or 0.0) < 1.0:
        return {"eligible": False, "stage": "RECEIPT_FRONTIER", "input": iq, "receipt": rq}
    try:
        events, rows, meta = tape_v1.build_archive_events(mid, trade_offset="mid")
        tq = {
            "usable": len(events) > 0,
            "events": len(events),
            "rows": len(rows),
            "normalizedTrades": int(meta.get("normalizedTrades") or 0),
            "firstReceivedMs": meta.get("firstReceivedMs"),
            "lastReceivedMs": meta.get("lastReceivedMs"),
        }
    except Exception as exc:
        return {"eligible": False, "stage": "EXECUTION_TAPE", "input": iq, "receipt": rq, "error": repr(exc)}
    if not tq["usable"]:
        return {"eligible": False, "stage": "EXECUTION_TAPE", "input": iq, "receipt": rq, "tape": tq}
    w = settlement(mid)
    if w not in {"UP", "DOWN"}:
        return {"eligible": False, "stage": "SETTLEMENT_PENDING", "input": iq, "receipt": rq, "tape": tq}
    return {"eligible": True, "input": iq, "receipt": rq, "tape": tq, "settlementAvailable": True}


def terminal_one_sided_tail_proof(mid: int, iq: dict[str, Any]) -> dict[str, Any]:
    """Prove that an apparently missing tail is an intentional 8783 health-gate stop.

    This is data-quality only: no settlement, winner, Candidate output, or PnL is read.
    The consumed Strategy Brain trace is not extended or fabricated.
    """
    reasons = set(str(x) for x in (iq.get("reasons") or []))
    allowed = {"MISSING_TAIL_COVERAGE", "ROW_COUNT_LT_600"}
    if not reasons or not reasons.issubset(allowed):
        return {"proven": False, "reason": "NON_TAIL_QUALITY_REASON", "inputReasons": sorted(reasons)}
    if float(iq.get("firstSecondsLeft") or 0.0) < 285.0:
        return {"proven": False, "reason": "OPENING_NOT_CAPTURED"}
    if float(iq.get("maxConsumerGapMs") or 0.0) > 2000.0 or float(iq.get("maxSampleGapMs") or 0.0) > 2500.0:
        return {"proven": False, "reason": "INTERNAL_GAP_TOO_LARGE"}
    last_consumed = int(iq.get("lastSampledAtMs") or 0)
    db = ROOT / "data/public_source_snapshot_archive_v2.db"
    con = sqlite3.connect(db)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "select sampled_at_ms,seconds_left,snapshot_json from public_source_snapshots_v2 where market_id=? order by sampled_at_ms",
            (int(mid),),
        ).fetchall()
    finally:
        con.close()
    if not rows:
        return {"proven": False, "reason": "NO_PUBLIC_SOURCE_ARCHIVE"}
    source_min_sec = min(float(r["seconds_left"]) for r in rows if r["seconds_left"] is not None)
    source_max_sec = max(float(r["seconds_left"]) for r in rows if r["seconds_left"] is not None)
    source_gaps = [int(rows[i]["sampled_at_ms"]) - int(rows[i-1]["sampled_at_ms"]) for i in range(1, len(rows))]
    max_source_gap = max(source_gaps) if source_gaps else 0
    if source_min_sec > 1.0 or source_max_sec < 285.0 or max_source_gap > 2500:
        return {"proven": False, "reason": "PUBLIC_SOURCE_NOT_FULL_MARKET", "sourceMinSec": source_min_sec, "sourceMaxSec": source_max_sec, "maxSourceGapMs": max_source_gap}
    post = [r for r in rows if int(r["sampled_at_ms"]) > last_consumed]
    if len(post) < 10:
        return {"proven": False, "reason": "INSUFFICIENT_POST_GATE_ROWS", "postRows": len(post)}
    req = ("sampledAtMs","marketId","windowEndMs","secondsLeft","predictUpBid","predictUpAsk","predictDownBid","predictDownAsk")
    one_sided_patterns = {("predictUpAsk","predictDownBid"), ("predictUpBid","predictDownAsk")}
    one_sided = 0
    fully_good = 0
    other_bad: dict[str, int] = {}
    first_one_sided_sec = None
    for r in post:
        try:
            snap = json.loads(r["snapshot_json"] or "{}")
        except Exception:
            snap = {}
        missing = []
        for key in req:
            try:
                val = float(snap.get(key))
                ok = math.isfinite(val)
            except Exception:
                ok = False
            if not ok:
                missing.append(key)
        pat = tuple(missing)
        if not missing:
            fully_good += 1
        elif pat in one_sided_patterns:
            one_sided += 1
            if first_one_sided_sec is None:
                first_one_sided_sec = float(r["seconds_left"]) if r["seconds_left"] is not None else None
        else:
            k = "|".join(missing)
            other_bad[k] = other_bad.get(k, 0) + 1
    frac = one_sided / len(post)
    proven = frac >= 0.98 and fully_good <= 1 and not other_bad
    return {
        "proven": proven,
        "reason": "TERMINAL_ONE_SIDED_FAIL_CLOSED_PROVEN" if proven else "TAIL_NOT_EXPLAINED_BY_ONE_SIDED_GATE",
        "publicSourceRows": len(rows), "sourceMinSec": source_min_sec, "sourceMaxSec": source_max_sec,
        "maxSourceGapMs": max_source_gap, "postRows": len(post), "oneSidedRows": one_sided,
        "oneSidedFraction": frac, "fullyGoodPostRows": fully_good, "otherBadPatterns": other_bad,
        "firstOneSidedSecondsLeft": first_one_sided_sec,
    }


def running_score(reg: dict[str, Any]) -> dict[str, Any]:
    scored = [x for x in reg.get("examMarkets", []) if x.get("scoreStatus") == "SCORED"]
    pnls = [float(x["result"]["realizedPnl"]) for x in scored]
    wins_n = sum(x > EPS for x in pnls)
    losses_n = sum(x < -EPS for x in pnls)
    return {
        "scoredMarkets": len(scored),
        "totalRealizedPnl": sum(pnls),
        "wins": wins_n,
        "losses": losses_n,
        "winRate": wins_n / len(pnls) if pnls else None,
    }


def save(reg: dict[str, Any]) -> None:
    reg.setdefault("examMarkets", []).sort(key=lambda x: (int(x.get("firstSampledAtMs") or 0), int(x.get("marketId") or 0)))
    for i, row in enumerate(reg.get("examMarkets", []), 1):
        row["ordinal"] = i
    reg["runningScore"] = running_score(reg)
    n = int(reg.get("formalTargetMarkets") or 10)
    rs = reg["runningScore"]
    if int(rs["scoredMarkets"]) >= n:
        reg["formalExamComplete"] = True
        reg["formalPassed"] = bool(float(rs["totalRealizedPnl"]) > 0 and float(rs["winRate"]) >= 0.5)
    else:
        reg["formalExamComplete"] = False
        reg["formalPassed"] = None
    REGISTRY.write_text(json.dumps(reg, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")


def main() -> int:
    warnings.filterwarnings("ignore")
    freeze = json.loads(FREEZE.read_text(encoding="utf-8"))
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    hv = verify_candidate_hashes(freeze)
    if not hv["ok"]:
        print(json.dumps({"ok": False, "status": "CANDIDATE_HASH_MISMATCH", "hashAudit": hv}, ensure_ascii=False))
        return 2
    target = int(reg.get("formalTargetMarkets") or 10)
    existing = {int(x["marketId"]): x for x in reg.get("examMarkets", [])}
    exclusions = reg.setdefault("qualityExclusions", [])
    excluded_ids = {int(x["marketId"]) for x in exclusions}
    market_rows = archive_markets_after(int(freeze["freezeEpochMs"]))
    pending: list[dict[str, Any]] = []

    for idx, mr in enumerate(market_rows):
        mid = int(mr["market_id"])
        if mid in existing or mid in excluded_ids or len(existing) >= target:
            continue
        qc = quality_check(mid)
        if not qc.get("eligible"):
            # Do not permanently exclude the newest/current market merely because it has not finished yet.
            is_latest = idx == len(market_rows) - 1
            reasons = list((qc.get("input") or {}).get("reasons") or [])
            possibly_in_progress = is_latest and any(r in reasons for r in ["ROW_COUNT_LT_600", "MISSING_TAIL_COVERAGE"])
            if possibly_in_progress or qc.get("stage") == "SETTLEMENT_PENDING":
                pending.append({"marketId": mid, "quality": qc})
                continue
            exclusions.append({"marketId": mid, "permanent": True, "quality": qc, "winnerPnlBlind": True})
            excluded_ids.add(mid)
            continue

        # Lock qualification BEFORE invoking candidate.run_market(), which opens winner/PnL scoring.
        entry = {
            "ordinal": len(existing) + 1,
            "marketId": mid,
            "firstSampledAtMs": int(mr["first_ms"]),
            "qualificationLockedBeforeScoring": True,
            "strategyInput": qc["input"],
            "receiptFrontier": qc["receipt"],
            "executionTape": qc["tape"],
            "settlementAvailable": True,
            "scoreStatus": "QUALIFIED_NOT_YET_SCORED",
        }
        reg.setdefault("examMarkets", []).append(entry)
        existing[mid] = entry
        save(reg)

        result = candidate.run_market(mid)
        entry["scoreStatus"] = "SCORED"
        entry["resultArtifactVersion"] = result.get("version")
        entry["result"] = {
            "realizedPnl": result["actualExecution"]["realizedPnl"],
            "win": bool(float(result["actualExecution"]["realizedPnl"]) > EPS),
            "paperMakerOrders": result["paperReference"]["makerOrders"],
            "onlineMakerIntents": result["strategyRollout"]["makerIntents"],
            "intentCountExact": int(result["paperReference"]["makerOrders"]) == int(result["strategyRollout"]["makerIntents"]),
            "makerRealizationRate": result["actualExecution"]["makerRealizationRate"],
            "finalAbsTrackingError": result["actualExecution"]["finalAbsTrackingError"],
            "combinedFinalAbsNet": result["actualExecution"]["combinedFinalAbsNet"],
            "actionCounts": result["lifecycle"]["actionCounts"],
            "zeroFillTakerChildren": int(result["lifecycle"]["takerChildStateCounts"].get("TERMINAL_ZERO_FILL", 0)),
            "cancelPendingAtDataEnd": result["lifecycle"]["cancelPendingAtDataEnd"],
        }
        save(reg)
        print(json.dumps({"scored": mid, "ordinal": entry["ordinal"], "pnl": entry["result"]["realizedPnl"], "running": reg["runningScore"]}, ensure_ascii=False), flush=True)
        if len(existing) >= target:
            break

    save(reg)
    print(json.dumps({
        "ok": True,
        "status": "FORMAL_COMPLETE" if reg.get("formalExamComplete") else "WAITING_MORE_ELIGIBLE_MARKETS",
        "hashAudit": hv,
        "runningScore": reg["runningScore"],
        "formalPassed": reg.get("formalPassed"),
        "examMarketIds": [x["marketId"] for x in reg.get("examMarkets", [])],
        "qualityExclusions": [x["marketId"] for x in exclusions],
        "pending": [x["marketId"] for x in pending],
        "registry": str(REGISTRY),
    }, ensure_ascii=False, allow_nan=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
