from __future__ import annotations

import importlib.util
import json
import math
import statistics
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "tools" / "backtest_mature_mm_parameter_sensitivity_v1.py"
spec = importlib.util.spec_from_file_location("mature_mm_sens_v1", V1)
v1 = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules[spec.name] = v1
spec.loader.exec_module(v1)
base = v1.base
dirv0 = v1.dirv0

REPORT = ROOT / "data" / "research" / "mature_mm_event_knob_regimes_v2_report.json"
VERSION = "MATURE_MM_EVENT_KNOB_REGIMES_V2"


@dataclass(frozen=True)
class EventParams:
    name: str
    filled_order_delay_ms: int = 0
    reprice_tolerance_ticks: int | None = None


class EventParamSim(v1.ParamSim):
    """Reference stable-directional controller plus one event-driven lifecycle knob.

    The existing historical reference stays fixed at:
      offset=1 tick, headwind block from |Maker net|>=18, no max-order-age refresh.

    New knobs are OPEN/MID only so the existing TAIL policy remains comparable:
      * filled_order_delay_ms: after a Maker fill on a side, temporarily suppress
        placement of the next same-side parent.
      * reprice_tolerance_ticks: keep a resting same-side quote until the currently
        desired quote has moved by at least N ticks; then cancel/replace once.
    """

    def __init__(self, models: dict[str, dict[str, Any]], event_params: EventParams) -> None:
        super().__init__(models, v1.Params("BASE", 1, 18.0, None))
        self.event_params = event_params
        self.last_fill_ms_by_side: dict[str, int] = {}
        self.delay_suppressed_plans = 0
        self.reprice_refresh_cancels = 0
        self.current_seconds_left: float | None = None

    def decide(self, snapshot: dict[str, Any], market_id: int, now_ms: int) -> dict[str, Any]:
        self.current_seconds_left = base.snapshot_value(snapshot, "seconds_left", "secondsLeft")
        return super().decide(snapshot, market_id, now_ms)

    def fill_existing(self, snapshot: dict[str, Any], snapshot_ns: int, now_ms: int) -> int:
        before = len(self.maker_fills)
        n = super().fill_existing(snapshot, snapshot_ns, now_ms)
        for f in self.maker_fills[before:]:
            side = str(f.get("side") or "")
            if side in {"UP", "DOWN"}:
                self.last_fill_ms_by_side[side] = int(f.get("at_ms") or now_ms)
        return n

    def apply_plan(self, decision: dict[str, Any], snapshot_ns: int, now_ms: int, allow_new: bool) -> None:
        d = dict(decision)
        rows = list(d.get("orders") or []) if d.get("decision") == "QUOTE" else []
        open_mid = self.current_seconds_left is not None and self.current_seconds_left > base.OPEN_MID_MIN_SECONDS_LEFT

        # Mature PMM knob: event-triggered refill delay, not a periodic timer refresh.
        delay = int(self.event_params.filled_order_delay_ms or 0)
        if open_mid and delay > 0 and rows:
            kept: list[dict[str, Any]] = []
            for row in rows:
                side = str(row.get("side") or "")
                last_fill = self.last_fill_ms_by_side.get(side)
                if last_fill is not None and now_ms - last_fill < delay:
                    self.delay_suppressed_plans += 1
                    continue
                kept.append(row)
            rows = kept
            d["orders"] = rows
            d["decision"] = "QUOTE" if rows else "IDLE"
            if not rows:
                d["reason"] = "FILLED_ORDER_DELAY"

        # Mature refresh-tolerance knob: reprice only after the desired quote has
        # moved N ticks away from the resting quote. This is price-move-driven,
        # not max-order-age / wall-clock refresh.
        tol = self.event_params.reprice_tolerance_ticks
        if open_mid and tol is not None and tol > 0 and rows:
            desired_by_side = {str(r["side"]): r for r in rows}
            for key, order in list(self.orders.items()):
                row = desired_by_side.get(str(order.side))
                if row is None:
                    continue
                desired_tick = int(row["priceTick"])
                if abs(desired_tick - int(order.price_tick)) < int(tol):
                    continue
                self.orders.pop(key, None)
                # Permit the intended replacement in this same state transition;
                # this is not a separate refill-cooldown experiment.
                self.last_closed[key] = now_ms - base.maker_ebm.REFILL_COOLDOWN_MS
                self.cancels += 1
                self.reprice_refresh_cancels += 1

        super().apply_plan(d, snapshot_ns, now_ms, allow_new)


def run_one(sim: EventParamSim, snaps: list[dict[str, Any]], seeds: list[dict[str, Any]], market_id: int) -> EventParamSim:
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


def pack(sim: EventParamSim, winner: str) -> dict[str, Any]:
    out = v1.pack(sim, winner)
    out["delaySuppressedPlans"] = int(sim.delay_suppressed_plans)
    out["repriceRefreshCancels"] = int(sim.reprice_refresh_cancels)
    return out


def mean(xs: list[float]) -> float | None:
    return statistics.mean(xs) if xs else None


def market_profile(items: list[dict[str, Any]]) -> dict[str, float | int | None]:
    """Public-only first-minute profile, usable at T240 without winner/Target data."""
    mids: list[float] = []
    spreads: list[float] = []
    spot_abs: list[float] = []
    chain_abs: list[float] = []
    dirs: list[str] = []

    for item in items:
        snap = dict(item["snapshot"])
        sec = base.snapshot_value(snap, "seconds_left", "secondsLeft")
        if sec is None or sec <= 240.0:
            continue
        mid = base.snapshot_value(snap, "predict_up_mid", "predictUpMid")
        if mid is not None:
            mids.append(float(mid))
        ub = base.snapshot_value(snap, "predict_up_bid", "predictUpBid")
        ua = base.snapshot_value(snap, "predict_up_ask", "predictUpAsk")
        db = base.snapshot_value(snap, "predict_down_bid", "predictDownBid")
        da = base.snapshot_value(snap, "predict_down_ask", "predictDownAsk")
        side_spreads: list[float] = []
        if ub is not None and ua is not None and ua >= ub:
            side_spreads.append(float(ua - ub))
        if db is not None and da is not None and da >= db:
            side_spreads.append(float(da - db))
        if side_spreads:
            spreads.append(statistics.mean(side_spreads))
        spot = base.snapshot_value(snap, "spot_minus_strike_bps", "spotMinusStrikeBps")
        if spot is not None:
            spot_abs.append(abs(float(spot)))
        chain = base.snapshot_value(snap, "chainlink_minus_strike_bps", "chainlinkMinusStrikeBps")
        if chain is not None:
            chain_abs.append(abs(float(chain)))
        d = dirv0.simple3(snap)
        if d in {"UP", "DOWN"}:
            dirs.append(str(d))

    total_var = sum(abs(b - a) for a, b in zip(mids, mids[1:])) if len(mids) >= 2 else 0.0
    net_move = abs(mids[-1] - mids[0]) if len(mids) >= 2 else 0.0
    flips = sum(1 for a, b in zip(dirs, dirs[1:]) if a != b)
    return {
        "samples": len(mids),
        "meanAbsPredictFromHalf": mean([abs(x - 0.5) for x in mids]),
        "predictRange": (max(mids) - min(mids)) if mids else None,
        "predictTotalVariation": total_var if mids else None,
        "predictTrendiness": (net_move / total_var) if total_var > 1e-12 else 0.0 if mids else None,
        "simple3FlipCount": flips,
        "simple3FlipRate": (flips / max(1, len(dirs) - 1)) if dirs else None,
        "meanAbsSpotStrikeBps": mean(spot_abs),
        "meanAbsChainlinkStrikeBps": mean(chain_abs),
        "meanPredictSpread": mean(spreads),
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
        "delaySuppressedPlans": sum(int(v["delaySuppressedPlans"]) for v in vals),
        "repriceRefreshCancels": sum(int(v["repriceRefreshCancels"]) for v in vals),
    }


def tercile_thresholds(values: list[float]) -> tuple[float, float] | None:
    xs = sorted(float(x) for x in values if x is not None and math.isfinite(float(x)))
    if len(xs) < 9:
        return None
    a = base.q(xs, 1.0 / 3.0)
    b = base.q(xs, 2.0 / 3.0)
    if a is None or b is None:
        return None
    return float(a), float(b)


def bucket(v: float, t1: float, t2: float) -> str:
    return "LOW" if v <= t1 else "MID" if v <= t2 else "HIGH"


def conditional_response(rows: list[dict[str, Any]], ref_name: str, variant_names: list[str]) -> dict[str, Any]:
    features = [
        "meanAbsPredictFromHalf",
        "predictRange",
        "predictTotalVariation",
        "predictTrendiness",
        "simple3FlipRate",
        "meanAbsSpotStrikeBps",
        "meanAbsChainlinkStrikeBps",
        "meanPredictSpread",
    ]
    out: dict[str, Any] = {}
    for feature in features:
        vals = [r["early60Profile"].get(feature) for r in rows]
        valid = [float(x) for x in vals if x is not None]
        th = tercile_thresholds(valid)
        if th is None:
            continue
        t1, t2 = th
        frow: dict[str, Any] = {"tercileThresholds": [t1, t2], "buckets": {}}
        for bn in ("LOW", "MID", "HIGH"):
            subset = [r for r in rows if r["early60Profile"].get(feature) is not None and bucket(float(r["early60Profile"][feature]), t1, t2) == bn]
            bdata: dict[str, Any] = {"n": len(subset), "variants": {}}
            for vn in variant_names:
                ds = [float(r[vn]["pnlUsdt"]) - float(r[ref_name]["pnlUsdt"]) for r in subset]
                vp = [float(r[vn]["pnlUsdt"]) for r in subset]
                rp = [float(r[ref_name]["pnlUsdt"]) for r in subset]
                bdata["variants"][vn] = {
                    "meanPnlDeltaVsRef": mean(ds),
                    "medianPnlDeltaVsRef": statistics.median(ds) if ds else None,
                    "betterRateVsRef": (sum(x > 1e-9 for x in ds) / len(ds)) if ds else None,
                    "variantPositiveRate": (sum(x > 0 for x in vp) / len(vp)) if vp else None,
                    "referencePositiveRate": (sum(x > 0 for x in rp) / len(rp)) if rp else None,
                }
            frow["buckets"][bn] = bdata
        out[feature] = frow
    return out


def regime_sensitivity_scores(resp: dict[str, Any], variant_names: list[str]) -> list[dict[str, Any]]:
    scores: list[dict[str, Any]] = []
    for feature, frow in resp.items():
        for vn in variant_names:
            bucket_values: dict[str, float] = {}
            for bn, br in frow["buckets"].items():
                v = br["variants"][vn]["meanPnlDeltaVsRef"]
                if v is not None:
                    bucket_values[bn] = float(v)
            if len(bucket_values) < 2:
                continue
            hi_bn = max(bucket_values, key=bucket_values.get)
            lo_bn = min(bucket_values, key=bucket_values.get)
            scores.append({
                "feature": feature,
                "variant": vn,
                "bestBucket": hi_bn,
                "bestMeanDelta": bucket_values[hi_bn],
                "worstBucket": lo_bn,
                "worstMeanDelta": bucket_values[lo_bn],
                "spreadAcrossRegimes": bucket_values[hi_bn] - bucket_values[lo_bn],
            })
    return sorted(scores, key=lambda x: abs(float(x["spreadAcrossRegimes"])), reverse=True)


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
            EventParams("REF_DELAY0_REPRICEINF", 0, None),
            EventParams("FILL_DELAY_500MS", 500, None),
            EventParams("FILL_DELAY_1000MS", 1_000, None),
            EventParams("FILL_DELAY_2000MS", 2_000, None),
            EventParams("REPRICE_TOL_1T", 0, 1),
            EventParams("REPRICE_TOL_2T", 0, 2),
            EventParams("REPRICE_TOL_3T", 0, 3),
        ]
        ref_name = params[0].name
        variant_names = [p.name for p in params[1:]]

        rows: list[dict[str, Any]] = []
        for market_id in markets:
            row: dict[str, Any] = {
                "marketId": market_id,
                "winner": winners[market_id],
                "early60Profile": market_profile(snaps[market_id]),
            }
            ss = list(seeds.get(market_id, []))
            for p in params:
                sim = run_one(EventParamSim(models, p), snaps[market_id], ss, market_id)
                row[p.name] = pack(sim, winners[market_id])
            rows.append(row)

        summaries = {p.name: summary(rows, p.name) for p in params}
        ref = summaries[ref_name]
        sensitivity: dict[str, Any] = {}
        for p in params[1:]:
            s = summaries[p.name]
            ds = [float(r[p.name]["pnlUsdt"]) - float(r[ref_name]["pnlUsdt"]) for r in rows]
            sensitivity[p.name] = {
                "params": {
                    "filledOrderDelayMs": p.filled_order_delay_ms,
                    "repriceToleranceTicks": p.reprice_tolerance_ticks,
                },
                "positiveRateDelta": float(s["positiveMarketRate"]) - float(ref["positiveMarketRate"]),
                "pnlDeltaUsdt": float(s["pnlUsdt"]) - float(ref["pnlUsdt"]),
                "edgePerPairedShareDelta": (
                    float(s["edgePerPairedShare"]) - float(ref["edgePerPairedShare"])
                    if s["edgePerPairedShare"] is not None and ref["edgePerPairedShare"] is not None else None
                ),
                "betterMarkets": sum(x > 1e-9 for x in ds),
                "worseMarkets": sum(x < -1e-9 for x in ds),
                "tiedMarkets": sum(abs(x) <= 1e-9 for x in ds),
                "deltaPerMarket": base.stats(ds),
            }

        response = conditional_response(rows, ref_name, variant_names)
        regime_scores = regime_sensitivity_scores(response, variant_names)
        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "purpose": "Test mature event-driven MM knobs and whether their effects depend on public first-minute market regime. Not a global parameter search.",
            "coverage": {"markets": len(rows)},
            "reference": {
                "name": ref_name,
                "policy": "stable offset1 + headwind block from 18 Maker shares + no timer refresh + no filled-order delay",
            },
            "families": {
                "filledOrderDelayMs": [0, 500, 1000, 2000],
                "priceMoveRepriceToleranceTicks": [None, 1, 2, 3],
            },
            "profileBoundary": "Public-only first ~60 seconds (seconds_left>240). May only be used as a controller input from T240 onward; no Target/winner data is used in profile.",
            "guard": [
                "One-factor-at-a-time only; no factorial grid.",
                "This reused cohort is for response-surface discovery, not choosing a production value.",
                "Do not modify the frozen 8785 STABLE_DIRECTIONAL_TOLERANCE_V1:R1 from these results.",
                "A useful result is a stable regime split showing that different market states prefer different knob values, not a single historical champion.",
            ],
            "summary": summaries,
            "sensitivityVsReference": sensitivity,
            "conditionalResponseByEarly60PublicRegime": response,
            "largestRegimeInteractions": regime_scores[:30],
            "rows": rows,
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        compact = {
            "coverage": report["coverage"],
            "reference": {
                "positiveRate": ref["positiveMarketRate"],
                "pnlUsdt": ref["pnlUsdt"],
                "edgePerPairedShare": ref["edgePerPairedShare"],
                "finalAbsNetMedian": ref["finalAbsNet"]["median"],
            },
            "variants": {k: {
                "positiveRate": v["positiveMarketRate"],
                "pnlUsdt": v["pnlUsdt"],
                "edgePerPairedShare": v["edgePerPairedShare"],
                "finalAbsNetMedian": v["finalAbsNet"]["median"],
                "delaySuppressedPlans": v["delaySuppressedPlans"],
                "repriceRefreshCancels": v["repriceRefreshCancels"],
            } for k, v in summaries.items()},
            "sensitivityVsReference": sensitivity,
            "largestRegimeInteractions": regime_scores[:18],
            "report": str(REPORT),
        }
        print(json.dumps(compact, ensure_ascii=False, indent=2))
    finally:
        our.close()
        target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
