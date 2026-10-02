from __future__ import annotations

import bisect
import importlib.util
import json
import math
import sqlite3
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, balanced_accuracy_score, f1_score

ROOT = Path(__file__).resolve().parents[1]
P = ROOT / "tools" / "bridge_target_reentry_hazard_to_our_open_counterfactual_v0.py"
spec = importlib.util.spec_from_file_location("ownstate_fast_base", P)
br = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = br
spec.loader.exec_module(br)
base = br.base
mod = br.mod
coord = br.coord

OUT = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
CONTRACT = OUT / "forward_contract_v1.json"
HIST_T = OUT / "taker_event_states_v1.csv"
FRESH_T = OUT / "forward_taker_states_v1.csv"
REPORT = OUT / "taker_students_on_our_own_state_fast_v1_report.json"
MARKETS_CSV = OUT / "taker_students_on_our_own_state_fast_v1_markets.csv"
EVENTS_CSV = OUT / "taker_students_on_our_own_state_fast_v1_event_predictions.csv"
STATES_CSV = OUT / "taker_students_on_our_own_state_fast_v1_states.csv"
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def end_to_book_market(book: sqlite3.Connection) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in book.execute(
        "select market_id,window_end_ms from maker_book_inference_markets "
        "where window_end_ms is not null order by market_id"
    ):
        out.setdefault(int(r["window_end_ms"]), int(r["market_id"]))
    return out


def teacher_events() -> pd.DataFrame:
    parts: list[pd.DataFrame] = []
    for p in (HIST_T, FRESH_T):
        if not p.exists():
            continue
        d = pd.read_csv(p)
        keep = [c for c in ("market_id", "checkpoint_ms", "parent_id", "label_side", "label_effect") if c in d.columns]
        if len(keep) == 5:
            parts.append(d[keep].copy())
    if not parts:
        return pd.DataFrame(columns=["market_id", "checkpoint_ms", "parent_id", "label_side", "label_effect"])
    return pd.concat(parts, ignore_index=True, sort=False).drop_duplicates("parent_id", keep="last")


def numeric(df: pd.DataFrame, features: list[str]) -> pd.DataFrame:
    return df.reindex(columns=features).apply(pd.to_numeric, errors="coerce")


def multi_metrics(y: list[str], pred: list[str]) -> dict[str, Any]:
    if not y:
        return {"n": 0}
    return {
        "n": len(y),
        "accuracy": float(accuracy_score(y, pred)),
        "balancedAccuracy": float(balanced_accuracy_score(y, pred)),
        "macroF1": float(f1_score(y, pred, average="macro", zero_division=0)),
        "truthDistribution": pd.Series(y).value_counts().to_dict(),
        "predictedDistribution": pd.Series(pred).value_counts().to_dict(),
    }


def build_states() -> tuple[pd.DataFrame, dict[str, int]]:
    our = mod.ro(mod.DEFAULT_OUR_DB)
    book = ro(BOOK_DB)
    try:
        snapshots = mod.load_snapshots(our)
        seeds_by_market = mod.load_seeds(our)
        models = mod.maker_ebm.load_models()
        end_map = end_to_book_market(book)
        rows: list[dict[str, Any]] = []
        dropped: dict[str, int] = {}

        for n_market, our_market_id in enumerate(sorted(snapshots), 1):
            items = snapshots[our_market_id]
            if not items:
                continue
            first_snap = dict(items[0]["snapshot"])
            window_end = int(mod.snapshot_value(first_snap, "window_end_ms", "windowEndMs") or 0)
            target_market_id = end_map.get(window_end)
            if target_market_id is None:
                dropped["no_matching_8778_market"] = dropped.get("no_matching_8778_market", 0) + 1
                continue

            updates = list(book.execute(
                "select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z "
                "from maker_book_inference_updates where market_id=? order by source_timestamp_ms,id",
                (target_market_id,),
            ))
            if not updates:
                dropped["no_book_updates"] = dropped.get("no_book_updates", 0) + 1
                continue

            state: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
            ui = 0
            last_book: int | None = None
            ref = base.DirectionalSim(models)
            seeds = list(seeds_by_market.get(our_market_id, []))
            seed_idx = 0

            for item in items:
                now = int(item["decision_ms"])
                snap = dict(item["snapshot"])
                while ui < len(updates) and int(updates[ui]["source_timestamp_ms"]) <= now:
                    u = updates[ui]
                    if int(u["is_checkpoint"]):
                        state = {
                            "bids": {float(k): float(v) for k, v in (coord.dec(u["native_bids_z"]) or {}).items()},
                            "asks": {float(k): float(v) for k, v in (coord.dec(u["native_asks_z"]) or {}).items()},
                        }
                    else:
                        coord.apply_changes(state, coord.dec(u["changes_z"]) or {})
                    last_book = int(u["source_timestamp_ms"])
                    ui += 1

                ns = int(mod.num(snap.get("timestampNs")) or mod.num(snap.get("timestamp_ns")) or now * 1_000_000)
                maker_filled = ref.fill_existing(snap, ns, now)
                decision = ref.decide(snap, our_market_id, now)
                seed_filled = False
                while seed_idx < len(seeds) and int(seeds[seed_idx]["filled_at_ms"]) <= now:
                    ref.apply_seed(seeds[seed_idx])
                    seed_idx += 1
                    seed_filled = True

                age = now - last_book if last_book is not None else 10**9
                if 0 <= age <= 2000:
                    _, feat, combined_net, dominant = br.own_inventory(ref, now)
                    bf = coord.outcome_book(state, dominant)
                    if bf is not None:
                        rows.append({
                            "our_market_id": int(our_market_id),
                            "target_market_id": int(target_market_id),
                            "window_end_ms": int(window_end),
                            "decision_ms": int(now),
                            "book_age_ms": int(age),
                            "seconds_left": float((window_end - now) / 1000.0),
                            **feat,
                            **bf,
                        })
                    else:
                        dropped["empty_book"] = dropped.get("empty_book", 0) + 1
                else:
                    dropped["book_stale"] = dropped.get("book_stale", 0) + 1

                ref.apply_plan(decision, ns, now, allow_new=(maker_filled == 0 and not seed_filled))

            if n_market % 40 == 0:
                print(json.dumps({"progressMarkets": n_market, "total": len(snapshots), "states": len(rows)}), flush=True)

        return pd.DataFrame(rows).sort_values(["window_end_ms", "decision_ms", "our_market_id"]).reset_index(drop=True), dropped
    finally:
        our.close()
        book.close()


def attach_teacher_events(states: pd.DataFrame, teacher: pd.DataFrame) -> pd.DataFrame:
    if states.empty or teacher.empty:
        return pd.DataFrame()
    chunks: list[dict[str, Any]] = []
    state_groups = {int(m): g.sort_values("decision_ms") for m, g in states.groupby("target_market_id")}
    for _, e in teacher.iterrows():
        tm = int(e["market_id"])
        g = state_groups.get(tm)
        if g is None or g.empty:
            continue
        ts = g["decision_ms"].astype("int64").to_numpy()
        t = int(e["checkpoint_ms"])
        j = int(np.searchsorted(ts, t, side="right") - 1)
        if j < 0 or t - int(ts[j]) > 2000:
            continue
        r = g.iloc[j].to_dict()
        r.update({
            "target_parent_id": str(e["parent_id"]),
            "target_event_checkpoint_ms": t,
            "state_age_ms": int(t - int(ts[j])),
            "truth_side": str(e["label_side"]),
            "truth_effect": str(e["label_effect"]),
        })
        chunks.append(r)
    return pd.DataFrame(chunks)


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    hazard = joblib.load(contract["artifacts"]["hazard_1s"])
    side = joblib.load(contract["artifacts"]["side"])
    effect = joblib.load(contract["artifacts"]["effect"])

    states, dropped = build_states()
    if states.empty:
        raise RuntimeError("no OUR own-state rows")

    # Batch inference: this is the main performance difference from the failed V0.
    hfs = list(hazard["features"])
    states["p_taker_1s"] = hazard["model"].predict_proba(numeric(states, hfs))[:, 1]

    target = ro(TARGET_DB)
    try:
        actual_counts = {
            int(r["market_id"]): int(r["n"])
            for r in target.execute(
                "select market_id,count(*) n from target_parent_orders "
                "where asset='BTC' and role='TAKER' and quote_type='BID' group by market_id"
            )
        }
    finally:
        target.close()

    market_rows: list[dict[str, Any]] = []
    for (our_m, target_m), g in states.groupby(["our_market_id", "target_market_id"], sort=False):
        g = g.sort_values("decision_ms")
        p = g["p_taker_1s"].astype(float).to_numpy()
        ts = g["decision_ms"].astype("int64").to_numpy()
        if len(ts) > 1:
            dt = np.diff(ts) / 1000.0
            dt = np.concatenate([dt, [1.0]])
        else:
            dt = np.array([1.0])
        dt = np.clip(dt, 0.0, 5.0)
        lam = -np.log(np.maximum(1e-9, 1.0 - np.clip(p, 0.0, 0.999999)))
        expected = float(np.sum(lam * dt))
        market_rows.append({
            "ourMarketId": int(our_m),
            "targetMarketId": int(target_m),
            "windowEndMs": int(g["window_end_ms"].iloc[0]),
            "states": int(len(g)),
            "sumP1": float(np.sum(p)),
            "exposureExpectedTakerCount": expected,
            "meanP1": float(np.mean(p)),
            "targetTakerParents": int(actual_counts.get(int(target_m), 0)),
        })
    mdf = pd.DataFrame(market_rows).sort_values("windowEndMs")

    teacher = teacher_events()
    events = attach_teacher_events(states, teacher)
    if len(events):
        events["p_taker_1s_on_our_state"] = hazard["model"].predict_proba(numeric(events, hfs))[:, 1]
        sfs = list(side["features"])
        efs = list(effect["features"])
        events["pred_side"] = side["model"].predict(numeric(events, sfs))
        events["pred_effect"] = effect["model"].predict(numeric(events, efs))

    STATES_CSV.parent.mkdir(parents=True, exist_ok=True)
    # Keep a bounded, useful state artifact rather than duplicating all raw JSON inputs.
    states.to_csv(STATES_CSV, index=False)
    mdf.to_csv(MARKETS_CSV, index=False)
    events.to_csv(EVENTS_CSV, index=False)

    hazard_summary: dict[str, Any] = {"markets": len(mdf)}
    if len(mdf):
        exp = mdf["exposureExpectedTakerCount"].astype(float)
        actual = mdf["targetTakerParents"].astype(float)
        hazard_summary.update({
            "targetParentsTotal": int(actual.sum()),
            "sumP1Total": float(mdf["sumP1"].sum()),
            "exposureExpectedTotal": float(exp.sum()),
            "meanTargetPerMarket": float(actual.mean()),
            "meanExpectedPerMarket": float(exp.mean()),
            "spearmanExpectedVsActual": float(exp.corr(actual, method="spearman")),
            "pearsonExpectedVsActual": float(exp.corr(actual, method="pearson")),
            "maeExpectedCount": float((exp - actual).abs().mean()),
            "medianExpectedCount": float(exp.median()),
            "medianActualCount": float(actual.median()),
        })

    side_metrics = multi_metrics(events["truth_side"].astype(str).tolist(), events["pred_side"].astype(str).tolist()) if len(events) else None
    effect_metrics = multi_metrics(events["truth_effect"].astype(str).tolist(), events["pred_effect"].astype(str).tolist()) if len(events) else None

    report = {
        "reportVersion": "TAKER_STUDENTS_ON_OUR_OWN_STATE_FAST_V1",
        "researchOnly": True,
        "runtimeTargetDataAllowed": False,
        "question": "On the same public market tape, how much do frozen Target Taker students retain when portfolio/lifecycle inputs are OUR own endogenous state rather than Target teacher state?",
        "coverage": {
            "ourOwnStateRows": int(len(states)),
            "markets": int(states["our_market_id"].nunique()),
            "targetEventComparisons": int(len(events)),
            "dropped": dropped,
        },
        "hazardExpectedCount": hazard_summary,
        "sideOnOurStateAtTargetEventTimes": side_metrics,
        "effectOnOurStateAtTargetEventTimes": effect_metrics,
        "meanP1AtTargetEventTimes": float(events["p_taker_1s_on_our_state"].mean()) if len(events) else None,
        "medianP1AtTargetEventTimes": float(events["p_taker_1s_on_our_state"].median()) if len(events) else None,
        "referenceTargetTeacherStateFresh": {
            "hazard1sAucApprox": 0.8505,
            "sideBalancedApprox": 0.8053,
            "effectBalancedApprox": 0.7446,
        },
        "artifacts": {
            "statesCsv": str(STATES_CSV),
            "marketCsv": str(MARKETS_CSV),
            "eventCsv": str(EVENTS_CSV),
        },
        "guards": [
            "Frozen Target models are unchanged; no threshold or retraining.",
            "Target action/effect appears only as retrospective evaluation truth, never as state input.",
            "Public market tape is time-aligned through 8778 window_end_ms; portfolio/lifecycle is reconstructed only from OUR paper path.",
            "Expected Taker count uses probability exposure, not an arbitrarily tuned 0.5 trigger threshold.",
        ],
        "interpretationBoundary": "If side/effect and hazard activity collapse on OUR own state while staying strong on Target teacher states, domain adaptation/state-distribution alignment is required before target-blind closed-loop. If they remain strong, a stochastic closed-loop replay is justified next.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
