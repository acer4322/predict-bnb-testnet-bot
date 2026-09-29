from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROLL_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
OUT_JSON = ROLL_DIR / "r2_execution_school_teacher_v0.json"
OUT_CSV = ROLL_DIR / "r2_execution_school_teacher_v0_rows.csv"
GRID = 0.01


def high_conf_lifecycles(con: sqlite3.Connection, market_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        """SELECT parent_id,market_id,target_side,target_price,placement_first_ms,placement_last_ms,
                  first_target_ms,last_target_ms,target_fill_count,target_filled_shares,resting_ms,post_action,
                  post_action_delay_ms,post_action_native_price,confidence
             FROM maker_book_inference_v21_parent_lifecycles
            WHERE market_id=? AND placement_supports_18=1 AND placement_coverage>=.85
              AND fill_allocation_coverage>=.70 AND confidence>=.75
              AND placement_first_ms IS NOT NULL AND last_target_ms IS NOT NULL
            ORDER BY placement_first_ms,parent_id""",
        (int(market_id),),
    )]


def target_fills(con: sqlite3.Connection, market_id: int) -> list[dict[str, Any]]:
    return [dict(r) for r in con.execute(
        """SELECT event_ms,role,side,quote_type,price,shares,order_hash
             FROM wallet_shadow_target_events
            WHERE asset='BTC' AND market_id=?
            ORDER BY event_ms,id""",
        (int(market_id),),
    )]


def target_inventory_before(fills: list[dict[str, Any]], cp: int) -> dict[str, float]:
    vals = {"makerUp": 0.0, "makerDown": 0.0, "takerUp": 0.0, "takerDown": 0.0}
    for f in fills:
        if int(f["event_ms"]) >= cp:
            break
        role = str(f["role"]).upper()
        side = str(f["side"]).upper()
        if role not in {"MAKER", "TAKER"} or side not in {"UP", "DOWN"}:
            continue
        vals[f"{role.lower()}{side.title()}"] += float(f["shares"] or 0.0)
    mu, md, tu, td = vals["makerUp"], vals["makerDown"], vals["takerUp"], vals["takerDown"]
    vals.update({
        "makerNet": mu - md,
        "makerAbsNet": abs(mu - md),
        "makerPairedCoverage": 2 * min(mu, md) / (mu + md) if mu + md > 1e-9 else 1.0,
        "combinedNet": (mu + tu) - (md + td),
    })
    return vals


def target_context(lifecycles: list[dict[str, Any]], cp: int, side: str) -> dict[str, Any]:
    opp = "DOWN" if side == "UP" else "UP"
    active_same = [x for x in lifecycles if str(x["target_side"]).upper() == side and int(x["placement_first_ms"]) <= cp < int(x["last_target_ms"])]
    active_opp = [x for x in lifecycles if str(x["target_side"]).upper() == opp and int(x["placement_first_ms"]) <= cp < int(x["last_target_ms"])]
    future = [x for x in lifecycles if cp < int(x["placement_first_ms"]) <= cp + 3000]
    future.sort(key=lambda x: (int(x["placement_first_ms"]), str(x["parent_id"])))
    nxt = future[0] if future else None
    keep_proxy = any(int(x["last_target_ms"]) > cp + 1000 for x in active_same) and not any(cp < int(x["placement_first_ms"]) <= cp + 1000 for x in lifecycles)
    out: dict[str, Any] = {
        "targetActiveSameCount": len(active_same),
        "targetActiveOppCount": len(active_opp),
        "targetKeepProxy1s": int(keep_proxy),
        "targetNextPlacementMs": int(nxt["placement_first_ms"]) if nxt else None,
        "targetNextPlacementDelayMs": int(nxt["placement_first_ms"]) - cp if nxt else None,
        "targetNextPlacementSide": str(nxt["target_side"]).upper() if nxt else None,
        "targetNextPlacementPrice": float(nxt["target_price"]) if nxt else None,
    }
    if nxt and active_same:
        cur = max(active_same, key=lambda x: int(x["placement_first_ms"]))
        if str(nxt["target_side"]).upper() == side:
            out["targetNextSamePriceDeltaTicks"] = abs(float(nxt["target_price"]) - float(cur["target_price"])) / GRID
        else:
            out["targetNextSamePriceDeltaTicks"] = None
    else:
        out["targetNextSamePriceDeltaTicks"] = None
    return out


def context_add_side(context: str) -> str | None:
    parts = str(context).split(":")
    if len(parts) >= 2 and parts[0] in {"BEFORE_ADD", "BEFORE_TAKER"} and parts[1] in {"UP", "DOWN"}:
        return parts[1]
    return None


def classify(row: dict[str, Any]) -> tuple[str, str]:
    context = str(row.get("context") or "")
    side = str(row.get("side") or "")
    action_side = context_add_side(context)
    remaining = float(row.get("remainingQty") or 0.0)
    soon3 = int(row.get("labelAnyFill3s") or 0) == 1 and int(row.get("censoredBefore3s") or 0) == 0
    soon5 = int(row.get("labelAnyFill5s") or 0) == 1 and int(row.get("censoredBefore5s") or 0) == 0
    target_keep = int(row.get("targetKeepProxy1s") or 0) == 1

    # Strongest practical mistake: student is about to add the same intent while an existing same-side
    # order is still live, and HFT hindsight says the pending order was about to fill. Target keep proxy
    # upgrades confidence but is not required for the physics-only candidate.
    if context.startswith("BEFORE_ADD:") and action_side == side and int(row.get("activeSameCount") or 0) >= 1 and remaining > EPS:
        if soon3 and target_keep:
            return "DUPLICATE_PENDING_INTENT_STRONG", "HFT pending order fills within 3s and Target lifecycle independently supports keep/no-new-order over next 1s"
        if soon3:
            return "DUPLICATE_PENDING_INTENT_HFT", "HFT pending order fills within 3s; new same-side intent risks double-counting unfinished execution"
        if soon5 and target_keep:
            return "DUPLICATE_PENDING_INTENT_TARGET_SUPPORTED", "HFT pending order fills within 5s and Target keep proxy supports continued resting"

    # Taker escalation while an intended-side passive order is still pending and about to fill.
    if context.startswith("BEFORE_TAKER:") and action_side == side and remaining > EPS and soon3:
        return "PREMATURE_TAKER_ESCALATION_CANDIDATE", "same-side passive Student order was still live and HFT hindsight fills it within 3s"

    if remaining > EPS and soon3 and target_keep:
        return "KEEP_RESTING_STRONG", "own HFT order fills within 3s and Target lifecycle supports keep proxy"
    if remaining > EPS and soon3:
        return "KEEP_RESTING_HFT", "own HFT order fills within 3s"
    if remaining > EPS and soon5 and target_keep:
        return "KEEP_RESTING_TARGET_SUPPORTED", "own HFT order fills within 5s and Target lifecycle supports keep proxy"

    # RELEASE teacher deliberately avoids any Student quote-offset threshold. Otherwise the model could
    # simply rediscover an artificial label rule. We require independent evidence instead: the Student
    # order does not fill within 5s, while Target has an active same-side lifecycle and creates a new
    # same-side high-confidence parent at a different price within 1s. This still does NOT prove private
    # cancel/replace; it only says the passive route deserves re-decision.
    cens5 = int(row.get("censoredBefore5s") or 0) == 1
    delta = row.get("targetNextSamePriceDeltaTicks")
    if remaining > EPS and not soon5 and not cens5 and int(row.get("targetActiveSameCount") or 0) > 0:
        try:
            if (row.get("targetNextPlacementDelayMs") is not None and int(row["targetNextPlacementDelayMs"]) <= 1000
                    and str(row.get("targetNextPlacementSide") or "") == side
                    and delta is not None and math.isfinite(float(delta)) and float(delta) >= 0.5):
                return "RELEASE_FOR_REDECISION_PROXY", "Student own order does not fill within 5s; Target active same-side lifecycle independently refreshes to a different-price parent within 1s"
        except Exception:
            pass
    return "UNRESOLVED_OBSERVATION", "insufficient strong evidence for an execution-management action label"


EPS = 1e-9


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--roll-dir", type=Path, default=ROLL_DIR)
    p.add_argument("--out-json", type=Path, default=OUT_JSON)
    p.add_argument("--out-csv", type=Path, default=OUT_CSV)
    args = p.parse_args()

    files = sorted(args.roll_dir.glob("r2_execution_school_market*_mid_risk_v0.json"))
    target = sqlite3.connect(TARGET_DB)
    target.row_factory = sqlite3.Row
    book = sqlite3.connect(BOOK_DB)
    book.row_factory = sqlite3.Row
    rows: list[dict[str, Any]] = []
    markets: list[dict[str, Any]] = []
    try:
        for path in files:
            rep = json.loads(path.read_text(encoding="utf-8"))
            mid = int(rep["marketId"])
            tf = target_fills(target, mid)
            lc = high_conf_lifecycles(book, mid)
            if not tf or not lc:
                continue
            start_n = len(rows)
            for src in rep.get("orderStateRows", []):
                row = dict(src)
                cp = int(row["checkpointMs"])
                side = str(row["side"]).upper()
                tc = target_context(lc, cp, side)
                ti = target_inventory_before(tf, cp)
                row.update(tc)
                # Target state is post-hoc grading context only and is explicitly prefixed.
                row.update({f"teacherTarget{k[0].upper()}{k[1:]}": v for k, v in ti.items()})
                label, reason = classify(row)
                row["teacherExecutionLabel"] = label
                row["teacherReason"] = reason
                row["teacherTargetDataRuntimeAllowed"] = False
                rows.append(row)
            markets.append({"marketId": mid, "rows": len(rows) - start_n, "targetFills": len(tf), "highConfidenceLifecycles": len(lc)})
    finally:
        target.close()
        book.close()

    rows.sort(key=lambda r: (int(r["marketId"]), int(r["checkpointMs"]), str(r["orderId"]), str(r["context"])))
    counts = Counter(str(r["teacherExecutionLabel"]) for r in rows)
    first_divergences = []
    for mid in sorted({int(r["marketId"]) for r in rows}):
        candidates = [r for r in rows if int(r["marketId"]) == mid and str(r["teacherExecutionLabel"]).startswith(("DUPLICATE_", "PREMATURE_"))]
        if candidates:
            r = min(candidates, key=lambda x: int(x["checkpointMs"]))
            first_divergences.append({
                "marketId": mid, "checkpointMs": int(r["checkpointMs"]), "label": r["teacherExecutionLabel"],
                "orderId": r["orderId"], "side": r["side"], "context": r["context"],
                "futureFillDelayMs": r.get("futureFirstFillDelayMs"), "targetKeepProxy1s": r.get("targetKeepProxy1s"),
            })

    payload = {
        "version": "R2_EXECUTION_SCHOOL_TEACHER_V0",
        "researchOnly": True,
        "student": "PRE_CAP100_R2",
        "dreamFillAllowed": False,
        "markets": len(markets),
        "rows": len(rows),
        "labelCounts": dict(counts),
        "firstExecutionDivergences": first_divergences,
        "marketCoverage": markets,
        "semantics": {
            "hftFutureFill": "post-episode physics label from Student's own HftBacktest order; never runtime input",
            "targetKeepProxy": "post-hoc teacher lifecycle proxy from high-confidence Target parent inference; not private cancel/replace ground truth",
            "targetActualFills": "real observed Target match/fill history, used only for post-hoc teacher inventory context",
            "runtimeBoundary": "No teacherTarget*, targetKeepProxy*, targetNextPlacement*, futureFill*, labelAnyFill*, eventualAdditionalFillShares, or teacherExecutionLabel field may be used as a runtime feature.",
        },
    }
    args.out_json.parent.mkdir(parents=True, exist_ok=True)
    args.out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if rows:
        fields = sorted({k for r in rows for k in r if k != "portfolio"}) + ["portfolio_json"]
        with args.out_csv.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            for r in rows:
                z = {k: v for k, v in r.items() if k != "portfolio"}
                z["portfolio_json"] = json.dumps(r.get("portfolio") or {}, ensure_ascii=False, separators=(",", ":"), allow_nan=True)
                w.writerow(z)
    print(json.dumps({"ok": True, "report": str(args.out_json), "rowsCsv": str(args.out_csv), "markets": len(markets), "rows": len(rows), "labelCounts": dict(counts), "firstDivergenceMarkets": len(first_divergences)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
