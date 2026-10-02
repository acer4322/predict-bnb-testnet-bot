from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
REPORT = ROOT / "data" / "research" / "target_maker_inventory_lifecycle_trajectory_v1_report.json"
TARGET_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"

TIME_BUCKETS = [
    ("T300_240", 240.0, 300.000001),
    ("T240_180", 180.0, 240.0),
    ("T180_120", 120.0, 180.0),
    ("T120_60", 60.0, 120.0),
    ("T60_30", 30.0, 60.0),
    ("T30_15", 15.0, 30.0),
    ("T15_0", 0.0, 15.0),
]
ABS_BINS = [
    ("NET_0_17", 0.0, 18.0),
    ("NET_18_35", 18.0, 36.0),
    ("NET_36_53", 36.0, 54.0),
    ("NET_54_89", 54.0, 90.0),
    ("NET_90_PLUS", 90.0, float("inf")),
]
RATIO_BINS = [
    ("RATIO_0_10", 0.0, 0.10),
    ("RATIO_10_25", 0.10, 0.25),
    ("RATIO_25_50", 0.25, 0.50),
    ("RATIO_50_PLUS", 0.50, float("inf")),
]


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def finite(v: Any) -> float | None:
    try:
        x = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    if len(ys) == 1:
        return ys[0]
    pos = (len(ys) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos)); w = pos - lo
    return ys[lo] * (1-w) + ys[hi] * w


def stats(xs: list[float]) -> dict[str, Any]:
    ys = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {
        "n": len(ys), "min": min(ys) if ys else None, "max": max(ys) if ys else None,
        "mean": statistics.mean(ys) if ys else None, "median": statistics.median(ys) if ys else None,
        "p25": pct(ys, .25), "p75": pct(ys, .75), "p90": pct(ys, .90),
    }


def bucket_name(v: float, bins: list[tuple[str, float, float]]) -> str:
    for name, lo, hi in bins:
        if lo <= v < hi:
            return name
    return bins[-1][0]


def time_bucket(sec: float | None) -> str:
    if sec is None:
        return "UNKNOWN"
    for name, lo, hi in TIME_BUCKETS:
        if lo <= sec < hi:
            return name
    return "OUTSIDE"


def cumulative_index(events: list[sqlite3.Row]) -> dict[int, dict[str, tuple[list[int], list[float]]]]:
    by: dict[int, dict[str, list[tuple[int, float]]]] = defaultdict(lambda: {"UP": [], "DOWN": []})
    for r in events:
        m = int(r["market_id"]); side = str(r["side"]); t = int(r["event_ms"]); sh = float(r["shares"])
        by[m][side].append((t, sh))
    out: dict[int, dict[str, tuple[list[int], list[float]]]] = {}
    for m, sides in by.items():
        out[m] = {}
        for side, rows in sides.items():
            rows.sort()
            times: list[int] = []; sums: list[float] = []; total = 0.0
            for t, sh in rows:
                total += sh; times.append(t); sums.append(total)
            out[m][side] = (times, sums)
    return out


def cum_at(idx: dict[int, dict[str, tuple[list[int], list[float]]]], market: int, side: str, t: int) -> float:
    pair = idx.get(market, {}).get(side)
    if not pair:
        return 0.0
    times, sums = pair
    i = bisect.bisect_right(times, t) - 1
    return sums[i] if i >= 0 else 0.0


def inventory_state(idx: dict[int, dict[str, tuple[list[int], list[float]]]], market: int, t: int) -> dict[str, Any]:
    up = cum_at(idx, market, "UP", t); down = cum_at(idx, market, "DOWN", t)
    net = up - down; gross = up + down; abs_net = abs(net)
    ratio = abs_net / gross if gross > 1e-9 else 0.0
    dom = "UP" if net > 1e-9 else "DOWN" if net < -1e-9 else "FLAT"
    minority = "DOWN" if dom == "UP" else "UP" if dom == "DOWN" else "FLAT"
    return {"up": up, "down": down, "net": net, "gross": gross, "absNet": abs_net,
            "ratio": ratio, "dominant": dom, "minority": minority}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"n": 0}
    eligible = [r for r in rows if r["nextAny5s"] is not None]
    next5 = [r for r in eligible if r["nextAny5s"]]
    delays = [float(r["nextDelayMs"]) for r in next5 if r["nextDelayMs"] is not None]
    distances = [float(r["nextDistanceTicks"]) for r in next5 if r["nextDistanceTicks"] is not None]
    denom = len(eligible)
    return {
        "n": len(rows),
        "eligible5sN": denom,
        "markets": len({r["marketId"] for r in rows}),
        "nextAnySameSide5sRate": (len(next5) / denom) if denom else None,
        "pauseNoSameSideNext5sRate": (1.0 - len(next5) / denom) if denom else None,
        "nextDelayMs": stats(delays),
        "nextDistanceTicks": stats(distances),
        "samePriceNextRateAmongAll": (sum(1 for r in eligible if r.get("nextAny5s") and r.get("nextDistanceTicks", 999) < .5) / denom) if denom else None,
        "near1To3TickNextRateAmongAll": (sum(1 for r in eligible if r.get("nextAny5s") and .5 <= r.get("nextDistanceTicks", 999) <= 3.000001) / denom) if denom else None,
        "far4PlusTickNextRateAmongAll": (sum(1 for r in eligible if r.get("nextAny5s") and r.get("nextDistanceTicks", -1) >= 3.999999) / denom) if denom else None,
        "postAbsNetShares": stats([r["postAbsNet"] for r in rows]),
        "postImbalanceRatio": stats([r["postRatio"] for r in rows]),
        "restingMs": stats([r["restingMs"] for r in rows if r["restingMs"] is not None]),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--book-db", type=Path, default=BOOK_DB)
    ap.add_argument("--target-db", type=Path, default=TARGET_DB)
    ap.add_argument("--report", type=Path, default=REPORT)
    args = ap.parse_args()

    book = ro(args.book_db); target = ro(args.target_db)
    try:
        latest = int(book.execute("SELECT COALESCE(MAX(source_timestamp_ms),0) FROM maker_book_inference_updates").fetchone()[0])
        markets = {int(r["market_id"]): int(r["window_end_ms"]) for r in book.execute(
            "SELECT market_id,window_end_ms FROM maker_book_inference_markets WHERE window_end_ms IS NOT NULL AND window_end_ms<=?",
            (latest - 15000,))}
        if not markets:
            raise SystemExit("no finalized retained markets")
        placeholders = ",".join("?" for _ in markets)
        event_rows = list(target.execute(
            f"""SELECT market_id,event_ms,side,shares FROM wallet_shadow_target_events
                 WHERE lower(wallet)=lower(?) AND asset='BTC' AND role='MAKER' AND quote_type='BID'
                   AND market_id IN ({placeholders}) ORDER BY market_id,event_ms""",
            [TARGET_WALLET, *markets.keys()]))
        idx = cumulative_index(event_rows)

        parents = [dict(r) for r in book.execute(
            f"""SELECT parent_id,market_id,target_side,native_price,first_target_ms,last_target_ms,
                       target_filled_shares,placement_first_ms,placement_last_ms,resting_ms,
                       placement_coverage,fill_allocation_coverage,placement_supports_18,confidence
                  FROM maker_book_inference_v21_parent_lifecycles
                 WHERE market_id IN ({placeholders})
                   AND placement_first_ms IS NOT NULL
                   AND placement_supports_18=1 AND placement_coverage>=0.85
                   AND fill_allocation_coverage>=0.70 AND confidence>=0.75
                 ORDER BY market_id,first_target_ms""", list(markets.keys()))]

        by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for p in parents:
            by_market[int(p["market_id"])].append(p)

        rows: list[dict[str, Any]] = []
        for m, ps in by_market.items():
            end = markets.get(m)
            if end is None:
                continue
            # For each filled parent, search ANY same-side newly placed parent in the next 5s.
            for p in ps:
                fill_t = int(p["last_target_ms"])
                side = str(p["target_side"])
                post = inventory_state(idx, m, fill_t)
                sec = (end - fill_t) / 1000.0
                if not (0 <= sec <= 300.5):
                    continue
                eligible5s = sec >= 5.0
                candidates = [q for q in ps if eligible5s and str(q["target_side"]) == side and q["parent_id"] != p["parent_id"]
                              and q["placement_first_ms"] is not None
                              and fill_t < int(q["placement_first_ms"]) <= fill_t + 5000]
                candidates.sort(key=lambda q: int(q["placement_first_ms"]))
                nxt = candidates[0] if candidates else None
                next_delay = int(nxt["placement_first_ms"]) - fill_t if nxt else None
                next_dist = abs(float(nxt["native_price"]) - float(p["native_price"])) / 0.01 if nxt else None
                post5 = inventory_state(idx, m, fill_t + 5000) if eligible5s else None
                delta_abs_5s = (post5["absNet"] - post["absNet"]) if post5 is not None else None
                relation = "DOMINANT" if post["dominant"] == side else "MINORITY" if post["minority"] == side else "FLAT"
                rows.append({
                    "marketId": m, "parentId": p["parent_id"], "side": side, "secondsLeft": sec,
                    "timeBucket": time_bucket(sec), "postUp": post["up"], "postDown": post["down"],
                    "postAbsNet": post["absNet"], "postRatio": post["ratio"],
                    "absNetBin": bucket_name(post["absNet"], ABS_BINS), "ratioBin": bucket_name(post["ratio"], RATIO_BINS),
                    "sideRelationPostFill": relation, "nextAny5s": (nxt is not None) if eligible5s else None,
                    "nextDelayMs": next_delay, "nextDistanceTicks": next_dist,
                    "restingMs": finite(p.get("resting_ms")), "deltaAbsNet5s": delta_abs_5s,
                })

        nonflat = [r for r in rows if r["sideRelationPostFill"] in {"DOMINANT", "MINORITY"}]
        by_abs: dict[str, Any] = {}
        for name, _, _ in ABS_BINS:
            rr = [r for r in nonflat if r["absNetBin"] == name]
            by_abs[name] = {
                "ALL": summarize(rr),
                "DOMINANT": summarize([r for r in rr if r["sideRelationPostFill"] == "DOMINANT"]),
                "MINORITY": summarize([r for r in rr if r["sideRelationPostFill"] == "MINORITY"]),
            }
        by_ratio: dict[str, Any] = {}
        for name, _, _ in RATIO_BINS:
            rr = [r for r in nonflat if r["ratioBin"] == name]
            by_ratio[name] = {
                "ALL": summarize(rr),
                "DOMINANT": summarize([r for r in rr if r["sideRelationPostFill"] == "DOMINANT"]),
                "MINORITY": summarize([r for r in rr if r["sideRelationPostFill"] == "MINORITY"]),
            }
        by_time: dict[str, Any] = {}
        for name, _, _ in TIME_BUCKETS:
            rr = [r for r in nonflat if r["timeBucket"] == name]
            by_time[name] = {
                "ALL": summarize(rr),
                "DOMINANT": summarize([r for r in rr if r["sideRelationPostFill"] == "DOMINANT"]),
                "MINORITY": summarize([r for r in rr if r["sideRelationPostFill"] == "MINORITY"]),
            }

        # Within time bucket + imbalance bin, compute dominant-minus-minority continuation rate.
        controlled: list[dict[str, Any]] = []
        for tname, _, _ in TIME_BUCKETS:
            for bname, _, _ in ABS_BINS:
                rr = [r for r in nonflat if r["timeBucket"] == tname and r["absNetBin"] == bname]
                d = [r for r in rr if r["sideRelationPostFill"] == "DOMINANT" and r["nextAny5s"] is not None]
                mi = [r for r in rr if r["sideRelationPostFill"] == "MINORITY" and r["nextAny5s"] is not None]
                if len(d) < 20 or len(mi) < 20:
                    continue
                dr = sum(bool(r["nextAny5s"]) for r in d) / len(d)
                mr = sum(bool(r["nextAny5s"]) for r in mi) / len(mi)
                controlled.append({"timeBucket": tname, "absNetBin": bname, "dominantN": len(d), "minorityN": len(mi),
                                   "dominantNext5sRate": dr, "minorityNext5sRate": mr,
                                   "dominantMinusMinority": dr - mr})

        def impact_group(rr: list[dict[str, Any]]) -> dict[str, Any]:
            vals = [float(x["deltaAbsNet5s"]) for x in rr if x.get("deltaAbsNet5s") is not None]
            return {
                "n": len(vals),
                "deltaAbsNet5s": stats(vals),
                "improvedRate": (sum(v < -1e-9 for v in vals) / len(vals)) if vals else None,
                "worsenedRate": (sum(v > 1e-9 for v in vals) / len(vals)) if vals else None,
                "unchangedRate": (sum(abs(v) <= 1e-9 for v in vals) / len(vals)) if vals else None,
            }
        impact = {}
        for rel in ("DOMINANT", "MINORITY"):
            rr = [x for x in nonflat if x["sideRelationPostFill"] == rel and x["nextAny5s"] is not None]
            impact[rel] = {
                "SAME_SIDE_CONTINUES_5S": impact_group([x for x in rr if x["nextAny5s"]]),
                "NO_SAME_SIDE_NEXT_5S": impact_group([x for x in rr if not x["nextAny5s"]]),
            }

        report = {
            "reportVersion": "TARGET_MAKER_INVENTORY_LIFECYCLE_TRAJECTORY_V1",
            "researchOnly": True,
            "layer": "INVENTORY_ONLY_NO_DIRECTION",
            "method": {
                "inventory": "strict-past Target official MAKER BID fills only; post-fill state at each anchored parent last_target_ms",
                "sample": "v2.1 high-confidence anchored ±18 placement parent: placement_supports_18=1, placement_coverage>=.85, fill_allocation_coverage>=.70, confidence>=.75",
                "nextParent": "ANY same-side inferred parent placement whose placement_first_ms is > current last_target_ms and <= +5s; unlike collector post_action this does not impose a <=3 tick distance cap",
                "directionUsed": False,
                "warning": "Target parent placement ownership is probabilistic even when later Target Maker fill anchors it. This is post-hoc lifecycle analysis, not a live decision label.",
            },
            "coverage": {"finalizedRetainedMarkets": len(markets), "officialMakerFillEvents": len(event_rows),
                         "anchoredParents": len(rows), "nonFlatPostFillParents": len(nonflat), "latestBookSourceMs": latest},
            "overall": {"ALL": summarize(rows), "NONFLAT": summarize(nonflat),
                        "DOMINANT": summarize([r for r in nonflat if r["sideRelationPostFill"] == "DOMINANT"]),
                        "MINORITY": summarize([r for r in nonflat if r["sideRelationPostFill"] == "MINORITY"])},
            "byPostFillAbsNetShares": by_abs,
            "byPostFillImbalanceRatio": by_ratio,
            "byTime": by_time,
            "timeAndAbsNetControlledCells": controlled,
            "inventoryImpact5sByLifecycle": impact,
        }
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        book.close(); target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
