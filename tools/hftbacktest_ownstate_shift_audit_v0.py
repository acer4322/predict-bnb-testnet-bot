from __future__ import annotations

import argparse
import csv
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex  # noqa: E402
from tools import hftbacktest_true_match_calibration_v0 as tm  # noqa: E402

STRATEGY_DB = ROOT / "data" / "strategy_target_compare_v1.db"
MATCHED_CSV = ROOT / "data" / "research" / "8784_r2_vs_8786_cap100_fresh_v1_markets.csv"
OUT_DIR = ROOT / "data" / "research" / "hftbacktest_execution_shift_v0"

MODULES = {
    "R1": "src.predict_bot.unified_controller_paper_v1",
    "R2": "src.predict_bot.unified_controller_paper_v2",
    "CAP100": "src.predict_bot.unified_controller_cap100_shadow_v1",
}
VERSIONS = ex.VERSIONS


def jload(value: Any) -> Any:
    if value is None or value == "":
        return {}
    try:
        return json.loads(value)
    except Exception:
        return {}


def load_module(label: str):
    import importlib
    return importlib.import_module(MODULES[label])


def load_market_ids(label: str, matched126: bool, market_id: int | None, max_markets: int | None, start_index: int = 0) -> list[int]:
    con = sqlite3.connect(STRATEGY_DB)
    try:
        ids = [int(r[0]) for r in con.execute(
            "SELECT DISTINCT market_id FROM our_decisions WHERE strategy_version=? ORDER BY market_id",
            (VERSIONS[label],),
        )]
    finally:
        con.close()
    if market_id is not None:
        ids = [m for m in ids if m == int(market_id)]
    if matched126:
        keep: set[int] = set()
        with MATCHED_CSV.open(encoding="utf-8-sig", newline="") as f:
            for i, row in enumerate(csv.DictReader(f)):
                if i >= 126: break
                keep.add(int(row["marketId"]))
        ids = [m for m in ids if m in keep]
    if start_index:
        ids = ids[max(0, int(start_index)):]
    if max_markets is not None:
        ids = ids[: max(0, int(max_markets))]
    return ids


def hft_book_dict(bt: Any) -> dict[str, dict[float, float]]:
    d = bt.depth(0)
    arr = d.snapshot()
    try:
        bids: dict[float, float] = {}
        asks: dict[float, float] = {}
        for row in arr:
            ev = int(row["ev"])
            px = round(float(row["px"]), 12)
            qty = float(row["qty"])
            if qty <= 1e-12:
                continue
            if ev & int(ex.BUY_EVENT):
                bids[px] = qty
            elif ev & int(ex.SELL_EVENT):
                asks[px] = qty
        return {"bids": bids, "asks": asks}
    finally:
        d.snapshot_free(arr)


def outcome_price_from_native(side: str, native_px: float | None, fallback: float) -> float:
    if native_px is None or not math.isfinite(float(native_px)):
        return float(fallback)
    if str(side).upper() == "UP":
        return float(native_px)
    return 1.0 - float(native_px)


def load_market_rows(con: sqlite3.Connection, version: str, market_id: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    decisions = [dict(r) for r in con.execute(
        """SELECT decision_id,decision_ms,source_snapshot_ms,seconds_left,phase,desired_portfolio_action,
                  execution_choice,side,size,primary_reason,portfolio_state_json,economics_state_json,
                  arbitration_state_json,public_state_json,payload_json
             FROM our_decisions WHERE strategy_version=? AND market_id=? ORDER BY decision_ms,decision_id""",
        (version, int(market_id)),
    )]
    orders = [dict(r) for r in con.execute(
        """SELECT order_id,side,price,shares,placed_at_ms,status,filled_at_ms,cancelled_at_ms
             FROM our_orders WHERE strategy_version=? AND market_id=? AND channel='MAKER'
             ORDER BY placed_at_ms,order_id""",
        (version, int(market_id)),
    )]
    takers = [dict(r) for r in con.execute(
        """SELECT fill_id,side,price,shares,filled_at_ms,decision_id
             FROM our_fills WHERE strategy_version=? AND market_id=? AND channel='TAKER'
             ORDER BY filled_at_ms,fill_id""",
        (version, int(market_id)),
    )]
    return decisions, orders, takers


def audit_market(
    label: str,
    market_id: int,
    *,
    entry_latency_ms: int,
    response_latency_ms: int,
    queue_model: str,
    p1: Any,
    p3: Any,
    mod: Any,
    true_matches: bool,
    trade_offset: str,
) -> dict[str, Any]:
    version = VERSIONS[label]
    book_con = sqlite3.connect(ex.BOOK_DB); book_con.row_factory = sqlite3.Row
    strat_con = sqlite3.connect(STRATEGY_DB); strat_con.row_factory = sqlite3.Row
    try:
        if true_matches:
            events, update_times, feed_meta = tm.depth_plus_true_trades(market_id, trade_offset=trade_offset)
        else:
            events, update_times, feed_meta = ex.build_market_events(book_con, market_id, depletion_as_trade=False)
        decisions, orders, takers = load_market_rows(strat_con, version, market_id)
    finally:
        book_con.close(); strat_con.close()
    if not decisions:
        raise RuntimeError("no decisions")

    bt = ex.new_bt(events, entry_latency_ms=entry_latency_ms, response_latency_ms=response_latency_ms, queue_model=queue_model)
    ex.initialize_bt(bt)
    inv = mod.Inventory()

    by_decision: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for d in decisions:
        by_decision[int(d["decision_ms"])].append(d)
    by_submit: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_terminal: dict[int, list[dict[str, Any]]] = defaultdict(list)
    numbered: dict[int, dict[str, Any]] = {}
    for num, o0 in enumerate(orders, start=1):
        o = dict(o0); o["num"] = num
        numbered[num] = o
        by_submit[int(o["placed_at_ms"])].append(o)
        # A paper FILLED timestamp is not a real cancellation boundary. If HftBacktest has not
        # filled the order by then, it must keep resting. Only explicit paper cancellation is
        # retained in this fixed-placement Phase 1.5 approximation.
        if str(o.get("status")) == "FILLED":
            o["paperTerminalMs"] = int(o.get("filled_at_ms") or feed_meta["lastReceivedMs"])
        else:
            terminal = o.get("cancelled_at_ms")
            if terminal is None:
                terminal = feed_meta["lastReceivedMs"]
            o["paperTerminalMs"] = int(terminal)
            by_terminal[int(terminal)].append(o)
    by_taker: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for t in takers:
        by_taker[int(t["filled_at_ms"])].append(t)

    timeline = sorted(set(update_times) | set(by_decision) | set(by_submit) | set(by_terminal) | set(by_taker))
    submitted: set[int] = set()
    prev_exec: dict[int, float] = defaultdict(float)
    fill_events: list[dict[str, Any]] = []
    out_decisions: list[dict[str, Any]] = []

    def collect_hft_fills(at_ms: int) -> None:
        for num in sorted(submitted):
            o = numbered[num]
            snap = ex.order_snapshot(bt, num)
            q = float(snap.get("cumExecQty") or 0.0)
            old = float(prev_exec[num])
            if q <= old + 1e-9:
                continue
            delta = q - old
            px = outcome_price_from_native(str(o["side"]), snap.get("execPrice"), float(o["price"]))
            inv.apply({"event_ms": int(at_ms), "role": "MAKER", "side": str(o["side"]), "price": float(px), "shares": float(delta)})
            fill_events.append({"atMs": int(at_ms), "orderNum": num, "side": o["side"], "deltaShares": delta, "price": px, "hftStatus": snap.get("status")})
            prev_exec[num] = q

    try:
        feed_exhausted = False
        for t in timeline:
            if (not feed_exhausted) and int(bt.current_timestamp) <= int(t) * 1_000_000:
                if not ex.advance_to(bt, t):
                    feed_exhausted = True
            collect_hft_fills(t)

            # Runtime decision sees book/fills first. Same-timestamp Taker fill and new Maker placement happen after the decision.
            for d in by_decision.get(t, []):
                feat = inv.features(int(t))
                cn = float(feat.pop("_combined_net"))
                dom = "UP" if cn > 1e-9 else "DOWN" if cn < -1e-9 else None
                book = hft_book_dict(bt)
                bf = mod.outcome_book(book, dom)
                if bf is None:
                    continue
                raw = {"seconds_left": float(d["seconds_left"]) if d.get("seconds_left") is not None else math.nan, **feat, **bf}
                hp1 = float(p1(raw)); hp3 = float(p3(raw))
                paper_port = jload(d.get("portfolio_state_json"))
                payload = jload(d.get("payload_json"))
                paper_models = payload.get("models") if isinstance(payload, dict) and isinstance(payload.get("models"), dict) else {}
                pp1 = paper_models.get("pTaker1s")
                pp3 = paper_models.get("pTaker3s")
                try: pp1 = float(pp1) if pp1 is not None else None
                except Exception: pp1 = None
                try: pp3 = float(pp3) if pp3 is not None else None
                except Exception: pp3 = None
                def fnum(obj: dict[str, Any], key: str) -> float | None:
                    try:
                        v = obj.get(key)
                        return float(v) if v is not None and math.isfinite(float(v)) else None
                    except Exception:
                        return None
                pmg = fnum(paper_port, "maker_gross")
                pnet = fnum(paper_port, "maker_net")
                ppc = fnum(paper_port, "maker_paired_coverage")
                out_decisions.append({
                    "decisionMs": int(t),
                    "secondsLeft": d.get("seconds_left"),
                    "phase": d.get("phase"),
                    "desired": d.get("desired_portfolio_action"),
                    "execution": d.get("execution_choice"),
                    "primaryReason": d.get("primary_reason"),
                    "paperMakerGross": pmg,
                    "hftMakerGross": float(raw["maker_gross"]),
                    "paperMakerNet": pnet,
                    "hftMakerNet": float(raw["maker_net"]),
                    "paperMakerPairedCoverage": ppc,
                    "hftMakerPairedCoverage": float(raw["maker_paired_coverage"]),
                    "paperCombinedGross": fnum(paper_port, "combined_gross"),
                    "hftCombinedGross": float(raw["combined_gross"]),
                    "paperPTaker1s": pp1,
                    "hftPTaker1s": hp1,
                    "paperPTaker3s": pp3,
                    "hftPTaker3s": hp3,
                    "hftWorstCaseFloor": float(raw["worst_case_floor"]),
                    "paperWorstCaseFloor": fnum(paper_port, "worst_case_floor"),
                    "activeHftOrders": None,
                })

            for tk in by_taker.get(t, []):
                inv.apply({"event_ms": int(t), "role": "TAKER", "side": str(tk["side"]), "price": float(tk["price"]), "shares": float(tk["shares"])})

            for o in by_terminal.get(t, []):
                num = int(o["num"])
                cur = bt.orders(0).get(num)
                if (not feed_exhausted) and cur is not None and int(cur.status) in {int(ex.NEW), int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
                    bt.cancel(0, num, False)
                submitted.discard(num)

            for o in by_submit.get(t, []):
                num = int(o["num"])
                if feed_exhausted:
                    o["hftSubmitRc"] = 1
                else:
                    o["hftSubmitRc"] = ex.submit_native(bt, num, str(o["side"]), float(o["price"]), float(o["shares"]))
                    submitted.add(num)
                    prev_exec[num] = 0.0
    finally:
        bt.close()

    def finite_pairs(a: str, b: str) -> list[tuple[float, float]]:
        out = []
        for r in out_decisions:
            x, y = r.get(a), r.get(b)
            if x is None or y is None:
                continue
            try:
                x = float(x); y = float(y)
                if math.isfinite(x) and math.isfinite(y): out.append((x, y))
            except Exception:
                pass
        return out

    mg = finite_pairs("paperMakerGross", "hftMakerGross")
    pc = finite_pairs("paperMakerPairedCoverage", "hftMakerPairedCoverage")
    p1s = finite_pairs("paperPTaker1s", "hftPTaker1s")
    p3s = finite_pairs("paperPTaker3s", "hftPTaker3s")
    taker_ds = [r for r in out_decisions if str(r.get("execution")) == "TAKER"]

    def med_abs_diff(ps: list[tuple[float, float]]) -> float | None:
        return statistics.median(abs(y-x) for x,y in ps) if ps else None
    def med_ratio(ps: list[tuple[float, float]]) -> float | None:
        vals = [y/x for x,y in ps if abs(x) > 1e-12]
        return statistics.median(vals) if vals else None

    return {
        "marketId": int(market_id),
        "feed": feed_meta,
        "paperMakerOrders": len(orders),
        "paperTakerFills": len(takers),
        "decisions": len(out_decisions),
        "hftMakerFillEvents": len(fill_events),
        "hftMakerFilledShares": sum(float(x["deltaShares"]) for x in fill_events),
        "summary": {
            "medianAbsMakerGrossDiff": med_abs_diff(mg),
            "medianHftToPaperMakerGrossRatio": med_ratio(mg),
            "medianAbsPairedCoverageDiff": med_abs_diff(pc),
            "medianPTaker1HftToPaperRatio": med_ratio(p1s),
            "medianPTaker3HftToPaperRatio": med_ratio(p3s),
            "paperTakerDecisionCount": len(taker_ds),
            "takerDecisionSnapshots": taker_ds[:5],
        },
        "fillEvents": fill_events,
        "decisionRows": out_decisions,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    decision_rows = [d for r in rows for d in r.get("decisionRows", [])]
    def pairs(a: str, b: str) -> list[tuple[float,float]]:
        out=[]
        for r in decision_rows:
            try:
                x=float(r[a]); y=float(r[b])
                if math.isfinite(x) and math.isfinite(y): out.append((x,y))
            except Exception: pass
        return out
    mg=pairs("paperMakerGross","hftMakerGross")
    pc=pairs("paperMakerPairedCoverage","hftMakerPairedCoverage")
    p1=pairs("paperPTaker1s","hftPTaker1s")
    p3=pairs("paperPTaker3s","hftPTaker3s")
    def median(vals): return statistics.median(vals) if vals else None
    def ratio(ps): return median([y/x for x,y in ps if abs(x)>1e-12])
    taker=[d for d in decision_rows if str(d.get("execution"))=="TAKER"]
    low_half=sum(y <= 0.5*x + 1e-9 for x,y in mg)
    near_zero=sum(y <= 18.0 + 1e-9 and x >= 72.0-1e-9 for x,y in mg)
    return {
        "markets": len(rows),
        "decisions": len(decision_rows),
        "paperMakerOrders": sum(int(r.get("paperMakerOrders") or 0) for r in rows),
        "paperTakerFills": sum(int(r.get("paperTakerFills") or 0) for r in rows),
        "hftMakerFillEvents": sum(int(r.get("hftMakerFillEvents") or 0) for r in rows),
        "hftMakerFilledShares": sum(float(r.get("hftMakerFilledShares") or 0.0) for r in rows),
        "makerGrossMedianAbsDiff": median([abs(y-x) for x,y in mg]),
        "makerGrossMedianHftToPaperRatio": ratio(mg),
        "makerGrossHftAtMostHalfPaperRate": low_half/len(mg) if mg else None,
        "makerGrossPaperGte72HftLte18Rate": near_zero/len(mg) if mg else None,
        "pairedCoverageMedianAbsDiff": median([abs(y-x) for x,y in pc]),
        "pTaker1MedianHftToPaperRatio": ratio(p1),
        "pTaker3MedianHftToPaperRatio": ratio(p3),
        "paperTakerDecisionSnapshots": len(taker),
        "paperTakerSnapshots": taker[:20],
    }


def main() -> int:
    p=argparse.ArgumentParser()
    p.add_argument("--strategy",choices=["R1","R2","CAP100"],required=True)
    p.add_argument("--market-id",type=int)
    p.add_argument("--matched126",action="store_true")
    p.add_argument("--max-markets",type=int)
    p.add_argument("--start-index",type=int,default=0)
    p.add_argument("--entry-latency-ms",type=int,default=1092)
    p.add_argument("--response-latency-ms",type=int,default=273)
    p.add_argument("--queue-model",choices=["risk","log"],default="risk")
    p.add_argument("--true-matches",action="store_true")
    p.add_argument("--trade-offset",choices=["early","mid","late"],default="mid")
    args=p.parse_args()

    mod=load_module(args.strategy)
    artifacts=mod._load_artifacts()
    p1=mod._fast_binary(artifacts["taker_1s"])
    p3=mod._fast_binary(artifacts["taker_3s"])
    ids=load_market_ids(args.strategy,args.matched126,args.market_id,args.max_markets,args.start_index)
    rows=[]; errors=[]
    for mid in ids:
        try:
            rows.append(audit_market(args.strategy,mid,entry_latency_ms=args.entry_latency_ms,response_latency_ms=args.response_latency_ms,queue_model=args.queue_model,p1=p1,p3=p3,mod=mod,true_matches=args.true_matches,trade_offset=args.trade_offset))
        except Exception as exc:
            errors.append({"marketId":mid,"error":f"{type(exc).__name__}: {exc}"})
    report={
        "version":"HFTBACKTEST_OWNSTATE_SHIFT_AUDIT_V0",
        "strategyLabel":args.strategy,
        "strategyVersion":VERSIONS[args.strategy],
        "config":{"entryLatencyMs":args.entry_latency_ms,"responseLatencyMs":args.response_latency_ms,"queueModel":args.queue_model,"fixedPlacementSchedule":True,"paperFilledOrdersRemainRestingUntilHftFill":True,"explicitPaperCancelsRetained":True,"originalTakersKeptFixed":True,"trueMatches":args.true_matches,"tradeOffset":args.trade_offset},
        "marketsRequested":len(ids),"marketsCompleted":len(rows),"errors":errors,
        "summary":summarize(rows),"markets":rows,
        "interpretationBoundary":"Phase 1.5. Maker placements and explicit paper cancels are fixed, but a paper FILLED timestamp does not remove an unfilled HftBacktest order; it remains resting until HFT fill or market end. Original Taker fills remain fixed. This isolates execution-induced own-state/model-score shift; it is not yet a full closed-loop strategy replay.",
    }
    OUT_DIR.mkdir(parents=True,exist_ok=True)
    tag=f"market{args.market_id}" if args.market_id is not None else (f"matched126_i{args.start_index}_n{args.max_markets}" if args.matched126 and (args.start_index or args.max_markets) else "matched126" if args.matched126 else f"i{args.start_index}_n{args.max_markets}" if args.max_markets else "full")
    mtag="_truematch_"+args.trade_offset if args.true_matches else ""
    out=OUT_DIR/f"{args.strategy.lower()}_ownstate_shift_{tag}{mtag}_lat{args.entry_latency_ms}_risk_v0.json"
    out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding="utf-8")
    print(json.dumps({"ok":True,"path":str(out),"summary":report["summary"],"errors":errors[:3]},ensure_ascii=False))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
