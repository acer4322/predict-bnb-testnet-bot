from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ROWS = ROOT / "data/research/target_taker_effect_split_v1_rows.csv"
TARGET_DB = ROOT / "data/target_wallet_official_v1.db"
OUT_JSON = ROOT / "data/research/target_taker_tail_floor_v1_report.json"
OUT_CSV = ROOT / "data/research/target_taker_tail_floor_v1_events.csv"
VERSION = "TARGET_TAKER_TAIL_FLOOR_V1"
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def f(v: Any) -> float | None:
    try:
        return None if v in (None, "") else float(v)
    except Exception:
        return None


def med(xs: list[float]) -> float | None:
    vals = [float(x) for x in xs if x is not None]
    return median(vals) if vals else None


def load_effect_rows() -> dict[tuple[int, int], dict[str, Any]]:
    # Multiple 5s states may point at the same Target Taker. Keep the latest strict-past state.
    chosen: dict[tuple[int, int], dict[str, Any]] = {}
    with ROWS.open("r", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("target_taker_within_5s", "")).lower() not in {"true", "1"}:
                continue
            event_ms = int(float(r["target_taker_event_ms"]))
            key = (int(r["market_id"]), event_ms)
            item = {
                "market_id": key[0],
                "event_ms": event_ms,
                "decision_ms": int(float(r["decision_ms"])),
                "effect_class": r["effect_class"],
                "base_direction_side": str(r.get("base_direction_side") or ""),
                "direction_strength": f(r.get("direction_strength")),
                "maker_hazard": f(r.get("maker_hazard")),
                "risk_deficit": f(r.get("risk_deficit")),
                "seconds_left": f(r.get("seconds_left")),
                "target_taker_side": str(r.get("target_taker_side") or ""),
            }
            prev = chosen.get(key)
            if prev is None or item["decision_ms"] > prev["decision_ms"]:
                chosen[key] = item
    return chosen


def load_events(con: sqlite3.Connection, markets: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not markets:
        return {}
    marks = ",".join("?" for _ in markets)
    q = f"SELECT leg_id,market_id,role,side,quote_type,event_ms,price,shares FROM wallet_shadow_target_events WHERE market_id IN ({marks}) ORDER BY market_id,event_ms,leg_id"
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in con.execute(q, markets):
        side = str(r["side"] or "").upper()
        role = str(r["role"] or "").upper()
        quote = str(r["quote_type"] or "").upper()
        if side not in {"UP", "DOWN"} or role not in {"MAKER", "TAKER"} or quote != "BID":
            continue
        out[int(r["market_id"])].append({
            "leg_id": str(r["leg_id"]), "role": role, "side": side,
            "event_ms": int(r["event_ms"]), "price": float(r["price"]), "shares": float(r["shares"]),
        })
    return out


def load_winners(con: sqlite3.Connection, markets: list[int]) -> dict[int, str]:
    if not markets:
        return {}
    marks = ",".join("?" for _ in markets)
    return {int(r["market_id"]): str(r["winner"]) for r in con.execute(f"SELECT market_id,winner FROM target_market_results WHERE market_id IN ({marks})", markets)}


def portfolio(events: list[dict[str, Any]]) -> dict[str, float]:
    up = down = cost = 0.0
    for e in events:
        sh = float(e["shares"]); px = float(e["price"])
        cost += px * sh
        if e["side"] == "UP": up += sh
        else: down += sh
    settle_up = up - cost
    settle_down = down - cost
    return {
        "up": up, "down": down, "cost": cost,
        "settle_up": settle_up, "settle_down": settle_down,
        "worst": min(settle_up, settle_down), "net": up - down,
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"events": 0, "markets": 0}
    floors = [float(r["worst_floor_change_usdt"]) for r in rows]
    contrib = [float(r["standalone_settlement_contribution_usdt"]) for r in rows if r["standalone_settlement_contribution_usdt"] is not None]
    return {
        "events": len(rows),
        "markets": len({int(r["market_id"]) for r in rows}),
        "floorImproveRate": sum(x > EPS for x in floors) / len(floors),
        "floorWorsenRate": sum(x < -EPS for x in floors) / len(floors),
        "worstFloorChangeMedianUsdt": med(floors),
        "worstFloorChangeSumUsdt": sum(floors),
        "standalonePositiveRate": (sum(x > EPS for x in contrib) / len(contrib)) if contrib else None,
        "standaloneContributionMedianUsdt": med(contrib),
        "makerHazardMedian": med([r["maker_hazard"] for r in rows if r["maker_hazard"] is not None]),
        "riskDeficitMedian": med([r["risk_deficit"] for r in rows if r["risk_deficit"] is not None]),
        "secondsLeftMedian": med([r["seconds_left"] for r in rows if r["seconds_left"] is not None]),
    }


def main() -> int:
    effect = load_effect_rows()
    markets = sorted({m for m, _ in effect})
    con = ro(TARGET_DB)
    try:
        events = load_events(con, markets)
        winners = load_winners(con, markets)
        out: list[dict[str, Any]] = []
        for (market_id, event_ms), state in sorted(effect.items()):
            evs = events.get(market_id, [])
            idx = next((i for i, e in enumerate(evs) if e["event_ms"] == event_ms and e["role"] == "TAKER" and e["side"] == state["target_taker_side"]), None)
            if idx is None:
                continue
            e = evs[idx]
            before = portfolio(evs[:idx])
            after = portfolio(evs[:idx + 1])
            winner = winners.get(market_id)
            contribution = None
            if winner in {"UP", "DOWN"}:
                contribution = float(e["shares"]) * ((1.0 if e["side"] == winner else 0.0) - float(e["price"]))
            dside = state["base_direction_side"]
            aligned = dside in {"UP", "DOWN"} and e["side"] == dside
            out.append({
                **state,
                "target_taker_price": float(e["price"]),
                "target_taker_shares": float(e["shares"]),
                "winner": winner,
                "direction_alignment": "ALIGNED" if aligned else "OPPOSED_OR_NEUTRAL",
                "before_worst_floor_usdt": before["worst"],
                "after_worst_floor_usdt": after["worst"],
                "worst_floor_change_usdt": after["worst"] - before["worst"],
                "before_abs_net_shares": abs(before["net"]),
                "after_abs_net_shares": abs(after["net"]),
                "standalone_settlement_contribution_usdt": contribution,
            })

        groups: dict[str, Any] = {}
        for effect_class in ("REPAIR_EFFECT", "ADD_EFFECT"):
            base = [r for r in out if r["effect_class"] == effect_class]
            groups[effect_class] = summarize(base)
            for alignment in ("ALIGNED", "OPPOSED_OR_NEUTRAL"):
                groups[f"{effect_class}__{alignment}"] = summarize([r for r in base if r["direction_alignment"] == alignment])

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "strategyInputChanged": False,
            "targetFutureUsedForDecision": False,
            "method": {
                "deduplication": "one row per unique Target Taker (market_id,event_ms), latest strict-past 5s state retained",
                "targetClock": "event_ms",
                "portfolioReconstruction": "All observed Target MAKER/TAKER BID fills strictly before the event; worst floor=min(UP shares-cost, DOWN shares-cost).",
                "directionAlignment": "Target Taker side compared with contemporaneous strict-past Base SIMPLE3 direction proxy.",
                "warning": "Post-hoc diagnostic reconstruction only; effect labels are inventory proxies and not Target intent labels. No threshold fitting.",
            },
            "coverage": {"uniqueTargetTakerEvents": len(out), "markets": len({r['market_id'] for r in out}), "withWinner": sum(r["winner"] in {"UP", "DOWN"} for r in out)},
            "groups": groups,
            "interpretationGuard": "A floor improvement supports repair/risk-control interpretation; a floor worsening despite ADD_EFFECT argues against calling the action insurance merely because it is not direction-aligned.",
            "outputs": {"json": str(OUT_JSON), "csv": str(OUT_CSV)},
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        fields = list(out[0]) if out else ["market_id"]
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=fields); w.writeheader(); w.writerows(out)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
