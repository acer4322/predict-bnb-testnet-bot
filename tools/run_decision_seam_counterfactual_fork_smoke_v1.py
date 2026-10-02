from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

ROOT = Path.cwd().resolve() if (Path.cwd() / "tools").exists() else Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as feed
try:
    from tools.hft_execution_event_cache_v1 import ExecutionEventCache
except ImportError:
    from hft_execution_event_cache_v1 import ExecutionEventCache

ENTRY_LATENCY_MS = 1092
RESPONSE_LATENCY_MS = 273
QUEUE_MODEL = "risk"
TRADE_OFFSET = "mid"
ACTIVE_TTL_MS = 2000
HORIZONS_MS = [5000, 15000, 30000, 60000]
EPS = 1e-9


def finite(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except Exception:
        return default


def book(bt: Any) -> tuple[dict[float, float], dict[float, float]]:
    d = bt.depth(0)
    snap = d.snapshot()
    try:
        bids: dict[float, float] = {}
        asks: dict[float, float] = {}
        for row in snap:
            qty = float(row["qty"])
            if qty <= EPS:
                continue
            px = round(float(row["px"]), 12)
            ev = int(row["ev"])
            if ev & int(ex.BUY_EVENT):
                bids[px] = qty
            elif ev & int(ex.SELL_EVENT):
                asks[px] = qty
        return bids, asks
    finally:
        d.snapshot_free(snap)


def outcome_quotes(bt: Any) -> dict[str, dict[str, float]] | None:
    bids, asks = book(bt)
    if not bids or not asks:
        return None
    nb = max(bids)
    na = min(asks)
    q = {
        "UP": {"bid": nb, "ask": na},
        "DOWN": {"bid": 1.0 - na, "ask": 1.0 - nb},
    }
    if any(not (0 < q[s][k] < 1) for s in q for k in q[s]):
        return None
    return q


def ceil_cent(v: float) -> float:
    return math.ceil(v * 100.0 - 1e-12) / 100.0


def legal_qty(price: float, base_qty: float) -> float:
    # Project venue legality guard: at least 1 USDT notional. Lot size is 0.01.
    p = max(0.01, min(0.99, float(price)))
    min_qty = ceil_cent(1.0 / p)
    return max(ceil_cent(float(base_qty)), min_qty)


def candidate_actions(seam: dict[str, Any]) -> list[dict[str, Any]]:
    bid = finite(seam["receipt_strict_best_bid"])
    ask = finite(seam["receipt_strict_best_ask"])
    if not (0 < bid < ask < 1):
        raise RuntimeError(f"invalid strict receipt book bid={bid} ask={ask}")
    out_bid = {"UP": bid, "DOWN": 1.0 - ask}
    out_ask = {"UP": ask, "DOWN": 1.0 - bid}
    actions: list[dict[str, Any]] = [{"name": "WAIT", "kind": "WAIT"}]
    for qty in (2.0, 5.0, 10.0):
        for side in ("UP", "DOWN"):
            px = out_bid[side]
            actions.append({
                "name": f"PASSIVE_{side}_{int(qty)}",
                "kind": "PASSIVE",
                "side": side,
                "price": px,
                "baseQty": qty,
                "qty": legal_qty(px, qty),
                "ttlMs": 60000,
            })
    for side in ("UP", "DOWN"):
        ask_px = out_ask[side]
        max_px = min(0.99, ask_px + 0.02)
        actions.append({
            "name": f"ACTIVE_{side}_2",
            "kind": "ACTIVE",
            "side": side,
            "price": max_px,
            "referenceAsk": ask_px,
            "baseQty": 2.0,
            "qty": legal_qty(max_px, 2.0),
            "ttlMs": ACTIVE_TTL_MS,
        })
    return actions


def submit(bt: Any, oid: int, action: dict[str, Any]) -> int:
    side = str(action["side"])
    px = float(action["price"])
    qty = float(action["qty"])
    if action["kind"] == "PASSIVE":
        return ex.submit_native(bt, oid, side, px, qty)
    native_side, native_price = ex.native_order(side, px)
    if native_side == "BUY":
        return int(bt.submit_buy_order(0, oid, native_price, qty, ex.hbt.GTC, ex.LIMIT, False))
    return int(bt.submit_sell_order(0, oid, native_price, qty, ex.hbt.GTC, ex.LIMIT, False))


def economics(pre_up: float, pre_down: float, pre_cost: float, side: str, qty: float, cost_px: float) -> dict[str, float]:
    up = pre_up + (qty if side == "UP" else 0.0)
    down = pre_down + (qty if side == "DOWN" else 0.0)
    cost = pre_cost + qty * cost_px
    pnl_up = up - cost
    pnl_down = down - cost
    floor = min(pnl_up, pnl_down)
    upside = max(pnl_up, pnl_down)
    return {
        "postUpShares": up,
        "postDownShares": down,
        "postNetCost": cost,
        "postPnlIfUp": pnl_up,
        "postPnlIfDown": pnl_down,
        "postFloor": floor,
        "postUpside": upside,
    }


def run_action(seam: dict[str, Any], action: dict[str, Any], tape_dir: Path, feed_cache: dict[int, tuple[Any, dict[str, Any]]], event_store: ExecutionEventCache) -> dict[str, Any]:
    mid = int(seam["market_id"])
    decision_ms = int(seam["action_event_ms"])
    cached = feed_cache.get(mid)
    if cached is None:
        events, meta = event_store.get(mid)
        feed_cache[mid] = (events, meta)
    else:
        events, meta = cached
    bt = ex.new_bt(events, entry_latency_ms=ENTRY_LATENCY_MS, response_latency_ms=RESPONSE_LATENCY_MS, queue_model=QUEUE_MODEL)
    ex.initialize_bt(bt)
    try:
        replay_start = int(bt.current_timestamp // 1_000_000)
        if replay_start > decision_ms:
            return {"error": "decision_before_replay_start", "replayStartMs": replay_start, "decisionMs": decision_ms}
        if not ex.advance_to(bt, decision_ms):
            return {"error": "feed_exhausted_before_decision", "decisionMs": decision_ms}
        strict_q = outcome_quotes(bt)
        if strict_q is None:
            return {"error": "no_book_at_decision", "decisionMs": decision_ms}

        pre_up = finite(seam.get("pre_up_shares"))
        pre_down = finite(seam.get("pre_down_shares"))
        pre_cost = finite(seam.get("pre_net_cost"))
        pre_floor = finite(seam.get("pre_floor"))
        pre_upside = finite(seam.get("pre_upside"))

        if action["kind"] == "WAIT":
            return {
                "marketId": mid,
                "seamId": seam["seam_id"],
                "action": action,
                "decisionMs": decision_ms,
                "submitRc": None,
                "strictBookAtDecision": strict_q,
                "horizons": [
                    {
                        "horizonMs": h,
                        "filledQty": 0.0,
                        "deltaFloor": 0.0,
                        "deltaUpside": 0.0,
                        "incrementalMarkoutUsdt": 0.0,
                    }
                    for h in HORIZONS_MS
                ],
                "tape": meta,
            }

        oid = 1
        rc = submit(bt, oid, action)
        cancel_issued = False
        rows: list[dict[str, Any]] = []
        checkpoints = sorted(set(HORIZONS_MS + ([ACTIVE_TTL_MS] if action["kind"] == "ACTIVE" else [])))
        last_snap: dict[str, Any] | None = None
        for offset in checkpoints:
            target_ms = decision_ms + int(offset)
            ex.advance_to(bt, target_ms)
            snap = ex.order_snapshot(bt, oid)
            last_snap = snap
            if action["kind"] == "ACTIVE" and offset >= ACTIVE_TTL_MS and not cancel_issued:
                cur = bt.orders(0).get(oid)
                if cur is not None and int(cur.status) in {int(ex.NEW), int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
                    try:
                        bt.cancel(0, oid, False)
                        cancel_issued = True
                    except Exception:
                        pass
            if offset not in HORIZONS_MS:
                continue
            fill_qty = finite(snap.get("cumExecQty"))
            # Conservative economic accounting: charge all realized quantity at submitted limit.
            # This never awards price improvement to ACTIVE probes.
            cost_px = float(action["price"])
            post = economics(pre_up, pre_down, pre_cost, str(action["side"]), fill_qty, cost_px)
            q = outcome_quotes(bt)
            future_bid = q[str(action["side"])]["bid"] if q is not None else None
            markout = fill_qty * (float(future_bid) - cost_px) if future_bid is not None else None
            rows.append({
                "horizonMs": int(offset),
                "orderStatus": snap.get("status"),
                "filledQty": fill_qty,
                "leavesQty": snap.get("leavesQty"),
                "hftExecPriceDiagnostic": snap.get("execPrice"),
                "accountingCostPrice": cost_px,
                "futureBid": future_bid,
                "incrementalMarkoutUsdt": markout,
                "deltaFloor": post["postFloor"] - pre_floor,
                "deltaUpside": post["postUpside"] - pre_upside,
                **post,
            })
        return {
            "marketId": mid,
            "seamId": seam["seam_id"],
            "decisionMs": decision_ms,
            "action": action,
            "submitRc": rc,
            "cancelIssued": cancel_issued,
            "strictBookAtDecision": strict_q,
            "finalOrder": last_snap,
            "horizons": rows,
            "tape": meta,
        }
    finally:
        bt.close()


def validate_seam(seam: dict[str, Any]) -> list[str]:
    errs = []
    decision = int(seam["action_event_ms"])
    receipt_ms = int(seam["receipt_strict_book_received_ms"])
    public_ms = int(seam["public_sampled_at_ms"])
    if not receipt_ms < decision:
        errs.append("receipt_strict_not_past")
    if not public_ms < decision:
        errs.append("public_not_past")
    if finite(seam.get("seconds_left")) <= 180.0:
        errs.append("seconds_left_not_above_180")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seams", required=True)
    ap.add_argument("--tape-dir", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--event-cache-dir", help="persistent normalized HFT event cache directory; default project research cache")
    ns = ap.parse_args()
    seam_payload = json.loads(Path(ns.seams).read_text(encoding="utf-8"))
    seams = list(seam_payload.get("rows") or [])
    tape_dir = Path(ns.tape_dir)
    all_rows: list[dict[str, Any]] = []
    seam_audits = []
    feed_cache: dict[int, tuple[Any, dict[str, Any]]] = {}
    event_store = ExecutionEventCache(tape_dir=tape_dir, cache_dir=Path(ns.event_cache_dir) if ns.event_cache_dir else ROOT / "data" / "research" / "hft_execution_event_cache_v1", trade_offset=TRADE_OFFSET)
    for i, seam in enumerate(seams, 1):
        errs = validate_seam(seam)
        seam_audits.append({"marketId": int(seam["market_id"]), "seamId": seam["seam_id"], "errors": errs})
        if errs:
            continue
        actions = candidate_actions(seam)
        for action in actions:
            try:
                all_rows.append(run_action(seam, action, tape_dir, feed_cache, event_store))
            except Exception as exc:
                all_rows.append({
                    "marketId": int(seam["market_id"]),
                    "seamId": seam["seam_id"],
                    "action": action,
                    "error": f"{type(exc).__name__}: {exc}",
                })
        print(json.dumps({"progress": i, "total": len(seams), "marketId": int(seam["market_id"]), "actions": len(actions)}), flush=True)

    valid = [r for r in all_rows if "error" not in r]
    action_rows = [r for r in valid if r.get("action", {}).get("kind") != "WAIT"]
    h5 = [h for r in action_rows for h in r.get("horizons", []) if int(h["horizonMs"]) == 5000]
    h60 = [h for r in action_rows for h in r.get("horizons", []) if int(h["horizonMs"]) == 60000]
    payload = {
        "version": "BTC5M_DECISION_SEAM_COUNTERFACTUAL_FORK_SMOKE_V1",
        "researchOnly": True,
        "seamSourceVersion": seam_payload.get("version"),
        "execution": {
            "engine": "HftBacktest",
            "marketData": "PREDICT_EXECUTION_TAPE_V1",
            "trueTradeSource": "raw Predict /v1/orders/matches from Execution Tape V1",
            "queueModel": QUEUE_MODEL,
            "entryLatencyMs": ENTRY_LATENCY_MS,
            "responseLatencyMs": RESPONSE_LATENCY_MS,
            "tradeOffset": TRADE_OFFSET,
            "partialFill": True,
            "dreamFillAllowed": False,
            "negativeDepthAsSyntheticTrade": False,
        },
        "candidateBoundary": {
            "runtimeInputs": ["strict-past receipt book", "strict-past portfolio geometry", "decision timestamp"],
            "currentTargetActionUsedForCandidateGeneration": False,
            "winnerUsed": False,
            "activeTtlMs": ACTIVE_TTL_MS,
            "horizonsMs": HORIZONS_MS,
            "minimumNotionalUsdt": 1.0,
            "economicAccounting": "realized HFT fill quantity, submitted-limit cost; ACTIVE price improvement deliberately not credited",
        },
        "seams": len(seams),
        "seamAudits": seam_audits,
        "runs": len(all_rows),
        "feedCache": {"marketsLoadedIntoMemory": len(feed_cache), "tapeParseOncePerMarket": True, "persistentEventCache": event_store.stats, "persistentCacheDir": str(event_store.cache_dir)},
        "validRuns": len(valid),
        "errors": [r for r in all_rows if "error" in r],
        "summary": {
            "actionRuns": len(action_rows),
            "filledBy5s": sum(finite(h.get("filledQty")) > EPS for h in h5),
            "filledBy60s": sum(finite(h.get("filledQty")) > EPS for h in h60),
            "positiveMarkout5s": sum((h.get("incrementalMarkoutUsdt") is not None and finite(h.get("incrementalMarkoutUsdt")) > EPS) for h in h5),
            "positiveFloorDelta60s": sum(finite(h.get("deltaFloor")) > EPS for h in h60),
            "positiveUpsideDelta60s": sum(finite(h.get("deltaUpside")) > EPS for h in h60),
        },
        "rows": all_rows,
        "promotionGate": {
            "allSeamsStrictPast": all(not x["errors"] for x in seam_audits),
            "zeroRunnerErrors": not any("error" in r for r in all_rows),
            "hasActualHftFills": any(finite(h.get("filledQty")) > EPS for h in h60),
            "noDreamFill": True,
            "pass": all(not x["errors"] for x in seam_audits) and not any("error" in r for r in all_rows) and any(finite(h.get("filledQty")) > EPS for h in h60),
        },
    }
    out = (Path(__import__("os").environ["BTC5M_LAN_RESULT_DIR"]) / "result.json") if str(ns.output).upper()=="AUTO" else Path(ns.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(out), "summary": payload["summary"], "promotionGate": payload["promotionGate"]}, indent=2, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
