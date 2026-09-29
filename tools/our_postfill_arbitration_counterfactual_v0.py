from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BASE_PATH = ROOT / "tools" / "backtest_stable_directional_tolerance_v0.py"
spec = importlib.util.spec_from_file_location("our_postfill_base", BASE_PATH)
base = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = base
spec.loader.exec_module(base)
mod = base.mod

VERSION = "OUR_POSTFILL_ARBITRATION_COUNTERFACTUAL_V0"
REPORT = ROOT / "data" / "research" / "our_postfill_arbitration_counterfactual_v0_report.json"
DATASET = ROOT / "data" / "research" / "our_postfill_arbitration_counterfactual_v0_states.jsonl"

CHECKPOINT_DELAY_MS = 1000
ARBITRATION_WINDOW_MS = 4000
HORIZON_MS = 20000
MIN_HORIZON_MS = 16000
MIN_SECONDS_LEFT = 35.0
MAX_STATES_PER_MARKET = 10
MIN_STATE_GAP_MS = 6000
EPS = 1e-9
ACTIONS = ("CONTINUE_SAME", "SWITCH_OPPOSITE", "PAUSE")


def clone_sim(src: base.DirectionalSim) -> base.DirectionalSim:
    dst = base.DirectionalSim(src.models)
    dst.orders = {k: type(v)(**vars(v)) for k, v in src.orders.items()}
    dst.last_closed = dict(src.last_closed)
    dst.up_shares = float(src.up_shares)
    dst.down_shares = float(src.down_shares)
    dst.up_cost = float(src.up_cost)
    dst.down_cost = float(src.down_cost)
    dst.placements = int(src.placements)
    dst.cancels = int(src.cancels)
    dst.maker_fills = [dict(x) for x in src.maker_fills]
    dst.seed_fills = [dict(x) for x in src.seed_fills]
    dst.headwind_blocks = int(getattr(src, "headwind_blocks", 0))
    dst.tailwind_allows = int(getattr(src, "tailwind_allows", 0))
    return dst


def maker_up_down(sim: base.DirectionalSim) -> tuple[float, float]:
    up = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "UP")
    down = sum(float(f["shares"]) for f in sim.maker_fills if str(f["side"]) == "DOWN")
    return up, down


def inventory_state(sim: base.DirectionalSim) -> dict[str, Any]:
    up, down = maker_up_down(sim)
    net = up - down
    gross = up + down
    dom = "UP" if net > EPS else "DOWN" if net < -EPS else None
    minority = "DOWN" if dom == "UP" else "UP" if dom == "DOWN" else None
    return {
        "up": up,
        "down": down,
        "net": net,
        "absNet": abs(net),
        "gross": gross,
        "imbalanceRatio": abs(net) / gross if gross > EPS else 0.0,
        "dominant": dom,
        "minority": minority,
        "pairedCoverage": min(up, down) * 2.0 / gross if gross > EPS else 0.0,
    }


def mids(snapshot: dict[str, Any]) -> tuple[float | None, float | None]:
    up = mod.snapshot_value(snapshot, "predict_up_mid", "predictUpMid")
    down = mod.snapshot_value(snapshot, "predict_down_mid", "predictDownMid")
    if down is None and up is not None:
        down = 1.0 - float(up)
    return up, down


def mtm(sim: base.DirectionalSim, snapshot: dict[str, Any]) -> float | None:
    up_mid, down_mid = mids(snapshot)
    if up_mid is None or down_mid is None:
        return None
    return (
        float(sim.up_shares) * float(up_mid)
        + float(sim.down_shares) * float(down_mid)
        - float(sim.up_cost)
        - float(sim.down_cost)
    )


def floor_value(sim: base.DirectionalSim) -> float:
    total_cost = float(sim.up_cost) + float(sim.down_cost)
    return min(float(sim.up_shares) - total_cost, float(sim.down_shares) - total_cost)


def pair_edge_proxy(snapshot: dict[str, Any], fill_side: str, fill_price: float) -> dict[str, float | None]:
    opp = "DOWN" if fill_side == "UP" else "UP"
    if opp == "UP":
        bid = mod.snapshot_value(snapshot, "predict_up_bid", "predictUpBid")
    else:
        bid = mod.snapshot_value(snapshot, "predict_down_bid", "predictDownBid")
    if bid is None:
        return {"oppBestBid": None, "oppBestBidLockedEdge": None, "oppMinus1LockedEdge": None}
    bid = float(bid)
    return {
        "oppBestBid": bid,
        "oppBestBidLockedEdge": 1.0 - float(fill_price) - bid,
        "oppMinus1LockedEdge": 1.0 - float(fill_price) - max(mod.maker_ebm.MIN_PRICE, bid - mod.maker_ebm.GRID),
    }


def force_decision(snapshot: dict[str, Any], action: str, dominant: str) -> dict[str, Any]:
    if action == "PAUSE":
        return {"decision": "IDLE", "orders": [], "reason": "POSTFILL_ARBITRATION_PAUSE"}
    desired_side = dominant if action == "CONTINUE_SAME" else ("DOWN" if dominant == "UP" else "UP")
    tick = mod.maker_ebm._quote_tick(snapshot, desired_side, 1)
    if tick is None:
        return {"decision": "IDLE", "orders": [], "reason": f"POSTFILL_{action}_NO_QUOTE"}
    row = {
        "side": desired_side,
        "priceTick": int(tick),
        "price": round(int(tick) * mod.maker_ebm.GRID, 2),
        "shares": mod.maker_ebm.SHARES_PER_ORDER,
        "offsetTicks": 1,
        "origin": VERSION,
    }
    return {"decision": "QUOTE", "orders": [row], "reason": f"POSTFILL_{action}"}


def apply_due_seeds(sim: base.DirectionalSim, seeds: list[dict[str, Any]], idx: int, now: int) -> tuple[int, bool]:
    used = False
    while idx < len(seeds) and int(seeds[idx]["filled_at_ms"]) <= now:
        sim.apply_seed(seeds[idx])
        idx += 1
        used = True
    return idx, used


def branch_run(
    src: base.DirectionalSim,
    action: str,
    items: list[dict[str, Any]],
    checkpoint_idx: int,
    market_id: int,
    seeds: list[dict[str, Any]],
    seed_idx: int,
    dominant: str,
) -> dict[str, Any] | None:
    sim = clone_sim(src)
    start = items[checkpoint_idx]
    start_ms = int(start["decision_ms"])
    start_snap = dict(start["snapshot"])
    start_mtm = mtm(sim, start_snap)
    if start_mtm is None:
        return None
    start_pair = mod.fifo_pair([dict(x) for x in sim.maker_fills])
    start_floor = floor_value(sim)
    start_inv = inventory_state(sim)
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
        filled = sim.fill_existing(snap, ns, now)
        local_seed, seed_filled = apply_due_seeds(sim, seeds, local_seed, now)
        if now - start_ms <= ARBITRATION_WINDOW_MS:
            decision = force_decision(snap, action, dominant)
        else:
            decision = sim.decide(snap, market_id, now)
        sim.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))
        end_snap = snap
        end_ms = now

    if end_ms - start_ms < MIN_HORIZON_MS:
        return None
    end_mtm = mtm(sim, end_snap)
    if end_mtm is None:
        return None
    end_pair = mod.fifo_pair([dict(x) for x in sim.maker_fills])
    end_inv = inventory_state(sim)
    return {
        "mtmDelta": float(end_mtm - start_mtm),
        "pairEdgeDelta": float(end_pair["lockedEdgeUsdt"] - start_pair["lockedEdgeUsdt"]),
        "pairedSharesDelta": float(end_pair["pairedShares"] - start_pair["pairedShares"]),
        "absNetDelta": float(end_inv["absNet"] - start_inv["absNet"]),
        "floorDelta": float(floor_value(sim) - start_floor),
        "makerFills": int(len(sim.maker_fills) - start_fill_n),
        "endAbsNet": float(end_inv["absNet"]),
        "horizonMs": int(end_ms - start_ms),
    }


def stat(xs: list[float]) -> dict[str, Any]:
    return mod.stats([float(x) for x in xs])


def collect() -> list[dict[str, Any]]:
    our = mod.ro(mod.DEFAULT_OUR_DB)
    try:
        snapshots = mod.load_snapshots(our)
        seeds_by_market = mod.load_seeds(our)
        models = mod.maker_ebm.load_models()
        rows: list[dict[str, Any]] = []

        for market_id in sorted(snapshots):
            items = snapshots[market_id]
            if not items:
                continue
            seeds = list(seeds_by_market.get(market_id, []))
            seed_idx = 0
            ref = base.DirectionalSim(models)
            last_sample_ms: int | None = None
            sampled = 0
            pending: list[dict[str, Any]] = []

            for i, item in enumerate(items):
                now = int(item["decision_ms"])
                snap = dict(item["snapshot"])
                ns = int(mod.num(snap.get("timestampNs")) or mod.num(snap.get("timestamp_ns")) or now * 1_000_000)
                before_n = len(ref.maker_fills)
                filled = ref.fill_existing(snap, ns, now)
                new_fills = [dict(x) for x in ref.maker_fills[before_n:]]
                decision = ref.decide(snap, market_id, now)
                seed_idx, seed_filled = apply_due_seeds(ref, seeds, seed_idx, now)

                # Any new fill that leaves its own side dominant can schedule a +1s checkpoint.
                if new_fills and sampled < MAX_STATES_PER_MARKET:
                    inv = inventory_state(ref)
                    for f in new_fills:
                        side = str(f.get("side"))
                        if inv["dominant"] == side and inv["absNet"] >= 18.0 - EPS:
                            pending.append({
                                "fillMs": int(f.get("at_ms") or now),
                                "fillSide": side,
                                "fillPrice": float(f["price"]),
                            })

                # Consume at most one matured pending checkpoint at this recorder snapshot.
                matured_idx = None
                for pi, p in enumerate(pending):
                    if now >= int(p["fillMs"]) + CHECKPOINT_DELAY_MS:
                        matured_idx = pi
                        break
                if matured_idx is not None and sampled < MAX_STATES_PER_MARKET:
                    p = pending.pop(matured_idx)
                    sec = mod.snapshot_value(snap, "seconds_left", "secondsLeft")
                    inv = inventory_state(ref)
                    eligible = (
                        sec is not None
                        and float(sec) >= MIN_SECONDS_LEFT
                        and inv["dominant"] == str(p["fillSide"])
                        and (last_sample_ms is None or now - last_sample_ms >= MIN_STATE_GAP_MS)
                    )
                    if eligible:
                        edge = pair_edge_proxy(snap, str(p["fillSide"]), float(p["fillPrice"]))
                        up_mid, down_mid = mids(snap)
                        fill_mid0 = None
                        # Approximate realized +1s side markout using fill price only as an execution anchor diagnostic.
                        side_mid_now = up_mid if str(p["fillSide"]) == "UP" else down_mid
                        markout_vs_fill_ticks = ((float(side_mid_now) - float(p["fillPrice"])) / mod.maker_ebm.GRID) if side_mid_now is not None else None
                        branches = {
                            a: branch_run(ref, a, items, i, market_id, seeds, seed_idx, str(p["fillSide"]))
                            for a in ACTIONS
                        }
                        if all(branches[a] is not None for a in ACTIONS):
                            dr = base.simple3(snap)
                            row = {
                                "marketId": market_id,
                                "checkpointMs": now,
                                "fillMs": int(p["fillMs"]),
                                "fillSide": str(p["fillSide"]),
                                "fillPrice": float(p["fillPrice"]),
                                "secondsLeft": float(sec),
                                "simple3Direction": dr,
                                "tailwind": bool(dr == str(p["fillSide"])) if dr else None,
                                "postMakerAbsNet": float(inv["absNet"]),
                                "postMakerGross": float(inv["gross"]),
                                "postMakerImbalanceRatio": float(inv["imbalanceRatio"]),
                                "postMakerPairedCoverage": float(inv["pairedCoverage"]),
                                "markoutVsFillTicksAtCheckpoint": markout_vs_fill_ticks,
                                **edge,
                                "actions": branches,
                            }
                            rows.append(row)
                            sampled += 1
                            last_sample_ms = now

                ref.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))

        return rows
    finally:
        our.close()


def main() -> int:
    rows = collect()
    if not rows:
        raise RuntimeError("no post-fill arbitration states")
    DATASET.parent.mkdir(parents=True, exist_ok=True)
    with DATASET.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    action_summary: dict[str, Any] = {}
    for a in ACTIONS:
        vals = [r["actions"][a] for r in rows]
        action_summary[a] = {
            "mtmDelta": stat([v["mtmDelta"] for v in vals]),
            "pairEdgeDelta": stat([v["pairEdgeDelta"] for v in vals]),
            "pairedSharesDelta": stat([v["pairedSharesDelta"] for v in vals]),
            "absNetDelta": stat([v["absNetDelta"] for v in vals]),
            "floorDelta": stat([v["floorDelta"] for v in vals]),
            "makerFills": stat([v["makerFills"] for v in vals]),
        }

    oracle_counts = {a: 0 for a in ACTIONS}
    for r in rows:
        best = max(ACTIONS, key=lambda a: float(r["actions"][a]["mtmDelta"]))
        oracle_counts[best] += 1

    # Descriptive pair-edge bins; fixed ex-ante bins, not selected from outcomes.
    bins = [(-999.0, 0.0, "EDGE_NEG"), (0.0, 0.01, "EDGE_0_1C"), (0.01, 0.03, "EDGE_1_3C"), (0.03, 0.06, "EDGE_3_6C"), (0.06, 999.0, "EDGE_6C_PLUS")]
    by_edge: dict[str, Any] = {}
    for lo, hi, name in bins:
        rr = [r for r in rows if r.get("oppBestBidLockedEdge") is not None and lo <= float(r["oppBestBidLockedEdge"]) < hi]
        by_edge[name] = {
            "n": len(rr),
            "oracleMtmBestCounts": {a: sum(max(ACTIONS, key=lambda x: float(r["actions"][x]["mtmDelta"])) == a for r in rr) for a in ACTIONS},
            "meanMtm": {a: statistics.mean([float(r["actions"][a]["mtmDelta"]) for r in rr]) if rr else None for a in ACTIONS},
            "meanFloor": {a: statistics.mean([float(r["actions"][a]["floorDelta"]) for r in rr]) if rr else None for a in ACTIONS},
            "meanAbsNet": {a: statistics.mean([float(r["actions"][a]["absNetDelta"]) for r in rr]) if rr else None for a in ACTIONS},
        }

    report = {
        "reportVersion": VERSION,
        "researchOnly": True,
        "liveTradingChanges": False,
        "coverage": {"states": len(rows), "markets": len({int(r["marketId"]) for r in rows})},
        "method": {
            "reference": "frozen stable-directional replay path",
            "checkpoint": "first recorder snapshot >=1s after a Maker fill that leaves the filled side dominant with abs Maker net>=18",
            "actions": {
                "CONTINUE_SAME": "for first 4s after checkpoint, allow only dominant-side new stable -1tick Maker",
                "SWITCH_OPPOSITE": "for first 4s, allow only minority/opposite-side new stable -1tick Maker",
                "PAUSE": "for first 4s, no new Maker; existing undesired orders are removed by normal apply_plan semantics",
            },
            "afterArbitration": "all branches return to identical frozen stable-directional policy until ~20s horizon",
            "labels": "same public future path paper counterfactual; no winner or Target behavior used",
        },
        "actionSummary": action_summary,
        "oracleMtmBestCounts": oracle_counts,
        "byComplementaryPairEdge": by_edge,
        "dataset": str(DATASET),
        "guards": [
            "No parameter sweep; 1s checkpoint, 4s arbitration window, and 20s horizon are fixed V0 semantics.",
            "Paper ask-touch fill proxy remains a limitation; this is local controller evidence, not live profitability.",
            "Target data is not used anywhere in OUR branch choice or outcome construction.",
        ],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
