from __future__ import annotations

import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
BOOK_DB = ROOT / "data" / "wallet_maker_book_inference.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
SIGNAL_DB = ROOT / "data" / "strategy_target_compare_v1.db"
REPORT = ROOT / "data" / "research" / "target_maker_direction_and_maker_only_v1_report.json"
VERSION = "TARGET_MAKER_DIRECTION_AND_MAKER_ONLY_V1"


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    ys = sorted(xs)
    pos = (len(ys) - 1) * p
    lo = int(math.floor(pos)); hi = int(math.ceil(pos)); w = pos - lo
    return ys[lo] * (1 - w) + ys[hi] * w


def stats(xs: list[float]) -> dict[str, Any]:
    xs = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    return {
        "n": len(xs),
        "min": min(xs) if xs else None,
        "max": max(xs) if xs else None,
        "mean": statistics.mean(xs) if xs else None,
        "median": statistics.median(xs) if xs else None,
        "p25": pct(xs, .25),
        "p75": pct(xs, .75),
        "p90": pct(xs, .90),
        "sum": sum(xs) if xs else None,
    }


def time_bucket(seconds_left: float | None) -> str:
    if seconds_left is None: return "UNKNOWN"
    if seconds_left > 240: return "T300_240"
    if seconds_left > 180: return "T240_180"
    if seconds_left > 120: return "T180_120"
    if seconds_left > 60: return "T120_60"
    if seconds_left > 30: return "T60_30"
    if seconds_left > 15: return "T30_15"
    return "T15_0"


def simple3(row: dict[str, Any] | None) -> tuple[str | None, str | None]:
    if not row:
        return None, None
    vals = [row.get("predictUpMid", row.get("predict_up_mid")), row.get("spotMinusStrikeBps", row.get("spot_minus_strike_bps")), row.get("chainlinkMinusStrikeBps", row.get("chainlink_minus_strike_bps"))]
    try:
        p = float(vals[0]); s = float(vals[1]); c = float(vals[2])
    except (TypeError, ValueError):
        return None, None
    if not all(math.isfinite(x) for x in (p, s, c)):
        return None, None
    votes = [("UP" if p > .5 else "DOWN"), ("UP" if s > 0 else "DOWN"), ("UP" if c > 0 else "DOWN")]
    up = sum(v == "UP" for v in votes)
    direction = "UP" if up >= 2 else "DOWN"
    strength = "STRONG" if votes.count(votes[0]) == 3 else "WEAK"
    return direction, strength


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    eligible = [r for r in rows if r["eligible5s"]]
    nxt = [r for r in eligible if r["next5s"]]
    return {
        "n": len(rows),
        "markets": len({r["market_id"] for r in rows}),
        "eligible5s": len(eligible),
        "nextAnySameSide5sRate": (len(nxt) / len(eligible)) if eligible else None,
        "pauseNoSameSideNext5sRate": (1 - len(nxt) / len(eligible)) if eligible else None,
        "nextDelayMs": stats([r["next_delay_ms"] for r in nxt if r["next_delay_ms"] is not None]),
        "nextDistanceTicks": stats([r["next_distance_ticks"] for r in nxt if r["next_distance_ticks"] is not None]),
        "preAbsNetShares": stats([r["pre_abs_net"] for r in rows]),
        "preImbalanceRatio": stats([r["pre_ratio"] for r in rows]),
    }


def maker_only_economics(target: sqlite3.Connection) -> dict[str, Any]:
    results = {int(r["market_id"]): dict(r) for r in target.execute(
        "SELECT * FROM target_market_results WHERE asset='BTC' AND winner IN ('UP','DOWN')"
    )}
    fills: dict[int, list[dict[str, Any]]] = defaultdict(list)
    role_counts = defaultdict(int)
    quote_counts = defaultdict(int)
    for r in target.execute("""
        SELECT market_id,role,side,quote_type,event_ms,price,shares
        FROM wallet_shadow_target_events
        WHERE asset='BTC' AND role IN ('MAKER','TAKER') AND side IN ('UP','DOWN')
        ORDER BY market_id,event_ms
    """):
        role_counts[str(r["role"])] += 1
        quote_counts[(str(r["role"]), str(r["quote_type"]))] += 1
        if str(r["role"]) == "MAKER" and str(r["quote_type"]) == "BID":
            fills[int(r["market_id"])].append(dict(r))

    rows = []
    for mid, result in results.items():
        fs = fills.get(mid, [])
        if not fs:
            continue
        winner = str(result["winner"])
        up_sh = down_sh = cost = 0.0
        for f in fs:
            sh = float(f["shares"]); px = float(f["price"])
            cost += sh * px
            if str(f["side"]) == "UP": up_sh += sh
            else: down_sh += sh
        payout = up_sh if winner == "UP" else down_sh
        pnl = payout - cost
        total_pnl = float(result["net_pnl_usdt"] or 0.0)
        rows.append({
            "market_id": mid, "winner": winner, "maker_up": up_sh, "maker_down": down_sh,
            "maker_cost": cost, "maker_payout": payout, "maker_only_pnl": pnl,
            "official_maker_net_pnl": float(result["maker_net_pnl_usdt"] or 0.0),
            "total_pnl": total_pnl,
        })

    maker_pos = [r for r in rows if r["maker_only_pnl"] > 1e-9]
    total_pos = [r for r in rows if r["total_pnl"] > 1e-9]
    both_pos = [r for r in rows if r["maker_only_pnl"] > 1e-9 and r["total_pnl"] > 1e-9]
    rescue = [r for r in rows if r["maker_only_pnl"] <= 1e-9 and r["total_pnl"] > 1e-9]
    drag = [r for r in rows if r["maker_only_pnl"] > 1e-9 and r["total_pnl"] <= 1e-9]
    diff = [abs(r["maker_only_pnl"] - r["official_maker_net_pnl"]) for r in rows]

    # FIFO Maker pairing diagnostic.
    pair_edges = []
    pair_shares_all = 0.0; pair_edge_all = 0.0
    for mid, result in results.items():
        fs = fills.get(mid, [])
        if not fs: continue
        q = {"UP": deque(), "DOWN": deque()}
        paired = edge = 0.0
        for f in fs:
            side = str(f["side"]); opp = "DOWN" if side == "UP" else "UP"
            rem = float(f["shares"]); px = float(f["price"])
            while rem > 1e-9 and q[opp]:
                old = q[opp][0]
                m = min(rem, old["shares"])
                paired += m; edge += m * (1.0 - px - old["price"])
                rem -= m; old["shares"] -= m
                if old["shares"] <= 1e-9: q[opp].popleft()
            if rem > 1e-9: q[side].append({"shares": rem, "price": px})
        pair_shares_all += paired; pair_edge_all += edge
        pair_edges.append(edge)

    n = len(rows)
    return {
        "definition": "Observed Target MAKER BID fills only; settle winning Maker shares at 1, ignore all Target TAKER fills. Actual-path attribution, NOT a no-Taker counterfactual policy replay.",
        "coverage": {"settledMarketsWithMakerFills": n, "roleCounts": dict(role_counts), "quoteCounts": {f"{k[0]}:{k[1]}": v for k, v in quote_counts.items()}},
        "makerOnlyPositiveMarkets": len(maker_pos),
        "makerOnlyPositiveRate": len(maker_pos) / n if n else None,
        "totalPositiveMarkets": len(total_pos),
        "totalPositiveRate": len(total_pos) / n if n else None,
        "bothPositiveMarkets": len(both_pos),
        "makerNonPositiveButTotalPositiveMarkets": len(rescue),
        "makerNonPositiveButTotalPositiveRate": len(rescue) / n if n else None,
        "makerPositiveButTotalNonPositiveMarkets": len(drag),
        "makerPositiveButTotalNonPositiveRate": len(drag) / n if n else None,
        "makerOnlyPnlUsdt": stats([r["maker_only_pnl"] for r in rows]),
        "totalPnlUsdt": stats([r["total_pnl"] for r in rows]),
        "makerOnlyVsOfficialMakerPnlAbsDiff": stats(diff),
        "makerPairing": {
            "totalPairedShares": pair_shares_all,
            "totalLockedEdgeUsdt": pair_edge_all,
            "edgePerPairedShare": pair_edge_all / pair_shares_all if pair_shares_all else None,
            "perMarketLockedEdgeUsdt": stats(pair_edges),
        },
        "rows": rows,
    }


def main() -> int:
    book = ro(BOOK_DB); target = ro(TARGET_DB); sig = ro(SIGNAL_DB)
    try:
        markets = {int(r["market_id"]): dict(r) for r in book.execute(
            "SELECT market_id,window_end_ms FROM maker_book_inference_markets WHERE window_end_ms IS NOT NULL"
        )}
        parents_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in book.execute("""
            SELECT * FROM maker_book_inference_v21_parent_lifecycles
            WHERE placement_supports_18=1 AND placement_coverage>=0.85
              AND fill_allocation_coverage>=0.70 AND confidence>=0.75
              AND placement_first_ms IS NOT NULL
            ORDER BY market_id,placement_first_ms
        """):
            parents_by_market[int(r["market_id"])].append(dict(r))

        maker_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for r in target.execute("""
            SELECT market_id,event_ms,side,shares FROM wallet_shadow_target_events
            WHERE asset='BTC' AND role='MAKER' AND quote_type='BID' AND side IN ('UP','DOWN')
            ORDER BY market_id,event_ms
        """):
            maker_events[int(r["market_id"])].append(dict(r))

        signals: dict[int, list[dict[str, Any]]] = defaultdict(list)
        signal_times: dict[int, list[int]] = defaultdict(list)
        seen_signal_keys: set[tuple[int, int]] = set()
        for r in sig.execute("""
            SELECT market_id,decision_ms,source_snapshot_ms,public_state_json
            FROM our_decisions
            WHERE strategy_version LIKE 'UNIFIED_CONTROLLER_PAPER_V1%'
            ORDER BY market_id,decision_ms
        """):
            try:
                d = json.loads(str(r["public_state_json"]))
            except Exception:
                continue
            if not isinstance(d, dict):
                continue
            mid = int(r["market_id"])
            sampled = int(d.get("sampledAtMs") or r["source_snapshot_ms"] or r["decision_ms"])
            key = (mid, sampled)
            if key in seen_signal_keys:
                continue
            seen_signal_keys.add(key)
            if simple3(d)[0] is None:
                continue
            d = dict(d); d["sampled_at_ms"] = sampled
            signals[mid].append(d); signal_times[mid].append(sampled)

        rows: list[dict[str, Any]] = []
        for mid, parents in parents_by_market.items():
            if mid not in maker_events or mid not in signals:
                continue
            events = maker_events[mid]
            ei = 0; up = down = 0.0
            side_places = {
                "UP": sorted([(int(p["placement_first_ms"]), p) for p in parents if str(p["target_side"]) == "UP"], key=lambda x: x[0]),
                "DOWN": sorted([(int(p["placement_first_ms"]), p) for p in parents if str(p["target_side"]) == "DOWN"], key=lambda x: x[0]),
            }
            side_times = {k: [x[0] for x in v] for k, v in side_places.items()}
            for p in parents:
                place_ms = int(p["placement_first_ms"])
                while ei < len(events) and int(events[ei]["event_ms"]) < place_ms:
                    e = events[ei]
                    if str(e["side"]) == "UP": up += float(e["shares"])
                    else: down += float(e["shares"])
                    ei += 1
                gross = up + down
                net = up - down
                if gross <= 1e-9 or abs(net) <= 1e-9:
                    continue
                dom = "UP" if net > 0 else "DOWN"
                minority = "DOWN" if dom == "UP" else "UP"
                st = signal_times[mid]
                idx = bisect.bisect_right(st, place_ms) - 1
                if idx < 0:
                    continue
                srow = signals[mid][idx]
                age = place_ms - int(srow["sampled_at_ms"])
                if age < 0 or age > 2000:
                    continue
                direction, strength = simple3(srow)
                if direction is None:
                    continue
                alignment = "TAILWIND" if dom == direction else "HEADWIND"
                sec_raw = srow.get("secondsLeft", srow.get("seconds_left"))
                try:
                    sec = float(sec_raw)
                except (TypeError, ValueError):
                    continue
                current_side = str(p["target_side"])
                role = "DOMINANT" if current_side == dom else "MINORITY"
                end_ms = int(p["last_target_ms"])
                eligible5 = sec >= 5.0
                arr = side_places[current_side]; times = side_times[current_side]
                j = bisect.bisect_right(times, end_ms)
                nxt = None
                while j < len(arr):
                    t, pp = arr[j]
                    if t <= end_ms:
                        j += 1; continue
                    if t > end_ms + 5000:
                        break
                    if str(pp["parent_id"]) != str(p["parent_id"]):
                        nxt = pp; break
                    j += 1
                ndelay = int(nxt["placement_first_ms"]) - end_ms if nxt is not None else None
                ndist = abs(float(nxt["native_price"]) - float(p["native_price"])) / .01 if nxt is not None else None
                rows.append({
                    "market_id": mid, "placement_ms": place_ms, "seconds_left": sec, "time_bucket": time_bucket(sec),
                    "pre_up": up, "pre_down": down, "pre_net": net, "pre_abs_net": abs(net), "pre_ratio": abs(net)/gross,
                    "dominant_side": dom, "minority_side": minority, "current_side": current_side, "inventory_role": role,
                    "simple3_direction": direction, "simple3_strength": strength, "alignment": alignment, "signal_age_ms": age,
                    "eligible5s": eligible5, "next5s": bool(nxt) if eligible5 else False,
                    "next_delay_ms": ndelay if eligible5 else None, "next_distance_ticks": ndist if eligible5 else None,
                })

        dominant = [r for r in rows if r["inventory_role"] == "DOMINANT"]
        minority = [r for r in rows if r["inventory_role"] == "MINORITY"]
        layer3 = {
            "coverage": {"rows": len(rows), "markets": len({r['market_id'] for r in rows}), "dominantRows": len(dominant), "minorityRows": len(minority)},
            "overall": {
                "DOMINANT_TAILWIND": summarize([r for r in dominant if r["alignment"] == "TAILWIND"]),
                "DOMINANT_HEADWIND": summarize([r for r in dominant if r["alignment"] == "HEADWIND"]),
                "MINORITY_TAILWIND": summarize([r for r in minority if r["alignment"] == "TAILWIND"]),
                "MINORITY_HEADWIND": summarize([r for r in minority if r["alignment"] == "HEADWIND"]),
            },
            "dominantByTime": {},
            "dominantByRatio": {},
            "dominantByStrength": {},
        }
        for tb in ["T300_240","T240_180","T180_120","T120_60","T60_30","T30_15","T15_0"]:
            layer3["dominantByTime"][tb] = {
                "TAILWIND": summarize([r for r in dominant if r["time_bucket"] == tb and r["alignment"] == "TAILWIND"]),
                "HEADWIND": summarize([r for r in dominant if r["time_bucket"] == tb and r["alignment"] == "HEADWIND"]),
            }
        ratio_bins = [(0,.10,"RATIO_0_10"),(.10,.25,"RATIO_10_25"),(.25,.50,"RATIO_25_50"),(.50,2,"RATIO_50_PLUS")]
        for lo,hi,name in ratio_bins:
            layer3["dominantByRatio"][name] = {
                "TAILWIND": summarize([r for r in dominant if lo <= r["pre_ratio"] < hi and r["alignment"] == "TAILWIND"]),
                "HEADWIND": summarize([r for r in dominant if lo <= r["pre_ratio"] < hi and r["alignment"] == "HEADWIND"]),
            }
        layer3["dominantByAbsNet"] = {}
        abs_bins = [(0,18,"NET_0_17"),(18,36,"NET_18_35"),(36,54,"NET_36_53"),(54,90,"NET_54_89"),(90,10**9,"NET_90_PLUS")]
        for lo,hi,name in abs_bins:
            layer3["dominantByAbsNet"][name] = {
                "TAILWIND": summarize([r for r in dominant if lo <= r["pre_abs_net"] < hi and r["alignment"] == "TAILWIND"]),
                "HEADWIND": summarize([r for r in dominant if lo <= r["pre_abs_net"] < hi and r["alignment"] == "HEADWIND"]),
            }
        for strength in ["WEAK","STRONG"]:
            layer3["dominantByStrength"][strength] = {
                "TAILWIND": summarize([r for r in dominant if r["simple3_strength"] == strength and r["alignment"] == "TAILWIND"]),
                "HEADWIND": summarize([r for r in dominant if r["simple3_strength"] == strength and r["alignment"] == "HEADWIND"]),
            }
        layer3["dominantHighSkewByTime"] = {}
        for tb in ["T300_240","T240_180","T180_120","T120_60","T60_30","T30_15","T15_0"]:
            layer3["dominantHighSkewByTime"][tb] = {
                "TAILWIND": summarize([r for r in dominant if r["time_bucket"] == tb and r["pre_ratio"] >= 0.25 and r["alignment"] == "TAILWIND"]),
                "HEADWIND": summarize([r for r in dominant if r["time_bucket"] == tb and r["pre_ratio"] >= 0.25 and r["alignment"] == "HEADWIND"]),
            }

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "layer3Direction": {
                "definition": "Strict-past Maker-only dominant inventory side before inferred placement; TAILWIND if dominant side == strict-past SIMPLE3 majority direction, HEADWIND otherwise. No future winner used.",
                "signalCoverageBoundary": "Uses Unified V0 recorder public_state_json from strategy_target_compare_v1.db; only <=2s strict-past public snapshots are accepted.",
                **layer3,
            },
            "makerOnlyEconomics": maker_only_economics(target),
            "interpretationGuards": [
                "Layer3 continuation is based on inferred later-anchored parent placements; ownership is probabilistic.",
                "Maker-only economics ignores Taker fills but does not replay a counterfactual world where Taker never existed; actual Maker behavior may itself have been influenced by prior Taker inventory/state.",
                "SIMPLE3 is a direction proxy, not Target private signal ground truth.",
            ],
        }
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({
            "layer3": report["layer3Direction"],
            "makerOnlyEconomics": {k:v for k,v in report["makerOnlyEconomics"].items() if k != "rows"},
            "report": str(REPORT),
        }, ensure_ascii=False, indent=2))
    finally:
        book.close(); target.close(); sig.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
