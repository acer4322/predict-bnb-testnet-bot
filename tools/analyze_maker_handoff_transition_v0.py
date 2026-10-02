from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, confusion_matrix, f1_score, log_loss

import train_target_maker_taker_coordination_big_v1 as coord

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
HANDOFF = OUT / "post_taker_handoff_states_v1.csv"
TAKER = OUT / "taker_event_states_v1.csv"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
BASELINE_REPORT = OUT / "report_handoff_full.json"
AUG_CSV = OUT / "post_taker_handoff_transition_v0.csv"
REPORT = OUT / "maker_handoff_transition_v0_report.json"

RUNTIME_TRANS = [
    "trans_duration_ms",
    "trans_combined_net_delta",
    "trans_combined_abs_net_delta",
    "trans_combined_paired_coverage_delta",
    "trans_floor_delta",
    "trans_best_case_delta",
    "trans_maker_net_delta",
    "trans_maker_abs_net_delta",
    "trans_maker_paired_coverage_delta",
    "trans_pair_bid_edge_delta",
    "trans_pair_ask_edge_delta",
    "trans_up_bid_delta",
    "trans_down_bid_delta",
    "trans_up_top3_bid_depth_delta",
    "trans_down_top3_bid_depth_delta",
    "trans_same_bid_delta",
    "trans_opp_bid_delta",
    "trans_same_top3_depth_delta",
    "trans_opp_top3_depth_delta",
    "trans_last_maker_same_age_delta_ms",
    "trans_last_maker_opp_age_delta_ms",
    "trans_maker_fills_during_count",
    "trans_maker_fills_during_shares",
    "trans_maker_same_fills_during_count",
    "trans_maker_opp_fills_during_count",
    "trans_maker_same_shares_during",
    "trans_maker_opp_shares_during",
    "trans_maker_fill_side_balance",
    "trans_maker_last_fill_same_is_recent",
]

ORACLE_TRANS = [
    "oracle_pre_place_1s_count",
    "oracle_pre_place_3s_count",
    "oracle_pre_place_5s_count",
    "oracle_pre_same_place_5s_count",
    "oracle_pre_opp_place_5s_count",
    "oracle_pre_same_place_5s_shares",
    "oracle_pre_opp_place_5s_shares",
    "oracle_pre_place_side_balance_5s",
    "oracle_last_place_age_ms",
    "oracle_last_same_place_age_ms",
    "oracle_last_opp_place_age_ms",
    "oracle_last_place_is_same",
    "oracle_prev_place_is_same",
    "oracle_last_two_place_switched",
    "oracle_place_switches_5s",
    "oracle_same_price_trend_5s",
    "oracle_opp_price_trend_5s",
    "oracle_recent_filled_parent_5s_count",
    "oracle_recent_same_resting_ms_mean",
    "oracle_recent_opp_resting_ms_mean",
]


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def fnum(v: Any) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else math.nan
    except Exception:
        return math.nan


def delta(post: pd.Series, pre: pd.Series, name: str) -> float:
    a, b = fnum(post.get(name)), fnum(pre.get(name))
    return a - b if math.isfinite(a) and math.isfinite(b) else math.nan


def side_value(row: pd.Series, side: str, suffix: str) -> float:
    return fnum(row.get(("up_" if side == "UP" else "down_") + suffix))


def last_age(row: pd.Series, role: str, side: str) -> float:
    return fnum(row.get(f"last_{role}_{side.lower()}_age_ms"))


def multi_metrics(y: pd.Series, pred: np.ndarray, prob: np.ndarray, classes: list[str]) -> dict[str, Any]:
    cm = confusion_matrix(y, pred, labels=classes)
    return {
        "n": len(y),
        "truthDistribution": {c: int((y == c).sum()) for c in classes},
        "predictedDistribution": {c: int(np.sum(pred == c)) for c in classes},
        "accuracy": float(accuracy_score(y, pred)),
        "balancedAccuracy": float(balanced_accuracy_score(y, pred)),
        "macroF1": float(f1_score(y, pred, labels=classes, average="macro", zero_division=0)),
        "logLoss": float(log_loss(y, prob, labels=classes)),
        "perClassRecall": {c: (float(cm[i, i] / cm[i].sum()) if cm[i].sum() else None) for i, c in enumerate(classes)},
        "confusionMatrix": {"labels": classes, "matrix": cm.tolist()},
    }


def train_eval(df: pd.DataFrame, features: list[str], name: str) -> tuple[dict[str, Any], Path]:
    sp = coord.split_markets(df)
    parts = {k: df[df.market_id.astype(int).isin(v)].copy() for k, v in sp.items()}
    m = coord.ebm(features)
    ytr = parts["train"]["label_handoff"].astype(str)
    m.fit(coord.numeric(parts["train"], features), ytr.tolist())
    classes = [str(x) for x in m.classes_]
    rep: dict[str, Any] = {"features": features, "splitMarkets": {k: len(v) for k, v in sp.items()}}
    for k in ("train", "validation", "test"):
        x = coord.numeric(parts[k], features)
        y = parts[k]["label_handoff"].astype(str)
        rep[k] = multi_metrics(y, m.predict(x), m.predict_proba(x), classes)
    rep["topTerms"] = coord.top_terms(m, 30)
    art = OUT / f"handoff_full_plus_{name}.joblib"
    joblib.dump({"version": "MAKER_HANDOFF_TRANSITION_V0", "features": features, "classes": classes, "model": m}, art)
    return rep, art


def build_runtime_transition(h: pd.DataFrame, t: pd.DataFrame, target: sqlite3.Connection) -> pd.DataFrame:
    pre_map = {str(r.parent_id): r for _, r in t.iterrows()}
    # Official Maker events are runtime-equivalent own fills; cache per market.
    maker_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for mid in sorted(set(h.market_id.astype(int))):
        for r in target.execute(
            """select event_ms,side,shares,price from wallet_shadow_target_events
               where asset='BTC' and quote_type='BID' and role='MAKER' and market_id=? order by event_ms,id""",
            (int(mid),),
        ):
            maker_events[int(mid)].append(dict(r))

    rows = []
    for _, post in h.iterrows():
        pre = pre_map.get(str(post.parent_id))
        if pre is None:
            continue
        side = str(post.intervention_side)
        opp = "DOWN" if side == "UP" else "UP"
        start_cp, end_cp = int(pre.checkpoint_ms), int(post.checkpoint_ms)
        evs = [e for e in maker_events[int(post.market_id)] if start_cp < int(e["event_ms"]) <= end_cp]
        same = [e for e in evs if str(e["side"]) == side]
        oppe = [e for e in evs if str(e["side"]) == opp]
        same_sh = sum(float(e["shares"]) for e in same)
        opp_sh = sum(float(e["shares"]) for e in oppe)
        total_sh = same_sh + opp_sh
        out = post.to_dict()
        out.update({
            "trans_duration_ms": float(end_cp - start_cp),
            "trans_combined_net_delta": delta(post, pre, "combined_net"),
            "trans_combined_abs_net_delta": delta(post, pre, "combined_abs_net"),
            "trans_combined_paired_coverage_delta": delta(post, pre, "combined_paired_coverage"),
            "trans_floor_delta": delta(post, pre, "worst_case_floor"),
            "trans_best_case_delta": delta(post, pre, "best_case_pnl"),
            "trans_maker_net_delta": delta(post, pre, "maker_net"),
            "trans_maker_abs_net_delta": delta(post, pre, "maker_abs_net"),
            "trans_maker_paired_coverage_delta": delta(post, pre, "maker_paired_coverage"),
            "trans_pair_bid_edge_delta": delta(post, pre, "pair_bid_edge"),
            "trans_pair_ask_edge_delta": delta(post, pre, "pair_ask_edge"),
            "trans_up_bid_delta": delta(post, pre, "up_bid"),
            "trans_down_bid_delta": delta(post, pre, "down_bid"),
            "trans_up_top3_bid_depth_delta": delta(post, pre, "up_top3_bid_depth"),
            "trans_down_top3_bid_depth_delta": delta(post, pre, "down_top3_bid_depth"),
            "trans_same_bid_delta": side_value(post, side, "bid") - side_value(pre, side, "bid"),
            "trans_opp_bid_delta": side_value(post, opp, "bid") - side_value(pre, opp, "bid"),
            "trans_same_top3_depth_delta": side_value(post, side, "top3_bid_depth") - side_value(pre, side, "top3_bid_depth"),
            "trans_opp_top3_depth_delta": side_value(post, opp, "top3_bid_depth") - side_value(pre, opp, "top3_bid_depth"),
            "trans_last_maker_same_age_delta_ms": last_age(post, "maker", side) - last_age(pre, "maker", side),
            "trans_last_maker_opp_age_delta_ms": last_age(post, "maker", opp) - last_age(pre, "maker", opp),
            "trans_maker_fills_during_count": float(len(evs)),
            "trans_maker_fills_during_shares": float(total_sh),
            "trans_maker_same_fills_during_count": float(len(same)),
            "trans_maker_opp_fills_during_count": float(len(oppe)),
            "trans_maker_same_shares_during": float(same_sh),
            "trans_maker_opp_shares_during": float(opp_sh),
            "trans_maker_fill_side_balance": float((same_sh - opp_sh) / total_sh) if total_sh > 1e-12 else 0.0,
            "trans_maker_last_fill_same_is_recent": float(bool(evs) and str(evs[-1]["side"]) == side),
        })
        rows.append(out)
    return pd.DataFrame(rows)


def add_oracle_transition(df: pd.DataFrame, book: sqlite3.Connection) -> pd.DataFrame:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    mids = sorted(set(df.market_id.astype(int)))
    for mid in mids:
        for r in book.execute(
            """select parent_id,target_side,target_price,placement_first_ms,placement_last_ms,
                      first_target_ms,last_target_ms,target_filled_shares,expected_parent_shares,
                      placement_allocated_shares,resting_ms,confidence
               from maker_book_inference_v21_parent_lifecycles where market_id=?
                and placement_first_ms is not null and placement_supports_18=1
                and placement_coverage>=.85 and fill_allocation_coverage>=.70 and confidence>=.75
               order by placement_first_ms,parent_id""",
            (int(mid),),
        ):
            by_market[int(mid)].append(dict(r))

    out_rows = []
    for _, row in df.iterrows():
        d = row.to_dict(); cp = int(row.checkpoint_ms); side = str(row.intervention_side); opp = "DOWN" if side == "UP" else "UP"
        ps = [p for p in by_market[int(row.market_id)] if int(p["placement_first_ms"]) <= cp]
        recent = [p for p in ps if cp - 5000 < int(p["placement_first_ms"]) <= cp]
        recent3 = [p for p in ps if cp - 3000 < int(p["placement_first_ms"]) <= cp]
        recent1 = [p for p in ps if cp - 1000 < int(p["placement_first_ms"]) <= cp]
        same = [p for p in recent if str(p["target_side"]) == side]
        oppl = [p for p in recent if str(p["target_side"]) == opp]
        ordered = sorted(recent, key=lambda p: (int(p["placement_first_ms"]), str(p["parent_id"])))
        switches = sum(1 for a,b in zip(ordered, ordered[1:]) if str(a["target_side"]) != str(b["target_side"]))
        def age_last(items: list[dict[str,Any]]) -> float:
            return float(cp - max(int(p["placement_first_ms"]) for p in items)) if items else math.nan
        def price_trend(items: list[dict[str,Any]]) -> float:
            q=sorted(items,key=lambda p:int(p["placement_first_ms"])); return float(q[-1]["target_price"])-float(q[0]["target_price"]) if len(q)>=2 else 0.0
        filled_recent = [p for p in ps if p.get("first_target_ms") is not None and cp-5000 < int(p["first_target_ms"]) <= cp]
        def rest_mean(items: list[dict[str,Any]], s: str) -> float:
            vals=[float(p["resting_ms"]) for p in items if str(p["target_side"])==s and p.get("resting_ms") is not None and int(p.get("first_target_ms") or 10**18)<=cp]
            return float(np.mean(vals)) if vals else math.nan
        same_sh=sum(float(p.get("placement_allocated_shares") or 0.0) for p in same); opp_sh=sum(float(p.get("placement_allocated_shares") or 0.0) for p in oppl); total=same_sh+opp_sh
        d.update({
            "oracle_pre_place_1s_count":float(len(recent1)), "oracle_pre_place_3s_count":float(len(recent3)), "oracle_pre_place_5s_count":float(len(recent)),
            "oracle_pre_same_place_5s_count":float(len(same)), "oracle_pre_opp_place_5s_count":float(len(oppl)),
            "oracle_pre_same_place_5s_shares":same_sh, "oracle_pre_opp_place_5s_shares":opp_sh,
            "oracle_pre_place_side_balance_5s":float((same_sh-opp_sh)/total) if total>1e-12 else 0.0,
            "oracle_last_place_age_ms":age_last(recent), "oracle_last_same_place_age_ms":age_last(same), "oracle_last_opp_place_age_ms":age_last(oppl),
            "oracle_last_place_is_same":float(bool(ordered) and str(ordered[-1]["target_side"])==side),
            "oracle_prev_place_is_same":float(len(ordered)>=2 and str(ordered[-2]["target_side"])==side),
            "oracle_last_two_place_switched":float(len(ordered)>=2 and str(ordered[-1]["target_side"])!=str(ordered[-2]["target_side"])),
            "oracle_place_switches_5s":float(switches), "oracle_same_price_trend_5s":price_trend(same), "oracle_opp_price_trend_5s":price_trend(oppl),
            "oracle_recent_filled_parent_5s_count":float(len(filled_recent)), "oracle_recent_same_resting_ms_mean":rest_mean(filled_recent,side), "oracle_recent_opp_resting_ms_mean":rest_mean(filled_recent,opp),
        })
        out_rows.append(d)
    return pd.DataFrame(out_rows)


def main() -> int:
    h = pd.read_csv(HANDOFF); t = pd.read_csv(TAKER)
    b, target = ro(BOOK_DB), ro(TARGET_DB)
    try:
        runtime = build_runtime_transition(h, t, target)
        oracle = add_oracle_transition(runtime, b)
    finally:
        b.close(); target.close()
    oracle.to_csv(AUG_CSV, index=False)

    base_features = coord.HANDOFF_FEATURE_SETS["FULL"]
    runtime_rep, runtime_art = train_eval(runtime, base_features + RUNTIME_TRANS, "runtime_transition_v0")
    oracle_rep, oracle_art = train_eval(oracle, base_features + RUNTIME_TRANS + ORACLE_TRANS, "runtime_transition_plus_oracle_v0")
    baseline = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    comp = {}
    for split in ("validation","test"):
        bb=float(baseline[split]["balancedAccuracy"]); bf=float(baseline[split]["macroF1"])
        comp[split]={
            "baselineBalancedAccuracy":bb,
            "runtimeTransitionBalancedAccuracy":float(runtime_rep[split]["balancedAccuracy"]),
            "runtimeTransitionLift":float(runtime_rep[split]["balancedAccuracy"])-bb,
            "oracleTransitionBalancedAccuracy":float(oracle_rep[split]["balancedAccuracy"]),
            "oracleTransitionLift":float(oracle_rep[split]["balancedAccuracy"])-bb,
            "baselineMacroF1":bf,
            "runtimeTransitionMacroF1":float(runtime_rep[split]["macroF1"]),
            "oracleTransitionMacroF1":float(oracle_rep[split]["macroF1"]),
        }
    report={
        "reportVersion":"MAKER_HANDOFF_TRANSITION_V0",
        "researchOnly":True,
        "question":"Does pre-to-post Taker completion execution/lifecycle transition explain subsequent 5s Maker handoff better than a completion snapshot?",
        "antiLeakGuard":"Runtime transition uses only state/fills available by Taker completion. Oracle placement features admit retrospective Target ownership inference but exclude placements after handoff checkpoint.",
        "coverage":{"rows":int(len(runtime)),"markets":int(runtime.market_id.nunique())},
        "runtimeTransitionFeatures":RUNTIME_TRANS,
        "teacherOracleTransitionFeatures":ORACLE_TRANS,
        "runtimeTransition":runtime_rep,
        "runtimePlusOracle":oracle_rep,
        "comparison":comp,
        "artifacts":{"runtime":str(runtime_art),"oracle":str(oracle_art),"augmentedCsv":str(AUG_CSV)},
        "interpretationBoundary":"A stable runtime-transition lift supports a deployable handoff state-transition model. Oracle-only lift would support further Target placement-state reconstruction; no lift rejects this feature family as the main handoff gap."
    }
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
