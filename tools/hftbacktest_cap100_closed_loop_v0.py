from __future__ import annotations

import argparse
import copy
import json
import math
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hftbacktest_execution_shift_audit_v0 as ex
from tools import hftbacktest_execution_tape_feed_v1 as tape_v1
from tools import execution_realism_guard_v1 as execution_guard
from src.predict_bot import unified_controller_cap100_shadow_v1 as mod

STRATEGY_DB = ROOT / "data" / "strategy_target_compare_v1.db"
OUT_DIR = ROOT / "data" / "research" / "hftbacktest_execution_shift_v0"
VERSION = mod.VERSION


def load_settlement(market_id: int) -> dict[str, Any]:
    import csv
    path = ROOT / "data" / "research" / "8784_r2_vs_8786_cap100_fresh_v1_markets.csv"
    with path.open(encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if int(row.get("marketId") or 0) == int(market_id):
                return {"winner": str(row.get("winner") or "").upper(), "paperCapPnl": float(row.get("cap_pnl") or 0.0)}
    return {"winner": None, "paperCapPnl": None}


class NoopRecorder:
    path = "OFFLINE_NOOP"
    def __getattr__(self, _name: str):
        return lambda *args, **kwargs: None


class HftBookAdapter:
    def __init__(self, market_id: int, entry_latency_ms: int, response_latency_ms: int, queue_model: str, trade_offset: str):
        self.market_id = int(market_id)
        self.events, self.update_times, self.meta = tape_v1.build_archive_events(self.market_id, trade_offset=trade_offset)
        self.bt = ex.new_bt(self.events, entry_latency_ms=entry_latency_ms, response_latency_ms=response_latency_ms, queue_model=queue_model)
        ex.initialize_bt(self.bt)
        # Keep the strategy-visible book identical to the frozen controller's PublicBookTailer
        # (source_timestamp timeline). HftBacktest uses the separate receipt-time feed only for execution.
        self.decision_book = mod.PublicBookTailer(ex.BOOK_DB)
        self.book: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
        self.last_source_ms: int | None = None
        self.order_num_by_key: dict[tuple[str,int], int] = {}
        self.prev_cum: dict[int, float] = defaultdict(float)
        self.next_num = 1
        self._refresh_book()

    def close(self) -> None:
        try: self.decision_book.close()
        except Exception: pass
        self.bt.close()

    def _refresh_book(self) -> None:
        d = self.bt.depth(0)
        arr = d.snapshot()
        try:
            bids: dict[float,float] = {}; asks: dict[float,float] = {}
            for row in arr:
                ev = int(row["ev"]); px = round(float(row["px"]), 12); qty = float(row["qty"])
                if qty <= 1e-12: continue
                if ev & int(ex.BUY_EVENT): bids[px] = qty
                elif ev & int(ex.SELL_EVENT): asks[px] = qty
            self.book = {"bids": bids, "asks": asks}
        finally:
            d.snapshot_free(arr)

    def reset(self, market_id: int, up_to_ms: int) -> bool:
        if int(market_id) != self.market_id:
            raise RuntimeError(f"adapter market mismatch {market_id} != {self.market_id}")
        ex.advance_to(self.bt, int(up_to_ms))
        ok=self.decision_book.reset(int(market_id),int(up_to_ms))
        self.book=self.decision_book.book
        self.last_source_ms=self.decision_book.last_source_ms
        return bool(ok and self.book["bids"] and self.book["asks"])

    def advance(self, market_id: int, up_to_ms: int, on_changes: Any = None) -> bool:
        if int(market_id) != self.market_id:
            return False
        ex.advance_to(self.bt, int(up_to_ms))
        ok=self.decision_book.advance(int(market_id),int(up_to_ms),on_changes)
        self.book=self.decision_book.book
        self.last_source_ms=self.decision_book.last_source_ms
        return bool(ok and self.book["bids"] and self.book["asks"])

    def submit(self, key: tuple[str,int], order: mod.PaperOrder) -> tuple[int,int]:
        num = self.next_num; self.next_num += 1
        rc = ex.submit_native(self.bt, num, order.side, order.price, order.shares)
        self.order_num_by_key[key] = num
        self.prev_cum[num] = 0.0
        return num, rc

    def cancel(self, key: tuple[str,int]) -> None:
        num = self.order_num_by_key.get(key)
        if num is None: return
        cur = self.bt.orders(0).get(num)
        if cur is not None and int(cur.status) in {int(ex.NEW), int(ex.PARTIALLY_FILLED)} and bool(cur.cancellable):
            try: self.bt.cancel(0, num, False)
            except Exception: pass

    def snap(self, key: tuple[str,int]) -> dict[str,Any]:
        num = self.order_num_by_key.get(key)
        return ex.order_snapshot(self.bt, num) if num is not None else {"status":"NONE","cumExecQty":0.0}

    def snap_num(self, num: int) -> dict[str,Any]:
        return ex.order_snapshot(self.bt, int(num))

    def submit_taker(self, side: str, max_price: float, shares: float) -> tuple[int,int]:
        num = self.next_num; self.next_num += 1
        native_side, native_price = ex.native_order(side, max_price)
        if native_side == "BUY":
            rc = int(self.bt.submit_buy_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        else:
            rc = int(self.bt.submit_sell_order(0, num, native_price, float(shares), ex.hbt.GTC, ex.LIMIT, False))
        return num, rc



def jload(v: Any) -> dict[str,Any]:
    try: return json.loads(v) if v else {}
    except Exception: return {}


def load_public_snapshots(market_id: int) -> list[dict[str,Any]]:
    con=sqlite3.connect(STRATEGY_DB); con.row_factory=sqlite3.Row
    try:
        rows=con.execute("""select decision_ms,public_state_json from our_decisions
                            where strategy_version=? and market_id=? order by decision_ms,decision_id""",(VERSION,int(market_id))).fetchall()
    finally: con.close()
    out=[]; seen=set()
    for r in rows:
        s=jload(r["public_state_json"])
        if not s: continue
        sampled=int(s.get("sampledAtMs") or r["decision_ms"])
        if sampled in seen: continue
        seen.add(sampled)
        s["sampledAtMs"]=sampled
        if not s.get("timestampNs"): s["timestampNs"]=sampled*1_000_000
        out.append(s)
    return out


def load_paper_truth(market_id: int) -> dict[str,Any]:
    con=sqlite3.connect(STRATEGY_DB); con.row_factory=sqlite3.Row
    try:
        tak=[dict(r) for r in con.execute("""select side,price,shares,filled_at_ms,decision_id from our_fills
            where strategy_version=? and market_id=? and channel='TAKER' order by filled_at_ms""",(VERSION,int(market_id)))]
        orders=[dict(r) for r in con.execute("""select side,price,shares,placed_at_ms,status,filled_at_ms,cancelled_at_ms from our_orders
            where strategy_version=? and market_id=? and channel='MAKER' order by placed_at_ms""",(VERSION,int(market_id)))]
        dec=[dict(r) for r in con.execute("""select decision_ms,seconds_left,desired_portfolio_action,execution_choice,primary_reason from our_decisions
            where strategy_version=? and market_id=? order by decision_ms""",(VERSION,int(market_id)))]
    finally: con.close()
    return {"takers":tak,"orders":orders,"decisions":dec}


def new_controller(adapter: HftBookAdapter) -> mod.UnifiedControllerCap100ShadowV1:
    c=object.__new__(mod.UnifiedControllerCap100ShadowV1)
    c.recorder=NoopRecorder(); c.book=adapter
    c.models=mod._load_artifacts()
    c.p_maker_up_base=mod._fast_binary(c.models["maker_up"]); c.p_maker_down_base=mod._fast_binary(c.models["maker_down"])
    c.p_taker1=mod._fast_binary(c.models["taker_1s"]); c.p_taker3=mod._fast_binary(c.models["taker_3s"])
    c.current_market_id=None; c.excluded_deployment_market_id=0; c.active=True; c.source_ready=True; c.book_ready=True
    c.source_wait_reason=None; c.source_health={}; c.last_snapshot_ms=None; c.last_eval_snapshot_ms=None; c.last_loop_ms=None; c.last_error=None; c.last_decision=None
    c.sequence=0; c.inventory=mod.Inventory(); c.orders={}; c.last_closed={}; c.placements=[]; c.episode=None; c.readiness=False; c.last_taker_ms=-10**18
    import numpy as np
    c.rng=np.random.default_rng(mod.RNG_SEED); c.residual_rng=np.random.default_rng(mod.RNG_SEED+7717)
    c.current_metrics={}; c.maker_spent_notional=0.0; c.taker_spent_notional=0.0; c.taker_fee_spent=0.0
    c.run_metrics={"marketsStarted":0,"decisions":0,"makerPlacements":0,"makerFills":0,"takerFills":0,"excursions":0,"passivePrioritySteps":0,"unresolvedGuardEntries":0,"residualWakes":0,"residualRecoveries":0,"burstPlacements":0,"makerCapBlocks":0,"takerCapBlocks":0}
    c._reset_metric_counters()
    return c


def run_market(market_id: int, *, entry_latency_ms: int, response_latency_ms: int, queue_model: str, trade_offset: str, taker_mode: str = "hft", forced_repair_wake_ms: int | None = None, repair_persistence_ms: int | None = None, diagnostic_only: bool = False) -> dict[str,Any]:
    execution_guard_meta = execution_guard.require_performance_grade_execution(
        test_name="HFTBACKTEST_CAP100_CLOSED_LOOP_V0",
        maker_mode="HFTBACKTEST_EXECUTION_TAPE_V1",
        taker_mode="HFT" if taker_mode == "hft" else "INSTANT",
        closed_loop=True,
        uses_predict_execution_tape=True,
        uses_hftbacktest=True,
        diagnostic_only=diagnostic_only,
    )
    snaps=load_public_snapshots(market_id)
    if not snaps: raise RuntimeError("no public snapshots")
    truth=load_paper_truth(market_id)
    a=HftBookAdapter(market_id,entry_latency_ms,response_latency_ms,queue_model,trade_offset)
    c=new_controller(a)
    closed_decisions=[]; maker_fill_events=[]; taker_events=[]; taker_attempts=[]; rejects=[]; forced_wakes=[]; persistence_wakes=[]; risk_since_ms=None

    orig_add=c._add_order; orig_cancel=c._cancel_order; orig_taker=c._record_taker

    def add_wrap(side:str, now:int, snapshot_ns:int, decision_id:str, reason:str, p:float, snapshot:dict[str,Any], allow_stack:bool=True, bypass_guard:bool=False)->bool:
        before=set(c.orders)
        made=orig_add(side,now,snapshot_ns,decision_id,reason,p,snapshot,allow_stack,bypass_guard)
        if made:
            new=list(set(c.orders)-before)
            if new:
                key=new[0]; num,rc=a.submit(key,c.orders[key])
                if rc!=0: rejects.append({"atMs":now,"key":list(key),"submitRc":rc})
        return made

    def cancel_wrap(key:tuple[str,int], at_ms:int, reason:str)->None:
        a.cancel(key)
        orig_cancel(key,at_ms,reason)

    def fill_wrap(snapshot:dict[str,Any], now:int)->list[dict[str,Any]]:
        fills=[]
        for key,order in list(c.orders.items()):
            snap=a.snap(key); num=a.order_num_by_key.get(key); cum=float(snap.get("cumExecQty") or 0.0); old=float(a.prev_cum.get(num,0.0)) if num is not None else 0.0
            if cum>old+1e-9:
                delta=cum-old; native_px=snap.get("execPrice")
                px=float(order.price)
                if native_px is not None and math.isfinite(float(native_px)):
                    px=float(native_px) if order.side=="UP" else 1.0-float(native_px)
                fill_ms=int((snap.get("exchangeTs") or now*1_000_000)//1_000_000)
                pre_net=c.inventory.maker_up-c.inventory.maker_down; pre_g=c.inventory.maker_up+c.inventory.maker_down; pre_pc=2*min(c.inventory.maker_up,c.inventory.maker_down)/pre_g if pre_g>mod.EPS else 1.0
                c.inventory.apply({"event_ms":fill_ms,"role":"MAKER","side":order.side,"price":px,"shares":delta})
                c.maker_spent_notional += px*delta
                c.current_metrics["makerFills"]+=1; c.run_metrics["makerFills"]+=1
                post_net=c.inventory.maker_up-c.inventory.maker_down
                if order.occupied_before:
                    predom="UP" if pre_net>mod.EPS else "DOWN" if pre_net<-mod.EPS else None
                    if predom==order.side and abs(pre_net)>=mod.SHARES-mod.EPS and abs(post_net)>abs(pre_net)+1:
                        c.episode={"kind":"OVERLAP","side":order.side,"risk_start_ms":fill_ms,"risk_pre_abs":abs(pre_net),"start_ms":fill_ms,"pre_abs":abs(pre_net),"start_abs":abs(post_net),"expansion":abs(post_net)-abs(pre_net),"pre_pc":pre_pc,"unresolved":False}; c.readiness=False; c.current_metrics["excursions"]+=1; c.run_metrics["excursions"]+=1
                if snap.get("status") not in {"FILLED","REJECTED","EXPIRED","CANCELED"}:
                    order.shares=max(0.0,float(order.shares)-delta)
                maker_fill_events.append({"atMs":fill_ms,"observedAtMs":now,"side":order.side,"price":px,"deltaShares":delta,"cumShares":cum,"status":snap.get("status"),"orderId":order.id,"occupiedBefore":bool(order.occupied_before)})
                fills.append({"side":order.side,"price":px,"shares":delta,"hftStatus":snap.get("status")})
                if num is not None: a.prev_cum[num]=cum
            if snap.get("status") in {"FILLED","REJECTED","EXPIRED","CANCELED"}:
                c.orders.pop(key,None); c.last_closed[key]=now
        nonlocal risk_since_ms
        maker_net=float(c.inventory.maker_up-c.inventory.maker_down); maker_abs=abs(maker_net); mg=float(c.inventory.maker_up+c.inventory.maker_down); pc=(2*min(c.inventory.maker_up,c.inventory.maker_down)/mg if mg>mod.EPS else 0.0)
        risky=maker_abs>=mod.SHARES-mod.EPS and pc<0.80
        if c.episode is not None or not risky:
            risk_since_ms=None
        else:
            if risk_since_ms is None: risk_since_ms=int(now)
            if repair_persistence_ms is not None and int(now)-int(risk_since_ms)>=int(repair_persistence_ms):
                es="UP" if maker_net>0 else "DOWN"
                c.episode={"kind":"RESIDUAL","side":es,"risk_start_ms":int(now),"risk_pre_abs":maker_abs,"start_ms":int(now),"pre_abs":maker_abs,"start_abs":maker_abs,"expansion":0.0,"pre_pc":pc,"unresolved":False}
                c.readiness=True; c.current_metrics["residualWakes"]+=1; c.run_metrics["residualWakes"]+=1
                persistence_wakes.append({"atMs":int(now),"side":es,"makerNet":maker_net,"makerAbs":maker_abs,"makerPairedCoverage":pc,"riskAgeMs":int(now)-int(risk_since_ms)})
                risk_since_ms=None
        if forced_repair_wake_ms is not None and int(now)==int(forced_repair_wake_ms) and c.episode is None:
            if maker_abs>1.0:
                es="UP" if maker_net>0 else "DOWN"
                c.episode={"kind":"RESIDUAL","side":es,"risk_start_ms":int(now),"risk_pre_abs":maker_abs,"start_ms":int(now),"pre_abs":maker_abs,"start_abs":maker_abs,"expansion":0.0,"pre_pc":pc,"unresolved":False}
                c.readiness=True
                forced_wakes.append({"atMs":int(now),"side":es,"makerNet":maker_net,"makerAbs":maker_abs,"makerPairedCoverage":pc})
        return fills

    def taker_wrap(side:str,price:float,now:int,decision_id:str,snapshot:dict[str,Any],raw:dict[str,Any],p1:float,p3:float,ppass:float,pred_effect:str)->bool:
        if taker_mode == "instant":
            ok=orig_taker(side,price,now,decision_id,snapshot,raw,p1,p3,ppass,pred_effect)
            if ok: taker_events.append({"atMs":now,"side":side,"price":price,"pTaker1s":p1,"pTaker3s":p3,"secondsLeft":snapshot.get("secondsLeft"),"decisionId":decision_id})
            return ok
        fee=mod.taker_fee(mod.SHARES,price,mod.FEE_BPS); need=float(price)*mod.SHARES+float(fee)
        committed=sum(float(o.price)*float(o.shares) for o in c.orders.values()); spent=c.maker_spent_notional+c.taker_spent_notional+c.taker_fee_spent
        if spent+committed+need>mod.CAP_TOTAL_USDT+1e-9:
            c.current_metrics["takerCapBlocks"]+=1; c.run_metrics["takerCapBlocks"]+=1; taker_attempts.append({"atMs":now,"side":side,"price":price,"result":"CAP_BLOCK"}); return False
        max_price=min(0.99,float(price)+0.02); num,rc=a.submit_taker(side,max_price,mod.SHARES); taker_attempts.append({"atMs":now,"side":side,"price":price,"maxPrice":max_price,"orderNum":num,"submitRc":rc})
        if rc!=0: return False
        target=now+2200; a.advance(market_id,target); fill_wrap(snapshot,target)
        snap=a.snap_num(num); cum=float(snap.get("cumExecQty") or 0.0)
        if cum<=mod.EPS: taker_attempts[-1].update({"result":"NO_FILL_2P2S","status":snap.get("status")}); return False
        native_px=snap.get("execPrice"); fill_px=float(price)
        if native_px is not None and math.isfinite(float(native_px)): fill_px=float(native_px) if side=="UP" else 1.0-float(native_px)
        fill_ms=int((snap.get("exchangeTs") or target*1_000_000)//1_000_000); c.inventory.apply({"event_ms":fill_ms,"role":"TAKER","side":side,"price":fill_px,"shares":cum}); c.taker_spent_notional += fill_px*cum; c.taker_fee_spent += mod.taker_fee(cum,fill_px,mod.FEE_BPS); c.current_metrics["takerFills"]+=1; c.run_metrics["takerFills"]+=1; c.last_taker_ms=fill_ms
        taker_events.append({"atMs":fill_ms,"decisionMs":now,"side":side,"price":fill_px,"shares":cum,"pTaker1s":p1,"pTaker3s":p3,"secondsLeft":snapshot.get("secondsLeft"),"decisionId":decision_id,"hftStatus":snap.get("status")}); taker_attempts[-1].update({"result":"FILLED","filledShares":cum,"fillPrice":fill_px,"fillMs":fill_ms,"status":snap.get("status")}); return True

    c._add_order=add_wrap; c._cancel_order=cancel_wrap; c._fill_orders=fill_wrap; c._record_taker=taker_wrap
    try:
        for s in snaps:
            if taker_mode == "hft" and int(a.bt.current_timestamp // 1_000_000) > int(s["sampledAtMs"]):
                continue
            c._step(dict(s))
            if c.last_decision is not None and int(c.last_decision.get("decisionMs") or -1)==int(s["sampledAtMs"]):
                closed_decisions.append(copy.deepcopy(c.last_decision))
        # drain remaining public feed only for terminal Maker inventory; no more decisions.
        a.advance(market_id,int(a.meta["lastReceivedMs"])); fill_wrap(snaps[-1],int(a.meta["lastReceivedMs"]))
        port=c.inventory.features(int(a.meta["lastReceivedMs"])); port.pop("_combined_net",None)
    finally:
        a.close()

    settlement=load_settlement(market_id)
    winner=settlement.get("winner")
    maker_cost=float(c.inventory.maker_up_cost+c.inventory.maker_down_cost)
    taker_cost=float(c.inventory.taker_up_cost+c.inventory.taker_down_cost)
    total_cost=maker_cost+taker_cost+float(c.taker_fee_spent)
    payout=(float(c.inventory.maker_up+c.inventory.taker_up) if winner=="UP" else float(c.inventory.maker_down+c.inventory.taker_down) if winner=="DOWN" else math.nan)
    realized_pnl=(payout-total_cost) if math.isfinite(payout) else math.nan
    ledger={"winner":winner,"makerUpShares":float(c.inventory.maker_up),"makerDownShares":float(c.inventory.maker_down),"takerUpShares":float(c.inventory.taker_up),"takerDownShares":float(c.inventory.taker_down),"makerCostUsdt":maker_cost,"takerCostUsdt":taker_cost,"takerFeesUsdt":float(c.taker_fee_spent),"totalCostUsdt":total_cost,"winnerPayoutUsdt":payout,"realizedPnlUsdt":realized_pnl,"paperCapPnlUsdt":settlement.get("paperCapPnl")}

    paper_by_ms={int(x["decision_ms"]):x for x in truth["decisions"]}
    first_div=None
    for d in closed_decisions:
        p=paper_by_ms.get(int(d["decisionMs"]))
        if p is None: continue
        cur=(d.get("desiredPortfolioAction"),d.get("executionChoice"),d.get("primaryReason"))
        old=(p.get("desired_portfolio_action"),p.get("execution_choice"),p.get("primary_reason"))
        if cur!=old:
            first_div={"decisionMs":d["decisionMs"],"secondsLeft":d.get("portfolio",{}).get("seconds_left"),"paper":old,"closedLoop":cur,"episode":d.get("episode"),"readiness":d.get("readiness"),"actions":d.get("actions")}
            break

    def first_taker(xs:list[dict[str,Any]])->dict[str,Any]|None:
        return xs[0] if xs else None
    pt=first_taker(truth["takers"]); ct=first_taker(taker_events)
    return {
        "version":"HFTBACKTEST_CAP100_CLOSED_LOOP_V0",
        "marketId":market_id,
        "executionRealism": execution_guard_meta,
        "config":{"entryLatencyMs":entry_latency_ms,"responseLatencyMs":response_latency_ms,"queueModel":queue_model,"tradeOffset":trade_offset,"makerExecution":"HFTBACKTEST_EXECUTION_TAPE_V1_ARCHIVE","takerExecution":"HFTBACKTEST_MARKETABLE_LIMIT_CONFIRM_2P2S_STAGE_4B" if taker_mode=="hft" else "ORIGINAL_INSTANT_OBSERVED_ASK_STAGE_4A","forcedRepairWakeMs":forced_repair_wake_ms,"repairPersistenceMs":repair_persistence_ms,"cancelSemantics":"controller removes immediately after HFT cancel request; cancel-latency fill race not yet modeled"},
        "feed":a.meta,
        "paper":{"makerOrders":len(truth["orders"]),"takerFills":len(truth["takers"]),"firstTaker":pt},
        "closedLoop":{"decisions":len(closed_decisions),"makerPlacements":c.run_metrics["makerPlacements"],"makerFillEvents":len(maker_fill_events),"makerFilledShares":sum(float(x["deltaShares"]) for x in maker_fill_events),"takerFills":len(taker_events),"firstTaker":ct,"runMetrics":dict(c.run_metrics),"finalPortfolio":port,"ledger":ledger,"activeOrdersAtEnd":len(c.orders),"submitRejects":rejects},
        "firstDecisionDivergence":first_div,
        "takerEvents":taker_events,
        "takerAttempts":taker_attempts,
        "forcedRepairWakes":forced_wakes,
        "persistenceRepairWakes":persistence_wakes,
        "makerFillEvents":maker_fill_events,
        "decisionRows":closed_decisions,
        "boundary": ("Stage4B performance-grade default: HftBacktest Maker + marketable-limit Taker execution from Predict Execution Tape V1 with own-state closed-loop feedback. Known remaining gaps (post-only reject and cancel-latency fill race) are reported by the execution-realism contract." if taker_mode == "hft" else "DIAGNOSTIC_ONLY Stage4A: realistic Maker execution but instant observed-ask Taker fill; forbidden as primary performance/promotion evidence."),
    }


def main()->int:
    p=argparse.ArgumentParser(); p.add_argument('--market-id',type=int,required=True); p.add_argument('--entry-latency-ms',type=int,default=1092); p.add_argument('--response-latency-ms',type=int,default=273); p.add_argument('--queue-model',choices=['risk','log'],default='risk'); p.add_argument('--trade-offset',choices=['early','mid','late'],default='mid'); p.add_argument('--taker-mode',choices=['instant','hft'],default='hft'); p.add_argument('--diagnostic-only',action='store_true'); args=p.parse_args()
    report=run_market(args.market_id,entry_latency_ms=args.entry_latency_ms,response_latency_ms=args.response_latency_ms,queue_model=args.queue_model,trade_offset=args.trade_offset,taker_mode=args.taker_mode,diagnostic_only=args.diagnostic_only)
    stage='stage4b' if args.taker_mode=='hft' else 'stage4a'; OUT_DIR.mkdir(parents=True,exist_ok=True); out=OUT_DIR/f"cap100_closed_loop_market{args.market_id}_{stage}_truematch_{args.trade_offset}_lat{args.entry_latency_ms}_risk_v0.json"; out.write_text(json.dumps(report,indent=2,ensure_ascii=False,allow_nan=True),encoding='utf-8')
    print(json.dumps({'ok':True,'path':str(out),'marketId':args.market_id,'paper':report['paper'],'closedLoop':report['closedLoop'],'firstDecisionDivergence':report['firstDecisionDivergence']},ensure_ascii=False))
    return 0

if __name__=='__main__': raise SystemExit(main())
