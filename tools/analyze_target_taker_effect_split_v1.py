from __future__ import annotations

import csv
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
FLASH_DB = ROOT / "data" / "strategy_target_flash_v1.db"
TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
OUT_JSON = ROOT / "data" / "research" / "target_taker_effect_split_v1_report.json"
OUT_CSV = ROOT / "data" / "research" / "target_taker_effect_split_v1_rows.csv"
VERSION = "TARGET_TAKER_EFFECT_SPLIT_V1"
STRATEGY = "FLASH:MID_LATE_WAIT_CONTEXT_V1:R1"
WINDOW_MS = 5000
BUCKET_MS = 5000
EPS = 1e-9


def ro(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    con.row_factory = sqlite3.Row
    return con


def num(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def med(values: list[float]) -> float | None:
    xs = [float(v) for v in values if v is not None]
    return median(xs) if xs else None


def load_wait_states(con: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = con.execute(
        "SELECT market_id,decision_ms,seconds_left,payload_json FROM our_decisions WHERE strategy_version=? ORDER BY market_id,decision_ms",
        (STRATEGY,),
    ).fetchall()
    # one representative state per market/5s bucket, choose the last strict-past state in the bucket
    bucketed: dict[tuple[int, int], dict[str, Any]] = {}
    for r in rows:
        try:
            payload = json.loads(str(r["payload_json"]))
        except Exception:
            continue
        collected = payload.get("collected") if isinstance(payload, dict) else None
        if not isinstance(collected, dict):
            continue
        item = {
            "market_id": int(r["market_id"]),
            "decision_ms": int(r["decision_ms"]),
            "seconds_left": num(collected.get("snapshot.secondsLeft")) or num(r["seconds_left"]),
            "direction_strength": num(collected.get("base.lastDecision.direction.strength")),
            "maker_hazard": num(collected.get("base.lastDecision.makerDecision.hazard.probability")),
            "risk_deficit": num(collected.get("base.portfolio.riskDeficitUsdt")),
            "repair_ask": num(collected.get("base.lastDecision.economics.repairAsk")),
            "base_net_shares": num(collected.get("base.portfolio.netShares")),
            "base_direction_side": collected.get("base.lastDecision.direction.side"),
            "base_repair_side": collected.get("base.lastDecision.economics.repairSide"),
            "base_execution": collected.get("base.lastDecision.executionChoice"),
            "base_reason": collected.get("base.lastDecision.primaryReason"),
        }
        key = (item["market_id"], item["decision_ms"] // BUCKET_MS)
        bucketed[key] = item
    return sorted(bucketed.values(), key=lambda x: (x["market_id"], x["decision_ms"]))


def load_target_events(con: sqlite3.Connection, markets: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not markets:
        return {}
    marks = ",".join("?" for _ in markets)
    rows = con.execute(
        f"SELECT leg_id,market_id,role,side,event_ms,price,shares FROM wallet_shadow_target_events WHERE market_id IN ({marks}) ORDER BY market_id,event_ms,leg_id",
        markets,
    ).fetchall()
    out: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        side = str(r["side"] or "").upper()
        role = str(r["role"] or "").upper()
        if side not in {"UP", "DOWN"} or role not in {"MAKER", "TAKER"}:
            continue
        out[int(r["market_id"])].append({
            "leg_id": str(r["leg_id"]),
            "role": role,
            "side": side,
            "event_ms": int(r["event_ms"]),
            "price": num(r["price"]),
            "shares": float(r["shares"] or 0.0),
        })
    return out


def inventory_before(events: list[dict[str, Any]], at_ms: int) -> tuple[float, float, float]:
    up = 0.0
    down = 0.0
    for e in events:
        if e["event_ms"] >= at_ms:
            break
        sh = float(e["shares"])
        if e["side"] == "UP":
            up += sh
        else:
            down += sh
    return up, down, up - down


def first_taker_after(events: list[dict[str, Any]], start_ms: int, end_ms: int) -> dict[str, Any] | None:
    for e in events:
        if e["event_ms"] <= start_ms:
            continue
        if e["event_ms"] > end_ms:
            break
        if e["role"] == "TAKER":
            return e
    return None


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


def summarize(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    subset = [r for r in rows if r.get("effect_class") == label]
    return {
        "states": len(subset),
        "markets": len({r["market_id"] for r in subset}),
        "secondsLeftMedian": med([r["seconds_left"] for r in subset if r["seconds_left"] is not None]),
        "directionStrengthMedian": med([r["direction_strength"] for r in subset if r["direction_strength"] is not None]),
        "makerHazardMedian": med([r["maker_hazard"] for r in subset if r["maker_hazard"] is not None]),
        "riskDeficitMedian": med([r["risk_deficit"] for r in subset if r["risk_deficit"] is not None]),
        "repairAskMedian": med([r["repair_ask"] for r in subset if r["repair_ask"] is not None]),
        "priorTargetAbsNetSharesMedian": med([abs(r["target_prior_net_shares"]) for r in subset if r["target_prior_net_shares"] is not None]),
        "targetAbsNetChangeMedian": med([abs(r["target_after_net_shares"]) - abs(r["target_prior_net_shares"]) for r in subset if r["target_after_net_shares"] is not None and r["target_prior_net_shares"] is not None]),
    }


def main() -> int:
    flash = ro(FLASH_DB)
    target = ro(TARGET_DB)
    try:
        states = load_wait_states(flash)
        markets = sorted({s["market_id"] for s in states})
        events = load_target_events(target, markets)
        out_rows: list[dict[str, Any]] = []
        for s in states:
            evs = events.get(s["market_id"], [])
            t = first_taker_after(evs, s["decision_ms"], s["decision_ms"] + WINDOW_MS)
            row = dict(s)
            row.update({
                "target_taker_within_5s": bool(t),
                "effect_class": "NO_TAKER",
                "target_taker_side": None,
                "target_taker_price": None,
                "target_taker_shares": None,
                "target_taker_event_ms": None,
                "target_prior_up_shares": None,
                "target_prior_down_shares": None,
                "target_prior_net_shares": None,
                "target_after_net_shares": None,
                "target_event_delay_ms": None,
            })
            if t:
                up, down, net = inventory_before(evs, t["event_ms"])
                cls, after = classify_effect(net, t["side"], float(t["shares"]))
                row.update({
                    "effect_class": cls,
                    "target_taker_side": t["side"],
                    "target_taker_price": t["price"],
                    "target_taker_shares": t["shares"],
                    "target_taker_event_ms": t["event_ms"],
                    "target_prior_up_shares": up,
                    "target_prior_down_shares": down,
                    "target_prior_net_shares": net,
                    "target_after_net_shares": after,
                    "target_event_delay_ms": t["event_ms"] - s["decision_ms"],
                })
            out_rows.append(row)

        classes = ["REPAIR_EFFECT", "ADD_EFFECT", "BUILD_FROM_FLAT", "NEUTRAL_EFFECT", "NO_TAKER"]
        summaries = {c: summarize(out_rows, c) for c in classes}
        taker_rows = [r for r in out_rows if r["target_taker_within_5s"]]
        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "strategyInputChanged": False,
            "targetFutureUsedForDecision": False,
            "method": {
                "sourceStrategy": STRATEGY,
                "stateBucketMs": BUCKET_MS,
                "targetLookaheadMsPostHoc": WINDOW_MS,
                "targetClock": "event_ms",
                "effectLabel": "Target cumulative normalized UP-DOWN shares strictly before the first Taker event in the post-hoc 5s window. Opposite-side action that reduces |net| => REPAIR_EFFECT; same-side/increasing |net| => ADD_EFFECT; prior net ~0 => BUILD_FROM_FLAT.",
                "warning": "Effect labels are diagnostic proxies, not semantic ground truth about Target intent. Normalized event-side accumulation is used as an inventory proxy and does not claim exact wallet accounting under every venue conversion path."
            },
            "coverage": {
                "fiveSecondStates": len(out_rows),
                "markets": len(markets),
                "targetTakerWithin5sStates": len(taker_rows),
                "targetTakerMarkets": len({r["market_id"] for r in taker_rows}),
            },
            "effectClassSummaries": summaries,
            "interpretationGuard": "Do not convert medians into thresholds. This test only asks whether the previously observed weak-direction/high-Maker-hazard pattern concentrates in repair-like versus add-like Target actions.",
            "outputs": {"json": str(OUT_JSON), "csv": str(OUT_CSV)},
        }
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        fields = [
            "market_id","decision_ms","seconds_left","direction_strength","maker_hazard","risk_deficit","repair_ask","base_net_shares","base_direction_side","base_repair_side","base_execution","base_reason","target_taker_within_5s","effect_class","target_taker_side","target_taker_price","target_taker_shares","target_taker_event_ms","target_event_delay_ms","target_prior_up_shares","target_prior_down_shares","target_prior_net_shares","target_after_net_shares"
        ]
        with OUT_CSV.open("w", newline="", encoding="utf-8-sig") as fh:
            w = csv.DictWriter(fh, fieldnames=fields)
            w.writeheader(); w.writerows(out_rows)
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        flash.close(); target.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
