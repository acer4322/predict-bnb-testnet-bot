from __future__ import annotations

import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "wallet_maker_book_inference.db"
OUT = ROOT / "data" / "research" / "target_maker_time_layer_v1_report.json"
VERSION = "TARGET_MAKER_TIME_LAYER_V1"

# Pure time-layer study. No inventory, no market-direction features.
BINS = [
    ("T300_240", 240.0, 300.000001),
    ("T240_180", 180.0, 240.0),
    ("T180_120", 120.0, 180.0),
    ("T120_60", 60.0, 120.0),
    ("T60_30", 30.0, 60.0),
    ("T30_15", 15.0, 30.0),
    ("T15_0", -0.000001, 15.0),
]
DURATIONS = {name: hi - lo for name, lo, hi in BINS}


def ro() -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True, timeout=20)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    xs = sorted(values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos)); w = pos - lo
    return xs[lo] * (1 - w) + xs[hi] * w


def stats(values: list[float]) -> dict[str, Any]:
    xs = [float(v) for v in values if v is not None and math.isfinite(float(v))]
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "max": max(xs) if xs else None,
        "mean": statistics.mean(xs) if xs else None,
        "median": statistics.median(xs) if xs else None,
        "p25": pct(xs, 0.25),
        "p75": pct(xs, 0.75),
        "p90": pct(xs, 0.90),
    }


def bin_name(seconds_left: float) -> str | None:
    for name, lo, hi in BINS:
        if lo < seconds_left <= hi:
            return name
    return None


def main() -> int:
    con = ro()
    try:
        latest_source_ms = int(con.execute("SELECT COALESCE(MAX(source_timestamp_ms),0) FROM maker_book_inference_updates").fetchone()[0])
        markets = {
            int(r["market_id"]): int(r["window_end_ms"])
            for r in con.execute(
                "SELECT market_id,window_end_ms FROM maker_book_inference_markets WHERE window_end_ms<=?",
                (latest_source_ms - 15_000,),
            )
        }
        market_ids = sorted(markets)
        if not market_ids:
            raise SystemExit("no finalized retained markets")
        marks = ",".join("?" for _ in market_ids)

        # Primary sample: later-confirmed Target Maker parent + strong 18-share placement support.
        rows = [dict(r) for r in con.execute(
            f"""SELECT p.* FROM maker_book_inference_v21_parent_lifecycles p
                WHERE p.market_id IN ({marks})
                  AND p.placement_last_ms IS NOT NULL
                  AND p.placement_supports_18=1
                  AND p.placement_coverage>=0.85
                  AND p.fill_allocation_coverage>=0.70
                  AND p.confidence>=0.75""",
            market_ids,
        )]

        primary: list[dict[str, Any]] = []
        for r in rows:
            end = markets[int(r["market_id"])]
            sec = (end - int(r["placement_last_ms"])) / 1000.0
            bn = bin_name(sec)
            if bn is None:
                continue
            item = dict(r)
            item["seconds_left_at_placement"] = sec
            item["time_bin"] = bn
            primary.append(item)

        by_bin: dict[str, list[dict[str, Any]]] = defaultdict(list)
        by_market_bin: dict[tuple[int, str], int] = defaultdict(int)
        by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in primary:
            by_bin[r["time_bin"]].append(r)
            by_market_bin[(int(r["market_id"]), r["time_bin"])] += 1
            by_market[int(r["market_id"])].append(r)

        phase = {}
        for name, _, _ in BINS:
            rs = by_bin.get(name, [])
            counts = [by_market_bin.get((m, name), 0) for m in market_ids]
            active = sum(1 for x in counts if x > 0)
            same = [r for r in rs if r.get("post_action") == "SAME_PRICE_REFILL_CONFIRMED_NEXT_PARENT"]
            reprice = [r for r in rs if r.get("post_action") == "REPRICE_1_3_TICKS_CONFIRMED_NEXT_PARENT"]
            no_next = [r for r in rs if r.get("post_action") == "NO_CONFIRMED_NEXT_PARENT_5S"]
            phase[name] = {
                "durationSeconds": DURATIONS[name],
                "placements": len(rs),
                "marketsActive": active,
                "marketActiveRate": active / len(market_ids),
                "placementsPerMarket": stats([float(x) for x in counts]),
                "placementsPerMinutePerMarketMean": statistics.mean(counts) * 60.0 / DURATIONS[name] if counts else None,
                "restingMs": stats([float(r["resting_ms"]) for r in rs if r.get("resting_ms") is not None and int(r["resting_ms"]) >= 0]),
                "postAction": {
                    "samePriceRefillRate": len(same) / len(rs) if rs else None,
                    "reprice1To3TicksRate": len(reprice) / len(rs) if rs else None,
                    "noConfirmedNextParent5sRate": len(no_next) / len(rs) if rs else None,
                    "samePriceRefillDelayMs": stats([float(r["post_action_delay_ms"]) for r in same if r.get("post_action_delay_ms") is not None]),
                    "repriceDelayMs": stats([float(r["post_action_delay_ms"]) for r in reprice if r.get("post_action_delay_ms") is not None]),
                },
                "sideCounts": {
                    "UP": sum(1 for r in rs if r.get("target_side") == "UP"),
                    "DOWN": sum(1 for r in rs if r.get("target_side") == "DOWN"),
                },
            }

        first_sec = []
        last_sec = []
        for m in market_ids:
            rs = by_market.get(m, [])
            if not rs:
                continue
            secs = [float(r["seconds_left_at_placement"]) for r in rs]
            first_sec.append(max(secs))
            last_sec.append(min(secs))

        # Secondary sample: anonymous +/-18-like add->remove candidates, no Target-fill confirmation.
        spec_rows = [dict(r) for r in con.execute(
            f"""SELECT c.* FROM maker_book_inference_v21_cancel_candidates c
                WHERE c.market_id IN ({marks})
                  AND c.allocated_quantity BETWEEN 15.3 AND 20.7
                  AND c.confidence>=0.70""",
            market_ids,
        )]
        spec_by_bin: dict[str, list[dict[str, Any]]] = defaultdict(list)
        spec_market_bin: dict[tuple[int, str], int] = defaultdict(int)
        for r in spec_rows:
            end = markets[int(r["market_id"])]
            sec = (end - int(r["placement_source_ms"])) / 1000.0
            bn = bin_name(sec)
            if bn is None:
                continue
            r["seconds_left_at_placement"] = sec
            spec_by_bin[bn].append(r)
            spec_market_bin[(int(r["market_id"]), bn)] += 1

        speculative = {}
        for name, _, _ in BINS:
            rs = spec_by_bin.get(name, [])
            counts = [spec_market_bin.get((m, name), 0) for m in market_ids]
            speculative[name] = {
                "candidates": len(rs),
                "marketsActive": sum(1 for x in counts if x > 0),
                "candidatesPerMarket": stats([float(x) for x in counts]),
                "restingMs": stats([float(r["resting_ms"]) for r in rs if r.get("resting_ms") is not None]),
                "samePriceRefreshRate": sum(1 for r in rs if r.get("post_action") == "SAME_PRICE_REFRESH") / len(rs) if rs else None,
                "reprice1To3TicksRate": sum(1 for r in rs if r.get("post_action") == "REPRICE_1_3_TICKS") / len(rs) if rs else None,
                "noVisibleReplacement3sRate": sum(1 for r in rs if r.get("post_action") == "NO_VISIBLE_REPLACEMENT_3S") / len(rs) if rs else None,
            }

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "layer": "TIME_ONLY",
            "inventoryUsed": False,
            "marketDirectionUsed": False,
            "method": {
                "primary": "ANCHORED_PLACEMENT: v2.1 consumable quantity allocation; later-confirmed Target Maker parent; placement_supports_18=1; placement_coverage>=0.85; fill_allocation_coverage>=0.70; confidence>=0.75.",
                "secondary": "INFERRED_UNFILLED: anonymous ~18-share public add->remove candidate with confidence>=0.70. Ownership is NOT proven and is reported separately.",
                "placementClock": "public book source_timestamp_ms; seconds_left from retained market window_end_ms",
                "restingDefinition": "first matched public fill decrease source time - latest allocated placement increase source time",
                "warning": "Primary placement identity remains probabilistic despite later Target-fill anchoring; secondary candidates are substantially weaker and must not be interpreted as Target ground truth.",
            },
            "coverage": {
                "finalizedRetainedMarkets": len(market_ids),
                "primaryAnchoredPlacements": len(primary),
                "primaryMarkets": len(by_market),
                "speculativeUnfilledCandidates": sum(len(v) for v in spec_by_bin.values()),
                "latestSourceMs": latest_source_ms,
            },
            "marketTiming": {
                "firstAnchoredPlacementSecondsLeft": stats(first_sec),
                "lastAnchoredPlacementSecondsLeft": stats(last_sec),
            },
            "anchoredByTime": phase,
            "speculativeUnfilledByTime": speculative,
            "nextLayerGuard": "Do not add inventory imbalance or direction conditioning to this report. Those are Layer 2 and Layer 3 respectively.",
        }
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
