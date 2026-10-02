from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1

EPS = 1e-9
GAP_MS = 6000


def _ms(row: np.void) -> int:
    return int(int(row["local_ts"]) // 1_000_000)


def _is_depth(ev: int) -> bool:
    return bool(ev & ex.DEPTH_EVENT) or bool(ev & ex.DEPTH_SNAPSHOT_EVENT)


def _is_trade(ev: int) -> bool:
    return bool(ev & ex.TRADE_EVENT)


def choose_gap(events: np.ndarray, gap_ms: int = GAP_MS) -> tuple[int, int, int]:
    trade_ms = sorted(_ms(r) for r in events if _is_trade(int(r["ev"])))
    depth_ms = sorted(_ms(r) for r in events if _is_depth(int(r["ev"])))
    if not trade_ms or not depth_ms:
        raise RuntimeError("events missing trade/depth data")
    lo = max(min(depth_ms) + 15_000, min(trade_ms))
    hi = min(max(depth_ms) - 15_000, max(trade_ms))
    if hi <= lo + gap_ms:
        raise RuntimeError("insufficient interior window")
    # Candidate starts at trade timestamps; choose the 6s interior window with most Predict matches.
    best = None
    j = 0
    for i, t in enumerate(trade_ms):
        if t < lo or t > hi - gap_ms:
            continue
        if j < i:
            j = i
        while j < len(trade_ms) and trade_ms[j] < t + gap_ms:
            j += 1
        n = j - i
        cand = (n, -t, t)
        if best is None or cand > best:
            best = cand
    if best is None:
        start = lo
        n = sum(start <= t < start + gap_ms for t in trade_ms)
    else:
        n, _, start = best
    return int(start), int(start + gap_ms), int(n)


def book_before(events: np.ndarray, at_ms: int) -> tuple[dict[float, float], dict[float, float]]:
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    for r in events:
        if _ms(r) >= at_ms:
            break
        ev = int(r["ev"])
        if not _is_depth(ev):
            continue
        p = float(r["px"]); q = max(0.0, float(r["qty"]))
        if ev & ex.BUY_EVENT:
            if q <= EPS: bids.pop(p, None)
            else: bids[p] = q
        elif ev & ex.SELL_EVENT:
            if q <= EPS: asks.pop(p, None)
            else: asks[p] = q
    return bids, asks


def _synthetic_depth_from_trade(row: np.void, *, side_flag: int, qty_after: float) -> np.ndarray:
    x = np.zeros(1, dtype=ex.event_dtype)
    x[0]["ev"] = int(ex.DEPTH_EVENT | side_flag)
    # Put the reconstructed depth mutation immediately after the trade event.
    x[0]["exch_ts"] = int(row["exch_ts"]) + 1000
    x[0]["local_ts"] = int(row["local_ts"]) + 1000
    x[0]["px"] = float(row["px"])
    x[0]["qty"] = max(0.0, float(qty_after))
    return x


def make_variants(events: np.ndarray, g0: int, g1: int) -> tuple[np.ndarray, np.ndarray]:
    # MASKED: remove L2 mutations in the gap but keep full Predict matches.
    keep = []
    for r in events:
        ms = _ms(r); ev = int(r["ev"])
        if g0 <= ms < g1 and _is_depth(ev):
            continue
        keep.append(r)
    masked = np.asarray(keep, dtype=ex.event_dtype)
    masked.sort(order=["local_ts", "exch_ts"])

    # RECONSTRUCTED: same masked feed, plus depth depletion inferred from each Predict match.
    bids, asks = book_before(events, g0)
    synth: list[np.ndarray] = []
    for r in events:
        ms = _ms(r); ev = int(r["ev"])
        if not (g0 <= ms < g1 and _is_trade(ev)):
            continue
        p = float(r["px"]); q = max(0.0, float(r["qty"]))
        if ev & ex.BUY_EVENT:  # aggressive BUY consumes ask liquidity
            old = float(asks.get(p, 0.0)); new = max(0.0, old - q); asks[p] = new
            synth.append(_synthetic_depth_from_trade(r, side_flag=ex.SELL_EVENT, qty_after=new))
        elif ev & ex.SELL_EVENT:  # aggressive SELL consumes bid liquidity
            old = float(bids.get(p, 0.0)); new = max(0.0, old - q); bids[p] = new
            synth.append(_synthetic_depth_from_trade(r, side_flag=ex.BUY_EVENT, qty_after=new))
    if synth:
        reconstructed = np.concatenate([masked] + synth)
        reconstructed.sort(order=["local_ts", "exch_ts"])
    else:
        reconstructed = masked.copy()
    return masked, reconstructed


def probe_prices(events: np.ndarray, g0: int) -> tuple[float, float]:
    bids, asks = book_before(events, g0)
    if not bids or not asks:
        raise RuntimeError("no two-sided book at gap start")
    up_bid = max(bids)
    native_ask = min(asks)
    down_bid = 1.0 - native_ask
    # R2-like passive placement one tick below the outcome bid.
    up_px = max(0.01, round(up_bid - 0.01, 2))
    down_px = max(0.01, round(down_bid - 0.01, 2))
    return up_px, down_px


def run_probe(events: np.ndarray, *, g0: int, g1: int, up_px: float, down_px: float) -> dict[str, Any]:
    bt = ex.new_bt(events, entry_latency_ms=1092, response_latency_ms=273, queue_model="risk")
    ex.initialize_bt(bt)
    try:
        ex.advance_to(bt, g0)
        rc_up = ex.submit_native(bt, 900001, "UP", up_px, 18.0)
        rc_down = ex.submit_native(bt, 900002, "DOWN", down_px, 18.0)
        ex.advance_to(bt, g1 + 3000)
        su = ex.order_snapshot(bt, 900001)
        sd = ex.order_snapshot(bt, 900002)
        return {
            "upPrice": up_px, "downPrice": down_px,
            "upSubmitRc": int(rc_up), "downSubmitRc": int(rc_down),
            "upFill": float(su.get("cumExecQty") or 0.0),
            "downFill": float(sd.get("cumExecQty") or 0.0),
            "upStatus": su.get("status"), "downStatus": sd.get("status"),
        }
    finally:
        bt.close()


def run_market(mid: int) -> dict[str, Any]:
    full, _, meta = tape_v1.build_archive_events(mid, trade_offset="mid")
    g0, g1, match_n = choose_gap(full)
    up_px, down_px = probe_prices(full, g0)
    masked, recon = make_variants(full, g0, g1)
    truth = run_probe(full, g0=g0, g1=g1, up_px=up_px, down_px=down_px)
    stale = run_probe(masked, g0=g0, g1=g1, up_px=up_px, down_px=down_px)
    rebuilt = run_probe(recon, g0=g0, g1=g1, up_px=up_px, down_px=down_px)

    def err(x: dict[str, Any]) -> float:
        return abs(float(x["upFill"]) - float(truth["upFill"])) + abs(float(x["downFill"]) - float(truth["downFill"]))

    return {
        "marketId": mid,
        "gapStartMs": g0, "gapEndMs": g1, "gapMs": g1-g0,
        "predictMatchesInGap": match_n,
        "nativeMeta": meta,
        "probe": {"upPrice": up_px, "downPrice": down_px},
        "truth": truth, "staleDepthPlusPredictMatches": stale, "predictTradeAdjustedDepth": rebuilt,
        "staleFillErrorShares": err(stale),
        "reconstructedFillErrorShares": err(rebuilt),
        "reconstructionImproved": err(rebuilt) + EPS < err(stale),
        "reconstructionTied": abs(err(rebuilt) - err(stale)) <= EPS,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market-ids", required=True)
    args = ap.parse_args()
    mids = [int(x) for x in args.market_ids.split(",") if x.strip()]
    rows=[]
    for i, mid in enumerate(mids,1):
        r=run_market(mid); rows.append(r)
        print(json.dumps({"progress":i,"marketId":mid,"matches":r["predictMatchesInGap"],"staleErr":r["staleFillErrorShares"],"reconErr":r["reconstructedFillErrorShares"],"improved":r["reconstructionImproved"]},ensure_ascii=False),flush=True)
    stale=sum(float(r["staleFillErrorShares"]) for r in rows)
    recon=sum(float(r["reconstructedFillErrorShares"]) for r in rows)
    out={
        "version":"PREDICT_GAP_RECONSTRUCTION_AUDIT_V1",
        "researchOnly":True,
        "markets":len(rows),
        "syntheticGapMs":GAP_MS,
        "definition":"Artificially remove native L2 depth events for 6s while retaining full Predict /v1/orders/matches-derived trades; reconstruct in-gap depth by decrementing the consumed price level for each normalized Predict match.",
        "aggregate":{"staleFillErrorShares":stale,"reconstructedFillErrorShares":recon,"improvedMarkets":sum(bool(r["reconstructionImproved"]) for r in rows),"tiedMarkets":sum(bool(r["reconstructionTied"]) for r in rows)},
        "rows":rows,
        "promotionRule":"Do not use reconstructed tapes for graduation merely because this diagnostic improves. Require repeatable reduction of fill error across chronological native-complete markets and label any accepted reconstruction PREDICT_RECONSTRUCTED_COMPLETE, distinct from native COMPLETE_FORWARD_V1.",
    }
    path=ROOT/'data/research/execution_aware_fill_lifecycle_v0/predict_gap_reconstruction_audit_v1.json'
    path.write_text(json.dumps(out,ensure_ascii=False,indent=2,allow_nan=True),encoding='utf-8')
    print(json.dumps({"ok":True,"report":str(path),"aggregate":out["aggregate"]},ensure_ascii=False))

if __name__=='__main__':
    main()
