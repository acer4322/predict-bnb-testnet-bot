from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WINDOWS_CSV = ROOT / "data/research/strategy_target_cross_compare_v3_unified_v0_windows.csv"
TARGET_DB = ROOT / "data/target_wallet_official_v1.db"
OUT_JSON = ROOT / "data/research/strategy_target_cross_compare_v3_unified_v0_effects_report.json"
OUT_CSV = ROOT / "data/research/strategy_target_cross_compare_v3_unified_v0_effects.csv"
VERSION = "STRATEGY_TARGET_CROSS_COMPARE_V3_UNIFIED_V0_EFFECTS_V1"
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=10.0)
    con.row_factory = sqlite3.Row
    return con


def fnum(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def med(xs: list[float]) -> float | None:
    return median(xs) if xs else None


def classify_effect(prior_net: float, side: str, shares: float) -> tuple[str, float]:
    sign = 1.0 if side == "UP" else -1.0
    after = prior_net + sign * shares
    before_abs = abs(prior_net)
    after_abs = abs(after)
    if before_abs <= EPS:
        return "BUILD_FROM_FLAT", after
    if after_abs + EPS < before_abs:
        return "REPAIR_EFFECT", after
    if after_abs > before_abs + EPS:
        return "ADD_EFFECT", after
    return "NEUTRAL_EFFECT", after


def load_v3_target_only_taker_windows() -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    with WINDOWS_CSV.open(encoding="utf-8-sig", newline="") as fh:
        for raw in csv.DictReader(fh):
            if raw.get("classification") != "TARGET_ONLY":
                continue
            if "TAKER" not in str(raw.get("target_channels") or "").split("|"):
                continue
            row = dict(raw)
            for k in ("market_id", "window_start_ms", "window_end_ms"):
                row[k] = int(row[k])
            out.append(row)
    return out


def load_events(con: sqlite3.Connection, markets: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not markets:
        return {}
    marks = ",".join("?" for _ in markets)
    rows = con.execute(
        f"""SELECT leg_id,market_id,role,side,quote_type,event_ms,price,shares
              FROM wallet_shadow_target_events
             WHERE market_id IN ({marks})
             ORDER BY market_id,event_ms,leg_id""",
        markets,
    ).fetchall()
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        role = str(r["role"] or "").upper()
        side = str(r["side"] or "").upper()
        quote = str(r["quote_type"] or "").upper()
        if role not in {"MAKER", "TAKER"} or side not in {"UP", "DOWN"}:
            continue
        out[int(r["market_id"])].append({
            "leg_id": str(r["leg_id"]),
            "role": role,
            "side": side,
            "quote_type": quote,
            "event_ms": int(r["event_ms"]),
            "price": float(r["price"] or 0.0),
            "shares": float(r["shares"] or 0.0),
        })
    return out


def winner_map(con: sqlite3.Connection, markets: list[int]) -> dict[int, str]:
    if not markets:
        return {}
    marks = ",".join("?" for _ in markets)
    return {int(r["market_id"]): str(r["winner"]).upper() for r in con.execute(
        f"SELECT market_id,winner FROM target_market_results WHERE market_id IN ({marks}) AND winner IN ('UP','DOWN')",
        markets,
    )}


def first_taker_in_window(events: list[dict[str, Any]], start: int, end: int) -> dict[str, Any] | None:
    for e in events:
        if e["event_ms"] < start:
            continue
        if e["event_ms"] >= end:
            break
        if e["role"] == "TAKER":
            return e
    return None


def prior_inventory_proxy(events: list[dict[str, Any]], at_ms: int) -> tuple[float, float, float]:
    # Preserve TARGET_TAKER_EFFECT_SPLIT_V1 semantics exactly: cumulative observed
    # event-side shares, regardless of role, strictly before the Taker event.
    up = down = 0.0
    for e in events:
        if e["event_ms"] >= at_ms:
            break
        if e["side"] == "UP":
            up += e["shares"]
        else:
            down += e["shares"]
    return up, down, up - down


def prior_bid_portfolio(events: list[dict[str, Any]], at_ms: int) -> tuple[float, float, float]:
    up = down = cost = 0.0
    for e in events:
        if e["event_ms"] >= at_ms:
            break
        if e["quote_type"] != "BID":
            continue
        cost += e["price"] * e["shares"]
        if e["side"] == "UP":
            up += e["shares"]
        else:
            down += e["shares"]
    return up, down, cost


def summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    xs = [r for r in rows if r["effect_class"] == label]
    floor = [float(r["worst_floor_change_usdt"]) for r in xs if r["worst_floor_change_usdt"] is not None]
    contrib = [float(r["standalone_contribution_usdt"]) for r in xs if r["standalone_contribution_usdt"] is not None]
    return {
        "windows": len(xs),
        "markets": len({r["market_id"] for r in xs}),
        "ourDecision": dict(Counter(str(r["our_decision"] or "NONE") for r in xs)),
        "ourReason": dict(Counter(str(r["our_reason"] or "NONE") for r in xs)),
        "ourPhase": dict(Counter(str(r["our_phase"] or "NONE") for r in xs)),
        "ourAlignment": dict(Counter(str(r["our_alignment"] or "UNKNOWN") for r in xs)),
        "secondsLeftMedian": med([float(r["seconds_left"]) for r in xs if r["seconds_left"] is not None]),
        "ourRiskDeficitMedianUsdt": med([float(r["our_risk_deficit_usdt"]) for r in xs if r["our_risk_deficit_usdt"] is not None]),
        "ourNetSharesAbsMedian": med([abs(float(r["our_net_shares"])) for r in xs if r["our_net_shares"] is not None]),
        "ourMakerHazardMedian": med([float(r["our_maker_hazard_probability"]) for r in xs if r["our_maker_hazard_probability"] is not None]),
        "targetPriorAbsNetSharesMedian": med([abs(float(r["target_prior_net_shares"])) for r in xs]),
        "targetAbsNetChangeMedian": med([abs(float(r["target_after_net_shares"])) - abs(float(r["target_prior_net_shares"])) for r in xs]),
        "floorImproveRate": sum(v > 1e-9 for v in floor) / len(floor) if floor else None,
        "worstFloorChangeMedianUsdt": med(floor),
        "standalonePositiveRate": sum(v > 1e-9 for v in contrib) / len(contrib) if contrib else None,
        "standaloneContributionMedianUsdt": med(contrib),
        "standaloneContributionSumUsdt": sum(contrib) if contrib else None,
    }


def grouped(rows: list[dict[str, Any]], label: str) -> list[dict[str, Any]]:
    g: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        if r["effect_class"] != label:
            continue
        key = (str(r["our_decision"] or "NONE"), str(r["our_reason"] or "NONE"), str(r["our_phase"] or "NONE"))
        g[key].append(r)
    out = []
    for (decision, reason, phase), xs in g.items():
        floor = [float(r["worst_floor_change_usdt"]) for r in xs if r["worst_floor_change_usdt"] is not None]
        contrib = [float(r["standalone_contribution_usdt"]) for r in xs if r["standalone_contribution_usdt"] is not None]
        out.append({
            "ourDecision": decision,
            "ourReason": reason,
            "ourPhase": phase,
            "windows": len(xs),
            "markets": len({r["market_id"] for r in xs}),
            "floorImproveRate": sum(v > 1e-9 for v in floor) / len(floor) if floor else None,
            "worstFloorChangeMedianUsdt": med(floor),
            "standalonePositiveRate": sum(v > 1e-9 for v in contrib) / len(contrib) if contrib else None,
            "standaloneContributionMedianUsdt": med(contrib),
            "ourRiskDeficitMedianUsdt": med([float(r["our_risk_deficit_usdt"]) for r in xs if r["our_risk_deficit_usdt"] is not None]),
            "ourNetSharesAbsMedian": med([abs(float(r["our_net_shares"])) for r in xs if r["our_net_shares"] is not None]),
            "ourMakerHazardMedian": med([float(r["our_maker_hazard_probability"]) for r in xs if r["our_maker_hazard_probability"] is not None]),
        })
    out.sort(key=lambda x: (-x["windows"], x["ourDecision"], x["ourReason"], x["ourPhase"]))
    return out


def main() -> int:
    windows = load_v3_target_only_taker_windows()
    markets = sorted({int(r["market_id"]) for r in windows})
    target = ro(TARGET_DB)
    try:
        events_by_market = load_events(target, markets)
        winners = winner_map(target, markets)
        out_rows: list[dict[str, Any]] = []
        unmatched = 0
        for w in windows:
            mid = int(w["market_id"])
            evs = events_by_market.get(mid, [])
            t = first_taker_in_window(evs, int(w["window_start_ms"]), int(w["window_end_ms"]))
            if t is None:
                unmatched += 1
                continue
            up, down, net = prior_inventory_proxy(evs, t["event_ms"])
            effect, after = classify_effect(net, t["side"], t["shares"])
            b_up, b_down, b_cost = prior_bid_portfolio(evs, t["event_ms"])
            before_floor = min(b_up - b_cost, b_down - b_cost)
            a_up, a_down, a_cost = b_up, b_down, b_cost
            if t["quote_type"] == "BID":
                a_cost += t["price"] * t["shares"]
                if t["side"] == "UP":
                    a_up += t["shares"]
                else:
                    a_down += t["shares"]
            after_floor = min(a_up - a_cost, a_down - a_cost)
            floor_change = after_floor - before_floor
            winner = winners.get(mid)
            standalone = None
            if winner in {"UP", "DOWN"} and t["quote_type"] == "BID":
                standalone = (t["shares"] if t["side"] == winner else 0.0) - t["price"] * t["shares"]
            our_dir = str(w.get("our_direction") or "")
            alignment = "NEUTRAL"
            if our_dir in {"UP", "DOWN"}:
                alignment = "ALIGNED" if our_dir == t["side"] else "OPPOSED"
            state = {}
            try:
                state = json.loads(str(w.get("our_state_json") or "{}"))
            except Exception:
                pass
            maker_hazard = None
            try:
                maker_hazard = ((state.get("makerDecision") or {}).get("hazard") or {}).get("probability")
                if maker_hazard is None:
                    maker_hazard = ((state.get("payload") or {}).get("makerDecision") or {}).get("hazard", {}).get("probability")
            except Exception:
                maker_hazard = None
            out_rows.append({
                "market_id": mid,
                "winner": winner,
                "window_start_ms": int(w["window_start_ms"]),
                "window_end_ms": int(w["window_end_ms"]),
                "seconds_left": fnum(w.get("seconds_left_at_start")),
                "target_taker_event_ms": t["event_ms"],
                "target_taker_leg_id": t["leg_id"],
                "target_taker_side": t["side"],
                "target_taker_price": t["price"],
                "target_taker_shares": t["shares"],
                "effect_class": effect,
                "target_prior_up_shares": up,
                "target_prior_down_shares": down,
                "target_prior_net_shares": net,
                "target_after_net_shares": after,
                "before_worst_floor_usdt": before_floor,
                "after_worst_floor_usdt": after_floor,
                "worst_floor_change_usdt": floor_change,
                "standalone_contribution_usdt": standalone,
                "target_side_vs_our_direction": alignment,
                "our_decision": w.get("our_decision") or "",
                "our_desired_action": w.get("our_desired_action") or "",
                "our_reason": w.get("our_reason") or "",
                "our_phase": w.get("our_phase") or "",
                "our_direction": our_dir,
                "our_direction_strength": fnum(w.get("our_direction_strength")),
                "our_alignment": w.get("our_alignment") or "",
                "our_net_shares": fnum(w.get("our_net_shares")),
                "our_risk_deficit_usdt": fnum(w.get("our_risk_deficit_usdt")),
                "our_worst_case_pnl_usdt": fnum(w.get("our_worst_case_pnl_usdt")),
                "our_repair_side": w.get("our_repair_side") or "",
                "our_repair_ask": fnum(w.get("our_repair_ask")),
                "our_maker_decision": w.get("our_maker_decision") or "",
                "our_maker_reason": w.get("our_maker_reason") or "",
                "our_maker_hazard_probability": fnum(maker_hazard),
            })

        labels = ["REPAIR_EFFECT", "ADD_EFFECT", "BUILD_FROM_FLAT", "NEUTRAL_EFFECT"]
        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "strategyInputChanged": False,
            "method": {
                "sourceReplay": str(WINDOWS_CSV),
                "sourceTarget": str(TARGET_DB),
                "scope": "V3 TARGET_ONLY windows containing Target TAKER",
                "eventSelection": "first official Target Taker leg inside each fixed 5s V3 window",
                "targetClock": "true event_ms",
                "effectLabel": "Same semantics as TARGET_TAKER_EFFECT_SPLIT_V1: cumulative observed Target UP-DOWN shares strictly before event; reducing |net| => REPAIR_EFFECT; increasing |net| => ADD_EFFECT; prior |net|~0 => BUILD_FROM_FLAT.",
                "warning": "Effect labels are diagnostic inventory-effect proxies, not Target semantic intent labels. Immediate floor and standalone settlement contribution are diagnostic, not path-dependent counterfactual strategy PnL.",
            },
            "coverage": {
                "v3TargetOnlyTakerWindows": len(windows),
                "matchedFirstTakerEvents": len(out_rows),
                "unmatchedWindows": unmatched,
                "markets": len({r["market_id"] for r in out_rows}),
            },
            "effectCounts": dict(Counter(r["effect_class"] for r in out_rows)),
            "effectSummaries": {label: summarize(out_rows, label) for label in labels},
            "repairByOurState": grouped(out_rows, "REPAIR_EFFECT")[:30],
            "addByOurState": grouped(out_rows, "ADD_EFFECT")[:30],
            "directionAlignment": {
                label: dict(Counter(r["target_side_vs_our_direction"] for r in out_rows if r["effect_class"] == label))
                for label in labels
            },
            "interpretationGuard": [
                "Do not treat REPAIR_EFFECT/ADD_EFFECT as ground-truth Target intent.",
                "Do not convert medians into thresholds; use them only to choose the next falsifiable strict-past discriminator.",
                "A Target-only action is not automatically a missing edge; harmful ADD-like actions may be TARGET_MISTAKE_CANDIDATE.",
            ],
            "outputs": {"reportJson": str(OUT_JSON), "rowsCsv": str(OUT_CSV)},
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        fields = list(out_rows[0].keys()) if out_rows else ["market_id"]
        with OUT_CSV.open("w", encoding="utf-8-sig", newline="") as fh:
            wr = csv.DictWriter(fh, fieldnames=fields)
            wr.writeheader(); wr.writerows(out_rows)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        target.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
