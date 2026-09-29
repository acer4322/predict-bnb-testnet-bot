from __future__ import annotations

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
from sklearn.metrics import average_precision_score, roc_auc_score

ROOT = Path(__file__).resolve().parents[1]

# OUR stable-directional replay/counterfactual helpers.
P_CF = ROOT / "tools" / "our_postfill_arbitration_counterfactual_v0.py"
spec = importlib.util.spec_from_file_location("our_open_bridge_cf", P_CF)
cf = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = cf
spec.loader.exec_module(cf)
base = cf.base
mod = cf.mod

# Target Maker↔Taker feature semantics.
P_COORD = ROOT / "tools" / "train_target_maker_taker_coordination_big_v1.py"
spec2 = importlib.util.spec_from_file_location("our_open_bridge_coord", P_COORD)
coord = importlib.util.module_from_spec(spec2)
assert spec2 and spec2.loader
sys.modules[spec2.name] = coord
spec2.loader.exec_module(coord)

VERSION = "TARGET_REENTRY_HAZARD_TO_OUR_OPEN_COUNTERFACTUAL_V0"
OUT = ROOT / "data" / "research" / "target_maker_taker_coordination_big_v1"
REPORT = OUT / "our_open_reentry_hazard_bridge_v0_report.json"
DATA = OUT / "our_open_reentry_hazard_bridge_v0_states.csv"

BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
SAME_ART = OUT / "post_taker_reentry_same_plus_memory_v0.joblib"
OPP_ART = OUT / "post_taker_reentry_opp_plus_memory_v0.joblib"

CHECKPOINT_DELAY_MS = 500
ARBITRATION_WINDOW_MS = 4000
HORIZON_MS = 20000
MIN_HORIZON_MS = 16000
ACTIONS = ("CONTINUE_SAME", "SWITCH_OPPOSITE", "PAUSE")
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("pragma query_only=on")
    return con


def first_book_market_by_end(book: sqlite3.Connection) -> dict[int, int]:
    out: dict[int, int] = {}
    for r in book.execute("select market_id,window_end_ms from maker_book_inference_markets where window_end_ms is not null order by market_id"):
        out.setdefault(int(r["window_end_ms"]), int(r["market_id"]))
    return out


def book_state_before(book: sqlite3.Connection, market_id: int, cp_ms: int) -> tuple[dict[str, dict[float, float]], int | None]:
    state: dict[str, dict[float, float]] = {"bids": {}, "asks": {}}
    last: int | None = None
    for u in book.execute(
        "select source_timestamp_ms,is_checkpoint,native_bids_z,native_asks_z,changes_z "
        "from maker_book_inference_updates where market_id=? and source_timestamp_ms<=? order by source_timestamp_ms,id",
        (market_id, cp_ms),
    ):
        ut = int(u["source_timestamp_ms"])
        if int(u["is_checkpoint"]):
            state = {
                "bids": {float(k): float(v) for k, v in (coord.dec(u["native_bids_z"]) or {}).items()},
                "asks": {float(k): float(v) for k, v in (coord.dec(u["native_asks_z"]) or {}).items()},
            }
        else:
            coord.apply_changes(state, coord.dec(u["changes_z"]) or {})
        last = ut
    return state, last


def own_inventory(ref: base.DirectionalSim, now_ms: int) -> tuple[coord.Inventory, dict[str, float], float, str | None]:
    inv = coord.Inventory()
    ev: list[dict[str, Any]] = []
    for f in ref.maker_fills:
        t = int(f.get("at_ms") or 0)
        if t <= now_ms:
            ev.append({"event_ms": t, "role": "MAKER", "side": str(f["side"]), "price": float(f["price"]), "shares": float(f["shares"])})
    for f in ref.seed_fills:
        t = int(f.get("filled_at_ms") or 0)
        if t <= now_ms:
            ev.append({"event_ms": t, "role": "TAKER", "side": str(f["side"]), "price": float(f["price"]), "shares": float(f["shares"])})
    ev.sort(key=lambda x: int(x["event_ms"]))
    for e in ev:
        inv.apply(e)
    feat = inv.features(now_ms)
    cn = float(feat.pop("_combined_net"))
    dom = "UP" if cn > EPS else "DOWN" if cn < -EPS else None
    return inv, feat, cn, dom


def post_placement_memory(ref: base.DirectionalSim, taker_ms: int, cp_ms: int, taker_side: str) -> dict[str, float]:
    # OUR simulator does not retain cancelled placement history. At the first +0.5s checkpoint,
    # current orders placed after the OPEN seed are a conservative runtime-equivalent memory proxy.
    opp = "DOWN" if taker_side == "UP" else "UP"
    active = [o for o in ref.orders.values() if taker_ms < int(o.placed_at_ms) <= cp_ms]
    same = [o for o in active if str(o.side) == taker_side]
    other = [o for o in active if str(o.side) == opp]
    ordered = sorted(active, key=lambda o: int(o.placed_at_ms))

    def age(xs: list[Any]) -> float:
        return float(cp_ms - max(int(o.placed_at_ms) for o in xs)) if xs else math.nan

    total = len(same) + len(other)
    return {
        "post_same_placements_so_far": float(len(same)),
        "post_opp_placements_so_far": float(len(other)),
        "post_both_seen": float(bool(same) and bool(other)),
        "post_last_place_age_ms": age(ordered),
        "post_last_place_is_same": float(bool(ordered) and str(ordered[-1].side) == taker_side),
        "post_last_same_place_age_ms": age(same),
        "post_last_opp_place_age_ms": age(other),
        "post_place_side_balance": float((len(same) - len(other)) / total) if total else 0.0,
    }


def force_relative(snapshot: dict[str, Any], action: str, taker_side: str) -> dict[str, Any]:
    if action == "PAUSE":
        return {"decision": "IDLE", "orders": [], "reason": "OPEN_BRIDGE_PAUSE"}
    side = taker_side if action == "CONTINUE_SAME" else ("DOWN" if taker_side == "UP" else "UP")
    tick = mod.maker_ebm._quote_tick(snapshot, side, 1)
    if tick is None:
        return {"decision": "IDLE", "orders": [], "reason": f"OPEN_BRIDGE_{action}_NO_QUOTE"}
    return {
        "decision": "QUOTE",
        "orders": [{
            "side": side,
            "priceTick": int(tick),
            "price": round(int(tick) * mod.maker_ebm.GRID, 2),
            "shares": mod.maker_ebm.SHARES_PER_ORDER,
            "offsetTicks": 1,
            "origin": VERSION,
        }],
        "reason": f"OPEN_BRIDGE_{action}",
    }


def branch_run(
    src: base.DirectionalSim,
    action: str,
    items: list[dict[str, Any]],
    checkpoint_idx: int,
    market_id: int,
    seeds: list[dict[str, Any]],
    seed_idx: int,
    taker_side: str,
) -> dict[str, Any] | None:
    sim = cf.clone_sim(src)
    start = items[checkpoint_idx]
    start_ms = int(start["decision_ms"])
    start_snap = dict(start["snapshot"])
    start_mtm = cf.mtm(sim, start_snap)
    if start_mtm is None:
        return None
    start_pair = mod.fifo_pair([dict(x) for x in sim.maker_fills])
    start_floor = cf.floor_value(sim)
    start_inv = cf.inventory_state(sim)
    start_fill_n = len(sim.maker_fills)
    local_seed = seed_idx
    end_snap = start_snap
    end_ms = start_ms

    for j in range(checkpoint_idx, len(items)):
        item = items[j]
        now = int(item["decision_ms"])
        if now - start_ms > HORIZON_MS:
            break
        snap = dict(item["snapshot"])
        ns = int(mod.num(snap.get("timestampNs")) or mod.num(snap.get("timestamp_ns")) or now * 1_000_000)
        # src already processed fill_existing for checkpoint_idx; do not process it twice.
        filled = 0 if j == checkpoint_idx else sim.fill_existing(snap, ns, now)
        local_seed, seed_filled = cf.apply_due_seeds(sim, seeds, local_seed, now)
        if now - start_ms <= ARBITRATION_WINDOW_MS:
            decision = force_relative(snap, action, taker_side)
        else:
            decision = sim.decide(snap, market_id, now)
        sim.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))
        end_snap = snap
        end_ms = now

    if end_ms - start_ms < MIN_HORIZON_MS:
        return None
    end_mtm = cf.mtm(sim, end_snap)
    if end_mtm is None:
        return None
    end_pair = mod.fifo_pair([dict(x) for x in sim.maker_fills])
    end_inv = cf.inventory_state(sim)
    return {
        "mtmDelta": float(end_mtm - start_mtm),
        "pairEdgeDelta": float(end_pair["lockedEdgeUsdt"] - start_pair["lockedEdgeUsdt"]),
        "pairedSharesDelta": float(end_pair["pairedShares"] - start_pair["pairedShares"]),
        "absNetDelta": float(end_inv["absNet"] - start_inv["absNet"]),
        "floorDelta": float(cf.floor_value(sim) - start_floor),
        "makerFills": int(len(sim.maker_fills) - start_fill_n),
        "endAbsNet": float(end_inv["absNet"]),
        "horizonMs": int(end_ms - start_ms),
    }


def score(artifact: dict[str, Any], row: dict[str, Any]) -> float:
    fs = list(artifact["features"])
    x = pd.DataFrame([{f: row.get(f, math.nan) for f in fs}]).apply(pd.to_numeric, errors="coerce")
    return float(artifact["model"].predict_proba(x)[0, 1])


def binary_metric(y: list[int], p: list[float]) -> dict[str, Any]:
    if not y:
        return {"n": 0}
    both = len(set(y)) == 2
    return {
        "n": len(y),
        "positives": int(sum(y)),
        "positiveRate": float(np.mean(y)),
        "rocAuc": float(roc_auc_score(y, p)) if both else None,
        "averagePrecision": float(average_precision_score(y, p)) if sum(y) > 0 else None,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for scope, rr in {
        "all": rows,
        "earlyHalf": rows[: len(rows) // 2],
        "lateHalf": rows[len(rows) // 2 :],
    }.items():
        if not rr:
            continue
        switch_best = [int(r["oracleMtmBest"] == "SWITCH_OPPOSITE") for r in rr]
        same_best = [int(r["oracleMtmBest"] == "CONTINUE_SAME") for r in rr]
        p_opp = [float(r["pOpp"]) for r in rr]
        p_same = [float(r["pSame"]) for r in rr]
        switch_minus_same = [float(r["switchMinusSameMtm"]) for r in rr]
        switch_minus_pause = [float(r["switchMinusPauseMtm"]) for r in rr]
        out[scope] = {
            "n": len(rr),
            "markets": len({int(r["marketId"]) for r in rr}),
            "pOppVsSwitchMtmBest": binary_metric(switch_best, p_opp),
            "pSameVsContinueMtmBest": binary_metric(same_best, p_same),
            "spearmanPOppVsSwitchMinusSameMtm": float(pd.Series(p_opp).corr(pd.Series(switch_minus_same), method="spearman")),
            "spearmanPOppVsSwitchMinusPauseMtm": float(pd.Series(p_opp).corr(pd.Series(switch_minus_pause), method="spearman")),
            "oracleMtmBestCounts": pd.Series([r["oracleMtmBest"] for r in rr]).value_counts().to_dict(),
            "meanPOpp": float(np.mean(p_opp)),
            "meanPSame": float(np.mean(p_same)),
        }
    return out


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    same_art = joblib.load(SAME_ART)
    opp_art = joblib.load(OPP_ART)
    our = mod.ro(mod.DEFAULT_OUR_DB)
    book = ro(BOOK_DB)
    try:
        snapshots = mod.load_snapshots(our)
        seeds_by_market = mod.load_seeds(our)
        models = mod.maker_ebm.load_models()
        end_to_book = first_book_market_by_end(book)
        rows: list[dict[str, Any]] = []
        dropped: dict[str, int] = {}

        for market_id in sorted(set(snapshots) & set(seeds_by_market)):
            items = snapshots[market_id]
            seeds = list(seeds_by_market.get(market_id, []))
            if not items or not seeds:
                continue
            # OPEN-only V0: one recorded OPEN_SEED per current baseline market.
            seed = seeds[0]
            taker_ms = int(seed["filled_at_ms"])
            taker_side = str(seed["side"])
            seed_idx = 0
            ref = base.DirectionalSim(models)
            sampled = False

            for i, item in enumerate(items):
                now = int(item["decision_ms"])
                snap = dict(item["snapshot"])
                ns = int(mod.num(snap.get("timestampNs")) or mod.num(snap.get("timestamp_ns")) or now * 1_000_000)
                filled = ref.fill_existing(snap, ns, now)
                decision = ref.decide(snap, market_id, now)
                seed_filled_this = False
                while seed_idx < len(seeds) and int(seeds[seed_idx]["filled_at_ms"]) <= now:
                    ref.apply_seed(seeds[seed_idx])
                    seed_idx += 1
                    seed_filled_this = True

                if (not sampled) and seed_idx > 0 and now >= taker_ms + CHECKPOINT_DELAY_MS:
                    window_end = int(mod.snapshot_value(snap, "window_end_ms", "windowEndMs") or 0)
                    book_market = end_to_book.get(window_end)
                    if book_market is None:
                        dropped["book_market_missing"] = dropped.get("book_market_missing", 0) + 1
                    else:
                        state, last_book = book_state_before(book, book_market, now)
                        age = now - last_book if last_book is not None else 10**9
                        if not (0 <= age <= 2000):
                            dropped["book_stale"] = dropped.get("book_stale", 0) + 1
                        else:
                            _, feat, cn, dom = own_inventory(ref, now)
                            bf = coord.outcome_book(state, dom)
                            if bf is None:
                                dropped["empty_book"] = dropped.get("empty_book", 0) + 1
                            else:
                                mem = post_placement_memory(ref, taker_ms, now, taker_side)
                                model_row: dict[str, Any] = {
                                    "seconds_left": (window_end - now) / 1000.0,
                                    **feat,
                                    **bf,
                                    "time_since_taker_ms": float(now - taker_ms),
                                    "intervention_side_is_up": float(taker_side == "UP"),
                                    "intervention_shares": float(seed["shares"]),
                                    "intervention_avg_price": float(seed["price"]),
                                    **mem,
                                }
                                p_same = score(same_art, model_row)
                                p_opp = score(opp_art, model_row)
                                branches = {
                                    a: branch_run(ref, a, items, i, market_id, seeds, seed_idx, taker_side)
                                    for a in ACTIONS
                                }
                                if all(branches[a] is not None for a in ACTIONS):
                                    best = max(ACTIONS, key=lambda a: float(branches[a]["mtmDelta"]))
                                    row = {
                                        "marketId": market_id,
                                        "windowEndMs": window_end,
                                        "checkpointMs": now,
                                        "bookMarketId": book_market,
                                        "bookAgeMs": age,
                                        "takerMs": taker_ms,
                                        "takerSide": taker_side,
                                        "takerShares": float(seed["shares"]),
                                        "takerPrice": float(seed["price"]),
                                        "secondsLeft": (window_end - now) / 1000.0,
                                        "pSame": p_same,
                                        "pOpp": p_opp,
                                        "combinedNet": cn,
                                        "makerNet": model_row.get("maker_net"),
                                        "combinedPairedCoverage": model_row.get("combined_paired_coverage"),
                                        "makerPairedCoverage": model_row.get("maker_paired_coverage"),
                                        "actions": branches,
                                        "oracleMtmBest": best,
                                        "switchMinusSameMtm": float(branches["SWITCH_OPPOSITE"]["mtmDelta"] - branches["CONTINUE_SAME"]["mtmDelta"]),
                                        "switchMinusPauseMtm": float(branches["SWITCH_OPPOSITE"]["mtmDelta"] - branches["PAUSE"]["mtmDelta"]),
                                    }
                                    rows.append(row)
                                else:
                                    dropped["short_horizon"] = dropped.get("short_horizon", 0) + 1
                    sampled = True

                ref.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled_this))

        rows.sort(key=lambda r: (int(r["windowEndMs"]), int(r["marketId"])))
        flat_rows: list[dict[str, Any]] = []
        for r in rows:
            q = {k: v for k, v in r.items() if k != "actions"}
            for a in ACTIONS:
                for k, v in r["actions"][a].items():
                    q[f"{a}_{k}"] = v
            flat_rows.append(q)
        pd.DataFrame(flat_rows).to_csv(DATA, index=False)

        fixed_bins = [(0.0, 0.2), (0.2, 0.4), (0.4, 0.6), (0.6, 0.8), (0.8, 1.000001)]
        by_popp: list[dict[str, Any]] = []
        for lo, hi in fixed_bins:
            rr = [r for r in rows if lo <= float(r["pOpp"]) < hi]
            if not rr:
                continue
            by_popp.append({
                "bin": f"[{lo:.1f},{min(hi,1.0):.1f})",
                "n": len(rr),
                "switchMtmBestRate": float(np.mean([r["oracleMtmBest"] == "SWITCH_OPPOSITE" for r in rr])),
                "meanSwitchMinusSameMtm": float(np.mean([r["switchMinusSameMtm"] for r in rr])),
                "meanSwitchMinusPauseMtm": float(np.mean([r["switchMinusPauseMtm"] for r in rr])),
                "meanSwitchFloorDelta": float(np.mean([r["actions"]["SWITCH_OPPOSITE"]["floorDelta"] for r in rr])),
                "meanSameFloorDelta": float(np.mean([r["actions"]["CONTINUE_SAME"]["floorDelta"] for r in rr])),
                "meanPauseFloorDelta": float(np.mean([r["actions"]["PAUSE"]["floorDelta"] for r in rr])),
            })

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "question": "Do frozen Target post-Taker SAME/OPP re-entry hazard scores rank economically useful Maker arbitration actions on OUR own post-OPEN_SEED states?",
            "semanticBoundary": "OUR currently has only OPEN_SEED active intervention in this replay. This V0 is OPEN-only and must not be generalized to REPAIR/ADD without OUR active-intervention states.",
            "coverage": {"states": len(rows), "markets": len({int(r["marketId"]) for r in rows}), "dropped": dropped},
            "hazardTrainingMaxMarketEndMs": 1787041800000,
            "ourBridgeMinMarketEndMs": min((int(r["windowEndMs"]) for r in rows), default=None),
            "allBridgeMarketsAfterHazardTraining": bool(rows and min(int(r["windowEndMs"]) for r in rows) > 1787041800000),
            "method": {
                "checkpoint": "first OUR recorder state >=500ms after recorded OPEN_SEED completion",
                "hazards": "frozen Target-trained SAME/OPP next-1s placement hazard; inputs mapped to OUR own portfolio/lifecycle + time-aligned 8778 public book; Target data is not used at inference",
                "counterfactual": "same OUR state branches 4s SAME-only / OPP-only / PAUSE then returns to frozen stable-directional policy for ~20s",
                "outcome": "paper MTM/floor/pair/inventory on the same public future tape; no winner and no Target future action used",
            },
            "association": summarize(rows),
            "byPOppFixedBins": by_popp,
            "artifacts": {"statesCsv": str(DATA), "sameModel": str(SAME_ART), "oppModel": str(OPP_ART)},
            "guards": [
                "No threshold or model tuning on this OUR cohort.",
                "All bridge markets occur after the Target re-entry model training cutoff.",
                "OPEN_SEED-only semantics; Target-trained hazards may behave differently after REPAIR/ADD.",
                "Paper fill model remains a limitation; this is a controller-signal bridge test, not live-profit evidence.",
            ],
        }
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    finally:
        our.close()
        book.close()


if __name__ == "__main__":
    raise SystemExit(main())
