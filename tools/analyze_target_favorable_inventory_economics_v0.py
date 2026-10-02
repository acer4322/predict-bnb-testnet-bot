from __future__ import annotations

import bisect
import csv
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RISK = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
PAIRS = ROOT / "data" / "research" / "target_maker_taker_complete_set_v2_pairs.csv"


def main() -> int:
    risk: dict[int, list[tuple]] = defaultdict(list)
    with RISK.open(encoding="utf-8", newline="") as f:
        for x in csv.DictReader(f):
            try:
                market_id = int(float(x["market_id"]))
                sampled_ms = int(float(x["sampled_ms"]))
                maker_delta = float(x["maker_delta"])
                combined_delta = float(x["combined_delta"])
                p = float(x["heavy_win_probability"])
            except (TypeError, ValueError):
                continue
            if abs(maker_delta) <= 1e-9:
                continue
            sign = 1.0 if maker_delta > 0 else -1.0
            oriented_combined = sign * combined_delta
            alignment = oriented_combined * (2.0 * p - 1.0)
            risk[market_id].append((
                sampled_ms, alignment, p, abs(maker_delta), oriented_combined,
                str(x["phase"]), str(x["lifecycle_state"]), str(x["regime"]),
            ))
    times: dict[int, list[int]] = {}
    for market_id, rows in risk.items():
        rows.sort(key=lambda row: row[0])
        times[market_id] = [int(row[0]) for row in rows]

    joined: list[dict] = []
    with PAIRS.open(encoding="utf-8", newline="") as f:
        for x in csv.DictReader(f):
            try:
                if int(float(x["repair_against_maker_heavy"])) != 1:
                    continue
                market_id = int(float(x["market_id"]))
                taker_ms = int(float(x["taker_first_event_ms"]))
                shares = float(x["paired_shares"])
                pair_cost = float(x["pair_cost"])
                raw_edge_usdt = float(x["raw_locked_edge_usdt"])
            except (TypeError, ValueError):
                continue
            market_times = times.get(market_id, [])
            pos = bisect.bisect_left(market_times, taker_ms) - 1
            if pos < 0:
                continue
            state = risk[market_id][pos]
            lag_ms = taker_ms - int(state[0])
            if lag_ms > 2000:
                continue
            alignment = float(state[1])
            state_name = "FAVORABLE" if alignment > 1e-9 else "UNFAVORABLE" if alignment < -1e-9 else "BALANCED"
            joined.append({
                "regime": str(x["regime"]),
                "market_id": market_id,
                "state": state_name,
                "alignment": alignment,
                "p": float(state[2]),
                "shares": shares,
                "pair_cost": pair_cost,
                "raw_edge_usdt": raw_edge_usdt,
                "pair_class": str(x["pair_class_raw"]),
                "lag_ms": lag_ms,
            })

    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        print("\n" + regime)
        for state_name in ("FAVORABLE", "UNFAVORABLE", "BALANCED"):
            rows = [r for r in joined if r["regime"] == regime and r["state"] == state_name]
            if not rows:
                continue
            shares = sum(float(r["shares"]) for r in rows)
            classes: dict[str, float] = defaultdict(float)
            for r in rows:
                classes[str(r["pair_class"])] += float(r["shares"])
            print(
                state_name,
                "frags", len(rows),
                "markets", len({int(r["market_id"]) for r in rows}),
                "shares", round(shares, 2),
                "pairCostSW", round(sum(float(r["pair_cost"]) * float(r["shares"]) for r in rows) / shares, 4),
                "rawEdge", round(sum(float(r["raw_edge_usdt"]) for r in rows), 2),
                "classShareRates", {k: round(v / shares, 3) for k, v in classes.items()},
                "alignSW", round(sum(float(r["alignment"]) * float(r["shares"]) for r in rows) / shares, 2),
            )

        fav = [r for r in joined if r["regime"] == regime and r["state"] == "FAVORABLE"]
        if fav:
            vals = sorted(float(r["alignment"]) for r in fav)
            q1 = vals[len(vals) // 3]
            q2 = vals[(2 * len(vals)) // 3]
            print("favorableAlignmentTerciles", round(q1, 2), round(q2, 2))
            for name, lo, hi in (("LOW", 0.0, q1), ("MID", q1, q2), ("HIGH", q2, float("inf"))):
                rows = [r for r in fav if float(r["alignment"]) > lo and float(r["alignment"]) <= hi]
                shares = sum(float(r["shares"]) for r in rows)
                classes: dict[str, float] = defaultdict(float)
                for r in rows:
                    classes[str(r["pair_class"])] += float(r["shares"])
                print(
                    "FAV_" + name,
                    "frags", len(rows),
                    "shares", round(shares, 2),
                    "pairCostSW", round(sum(float(r["pair_cost"]) * float(r["shares"]) for r in rows) / shares, 4) if shares else None,
                    "classShareRates", {k: round(v / shares, 3) for k, v in classes.items()} if shares else {},
                )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
