from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sqlite3
import statistics
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIR_PATH = ROOT / "tools" / "analyze_target_maker_direction_and_maker_only_v1.py"
TOX_PATH = ROOT / "tools" / "analyze_target_maker_fill_toxicity_v1.py"

spec = importlib.util.spec_from_file_location("target_dir_v1", DIR_PATH)
dirv = importlib.util.module_from_spec(spec); assert spec and spec.loader; sys.modules[spec.name]=dirv; spec.loader.exec_module(dirv)
spec2 = importlib.util.spec_from_file_location("target_tox_v1", TOX_PATH)
tox = importlib.util.module_from_spec(spec2); assert spec2 and spec2.loader; sys.modules[spec2.name]=tox; spec2.loader.exec_module(tox)

BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
PUBLIC_DB = ROOT / "data" / "strategy_target_compare_v1.db"
REPORT = ROOT / "data" / "research" / "target_mature_mm_knob_projection_v0_report.json"
PLACEMENT_CSV = ROOT / "data" / "research" / "target_mature_mm_knob_projection_v0_placements.csv"
REACTION_CSV = ROOT / "data" / "research" / "target_mature_mm_knob_projection_v0_reactions.csv"
VERSION = "TARGET_MATURE_MM_KNOB_PROJECTION_V0"
GRID = 0.01
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def q(xs: list[float], p: float) -> float | None:
    if not xs: return None
    ys = sorted(xs)
    if len(ys)==1: return ys[0]
    pos=(len(ys)-1)*p; lo=int(math.floor(pos)); hi=int(math.ceil(pos)); w=pos-lo
    return ys[lo]*(1-w)+ys[hi]*w


def stats(xs: list[float]) -> dict[str, Any]:
    ys=[float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {"n":len(ys),"min":min(ys) if ys else None,"max":max(ys) if ys else None,
            "mean":statistics.mean(ys) if ys else None,"median":statistics.median(ys) if ys else None,
            "p25":q(ys,.25),"p75":q(ys,.75),"p90":q(ys,.90)}


def load_public(con: sqlite3.Connection) -> dict[int,list[tuple[int,dict[str,Any]]]]:
    return tox.load_public(con)


def best_bid(s: dict[str,Any], side: str) -> float | None:
    return tox.side_bid(s, side)


def side_mid(s: dict[str,Any], side: str) -> float | None:
    return tox.side_mid(s, side)


def public_before(rows:list[tuple[int,dict[str,Any]]], ms:int, max_age:int=2000):
    return tox.at_or_before(rows, ms, max_age)


def public_after(rows:list[tuple[int,dict[str,Any]]], ms:int, max_delay:int=2000):
    return tox.at_or_after(rows, ms, max_delay)


def load_parents(book:sqlite3.Connection, markets:set[int]) -> dict[int,list[dict[str,Any]]]:
    return tox.load_parents(book, markets)


def load_fills(target:sqlite3.Connection, markets:set[int]) -> dict[int,list[dict[str,Any]]]:
    return tox.load_maker_fills(target, markets)


def inventory_before(fills:list[dict[str,Any]], ms:int) -> dict[str,float|str|None]:
    up=down=up_cost=down_cost=0.0
    for f in fills:
        if int(f["event_ms"]) >= ms: break
        side=str(f["side"]); sh=float(f["shares"]); px=float(f["price"])
        if side=="UP": up+=sh; up_cost+=sh*px
        else: down+=sh; down_cost+=sh*px
    gross=up+down; net=up-down; absn=abs(net); cost=up_cost+down_cost
    dom="UP" if net>EPS else "DOWN" if net<-EPS else None
    return {
        "up":up,"down":down,"upCost":up_cost,"downCost":down_cost,"gross":gross,"net":net,"absNet":absn,
        "ratio":absn/gross if gross>EPS else 0.0,
        "pairedCoverage":2*min(up,down)/gross if gross>EPS else 1.0,
        "dominant":dom,
        "minority":"DOWN" if dom=="UP" else "UP" if dom=="DOWN" else None,
        "worstCaseFloor":min(up-cost,down-cost),
    }


def inventory_after(fills:list[dict[str,Any]], ms:int) -> dict[str,float|str|None]:
    up=down=up_cost=down_cost=0.0
    for f in fills:
        if int(f["event_ms"]) > ms: break
        side=str(f["side"]); sh=float(f["shares"]); px=float(f["price"])
        if side=="UP": up+=sh; up_cost+=sh*px
        else: down+=sh; down_cost+=sh*px
    gross=up+down; net=up-down; absn=abs(net); cost=up_cost+down_cost
    dom="UP" if net>EPS else "DOWN" if net<-EPS else None
    return {
        "up":up,"down":down,"gross":gross,"net":net,"absNet":absn,"ratio":absn/gross if gross>EPS else 0.0,
        "pairedCoverage":2*min(up,down)/gross if gross>EPS else 1.0,
        "dominant":dom,"minority":"DOWN" if dom=="UP" else "UP" if dom=="DOWN" else None,
        "worstCaseFloor":min(up-cost,down-cost),
    }


def last_fill_before(fills:list[dict[str,Any]], ms:int, side:str|None=None) -> dict[str,Any]|None:
    for f in reversed(fills):
        if int(f["event_ms"]) >= ms: continue
        if side is None or str(f["side"])==side: return f
    return None


def fill_streak_before(fills:list[dict[str,Any]], ms:int) -> int:
    prev=[f for f in fills if int(f["event_ms"])<ms]
    if not prev: return 0
    side=str(prev[-1]["side"]); n=0
    for f in reversed(prev):
        if str(f["side"])!=side: break
        n+=1
    return n


def markout1s_known(pub:list[tuple[int,dict[str,Any]]], fill:dict[str,Any]|None, decision_ms:int) -> float|None:
    if not fill: return None
    t0=int(fill["event_ms"]); side=str(fill["side"])
    before=public_before(pub,t0,2000); after=public_after(pub,t0+1000,2000)
    if before is None or after is None or int(after[0])>decision_ms: return None
    m0=side_mid(before[1],side); m1=side_mid(after[1],side)
    if m0 is None or m1 is None: return None
    return (float(m1)-float(m0))/GRID


def next_parent(parents:list[dict[str,Any]], lo:int, hi:int, side:str|None=None) -> dict[str,Any]|None:
    for p in parents:
        t=int(p["placement_first_ms"])
        if t<=lo: continue
        if t>hi: break
        if side is None or str(p["target_side"])==side: return p
    return None


def knob_summary(rows:list[dict[str,Any]]) -> dict[str,Any]:
    return {
        "n":len(rows),"markets":len({int(r["marketId"]) for r in rows}),
        "secondsLeft":stats([r["secondsLeft"] for r in rows]),
        "absNet":stats([r["absNet"] for r in rows]),
        "imbalanceRatio":stats([r["imbalanceRatio"] for r in rows]),
        "pairedCoverage":stats([r["pairedCoverage"] for r in rows]),
        "worstCaseFloor":stats([r["worstCaseFloor"] for r in rows]),
        "quoteOffsetTicks":stats([r["quoteOffsetTicks"] for r in rows if r.get("quoteOffsetTicks") is not None]),
        "restingMs":stats([r["restingMs"] for r in rows if r.get("restingMs") is not None]),
        "lastSameSideFillAgeMs":stats([r["lastSameSideFillAgeMs"] for r in rows if r.get("lastSameSideFillAgeMs") is not None]),
        "lastSameSideMarkout1sTicks":stats([r["lastSameSideMarkout1sTicks"] for r in rows if r.get("lastSameSideMarkout1sTicks") is not None]),
        "sameSideFillStreak":stats([r["sameSideFillStreak"] for r in rows]),
    }


def reaction_summary(rows:list[dict[str,Any]]) -> dict[str,Any]:
    return {
        "n":len(rows),"markets":len({int(r["marketId"]) for r in rows}),
        "secondsLeftAt1s":stats([r["secondsLeftAt1s"] for r in rows]),
        "postAbsNet":stats([r["postAbsNet"] for r in rows]),
        "postPairedCoverage":stats([r["postPairedCoverage"] for r in rows]),
        "markout1sTicks":stats([r["markout1sTicks"] for r in rows]),
        "currentParentOffsetTicks":stats([r["currentParentOffsetTicks"] for r in rows if r.get("currentParentOffsetTicks") is not None]),
        "currentRestingMs":stats([r["currentRestingMs"] for r in rows if r.get("currentRestingMs") is not None]),
        "nextSameDelayFromFillMs":stats([r["nextSameDelayFromFillMs"] for r in rows if r.get("nextSameDelayFromFillMs") is not None]),
    }


def main()->int:
    book=ro(BOOK_DB); target=ro(TARGET_DB); public_db=ro(PUBLIC_DB)
    try:
        public=load_public(public_db); markets=set(public)
        parents_by=load_parents(book,markets); fills_by=load_fills(target,markets)
        placement_rows=[]; reaction_rows=[]
        for mid in sorted(markets):
            pub=public.get(mid,[]); parents=parents_by.get(mid,[]); fills=fills_by.get(mid,[])
            if not pub or not parents or not fills: continue
            for p in parents:
                place=int(p["placement_first_ms"]); fill_ms=int(p["last_target_ms"]); side=str(p["target_side"])
                pb=public_before(pub,place,2000)
                if pb is not None:
                    inv=inventory_before(fills,place); dom=inv["dominant"]; minority=inv["minority"]
                    direction,strength=dirv.simple3(pb[1]); sec=tox.seconds_left(pb[1]); bid=best_bid(pb[1],side)
                    offset=(float(bid)-float(p["target_price"]))/GRID if bid is not None else None
                    role="DOMINANT" if dom and side==dom else "MINORITY" if minority and side==minority else "FLAT"
                    align="TAILWIND" if dom and direction and dom==direction else "HEADWIND" if dom and direction else "UNKNOWN"
                    last_same=last_fill_before(fills,place,side)
                    last_any=last_fill_before(fills,place,None)
                    m1=markout1s_known(pub,last_same,place)
                    action="OTHER"
                    if role=="DOMINANT" and align=="HEADWIND" and float(inv["absNet"])>=18-EPS: action="ALLOW_HEADWIND_DOMINANT"
                    elif role=="MINORITY" and align=="HEADWIND" and float(inv["absNet"])>=18-EPS: action="CORRECT_HEADWIND_MINORITY"
                    elif role=="DOMINANT" and align=="TAILWIND" and float(inv["absNet"])>=18-EPS: action="ALLOW_TAILWIND_DOMINANT"
                    elif role=="MINORITY" and align=="TAILWIND" and float(inv["absNet"])>=18-EPS: action="CORRECT_TAILWIND_MINORITY"
                    placement_rows.append({
                        "marketId":mid,"placementMs":place,"side":side,"actionClass":action,
                        "inventoryRole":role,"alignment":align,"simple3Direction":direction,"simple3Strength":strength,
                        "secondsLeft":sec,"absNet":inv["absNet"],"gross":inv["gross"],"imbalanceRatio":inv["ratio"],
                        "pairedCoverage":inv["pairedCoverage"],"worstCaseFloor":inv["worstCaseFloor"],
                        "quoteOffsetTicks":offset,"restingMs":p.get("resting_ms"),
                        "lastSameSideFillAgeMs":place-int(last_same["event_ms"]) if last_same else None,
                        "lastAnyFillAgeMs":place-int(last_any["event_ms"]) if last_any else None,
                        "lastSameSideMarkout1sTicks":m1,
                        "sameSideFillStreak":fill_streak_before(fills,place),
                    })

                # Post-fill +1s reaction checkpoint: markout is known, then inspect (1s,5s].
                b0=public_before(pub,fill_ms,2000); a1=public_after(pub,fill_ms+1000,2000)
                if b0 is None or a1 is None: continue
                mid0=side_mid(b0[1],side); mid1=side_mid(a1[1],side)
                if mid0 is None or mid1 is None: continue
                sec1=tox.seconds_left(a1[1])
                if sec1 is None or sec1<4.0: continue
                post=inventory_after(fills,fill_ms); dom2=post["dominant"]; min2=post["minority"]
                direction2,strength2=dirv.simple3(a1[1]); align2="TAILWIND" if dom2 and direction2 and dom2==direction2 else "HEADWIND" if dom2 and direction2 else "UNKNOWN"
                role2="DOMINANT" if dom2 and side==dom2 else "MINORITY" if min2 and side==min2 else "FLAT"
                ns=next_parent(parents,fill_ms+1000,fill_ms+5000,side)
                no=next_parent(parents,fill_ms+1000,fill_ms+5000,("DOWN" if side=="UP" else "UP"))
                anyp=next_parent(parents,fill_ms+1000,fill_ms+5000,None)
                if ns is not None and (anyp is None or int(ns["placement_first_ms"])==int(anyp["placement_first_ms"])):
                    reaction="CONTINUE_SAME_FIRST"
                elif no is not None and (anyp is None or int(no["placement_first_ms"])==int(anyp["placement_first_ms"])):
                    reaction="SWITCH_OPPOSITE_FIRST"
                else:
                    reaction="PAUSE_NO_PARENT_1_TO_5S"
                bid0=best_bid(b0[1],side)
                off0=(float(bid0)-float(p["target_price"]))/GRID if bid0 is not None else None
                reaction_rows.append({
                    "marketId":mid,"fillMs":fill_ms,"side":side,"reactionClass":reaction,
                    "inventoryRolePostFill":role2,"alignmentAt1s":align2,"simple3DirectionAt1s":direction2,"simple3StrengthAt1s":strength2,
                    "secondsLeftAt1s":sec1,"postAbsNet":post["absNet"],"postImbalanceRatio":post["ratio"],
                    "postPairedCoverage":post["pairedCoverage"],"postWorstCaseFloor":post["worstCaseFloor"],
                    "markout1sTicks":(float(mid1)-float(mid0))/GRID,
                    "toxicity1s":tox.toxicity_label((float(mid1)-float(mid0))/GRID),
                    "currentParentOffsetTicks":off0,"currentRestingMs":p.get("resting_ms"),
                    "nextSameDelayFromFillMs":int(ns["placement_first_ms"])-fill_ms if ns is not None else None,
                })

        # Descriptive mature-knob projection.
        placement_groups={}
        for name in ["ALLOW_HEADWIND_DOMINANT","CORRECT_HEADWIND_MINORITY","ALLOW_TAILWIND_DOMINANT","CORRECT_TAILWIND_MINORITY"]:
            placement_groups[name]=knob_summary([r for r in placement_rows if r["actionClass"]==name])

        headwind_dom=[r for r in placement_rows if r["actionClass"]=="ALLOW_HEADWIND_DOMINANT"]
        budget_bins={}
        for lo,hi,name in [(18,36,"18_35"),(36,54,"36_53"),(54,90,"54_89"),(90,10**9,"90_PLUS")]:
            rr=[r for r in headwind_dom if lo<=float(r["absNet"])<hi]
            budget_bins[name]=knob_summary(rr)

        reactions_dom=[r for r in reaction_rows if r["inventoryRolePostFill"]=="DOMINANT"]
        reaction_groups={name:reaction_summary([r for r in reactions_dom if r["reactionClass"]==name])
                         for name in ["CONTINUE_SAME_FIRST","SWITCH_OPPOSITE_FIRST","PAUSE_NO_PARENT_1_TO_5S"]}
        reaction_by_tox={}
        for label in ["TOXIC_1T_PLUS","NEUTRAL_LT1T","FAVORABLE_1T_PLUS"]:
            rr=[r for r in reactions_dom if r["toxicity1s"]==label]
            reaction_by_tox[label]={
                "ALL":reaction_summary(rr),
                "continueSameRate":sum(r["reactionClass"]=="CONTINUE_SAME_FIRST" for r in rr)/len(rr) if rr else None,
                "switchOppositeRate":sum(r["reactionClass"]=="SWITCH_OPPOSITE_FIRST" for r in rr)/len(rr) if rr else None,
                "pauseRate":sum(r["reactionClass"]=="PAUSE_NO_PARENT_1_TO_5S" for r in rr)/len(rr) if rr else None,
            }

        report={
            "reportVersion":VERSION,"researchOnly":True,"liveTradingChanges":False,
            "purpose":"Project real Target Maker decisions into mature-MM knob language instead of fitting one global policy.",
            "coverage":{"publicMarkets":len(public),"placementRows":len(placement_rows),"placementMarkets":len({r['marketId'] for r in placement_rows}),
                        "reactionRows":len(reaction_rows),"reactionMarkets":len({r['marketId'] for r in reaction_rows})},
            "method":{
                "placementCheckpoint":"At inferred Target parent placement, use only <=2s strict-past public snapshot and strict-past Target Maker BID inventory.",
                "reactionCheckpoint":"At parent fill +1s, after 1s markout is observable, inspect next inferred parent in (1s,5s].",
                "matureKnobProjection":["quote depth offset ticks","resting time","inventory budget / tolerated headwind abs-net","paired coverage","worst-case floor","refill delay / side switch / pause","1s fill toxicity"],
                "warning":"Target placement/cancel ownership is inferred from public depth plus later Target fill anchor; not private-order ground truth. SIMPLE3 is only a public direction proxy."
            },
            "placementCheckpoint":{
                "actionGroups":placement_groups,
                "headwindDominantAllowanceByCurrentAbsNet":budget_bins,
                "interpretation":"ALLOW_HEADWIND_DOMINANT means Target created a dominant-side anchored parent despite strict-past dominant Maker inventory already opposing SIMPLE3 and abs-net>=18; it is an observational lower bound on tolerated inventory budget, not proof of a hard threshold."
            },
            "postFillReactionCheckpoint":{
                "dominantReactionGroups":reaction_groups,
                "dominantReactionBy1sToxicity":reaction_by_tox,
            },
        }
        REPORT.parent.mkdir(parents=True,exist_ok=True)
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        try:
            import pandas as pd
            pd.DataFrame(placement_rows).sort_values(["placementMs","marketId"]).to_csv(PLACEMENT_CSV,index=False)
            pd.DataFrame(reaction_rows).sort_values(["fillMs","marketId"]).to_csv(REACTION_CSV,index=False)
        except Exception:
            pass
        print(json.dumps(report,ensure_ascii=False,indent=2))
    finally:
        book.close(); target.close(); public_db.close()
    return 0


if __name__=="__main__":
    raise SystemExit(main())
