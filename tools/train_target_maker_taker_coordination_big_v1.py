from __future__ import annotations

import argparse
import bisect
import os
import json
import math
import sqlite3
import statistics
import zlib
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from interpret.glassbox import ExplainableBoostingClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    brier_score_loss,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_score,
    recall_score,
    roc_auc_score,
)

ROOT = Path(__file__).resolve().parents[1]
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
OUT = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
HAZARD_CSV = OUT / "hazard_states_v1.csv"
TAKER_CSV = OUT / "taker_event_states_v1.csv"
HANDOFF_CSV = OUT / "post_taker_handoff_states_v1.csv"
MANIFEST = OUT / "dataset_manifest.json"
COMPARISON = OUT / "model_comparison.json"
VERSION = "TARGET_MAKER_TAKER_COORDINATION_BIG_V1"
GRID = 0.01

# Features deliberately exclude Target future action / winner. They are quantities an own-state
# runtime controller could reconstruct from its own fills plus the public Predict book.
CORE = [
    "seconds_left",
    "maker_gross", "maker_net", "maker_abs_net", "maker_imbalance_ratio", "maker_paired_coverage",
    "taker_gross", "taker_net", "taker_abs_net", "taker_imbalance_ratio", "taker_paired_coverage",
    "combined_gross", "combined_net", "combined_abs_net", "combined_imbalance_ratio", "combined_paired_coverage",
    "worst_case_floor", "best_case_pnl", "abs_payoff_gap",
    "maker_taker_net_same_sign",
]
BOOK = [
    "up_bid", "up_ask", "up_spread_ticks", "up_bid_depth", "up_ask_depth", "up_top3_bid_depth",
    "down_bid", "down_ask", "down_spread_ticks", "down_bid_depth", "down_ask_depth", "down_top3_bid_depth",
    "pair_bid_edge", "pair_ask_edge",
    "dominant_bid", "dominant_ask", "opposite_bid", "opposite_ask",
    "dominant_opp_bid_pair_edge",
]
LIFE = [
    "last_maker_age_ms", "last_taker_age_ms",
    "last_maker_up_age_ms", "last_maker_down_age_ms",
    "last_taker_up_age_ms", "last_taker_down_age_ms",
    "maker_fills_1s", "maker_fills_5s", "maker_fills_10s",
    "taker_fills_1s", "taker_fills_5s", "taker_fills_10s",
    "maker_shares_5s", "maker_shares_10s", "taker_shares_5s", "taker_shares_10s",
    "maker_side_streak", "taker_side_streak",
    "combined_absnet_change_10s", "maker_absnet_change_10s",
]
ECON = [
    "maker_up_avg_price", "maker_down_avg_price", "maker_avg_pair_edge",
    "taker_up_avg_price", "taker_down_avg_price", "taker_avg_pair_edge",
    "combined_up_avg_price", "combined_down_avg_price", "combined_avg_pair_edge",
]
TAKER_OWN = [
    "intervention_side_is_up", "intervention_shares", "intervention_avg_price",
    "post_taker_combined_abs_net", "post_taker_combined_imbalance_ratio", "post_taker_combined_paired_coverage",
    "post_taker_worst_case_floor",
]
FEATURE_SETS = {
    "CORE": CORE,
    "CORE_BOOK": CORE + BOOK,
    "CORE_BOOK_LIFECYCLE": CORE + BOOK + LIFE,
    "FULL": CORE + BOOK + LIFE + ECON,
}
HANDOFF_FEATURE_SETS = {
    "CORE": CORE + TAKER_OWN,
    "CORE_BOOK": CORE + BOOK + TAKER_OWN,
    "FULL": CORE + BOOK + LIFE + ECON + TAKER_OWN,
}


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def dec(blob: bytes | None) -> Any:
    return json.loads(zlib.decompress(blob).decode("utf-8")) if blob else None


def apply_changes(book: dict[str, dict[float, float]], changes: Any) -> None:
    if not isinstance(changes, dict):
        return
    for key in ("bids", "asks"):
        for ch in changes.get(key, []) or []:
            p = float(ch["price"])
            after = float(ch["after"])
            if after <= 1e-12:
                book[key].pop(p, None)
            else:
                book[key][p] = after


def top3(side: dict[float, float], *, reverse: bool) -> float:
    return float(sum(v for _, v in sorted(side.items(), key=lambda kv: kv[0], reverse=reverse)[:3]))


def outcome_book(book: dict[str, dict[float, float]], dominant: str | None) -> dict[str, float] | None:
    bids, asks = book["bids"], book["asks"]
    if not bids or not asks:
        return None
    bb, ba = max(bids), min(asks)
    bbd, bad = float(bids[bb]), float(asks[ba])
    up_bid, up_ask = bb, ba
    down_bid, down_ask = 1.0 - ba, 1.0 - bb
    out = {
        "up_bid": up_bid,
        "up_ask": up_ask,
        "up_spread_ticks": (up_ask - up_bid) / GRID,
        "up_bid_depth": bbd,
        "up_ask_depth": bad,
        "up_top3_bid_depth": top3(bids, reverse=True),
        "down_bid": down_bid,
        "down_ask": down_ask,
        "down_spread_ticks": (down_ask - down_bid) / GRID,
        "down_bid_depth": bad,
        "down_ask_depth": bbd,
        "down_top3_bid_depth": top3(asks, reverse=False),
        "pair_bid_edge": 1.0 - up_bid - down_bid,
        "pair_ask_edge": 1.0 - up_ask - down_ask,
    }
    if dominant == "UP":
        db, da, ob, oa = up_bid, up_ask, down_bid, down_ask
    elif dominant == "DOWN":
        db, da, ob, oa = down_bid, down_ask, up_bid, up_ask
    else:
        db = da = ob = oa = math.nan
    out.update({
        "dominant_bid": db,
        "dominant_ask": da,
        "opposite_bid": ob,
        "opposite_ask": oa,
        "dominant_opp_bid_pair_edge": (1.0 - db - ob) if math.isfinite(db) and math.isfinite(ob) else math.nan,
    })
    return out


def ratio(abs_net: float, gross: float) -> float:
    return abs_net / gross if gross > 1e-12 else 0.0


def coverage(up: float, down: float) -> float:
    gross = up + down
    return (2.0 * min(up, down) / gross) if gross > 1e-12 else 0.0


def avg(cost: float, shares: float) -> float:
    return cost / shares if shares > 1e-12 else math.nan


class Inventory:
    def __init__(self) -> None:
        self.maker_up = self.maker_down = self.taker_up = self.taker_down = 0.0
        self.maker_up_cost = self.maker_down_cost = 0.0
        self.taker_up_cost = self.taker_down_cost = 0.0
        self.cash = 0.0
        self.events: list[dict[str, Any]] = []
        self.times: list[int] = []

    def apply(self, e: dict[str, Any]) -> None:
        role, side = str(e["role"]), str(e["side"])
        sh, px = float(e["shares"]), float(e["price"])
        if role == "MAKER":
            if side == "UP": self.maker_up += sh; self.maker_up_cost += px * sh
            else: self.maker_down += sh; self.maker_down_cost += px * sh
        else:
            if side == "UP": self.taker_up += sh; self.taker_up_cost += px * sh
            else: self.taker_down += sh; self.taker_down_cost += px * sh
        self.cash -= px * sh
        self.events.append(e); self.times.append(int(e["event_ms"]))

    def _recent(self, now: int, role: str, window: int) -> list[dict[str, Any]]:
        lo = bisect.bisect_right(self.times, now - window)
        hi = bisect.bisect_right(self.times, now)
        return [e for e in self.events[lo:hi] if e["role"] == role]

    def _last_age(self, now: int, role: str, side: str | None = None) -> float:
        for e in reversed(self.events):
            if e["role"] == role and (side is None or e["side"] == side):
                return float(now - int(e["event_ms"]))
        return math.nan

    def _streak(self, role: str) -> float:
        side = None; n = 0
        for e in reversed(self.events):
            if e["role"] != role: continue
            if side is None: side = e["side"]
            if e["side"] != side: break
            n += 1
        return float(n)

    def _absnet_at(self, now: int, role: str | None = None) -> float:
        up = down = 0.0
        for e in self.events:
            if int(e["event_ms"]) > now: break
            if role is not None and e["role"] != role: continue
            if e["side"] == "UP": up += float(e["shares"])
            else: down += float(e["shares"])
        return abs(up - down)

    def features(self, now: int) -> dict[str, float]:
        mu, md, tu, td = self.maker_up, self.maker_down, self.taker_up, self.taker_down
        cu, cd = mu + tu, md + td
        mn, tn, cn = mu-md, tu-td, cu-cd
        mg, tg, cg = mu+md, tu+td, cu+cd
        ma, ta, ca = abs(mn), abs(tn), abs(cn)
        floor = min(self.cash + cu, self.cash + cd)
        best = max(self.cash + cu, self.cash + cd)
        r_m1 = self._recent(now, "MAKER", 1000); r_m5 = self._recent(now, "MAKER", 5000); r_m10 = self._recent(now, "MAKER", 10000)
        r_t1 = self._recent(now, "TAKER", 1000); r_t5 = self._recent(now, "TAKER", 5000); r_t10 = self._recent(now, "TAKER", 10000)
        mau, mad = avg(self.maker_up_cost, mu), avg(self.maker_down_cost, md)
        tau, tad = avg(self.taker_up_cost, tu), avg(self.taker_down_cost, td)
        cau, cad = avg(self.maker_up_cost+self.taker_up_cost, cu), avg(self.maker_down_cost+self.taker_down_cost, cd)
        return {
            "maker_gross": mg, "maker_net": mn, "maker_abs_net": ma, "maker_imbalance_ratio": ratio(ma, mg), "maker_paired_coverage": coverage(mu,md),
            "taker_gross": tg, "taker_net": tn, "taker_abs_net": ta, "taker_imbalance_ratio": ratio(ta, tg), "taker_paired_coverage": coverage(tu,td),
            "combined_gross": cg, "combined_net": cn, "combined_abs_net": ca, "combined_imbalance_ratio": ratio(ca, cg), "combined_paired_coverage": coverage(cu,cd),
            "worst_case_floor": floor, "best_case_pnl": best, "abs_payoff_gap": abs(cu-cd),
            "maker_taker_net_same_sign": float(1 if mn*tn>0 else 0 if mn*tn==0 else -1),
            "last_maker_age_ms": self._last_age(now,"MAKER"), "last_taker_age_ms": self._last_age(now,"TAKER"),
            "last_maker_up_age_ms": self._last_age(now,"MAKER","UP"), "last_maker_down_age_ms": self._last_age(now,"MAKER","DOWN"),
            "last_taker_up_age_ms": self._last_age(now,"TAKER","UP"), "last_taker_down_age_ms": self._last_age(now,"TAKER","DOWN"),
            "maker_fills_1s": float(len(r_m1)), "maker_fills_5s": float(len(r_m5)), "maker_fills_10s": float(len(r_m10)),
            "taker_fills_1s": float(len(r_t1)), "taker_fills_5s": float(len(r_t5)), "taker_fills_10s": float(len(r_t10)),
            "maker_shares_5s": sum(float(e['shares']) for e in r_m5), "maker_shares_10s": sum(float(e['shares']) for e in r_m10),
            "taker_shares_5s": sum(float(e['shares']) for e in r_t5), "taker_shares_10s": sum(float(e['shares']) for e in r_t10),
            "maker_side_streak": self._streak("MAKER"), "taker_side_streak": self._streak("TAKER"),
            "combined_absnet_change_10s": ca - self._absnet_at(now-10000,None),
            "maker_absnet_change_10s": ma - self._absnet_at(now-10000,"MAKER"),
            "maker_up_avg_price": mau, "maker_down_avg_price": mad, "maker_avg_pair_edge": 1-mau-mad if math.isfinite(mau) and math.isfinite(mad) else math.nan,
            "taker_up_avg_price": tau, "taker_down_avg_price": tad, "taker_avg_pair_edge": 1-tau-tad if math.isfinite(tau) and math.isfinite(tad) else math.nan,
            "combined_up_avg_price": cau, "combined_down_avg_price": cad, "combined_avg_pair_edge": 1-cau-cad if math.isfinite(cau) and math.isfinite(cad) else math.nan,
            "_combined_net": cn,
        }


def load_events(t: sqlite3.Connection, markets: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if not markets: return out
    for m in markets:
        for r in t.execute("""select event_ms,role,side,price,shares from wallet_shadow_target_events
                               where asset='BTC' and quote_type='BID' and role in ('MAKER','TAKER') and market_id=?
                               order by event_ms,id""", (m,)):
            out[m].append({"event_ms":int(r["event_ms"]),"role":str(r["role"]),"side":str(r["side"]),"price":float(r["price"]),"shares":float(r["shares"])})
    return out


def load_taker_parents(t: sqlite3.Connection, markets: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in markets:
        for r in t.execute("""select parent_id,side,first_event_ms,last_event_ms,average_price,shares,fill_legs
                               from target_parent_orders where asset='BTC' and role='TAKER' and quote_type='BID' and market_id=?
                               order by first_event_ms,parent_id""", (m,)):
            out[m].append(dict(r))
    return out


def load_anchored_maker_parents(b: sqlite3.Connection, markets: set[int]) -> dict[int, list[dict[str, Any]]]:
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for m in markets:
        for r in b.execute("""select parent_id,target_side,placement_first_ms,placement_last_ms,last_target_ms
                               from maker_book_inference_v21_parent_lifecycles where market_id=?
                               and placement_first_ms is not null and placement_supports_18=1
                               and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75
                               order by placement_first_ms,parent_id""", (m,)):
            out[m].append(dict(r))
    return out


def load_market_meta(b: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {int(r["market_id"]): dict(r) for r in b.execute("select * from maker_book_inference_markets where window_end_ms is not null")}


def effect_label(pre_net: float, side: str, shares: float) -> str:
    post = pre_net + (shares if side == "UP" else -shares)
    if abs(pre_net) < 1e-9:
        return "BUILD_FROM_FLAT"
    if abs(post) < abs(pre_net) - 1e-9:
        return "REPAIR_EFFECT"
    if abs(post) > abs(pre_net) + 1e-9:
        return "ADD_EFFECT"
    return "NEUTRAL_EFFECT"


def maker_handoff_label(parents: list[dict[str, Any]], taker_side: str, start: int, end: int) -> str:
    sides = {str(p["target_side"]) for p in parents if int(p["placement_first_ms"]) > start and int(p["placement_first_ms"]) <= end}
    if not sides: return "PAUSE"
    if len(sides) >= 2: return "BOTH"
    only = next(iter(sides))
    return "SAME" if only == taker_side else "OPP"


def split_markets(df: pd.DataFrame) -> dict[str, set[int]]:
    ms = df[["market_id","market_end_ms"]].drop_duplicates().sort_values(["market_end_ms","market_id"])
    ids = [int(x) for x in ms.market_id]
    n = len(ids); a = int(n*.70); b = int(n*.85)
    return {"train":set(ids[:a]),"validation":set(ids[a:b]),"test":set(ids[b:])}


def build() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    b, t = ro(BOOK_DB), ro(TARGET_DB)
    try:
        meta = load_market_meta(b)
        update_markets = {int(r[0]) for r in b.execute("select distinct market_id from maker_book_inference_updates")}
        target_markets = {int(r[0]) for r in t.execute("select distinct market_id from wallet_shadow_target_events where asset='BTC' and quote_type='BID'")}
        markets = set(meta) & update_markets & target_markets
        max_end_env = int(os.environ.get("COORD_BUILD_MAX_END_MS", "0") or 0)
        if max_end_env > 0:
            markets = {m for m in markets if int(meta[m]["window_end_ms"]) <= max_end_env}
        events = load_events(t, markets)
        takers = load_taker_parents(t, markets)
        maker_parents = load_anchored_maker_parents(b, markets)
        valid = [m for m in markets if events.get(m)]

        hazard_rows: list[dict[str, Any]] = []
        taker_rows: list[dict[str, Any]] = []
        handoff_rows: list[dict[str, Any]] = []
        dropped = defaultdict(int); book_ages=[]

        for mi,m in enumerate(sorted(valid,key=lambda x:int(meta[x]["window_end_ms"])),1):
            mend = int(meta[m]["window_end_ms"]); mstart = mend - 300000
            ev = events[m]; tp = takers.get(m,[]); mp = maker_parents.get(m,[])
            taker_times = [int(p["first_event_ms"]) for p in tp]
            # Half-second offset avoids equality with second-quantized Target timestamps.
            checkpoints = list(range(mstart+500, mend-4500, 1000))
            taker_candidates = [{"kind":"TAKER","cp":int(p["first_event_ms"])-1,"p":p} for p in tp if mstart+1000 <= int(p["first_event_ms"]) <= mend-5000]
            hand_candidates = [{"kind":"HAND","cp":int(p["last_event_ms"])+1,"p":p} for p in tp if mstart+1000 <= int(p["last_event_ms"]) <= mend-5000]
            all_candidates = [{"kind":"HAZ","cp":cp} for cp in checkpoints] + taker_candidates + hand_candidates
            all_candidates.sort(key=lambda x:(int(x["cp"]),x["kind"]))

            inv = Inventory(); ei=0; ci=0; state={"bids":{},"asks":{}}; last_update=None
            for u in b.execute("select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id",(m,)):
                ut=int(u["source_timestamp_ms"])
                while ci<len(all_candidates) and int(all_candidates[ci]["cp"]) < ut:
                    c=all_candidates[ci]; cp=int(c["cp"])
                    while ei<len(ev) and int(ev[ei]["event_ms"]) <= cp:
                        inv.apply(ev[ei]); ei+=1
                    age=(cp-last_update) if last_update is not None else 10**9
                    if 0<=age<=2000:
                        f=inv.features(cp); cn=float(f.pop("_combined_net")); dom="UP" if cn>1e-9 else "DOWN" if cn<-1e-9 else None
                        bf=outcome_book(state,dom)
                        if bf:
                            base={"market_id":m,"market_end_ms":mend,"checkpoint_ms":cp,"book_age_ms":age,"seconds_left":(mend-cp)/1000.0,**f,**bf}
                            if c["kind"]=="HAZ":
                                j=bisect.bisect_right(taker_times,cp)
                                nxt=taker_times[j] if j<len(taker_times) else None
                                base.update({
                                    "label_taker_1s":int(nxt is not None and nxt<=cp+1000),
                                    "label_taker_3s":int(nxt is not None and nxt<=cp+3000),
                                    "label_taker_5s":int(nxt is not None and nxt<=cp+5000),
                                })
                                hazard_rows.append(base)
                            elif c["kind"]=="TAKER":
                                p=c["p"]; side=str(p["side"]); sh=float(p["shares"]); px=float(p["average_price"])
                                base.update({"parent_id":str(p["parent_id"]),"label_side":side,"label_effect":effect_label(cn,side,sh),"intervention_shares_label":sh,"intervention_avg_price_label":px})
                                taker_rows.append(base)
                            else:
                                p=c["p"]; side=str(p["side"]); sh=float(p["shares"]); px=float(p["average_price"])
                                # At last_event_ms+1, all own Taker fills for the parent are already part of inventory.
                                f2=base
                                post_cn = (inv.maker_up+inv.taker_up)-(inv.maker_down+inv.taker_down)
                                pg=inv.maker_up+inv.taker_up+inv.maker_down+inv.taker_down
                                pa=abs(post_cn)
                                f2.update({
                                    "parent_id":str(p["parent_id"]),
                                    "intervention_side":side,"intervention_side_is_up":float(side=="UP"),
                                    "intervention_shares":sh,"intervention_avg_price":px,
                                    "post_taker_combined_abs_net":pa,
                                    "post_taker_combined_imbalance_ratio":ratio(pa,pg),
                                    "post_taker_combined_paired_coverage":coverage(inv.maker_up+inv.taker_up,inv.maker_down+inv.taker_down),
                                    "post_taker_worst_case_floor":min(inv.cash+inv.maker_up+inv.taker_up,inv.cash+inv.maker_down+inv.taker_down),
                                    "label_handoff":maker_handoff_label(mp,side,int(p["last_event_ms"]),int(p["last_event_ms"])+5000),
                                })
                                handoff_rows.append(f2)
                            book_ages.append(age)
                        else: dropped['empty_book']+=1
                    else: dropped['book_stale']+=1
                    ci+=1
                if int(u["is_checkpoint"]):
                    state={"bids":{float(k):float(v) for k,v in (dec(u["native_bids_z"]) or {}).items()},"asks":{float(k):float(v) for k,v in (dec(u["native_asks_z"]) or {}).items()}}
                else: apply_changes(state,dec(u["changes_z"]) or {})
                last_update=ut
            while ci<len(all_candidates):
                c=all_candidates[ci]; cp=int(c["cp"])
                while ei<len(ev) and int(ev[ei]["event_ms"]) <= cp: inv.apply(ev[ei]); ei+=1
                # end-of-market leftovers are intentionally skipped if book is stale
                ci+=1
            if mi%50==0:
                print(json.dumps({"progressMarkets":mi,"totalMarkets":len(valid),"hazardRows":len(hazard_rows),"takerRows":len(taker_rows),"handoffRows":len(handoff_rows)}),flush=True)

        hz=pd.DataFrame(hazard_rows).sort_values(["market_end_ms","checkpoint_ms","market_id"]).reset_index(drop=True)
        tk=pd.DataFrame(taker_rows).sort_values(["market_end_ms","checkpoint_ms","market_id"]).reset_index(drop=True)
        hd=pd.DataFrame(handoff_rows).sort_values(["market_end_ms","checkpoint_ms","market_id"]).reset_index(drop=True)
        hz.to_csv(HAZARD_CSV,index=False); tk.to_csv(TAKER_CSV,index=False); hd.to_csv(HANDOFF_CSV,index=False)
        def stats(x:list[float])->dict[str,Any]:
            if not x:return {"n":0}
            a=sorted(float(v) for v in x); q=lambda p:a[int(round((len(a)-1)*p))]
            return {"n":len(a),"mean":statistics.mean(a),"median":statistics.median(a),"p90":q(.9),"max":max(a)}
        man={
            "reportVersion":VERSION,"researchOnly":True,"runtimeTargetDataAllowed":False,
            "coverage":{
                "candidateMarkets":len(valid),
                "hazard":{"rows":len(hz),"markets":int(hz.market_id.nunique()) if len(hz) else 0,"positiveRates":{h:float(hz[f'label_taker_{h}s'].mean()) if len(hz) else None for h in (1,3,5)}},
                "takerEvents":{"rows":len(tk),"markets":int(tk.market_id.nunique()) if len(tk) else 0,"side":tk.label_side.value_counts().to_dict() if len(tk) else {},"effect":tk.label_effect.value_counts().to_dict() if len(tk) else {}},
                "handoff":{"rows":len(hd),"markets":int(hd.market_id.nunique()) if len(hd) else 0,"labels":hd.label_handoff.value_counts().to_dict() if len(hd) else {}},
                "bookAgeMs":stats(book_ages),"dropped":dict(dropped),
            },
            "datasets":{"hazard":str(HAZARD_CSV),"taker":str(TAKER_CSV),"handoff":str(HANDOFF_CSV)},
            "featureSets":FEATURE_SETS,"handoffFeatureSets":HANDOFF_FEATURE_SETS,
            "semantics":{
                "hazard":"1-second half-offset checkpoints; strict-past own-reconstructable state; label if next Target Taker parent begins within horizon.",
                "effect":"post-hoc effect proxy from strict-past combined inventory plus full realized Taker parent shares; training label only.",
                "handoff":"after Taker parent completion, high-confidence anchored Maker placements in next 5s -> SAME/OPP/BOTH/PAUSE.",
            },
            "guards":["No winner input.","Target future action is label only.","No same-cohort parameter sweep.","Inference artifact must accept only equivalent own-state/public-book inputs."],
        }
        MANIFEST.write_text(json.dumps(man,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(man,ensure_ascii=False,indent=2))
        return man
    finally:
        b.close(); t.close()


def numeric(df:pd.DataFrame,features:list[str])->pd.DataFrame:
    return df[features].apply(pd.to_numeric,errors="coerce")


def bin_metrics(y:pd.Series,p:np.ndarray)->dict[str,Any]:
    p=np.clip(np.asarray(p,float),1e-7,1-1e-7); pred=(p>=.5).astype(int); both=set(y.astype(int).unique())=={0,1}
    return {"n":len(y),"positives":int(y.sum()),"positiveRate":float(y.mean()),"rocAuc":float(roc_auc_score(y,p)) if both else None,"averagePrecision":float(average_precision_score(y,p)) if int(y.sum()) else None,"logLoss":float(log_loss(y,p,labels=[0,1])),"brier":float(brier_score_loss(y,p)),"accuracy":float(accuracy_score(y,pred)),"balancedAccuracy":float(balanced_accuracy_score(y,pred)),"precisionAt05":float(precision_score(y,pred,zero_division=0)),"recallAt05":float(recall_score(y,pred,zero_division=0)),"f1At05":float(f1_score(y,pred,zero_division=0))}


def multi_metrics(y:pd.Series,pred:np.ndarray,prob:np.ndarray,classes:list[str])->dict[str,Any]:
    cm=confusion_matrix(y,pred,labels=classes); return {"n":len(y),"truthDistribution":{c:int((y==c).sum()) for c in classes},"predictedDistribution":{c:int(np.sum(pred==c)) for c in classes},"accuracy":float(accuracy_score(y,pred)),"balancedAccuracy":float(balanced_accuracy_score(y,pred)),"macroF1":float(f1_score(y,pred,labels=classes,average='macro',zero_division=0)),"logLoss":float(log_loss(y,prob,labels=classes)),"perClassRecall":{c:(float(cm[i,i]/cm[i].sum()) if cm[i].sum() else None) for i,c in enumerate(classes)},"confusionMatrix":{"labels":classes,"matrix":cm.tolist()}}


def train_hazard(feature_set:str) -> dict[str,Any]:
    df=pd.read_csv(HAZARD_CSV); sp=split_markets(df); features=FEATURE_SETS[feature_set]
    parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}; out={"reportVersion":VERSION,"task":"TAKER_HAZARD","featureSet":feature_set,"features":features,"splitMarkets":{k:len(v) for k,v in sp.items()},"horizons":{}}
    for h in (1,3,5):
        label=f"label_taker_{h}s"; tr,va,te=parts['train'],parts['validation'],parts['test']
        m=HistGradientBoostingClassifier(max_iter=350,learning_rate=.06,max_leaf_nodes=31,l2_regularization=1.0,early_stopping=True,validation_fraction=.12,n_iter_no_change=30,random_state=20260819+h)
        m.fit(numeric(tr,features),tr[label].astype(int)); art=OUT/f"hazard_{h}s_{feature_set.lower()}.joblib"; joblib.dump({"version":VERSION,"task":f"hazard_{h}s","featureSet":feature_set,"features":features,"model":m},art)
        out['horizons'][str(h)]={"train":bin_metrics(tr[label].astype(int),m.predict_proba(numeric(tr,features))[:,1]),"validation":bin_metrics(va[label].astype(int),m.predict_proba(numeric(va,features))[:,1]),"test":bin_metrics(te[label].astype(int),m.predict_proba(numeric(te,features))[:,1]),"artifact":str(art)}
    p=OUT/f"report_hazard_{feature_set.lower()}.json"; p.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2)); return out


def ebm(features:list[str])->ExplainableBoostingClassifier:
    return ExplainableBoostingClassifier(feature_names=features,max_bins=96,max_interaction_bins=48,interactions=6,outer_bags=6,learning_rate=.035,max_rounds=2500,early_stopping_rounds=100,min_samples_leaf=8,n_jobs=-2,random_state=20260819)


def top_terms(m:ExplainableBoostingClassifier,n:int=18)->list[dict[str,Any]]:
    imp=list(m.term_importances()); names=list(m.term_names_); ix=sorted(range(len(imp)),key=lambda i:float(imp[i]),reverse=True)[:n]; return [{"term":str(names[i]),"importance":float(imp[i])} for i in ix]


def train_multiclass(task:str,feature_set:str) -> dict[str,Any]:
    if task in {"SIDE","EFFECT"}:
        df=pd.read_csv(TAKER_CSV); label="label_side" if task=="SIDE" else "label_effect"; fsets=FEATURE_SETS
        # intervention_shares_label/price are labels/outcomes and intentionally not features.
    else:
        df=pd.read_csv(HANDOFF_CSV); label="label_handoff"; fsets=HANDOFF_FEATURE_SETS
    sp=split_markets(df); features=fsets[feature_set]; parts={k:df[df.market_id.astype(int).isin(v)].copy() for k,v in sp.items()}
    m=ebm(features); m.fit(numeric(parts['train'],features),parts['train'][label].astype(str).tolist()); classes=[str(x) for x in m.classes_]
    art=OUT/f"{task.lower()}_{feature_set.lower()}.joblib"; joblib.dump({"version":VERSION,"task":task,"featureSet":feature_set,"features":features,"classes":classes,"model":m},art)
    rep={"reportVersion":VERSION,"task":task,"featureSet":feature_set,"features":features,"splitMarkets":{k:len(v) for k,v in sp.items()},"train":multi_metrics(parts['train'][label].astype(str),m.predict(numeric(parts['train'],features)),m.predict_proba(numeric(parts['train'],features)),classes),"validation":multi_metrics(parts['validation'][label].astype(str),m.predict(numeric(parts['validation'],features)),m.predict_proba(numeric(parts['validation'],features)),classes),"test":multi_metrics(parts['test'][label].astype(str),m.predict(numeric(parts['test'],features)),m.predict_proba(numeric(parts['test'],features)),classes),"topTerms":top_terms(m),"artifact":str(art)}
    p=OUT/f"report_{task.lower()}_{feature_set.lower()}.json"; p.write_text(json.dumps(rep,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(rep,ensure_ascii=False,indent=2)); return rep


def consolidate()->dict[str,Any]:
    reports={}
    for p in OUT.glob('report_*.json'):
        try: reports[p.stem]=json.loads(p.read_text(encoding='utf-8'))
        except Exception: pass
    out={"reportVersion":VERSION,"manifest":json.loads(MANIFEST.read_text(encoding='utf-8')) if MANIFEST.exists() else None,"reports":reports,"guards":["Historical teacher labels never runtime features.","Chronological market split inside each dataset.","No hyperparameter sweep."]}
    COMPARISON.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8'); print(json.dumps(out,ensure_ascii=False,indent=2)); return out


def main()->int:
    ap=argparse.ArgumentParser(); ap.add_argument('--mode',required=True,choices=['build','hazard','side','effect','handoff','consolidate']); ap.add_argument('--feature-set',default='FULL'); a=ap.parse_args(); OUT.mkdir(parents=True,exist_ok=True)
    if a.mode=='build': build()
    elif a.mode=='hazard': train_hazard(a.feature_set)
    elif a.mode=='side': train_multiclass('SIDE',a.feature_set)
    elif a.mode=='effect': train_multiclass('EFFECT',a.feature_set)
    elif a.mode=='handoff': train_multiclass('HANDOFF',a.feature_set)
    else: consolidate()
    return 0

if __name__=='__main__': raise SystemExit(main())
