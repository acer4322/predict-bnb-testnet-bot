from __future__ import annotations

import importlib.util
import json
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DIR_V0 = ROOT / "tools" / "backtest_stable_directional_tolerance_v0.py"
spec = importlib.util.spec_from_file_location("mature_mm_dirv0", DIR_V0)
dirv0 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = dirv0
spec.loader.exec_module(dirv0)
base = dirv0.mod

REPORT = ROOT / "data" / "research" / "mature_mm_parameter_sensitivity_v1_report.json"
VERSION = "MATURE_MM_PARAMETER_SENSITIVITY_V1"


def both_offset(snapshot: dict[str, Any], offset_ticks: int) -> dict[str, Any]:
    sec = base.snapshot_value(snapshot, "seconds_left", "secondsLeft")
    if sec is None or sec <= base.OPEN_MID_MIN_SECONDS_LEFT:
        return {"decision": "DEFER_TO_BASE", "orders": []}
    rows: list[dict[str, Any]] = []
    for side in ("UP", "DOWN"):
        tick = base.maker_ebm._quote_tick(snapshot, side, int(offset_ticks))
        if tick is None:
            continue
        rows.append({
            "side": side,
            "priceTick": tick,
            "price": round(tick * base.maker_ebm.GRID, 2),
            "shares": base.maker_ebm.SHARES_PER_ORDER,
            "offsetTicks": int(offset_ticks),
            "origin": VERSION,
        })
    if len(rows) == 2:
        while sum(float(x["price"]) for x in rows) > base.maker_ebm.MAX_PAIR_PRICE_SUM + 1e-9:
            expensive = max(rows, key=lambda x: float(x["price"]))
            nxt = int(expensive["priceTick"]) - 1
            if nxt < int(round(base.maker_ebm.MIN_PRICE / base.maker_ebm.GRID)):
                return {"decision": "IDLE", "reason": "PAIR_PRICE_CAP_UNSATISFIABLE", "orders": []}
            expensive["priceTick"] = nxt
            expensive["price"] = round(nxt * base.maker_ebm.GRID, 2)
            expensive["offsetTicks"] = int(expensive["offsetTicks"]) + 1
    return {
        "decision": "QUOTE" if rows else "IDLE",
        "orders": rows,
        "reason": f"STABLE_BOTH_OFFSET_{int(offset_ticks)}",
    }


@dataclass(frozen=True)
class Params:
    name: str
    offset_ticks: int = 1
    headwind_block_min_abs_net: float = 18.0
    max_order_age_ms: int | None = None


class ParamSim(base.Simulator):
    def __init__(self, models: dict[str, dict[str, Any]], params: Params) -> None:
        super().__init__(models, "CUSTOM")
        self.params = params
        self.headwind_blocks = 0
        self.tailwind_allows = 0
        self.age_refresh_cancels = 0

    def maker_net(self) -> float:
        up = sum(float(f["shares"]) for f in self.maker_fills if f["side"] == "UP")
        down = sum(float(f["shares"]) for f in self.maker_fills if f["side"] == "DOWN")
        return up - down

    def decide(self, snapshot: dict[str, Any], market_id: int, now_ms: int) -> dict[str, Any]:
        sec = base.snapshot_value(snapshot, "seconds_left", "secondsLeft")
        if sec is not None and sec > base.OPEN_MID_MIN_SECONDS_LEFT:
            d = both_offset(snapshot, self.params.offset_ticks)
            net = self.maker_net()
            dr = dirv0.simple3(snapshot)
            if (
                d.get("decision") == "QUOTE"
                and abs(net) + 1e-9 >= float(self.params.headwind_block_min_abs_net)
                and dr in {"UP", "DOWN"}
            ):
                dom = "UP" if net > 0 else "DOWN" if net < 0 else None
                if dom is not None:
                    if dom != dr:
                        rows = [r for r in d.get("orders", []) if str(r["side"]) != dom]
                        self.headwind_blocks += 1
                        d = dict(d)
                        d["orders"] = rows
                        d["decision"] = "QUOTE" if rows else "IDLE"
                        d["reason"] = "HEADWIND_DOMINANT_BLOCK"
                    else:
                        self.tailwind_allows += 1
            return d
        return base.maker_ebm.decide(
            snapshot,
            self.models,
            self.inventory(),
            cohort=base.maker_ebm.COMBINED_COHORT,
            expected_market_id=market_id,
            now_ms=now_ms,
        )

    def apply_plan(self, decision: dict[str, Any], snapshot_ns: int, now_ms: int, allow_new: bool) -> None:
        max_age = self.params.max_order_age_ms
        if max_age is not None and max_age > 0:
            for key, order in list(self.orders.items()):
                if now_ms - int(order.placed_at_ms) >= int(max_age):
                    self.orders.pop(key, None)
                    self.last_closed[key] = now_ms - base.maker_ebm.REFILL_COOLDOWN_MS
                    self.cancels += 1
                    self.age_refresh_cancels += 1
        super().apply_plan(decision, snapshot_ns, now_ms, allow_new)


def run_one(sim: ParamSim, snaps: list[dict[str, Any]], seeds: list[dict[str, Any]], market_id: int) -> ParamSim:
    i = 0
    for item in snaps:
        now = int(item["decision_ms"])
        snap = dict(item["snapshot"])
        ns = int(base.num(snap.get("timestampNs")) or base.num(snap.get("timestamp_ns")) or now * 1_000_000)
        filled = sim.fill_existing(snap, ns, now)
        decision = sim.decide(snap, market_id, now)
        seed_filled = False
        while i < len(seeds) and int(seeds[i]["filled_at_ms"]) <= now:
            sim.apply_seed(seeds[i])
            i += 1
            seed_filled = True
        sim.apply_plan(decision, ns, now, allow_new=(filled == 0 and not seed_filled))
    while i < len(seeds):
        sim.apply_seed(seeds[i])
        i += 1
    return sim


def pack(sim: ParamSim, winner: str) -> dict[str, Any]:
    maker = [dict(x) for x in sim.maker_fills]
    allf = maker + [
        {"side": s["side"], "price": s["price"], "shares": s["shares"], "at_ms": s["filled_at_ms"]}
        for s in sim.seed_fills
    ]
    pair = base.fifo_pair(maker)
    up = sum(float(f["shares"]) for f in maker if f["side"] == "UP")
    down = sum(float(f["shares"]) for f in maker if f["side"] == "DOWN")
    return {
        "pnlUsdt": base.pnl_from_fills(allf, winner),
        "placements": sim.placements,
        "cancels": sim.cancels,
        "makerFills": len(maker),
        "finalAbsNet": abs(up - down),
        "pair": pair,
        "headwindBlocks": sim.headwind_blocks,
        "tailwindAllows": sim.tailwind_allows,
        "ageRefreshCancels": sim.age_refresh_cancels,
    }


def summary(rows: list[dict[str, Any]], name: str) -> dict[str, Any]:
    vals = [r[name] for r in rows]
    pnls = [float(v["pnlUsdt"]) for v in vals]
    paired = sum(float(v["pair"]["pairedShares"]) for v in vals)
    edge = sum(float(v["pair"]["lockedEdgeUsdt"]) for v in vals)
    return {
        "pnlUsdt": sum(pnls),
        "positiveMarkets": sum(x > 0 for x in pnls),
        "positiveMarketRate": sum(x > 0 for x in pnls) / len(pnls) if pnls else None,
        "pnlPerMarket": base.stats(pnls),
        "placementsPerMarket": base.stats([float(v["placements"]) for v in vals]),
        "cancelsPerMarket": base.stats([float(v["cancels"]) for v in vals]),
        "makerFillsPerMarket": base.stats([float(v["makerFills"]) for v in vals]),
        "finalAbsNet": base.stats([float(v["finalAbsNet"]) for v in vals]),
        "pairedShares": paired,
        "lockedEdgeUsdt": edge,
        "edgePerPairedShare": edge / paired if paired else None,
        "pairedCoverage": base.stats([float(v["pair"]["pairedCoverage"]) for v in vals]),
        "headwindBlocks": sum(int(v["headwindBlocks"]) for v in vals),
        "tailwindAllows": sum(int(v["tailwindAllows"]) for v in vals),
        "ageRefreshCancels": sum(int(v["ageRefreshCancels"]) for v in vals),
    }


def main() -> int:
    our = base.ro(base.DEFAULT_OUR_DB)
    target = base.ro(base.DEFAULT_TARGET_DB)
    try:
        snaps = base.load_snapshots(our)
        seeds = base.load_seeds(our)
        winners = base.load_winners(target)
        models = base.maker_ebm.load_models()
        markets = sorted(set(snaps) & set(winners))

        params = [
            Params("REF_OFFSET1_NET18_AGEINF", 1, 18.0, None),
            Params("OFFSET2", 2, 18.0, None),
            Params("OFFSET3", 3, 18.0, None),
            Params("BLOCK_NET36", 1, 36.0, None),
            Params("BLOCK_NET54", 1, 54.0, None),
            Params("MAX_AGE_2S", 1, 18.0, 2_000),
            Params("MAX_AGE_5S", 1, 18.0, 5_000),
            Params("MAX_AGE_10S", 1, 18.0, 10_000),
        ]

        rows: list[dict[str, Any]] = []
        for market_id in markets:
            row: dict[str, Any] = {"marketId": market_id, "winner": winners[market_id]}
            ss = list(seeds.get(market_id, []))
            for p in params:
                sim = run_one(ParamSim(models, p), snaps[market_id], ss, market_id)
                row[p.name] = pack(sim, winners[market_id])
            rows.append(row)

        summaries = {p.name: summary(rows, p.name) for p in params}
        ref = summaries[params[0].name]
        sensitivity: dict[str, Any] = {}
        for p in params[1:]:
            s = summaries[p.name]
            deltas = [float(r[p.name]["pnlUsdt"]) - float(r[params[0].name]["pnlUsdt"]) for r in rows]
            sensitivity[p.name] = {
                "params": {
                    "offsetTicks": p.offset_ticks,
                    "headwindBlockMinAbsNet": p.headwind_block_min_abs_net,
                    "maxOrderAgeMs": p.max_order_age_ms,
                },
                "positiveRateDelta": float(s["positiveMarketRate"]) - float(ref["positiveMarketRate"]),
                "pnlDeltaUsdt": float(s["pnlUsdt"]) - float(ref["pnlUsdt"]),
                "edgePerPairedShareDelta": (
                    float(s["edgePerPairedShare"]) - float(ref["edgePerPairedShare"])
                    if s["edgePerPairedShare"] is not None and ref["edgePerPairedShare"] is not None else None
                ),
                "betterMarkets": sum(x > 1e-9 for x in deltas),
                "worseMarkets": sum(x < -1e-9 for x in deltas),
                "tiedMarkets": sum(abs(x) <= 1e-9 for x in deltas),
                "deltaPerMarket": base.stats(deltas),
            }

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "purpose": "One-factor-at-a-time sensitivity around STABLE_DIRECTIONAL_TOLERANCE_V0. This is descriptive sensitivity, not parameter selection or promotion tuning.",
            "graduationReference": 0.35,
            "coverage": {"markets": len(rows)},
            "reference": {
                "name": params[0].name,
                "params": {"offsetTicks": 1, "headwindBlockMinAbsNet": 18, "maxOrderAgeMs": None},
            },
            "families": {
                "quoteDepthTicks": [1, 2, 3],
                "headwindBlockMinAbsNetShares": [18, 36, 54],
                "maxOrderAgeMs": [None, 2_000, 5_000, 10_000],
            },
            "guard": [
                "No factorial grid was run.",
                "Only one parameter differs from the reference in each variant.",
                "Do not select a production parameter from this reused historical cohort; any interesting region must be frozen and checked fresh-forward.",
                "TAIL and recorded OPEN_SEED behavior remain as in the prior replay for comparability.",
            ],
            "summary": summaries,
            "sensitivityVsReference": sensitivity,
            "rows": rows,
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        compact = {
            "coverage": report["coverage"],
            "reference": ref,
            "variants": {k: {
                "positiveMarketRate": v["positiveMarketRate"],
                "positiveMarkets": v["positiveMarkets"],
                "pnlUsdt": v["pnlUsdt"],
                "edgePerPairedShare": v["edgePerPairedShare"],
                "finalAbsNetMedian": v["finalAbsNet"]["median"],
                "finalAbsNetMax": v["finalAbsNet"]["max"],
                "pairedCoverageMean": v["pairedCoverage"]["mean"],
                "ageRefreshCancels": v["ageRefreshCancels"],
            } for k, v in summaries.items()},
            "sensitivityVsReference": sensitivity,
            "report": str(REPORT),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    finally:
        our.close()
        target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
