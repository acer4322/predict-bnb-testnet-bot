from __future__ import annotations

import csv
import json
import math
import os
import sqlite3
import statistics
import time
from collections import deque
from pathlib import Path
from typing import Any

import httpx
from predict_bot.core import taker_fee
from predict_bot.target_wallet_official_v1 import API_BASE, resolved_winner

ROOT = Path(__file__).resolve().parents[1]
RECORDER_DB = ROOT / "data" / "strategy_target_compare_v1.db"
SETTLEMENT_DB = ROOT / "data" / "target_wallet_official_v1.db"
OUT = ROOT / "data" / "research"
REPORT = OUT / "8784_promoted_ownstate_v4_r1_forward_report.json"
MARKETS = OUT / "8784_promoted_ownstate_v4_r1_forward_markets.csv"
SETTLEMENT_CACHE = OUT / "8784_promoted_settlement_cache_v1.json"
VERSION = "UNIFIED_PROMOTED_OWNSTATE_V4_R1_FORWARD_PAPER"
FEE_BPS = 200
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def safe_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(str(raw))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def quantiles(xs: list[float]) -> dict[str, Any]:
    ys = sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not ys:
        return {"n": 0, "mean": None, "median": None, "p25": None, "p75": None, "sum": 0.0}
    def q(p: float) -> float:
        if len(ys) == 1:
            return ys[0]
        z = (len(ys)-1)*p
        lo, hi = math.floor(z), math.ceil(z)
        w = z-lo
        return ys[lo]*(1-w)+ys[hi]*w
    return {
        "n": len(ys), "mean": statistics.mean(ys), "median": statistics.median(ys),
        "p25": q(.25), "p75": q(.75), "sum": sum(ys),
    }




def api_key() -> str:
    value = str(os.environ.get("PREDICT_FUN_API_KEY") or "").strip()
    if value:
        return value
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            raw, _ = winreg.QueryValueEx(key, "PREDICT_FUN_API_KEY")
            return str(raw or "").strip()
    except Exception:
        return ""


def load_settlement_cache() -> dict[str, dict[str, Any]]:
    try:
        raw = json.loads(SETTLEMENT_CACHE.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def fetch_market_settlement(client: httpx.Client, market_id: int) -> dict[str, Any] | None:
    response = client.get(f"{API_BASE}/v1/markets/{int(market_id)}")
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        return None
    if payload.get("success") is False:
        return None
    market = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    if not isinstance(market, dict):
        return None
    winner = resolved_winner(market)
    variant = market.get("variantData") if isinstance(market.get("variantData"), dict) else {}
    return {
        "marketId": int(market_id),
        "title": str(market.get("title") or market.get("question") or ""),
        "winner": winner,
        "startPrice": variant.get("startPrice"),
        "endPrice": variant.get("endPrice"),
        "fetchedAtMs": int(time.time()*1000),
    }


def fifo_pair(fills: list[dict[str, Any]]) -> tuple[float, float]:
    queues = {"UP": deque(), "DOWN": deque()}
    for f in sorted(fills, key=lambda x: (int(x["filled_at_ms"]), str(x["fill_id"]))):
        queues[str(f["side"])].append([float(f["shares"]), float(f["price"])])
    paired = edge = 0.0
    while queues["UP"] and queues["DOWN"]:
        u, d = queues["UP"][0], queues["DOWN"][0]
        qty = min(u[0], d[0])
        paired += qty
        edge += qty * (1.0-u[1]-d[1])
        u[0] -= qty; d[0] -= qty
        if u[0] <= EPS: queues["UP"].popleft()
        if d[0] <= EPS: queues["DOWN"].popleft()
    return paired, edge


def main() -> int:
    recorder = ro(RECORDER_DB)
    settlement = ro(SETTLEMENT_DB)
    cache = load_settlement_cache()
    key = api_key()
    client = httpx.Client(timeout=httpx.Timeout(8.0, connect=2.0), trust_env=False, headers={"Accept":"application/json", **({"x-api-key": key} if key else {})})
    cache_dirty = False
    fallback_fetches = 0
    fallback_hits = 0
    fallback_errors = 0
    try:
        market_ids = {
            int(r[0]) for r in recorder.execute(
                "select distinct market_id from our_decisions where strategy_version=?",
                (VERSION,),
            )
        }
        market_ids |= {
            int(r[0]) for r in recorder.execute(
                "select distinct market_id from our_fills where strategy_version=?",
                (VERSION,),
            )
        }
        rows: list[dict[str, Any]] = []
        for market_id in sorted(market_ids):
            settle = settlement.execute(
                "select market_id,title,window_end_ms,status,winner,resolved_at_ms from target_markets where market_id=? and asset='BTC'",
                (market_id,),
            ).fetchone()
            winner = str(settle["winner"]) if settle is not None and settle["winner"] in {"UP","DOWN"} else None
            fills = [dict(r) for r in recorder.execute(
                "select * from our_fills where strategy_version=? and market_id=? order by filled_at_ms,fill_id",
                (VERSION, market_id),
            )]
            orders = [dict(r) for r in recorder.execute(
                "select * from our_orders where strategy_version=? and market_id=? order by placed_at_ms,order_id",
                (VERSION, market_id),
            )]
            decisions = [dict(r) for r in recorder.execute(
                "select * from our_decisions where strategy_version=? and market_id=? order by decision_ms,decision_id",
                (VERSION, market_id),
            )]
            window_end = int(settle["window_end_ms"]) if settle is not None and settle["window_end_ms"] is not None else None
            if window_end is None:
                for decision_row in decisions:
                    public_state = safe_json(decision_row.get("public_state_json"))
                    candidate = public_state.get("windowEndMs") or public_state.get("window_end_ms")
                    try:
                        if candidate is not None:
                            window_end = int(candidate); break
                    except Exception:
                        pass
            fallback = cache.get(str(market_id)) if isinstance(cache.get(str(market_id)), dict) else None
            if winner is None and fallback is not None and fallback.get("winner") in {"UP","DOWN"}:
                winner = str(fallback["winner"]); fallback_hits += 1
            if winner is None and window_end is not None and int(time.time()*1000) >= window_end + 2_000:
                try:
                    fetched = fetch_market_settlement(client, market_id); fallback_fetches += 1
                    if fetched is not None:
                        cache[str(market_id)] = fetched; cache_dirty = True; fallback = fetched
                        if fetched.get("winner") in {"UP","DOWN"}: winner = str(fetched["winner"]); fallback_hits += 1
                except Exception:
                    fallback_errors += 1
            maker = [f for f in fills if str(f["channel"]).upper()=="MAKER"]
            taker = [f for f in fills if str(f["channel"]).upper()=="TAKER"]
            up = sum(float(f["shares"]) for f in fills if f["side"]=="UP")
            down = sum(float(f["shares"]) for f in fills if f["side"]=="DOWN")
            maker_up = sum(float(f["shares"]) for f in maker if f["side"]=="UP")
            maker_down = sum(float(f["shares"]) for f in maker if f["side"]=="DOWN")
            cost = sum(float(f["shares"])*float(f["price"]) for f in fills)
            fees = sum(taker_fee(float(f["shares"]), float(f["price"]), FEE_BPS) for f in taker)
            pnl = (up if winner=="UP" else down if winner=="DOWN" else 0.0) - cost - fees if winner else None
            maker_paired, locked_edge = fifo_pair(maker)
            maker_gross = maker_up+maker_down
            paired_coverage = 2*min(maker_up,maker_down)/maker_gross if maker_gross>EPS else None
            action_counts = {"EXCURSION_RECOVERED":0,"UNRESOLVED_GUARD_ENTER":0,"TAKER_READINESS_LATCH":0,"PASSIVE_REPAIR_PRIORITY":0,"TAKER_INTERVENE":0,"MAKER_BURST":0}
            for d in decisions:
                payload = safe_json(d.get("payload_json"))
                for action in payload.get("actions", []) if isinstance(payload.get("actions"), list) else []:
                    if isinstance(action, dict) and str(action.get("action")) in action_counts:
                        action_counts[str(action["action"])] += 1
            open_cut = window_end-240_000 if window_end is not None else None
            row = {
                "marketId": market_id,
                "title": str(settle["title"] or "") if settle is not None else str((fallback or {}).get("title") or ""),
                "windowEndMs": window_end,
                "settled": int(winner is not None),
                "winner": winner,
                "decisions": len(decisions),
                "makerPlacements": len(orders),
                "makerFills": len(maker),
                "takerFills": len(taker),
                "makerPlacementsOpen60": sum(1 for o in orders if open_cut is not None and int(o["placed_at_ms"]) <= open_cut),
                "makerFillsOpen60": sum(1 for f in maker if open_cut is not None and int(f["filled_at_ms"]) <= open_cut),
                "grossShares": up+down,
                "makerGrossShares": maker_gross,
                "finalAbsNetShares": abs(up-down),
                "finalMakerAbsNetShares": abs(maker_up-maker_down),
                "makerPairedCoverage": paired_coverage,
                "pairedShares": maker_paired,
                "lockedEdgeUsdt": locked_edge,
                "costUsdt": cost,
                "takerFeesUsdt": fees,
                "pnlUsdt": pnl,
                "positive": int(pnl>0) if pnl is not None else None,
                **{k: int(v) for k,v in action_counts.items()},
            }
            rows.append(row)

        OUT.mkdir(parents=True, exist_ok=True)
        fields = list(rows[0].keys()) if rows else ["marketId"]
        with MARKETS.open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
        settled_rows = [r for r in rows if r["settled"]]
        traded_settled = [r for r in settled_rows if r["grossShares"]>EPS]
        pnl_rows = [float(r["pnlUsdt"]) for r in traded_settled if r["pnlUsdt"] is not None]
        rep = {
            "reportVersion": "8784_PROMOTED_OWNSTATE_V4_R1_FORWARD_REPORT_V1",
            "strategyVersion": VERSION,
            "researchOnly": True,
            "postHocSettlementOnly": True,
            "runtimeTargetDataAllowed": False,
            "marketsObserved": len(rows),
            "settledMarkets": len(settled_rows),
            "tradedSettledMarkets": len(traded_settled),
            "settlementSource": {"localDb": str(SETTLEMENT_DB), "fallbackApi": f"{API_BASE}/v1/markets/{{marketId}}", "fallbackFetches": fallback_fetches, "fallbackHits": fallback_hits, "fallbackErrors": fallback_errors, "cache": str(SETTLEMENT_CACHE)},
            "outcome": {
                "pnlUsdt": quantiles(pnl_rows),
                "positiveMarkets": sum(int(r["positive"] or 0) for r in traded_settled),
                "positiveMarketRate": (sum(int(r["positive"] or 0) for r in traded_settled)/len(traded_settled)) if traded_settled else None,
            },
            "activity": {
                "makerPlacements": quantiles([r["makerPlacements"] for r in rows]),
                "makerFills": quantiles([r["makerFills"] for r in rows]),
                "takerFills": quantiles([r["takerFills"] for r in rows]),
                "makerPlacementsOpen60": quantiles([r["makerPlacementsOpen60"] for r in rows if r["windowEndMs"] is not None]),
                "makerFillsOpen60": quantiles([r["makerFillsOpen60"] for r in rows if r["windowEndMs"] is not None]),
            },
            "inventory": {
                "finalAbsNetShares": quantiles([r["finalAbsNetShares"] for r in rows]),
                "finalMakerAbsNetShares": quantiles([r["finalMakerAbsNetShares"] for r in rows]),
                "makerPairedCoverage": quantiles([r["makerPairedCoverage"] for r in rows if r["makerPairedCoverage"] is not None]),
                "pairedShares": quantiles([r["pairedShares"] for r in rows]),
                "lockedEdgeUsdt": quantiles([r["lockedEdgeUsdt"] for r in rows]),
            },
            "coordination": {
                "passivePrioritySteps": sum(r["PASSIVE_REPAIR_PRIORITY"] for r in rows),
                "readinessLatches": sum(r["TAKER_READINESS_LATCH"] for r in rows),
                "takerInterventions": sum(r["TAKER_INTERVENE"] for r in rows),
                "unresolvedGuardEntries": sum(r["UNRESOLVED_GUARD_ENTER"] for r in rows),
                "makerBurstActions": sum(r["MAKER_BURST"] for r in rows),
            },
            "benchmarks": {
                "targetMakerOnlyPositiveMarketRateApprox": 0.525,
                "targetFullPositiveMarketRateApprox": 0.570,
                "interpretation": "benchmarks only; OUR is not forced to imitate Target if frozen forward performance is better",
            },
            "classification": (
                "OUR_EDGE_CANDIDATE" if len(traded_settled)>=25 and (sum(int(r["positive"] or 0) for r in traded_settled)/len(traded_settled))>0.57 and sum(pnl_rows)>0
                else "ACCUMULATING_FORWARD_EVIDENCE"
            ),
            "guards": [
                "Only R1 strategy_version rows are included.",
                "Winner is read post-hoc after settlement and is never available to 8784 runtime.",
                "No parameter selection or same-cohort retuning is performed here.",
                "Classification requires at least 25 traded settled markets before OUR_EDGE_CANDIDATE can appear.",
            ],
            "marketCsv": str(MARKETS),
        }
        REPORT.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    finally:
        if cache_dirty:
            OUT.mkdir(parents=True, exist_ok=True)
            SETTLEMENT_CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")
        client.close(); recorder.close(); settlement.close()


if __name__ == "__main__":
    raise SystemExit(main())
