from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_objective_state_machine_v1_report.json"
DEFAULT_TRANSITIONS = ROOT / "data" / "research" / "target_maker_objective_state_machine_v1_transitions.csv"
REPORT_VERSION = "TARGET_MAKER_OBJECTIVE_STATE_MACHINE_V1"
DEFAULT_STRESS_START = "2026-08-16T03:40:00+08:00"
DEFAULT_STRESS_END = "2026-08-16T11:35:00+08:00"
EPS = 1e-9

TRANSITION_FIELDS = [
    "market_id", "regime", "maker_parent_id", "maker_side", "maker_quote_type",
    "maker_first_event_ms", "maker_last_event_ms", "maker_shares", "maker_average_price",
    "phase", "seconds_left", "public_lag_ms", "predict_up_mid", "predict_down_mid",
    "maker_delta_before_parent", "maker_delta_after_completion", "maker_abs_after_completion",
    "combined_delta_after_completion", "combined_abs_after_completion",
    "maker_up_after", "maker_down_after", "taker_up_after", "taker_down_after",
    "maker_paired_long_after", "maker_total_cost_after", "maker_worst_case_pnl_proxy_after",
    "residual_side", "residual_shares", "residual_bucket", "residual_age_ms",
    "current_maker_effect", "overlap_parent_count_at_completion", "overlap_taker_active",
    "next_actor", "next_action", "next_parent_id", "next_side", "next_quote_type",
    "next_delay_ms", "next_effect_directional", "next_reduces_combined_abs",
    "next_opposes_maker_heavy", "first_combined_repair_actor_60s", "first_combined_repair_delay_ms_60s",
    "combined_repair_within_5s", "combined_repair_within_15s", "combined_repair_within_30s",
    "combined_repair_within_60s", "market_maker_net_pnl_usdt", "market_taker_net_pnl_usdt",
    "market_combined_net_pnl_usdt", "lifecycle_repair_surplus_usdt", "lifecycle_repair_success",
]


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _parse_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return dt


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=TRANSITION_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _heavy_side(delta: float) -> str:
    if delta > EPS:
        return "UP"
    if delta < -EPS:
        return "DOWN"
    return "BALANCED"


def _residual_bucket(value: float) -> str:
    x = abs(float(value))
    if x <= 1.0:
        return "BALANCED_LE_1"
    if x <= 18.5:
        return "LE_18"
    if x <= 36.5:
        return "LE_36"
    if x <= 54.5:
        return "LE_54"
    return "GT_54"


def _effect_class(before: float, effect: float) -> str:
    after = float(before) + float(effect)
    if abs(before) <= EPS:
        return "FROM_BALANCED_ADD" if abs(after) > EPS else "NEUTRAL"
    if before * after < -EPS:
        return "FLIP_THROUGH"
    if abs(after) < abs(before) - EPS:
        return "PASSIVE_REPAIR"
    if abs(after) > abs(before) + EPS:
        return "ADD_EXPOSURE"
    return "NEUTRAL"


def _next_action_class(role: str, before_maker: float, before_combined: float, effect: float) -> tuple[str, bool]:
    role = str(role).upper()
    if role == "MAKER":
        label = _effect_class(before_maker, effect)
        mapping = {
            "PASSIVE_REPAIR": "MAKER_REPAIR",
            "ADD_EXPOSURE": "MAKER_ADD",
            "FLIP_THROUGH": "MAKER_FLIP",
            "FROM_BALANCED_ADD": "MAKER_ADD_FROM_BALANCED",
            "NEUTRAL": "MAKER_NEUTRAL",
        }
        reduces = abs(before_combined + effect) < abs(before_combined) - EPS
        return mapping[label], reduces
    if role == "TAKER":
        after = before_combined + effect
        if before_combined * after < -EPS:
            return "TAKER_FLIP", abs(after) < abs(before_combined) - EPS
        if abs(after) < abs(before_combined) - EPS:
            return "TAKER_REPAIR", True
        if abs(after) > abs(before_combined) + EPS:
            return "TAKER_ADD", False
        return "TAKER_NEUTRAL", False
    return "OTHER", False


def _predict_mid(row: dict[str, Any]) -> tuple[float | None, float | None]:
    up = _finite(row.get("predict_up_mid"))
    down = _finite(row.get("predict_down_mid"))
    if up is None:
        bid = _finite(row.get("predict_up_bid"))
        ask = _finite(row.get("predict_up_ask"))
        if bid is not None and ask is not None:
            up = (bid + ask) / 2.0
    if down is None:
        bid = _finite(row.get("predict_down_bid"))
        ask = _finite(row.get("predict_down_ask"))
        if bid is not None and ask is not None:
            down = (bid + ask) / 2.0
    if up is None and down is not None:
        up = 1.0 - down
    if down is None and up is not None:
        down = 1.0 - up
    return up, down


class PublicIndex:
    def __init__(self, path: Path) -> None:
        self.path = path.expanduser().resolve()
        self.rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.times: dict[int, list[int]] = {}
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            fields = set(reader.fieldnames or [])
            time_col = "decision_sampled_at_ms" if "decision_sampled_at_ms" in fields else "sampled_at_ms" if "sampled_at_ms" in fields else None
            if time_col is None or "market_id" not in fields:
                return
            for raw in reader:
                try:
                    market_id = int(float(raw["market_id"]))
                    sampled_ms = int(float(raw[time_col]))
                except (TypeError, ValueError):
                    continue
                row = dict(raw)
                row["sampled_ms"] = sampled_ms
                self.rows[market_id].append(row)
        for market_id, rows in self.rows.items():
            rows.sort(key=lambda item: int(item["sampled_ms"]))
            self.times[market_id] = [int(item["sampled_ms"]) for item in rows]

    def asof(self, market_id: int, at_ms: int, max_lag_ms: int) -> dict[str, Any] | None:
        rows = self.rows.get(int(market_id))
        times = self.times.get(int(market_id))
        if not rows or not times:
            return None
        pos = bisect.bisect_right(times, int(at_ms)) - 1
        if pos < 0:
            return None
        row = rows[pos]
        lag = int(at_ms) - int(row["sampled_ms"])
        if lag < 0 or lag > int(max_lag_ms):
            return None
        seconds = _finite(row.get("seconds_left"))
        phase = str(row.get("macro_phase") or "").upper()
        if phase not in {"OPEN", "MID", "TAIL"}:
            phase = "OPEN" if seconds is not None and seconds > 180 else "MID" if seconds is not None and seconds > 60 else "TAIL" if seconds is not None else "UNKNOWN"
        up, down = _predict_mid(row)
        return {
            "sampled_ms": int(row["sampled_ms"]),
            "lag_ms": lag,
            "seconds_left": seconds,
            "phase": phase,
            "predict_up_mid": up,
            "predict_down_mid": down,
        }


def _regime(at_ms: int, ordinary: tuple[int, int], stress: tuple[int, int]) -> str | None:
    if ordinary[0] <= at_ms < ordinary[1]:
        return "ORDINARY_CONTROL"
    if stress[0] <= at_ms < stress[1]:
        return "STRESS_2026_08_16"
    return None


def _market_pnl_payload(result: dict[str, Any] | None) -> dict[str, Any]:
    result = result or {}
    maker = _finite(result.get("maker_net_pnl_usdt"))
    taker = _finite(result.get("taker_net_pnl_usdt"))
    combined = _finite(result.get("net_pnl_usdt"))
    if combined is None and maker is not None and taker is not None:
        combined = maker + taker
    surplus = None
    success = None
    if maker is not None and taker is not None and maker < 0:
        surplus = taker - abs(maker)
        success = int(taker > abs(maker))
    return {
        "maker": maker,
        "taker": taker,
        "combined": combined,
        "repair_surplus": surplus,
        "repair_success": success,
    }


def _summary_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    next_counts = Counter(str(row["next_action"]) for row in rows)
    current_counts = Counter(str(row["current_maker_effect"]) for row in rows)
    handoff = [row for row in rows if str(row["next_actor"]) == "TAKER"]
    handoff_repairs = [row for row in handoff if str(row["next_action"]) == "TAKER_REPAIR"]
    residual_ages = [float(row["residual_age_ms"]) for row in rows if row.get("residual_age_ms") not in (None, "")]
    delays = [float(row["next_delay_ms"]) for row in rows if row.get("next_delay_ms") not in (None, "")]
    residuals = [float(row["residual_shares"]) for row in rows]
    return {
        "rows": len(rows),
        "currentMakerEffectCounts": dict(current_counts),
        "nextActionCounts": dict(next_counts),
        "makerNextRepairRate": _rate(next_counts["MAKER_REPAIR"], len(rows)),
        "makerNextAddRate": _rate(next_counts["MAKER_ADD"] + next_counts["MAKER_ADD_FROM_BALANCED"], len(rows)),
        "takerHandoffRate": _rate(len(handoff), len(rows)),
        "takerRepairRateAmongHandoffs": _rate(len(handoff_repairs), len(handoff)),
        "combinedRepairWithin5sRate": _rate(sum(int(row["combined_repair_within_5s"]) for row in rows), len(rows)),
        "combinedRepairWithin15sRate": _rate(sum(int(row["combined_repair_within_15s"]) for row in rows), len(rows)),
        "combinedRepairWithin30sRate": _rate(sum(int(row["combined_repair_within_30s"]) for row in rows), len(rows)),
        "combinedRepairWithin60sRate": _rate(sum(int(row["combined_repair_within_60s"]) for row in rows), len(rows)),
        "medianResidualShares": _median(residuals),
        "medianResidualAgeMs": _median(residual_ages),
        "medianNextDelayMs": _median(delays),
    }


def _grouped_summary(rows: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(key_fn(row))].append(row)
    return {key: _summary_rows(values) for key, values in sorted(groups.items())}


def _market_pnl_summary(rows: list[dict[str, Any]], market_results: dict[int, dict[str, Any]]) -> dict[str, Any]:
    market_ids = sorted({int(row["market_id"]) for row in rows})
    payloads = [_market_pnl_payload(market_results.get(mid)) for mid in market_ids]
    known = [item for item in payloads if item["maker"] is not None and item["taker"] is not None]
    maker_negative = [item for item in known if float(item["maker"]) < 0]
    maker_neg_taker_pos = [item for item in maker_negative if float(item["taker"]) > 0]
    repair_success = [item for item in maker_negative if item["repair_success"] == 1]
    return {
        "markets": len(market_ids),
        "marketsWithMakerAndTakerPnl": len(known),
        "totalMakerNetPnlUsdt": sum(float(item["maker"]) for item in known),
        "totalTakerNetPnlUsdt": sum(float(item["taker"]) for item in known),
        "totalCombinedNetPnlUsdt": sum(float(item["combined"] or 0.0) for item in known),
        "makerNegativeMarkets": len(maker_negative),
        "makerNegativeTakerPositiveMarkets": len(maker_neg_taker_pos),
        "lifecycleRepairSuccessMarkets": len(repair_success),
        "lifecycleRepairSuccessRateAmongMakerNegative": _rate(len(repair_success), len(maker_negative)),
        "definition": "repair success iff maker_net_pnl_usdt < 0 and taker_net_pnl_usdt > abs(maker_net_pnl_usdt); official market winner/settlement is not used",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare ordinary vs 2026-08-16 stress Maker inventory-state transitions and Taker handoff behavior. No fitting.")
    parser.add_argument("--db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--transitions", type=Path, default=DEFAULT_TRANSITIONS)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--stress-start", default=DEFAULT_STRESS_START)
    parser.add_argument("--stress-end", default=DEFAULT_STRESS_END)
    parser.add_argument("--ordinary-start", default="")
    parser.add_argument("--ordinary-end", default="")
    parser.add_argument("--max-public-lag-ms", type=int, default=2000)
    args = parser.parse_args()

    stress_start_dt = _parse_iso(args.stress_start)
    stress_end_dt = _parse_iso(args.stress_end)
    if stress_end_dt <= stress_start_dt:
        raise SystemExit("--stress-end must be after --stress-start")
    ordinary_start_dt = _parse_iso(args.ordinary_start) if args.ordinary_start else stress_start_dt - timedelta(days=1)
    ordinary_end_dt = _parse_iso(args.ordinary_end) if args.ordinary_end else stress_end_dt - timedelta(days=1)
    if ordinary_end_dt <= ordinary_start_dt:
        raise SystemExit("--ordinary-end must be after --ordinary-start")

    stress = (int(stress_start_dt.timestamp() * 1000), int(stress_end_dt.timestamp() * 1000))
    ordinary = (int(ordinary_start_dt.timestamp() * 1000), int(ordinary_end_dt.timestamp() * 1000))

    db = lifecycle._connect_ro(args.db)
    try:
        events, event_audit = lifecycle._load_official_events(db, asset=str(args.asset).upper())
        parents = lifecycle._build_parents(events)
        market_results = lifecycle._load_market_results(db, str(args.asset).upper())
    finally:
        db.close()

    public = PublicIndex(args.public_dataset)
    parent_map = {str(row["parent_id"]): row for row in parents}
    by_market_parents: dict[int, list[dict[str, Any]]] = defaultdict(list)
    by_market_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in parents:
        by_market_parents[int(row["market_id"])].append(row)
    for row in events:
        by_market_events[int(row["market_id"])].append(row)
    for rows in by_market_parents.values():
        rows.sort(key=lambda item: (int(item["first_event_ms"]), int(item["last_event_ms"]), str(item["parent_id"])))

    transition_rows: list[dict[str, Any]] = []

    for market_id, market_events in sorted(by_market_events.items()):
        ordered_events = sorted(market_events, key=lambda item: (int(item["event_ms"]), str(item["leg_id"])))
        market_parents = by_market_parents.get(market_id, [])
        if not market_parents:
            continue

        last_leg_key: dict[str, tuple[int, str]] = {}
        first_leg_key: dict[str, tuple[int, str]] = {}
        for event in ordered_events:
            pid = str(event["parent_id"])
            key = (int(event["event_ms"]), str(event["leg_id"]))
            first_leg_key[pid] = min(first_leg_key.get(pid, key), key)
            last_leg_key[pid] = max(last_leg_key.get(pid, key), key)

        state = lifecycle._empty_state()
        maker_cost = {"UP": 0.0, "DOWN": 0.0}
        before_maker_delta: dict[str, float] = {}
        residual_since_ms: int | None = None
        completion_snapshot: dict[str, dict[str, Any]] = {}

        for event in ordered_events:
            pid = str(event["parent_id"])
            key = (int(event["event_ms"]), str(event["leg_id"]))
            before_metrics = lifecycle._state_metrics(state)
            before_heavy = _heavy_side(float(before_metrics["maker_delta"]))
            if key == first_leg_key[pid]:
                before_maker_delta[pid] = float(before_metrics["maker_delta"])

            role = str(event["role"]).upper()
            side = str(event["side"]).upper()
            quote_type = str(event["quote_type"]).upper()
            shares = float(event["shares"])
            price = float(event["price"])
            lifecycle._apply_flow(state, role, side, quote_type, shares)
            if role == "MAKER" and side in {"UP", "DOWN"}:
                maker_cost[side] += lifecycle._signed_token_shares(quote_type, shares) * price

            after_metrics = lifecycle._state_metrics(state)
            after_heavy = _heavy_side(float(after_metrics["maker_delta"]))
            if after_heavy == "BALANCED":
                residual_since_ms = None
            elif after_heavy != before_heavy or residual_since_ms is None:
                residual_since_ms = int(event["event_ms"])

            if key != last_leg_key[pid]:
                continue
            maker_up = float(after_metrics["maker_up"])
            maker_down = float(after_metrics["maker_down"])
            maker_total_cost = float(maker_cost["UP"] + maker_cost["DOWN"])
            wc_proxy = None
            if maker_up >= -EPS and maker_down >= -EPS:
                wc_proxy = min(maker_up, maker_down) - maker_total_cost
            completion_snapshot[pid] = {
                "metrics": dict(after_metrics),
                "maker_total_cost": maker_total_cost,
                "maker_wc_proxy": wc_proxy,
                "residual_since_ms": residual_since_ms,
                "before_maker_delta": before_maker_delta.get(pid, 0.0),
                "completed_at_ms": int(event["event_ms"]),
            }

        result_pnl = _market_pnl_payload(market_results.get(market_id))
        for parent in market_parents:
            if str(parent["role"]).upper() != "MAKER":
                continue
            pid = str(parent["parent_id"])
            snap = completion_snapshot.get(pid)
            if snap is None:
                continue
            completed_at = int(snap["completed_at_ms"])
            regime = _regime(completed_at, ordinary, stress)
            if regime is None:
                continue

            metrics = snap["metrics"]
            maker_delta = float(metrics["maker_delta"])
            combined_delta = float(metrics["combined_delta"])
            residual_side = _heavy_side(maker_delta)
            residual_shares = abs(maker_delta)
            residual_age_ms = None if residual_side == "BALANCED" or snap["residual_since_ms"] is None else completed_at - int(snap["residual_since_ms"])
            current_effect = lifecycle._directional_effect(str(parent["side"]), str(parent["quote_type"]), float(parent["shares"]))
            current_class = _effect_class(float(snap["before_maker_delta"]), current_effect)

            overlapping = [
                other for other in market_parents
                if str(other["parent_id"]) != pid
                and int(other["first_event_ms"]) <= completed_at < int(other["last_event_ms"])
            ]
            overlap_taker = any(str(other["role"]).upper() == "TAKER" for other in overlapping)
            future = [other for other in market_parents if int(other["first_event_ms"]) > completed_at]
            future.sort(key=lambda item: (int(item["first_event_ms"]), int(item["last_event_ms"]), str(item["parent_id"])))
            next_parent = future[0] if future else None

            next_actor = "NONE"
            next_action = "END_OF_OBSERVED_MARKET"
            next_pid = ""
            next_side = ""
            next_quote = ""
            next_delay = None
            next_effect = None
            next_reduces = 0
            next_opposes = 0
            if next_parent is not None:
                next_actor = str(next_parent["role"]).upper()
                next_pid = str(next_parent["parent_id"])
                next_side = str(next_parent["side"]).upper()
                next_quote = str(next_parent["quote_type"]).upper()
                next_delay = int(next_parent["first_event_ms"]) - completed_at
                next_effect = lifecycle._directional_effect(next_side, next_quote, float(next_parent["shares"]))
                next_action, reduces = _next_action_class(next_actor, maker_delta, combined_delta, next_effect)
                next_reduces = int(reduces)
                next_opposes = int(abs(maker_delta) > EPS and maker_delta * next_effect < 0)

            sim_combined = combined_delta
            first_repair_actor = "NONE"
            first_repair_delay = None
            for other in future:
                delay = int(other["first_event_ms"]) - completed_at
                if delay > 60_000:
                    break
                effect = lifecycle._directional_effect(str(other["side"]), str(other["quote_type"]), float(other["shares"]))
                before_abs = abs(sim_combined)
                sim_combined += effect
                if first_repair_delay is None and abs(sim_combined) < before_abs - EPS:
                    first_repair_actor = str(other["role"]).upper()
                    first_repair_delay = delay

            public_row = public.asof(market_id, completed_at, max(0, int(args.max_public_lag_ms)))
            row = {
                "market_id": market_id,
                "regime": regime,
                "maker_parent_id": pid,
                "maker_side": str(parent["side"]),
                "maker_quote_type": str(parent["quote_type"]),
                "maker_first_event_ms": int(parent["first_event_ms"]),
                "maker_last_event_ms": completed_at,
                "maker_shares": float(parent["shares"]),
                "maker_average_price": float(parent["average_price"]),
                "phase": public_row["phase"] if public_row else "UNKNOWN",
                "seconds_left": public_row["seconds_left"] if public_row else "",
                "public_lag_ms": public_row["lag_ms"] if public_row else "",
                "predict_up_mid": public_row["predict_up_mid"] if public_row and public_row["predict_up_mid"] is not None else "",
                "predict_down_mid": public_row["predict_down_mid"] if public_row and public_row["predict_down_mid"] is not None else "",
                "maker_delta_before_parent": float(snap["before_maker_delta"]),
                "maker_delta_after_completion": maker_delta,
                "maker_abs_after_completion": abs(maker_delta),
                "combined_delta_after_completion": combined_delta,
                "combined_abs_after_completion": abs(combined_delta),
                "maker_up_after": float(metrics["maker_up"]),
                "maker_down_after": float(metrics["maker_down"]),
                "taker_up_after": float(metrics["taker_up"]),
                "taker_down_after": float(metrics["taker_down"]),
                "maker_paired_long_after": max(0.0, min(float(metrics["maker_up"]), float(metrics["maker_down"]))),
                "maker_total_cost_after": float(snap["maker_total_cost"]),
                "maker_worst_case_pnl_proxy_after": snap["maker_wc_proxy"] if snap["maker_wc_proxy"] is not None else "",
                "residual_side": residual_side,
                "residual_shares": residual_shares,
                "residual_bucket": _residual_bucket(residual_shares),
                "residual_age_ms": residual_age_ms if residual_age_ms is not None else "",
                "current_maker_effect": current_class,
                "overlap_parent_count_at_completion": len(overlapping),
                "overlap_taker_active": int(overlap_taker),
                "next_actor": next_actor,
                "next_action": next_action,
                "next_parent_id": next_pid,
                "next_side": next_side,
                "next_quote_type": next_quote,
                "next_delay_ms": next_delay if next_delay is not None else "",
                "next_effect_directional": next_effect if next_effect is not None else "",
                "next_reduces_combined_abs": next_reduces,
                "next_opposes_maker_heavy": next_opposes,
                "first_combined_repair_actor_60s": first_repair_actor,
                "first_combined_repair_delay_ms_60s": first_repair_delay if first_repair_delay is not None else "",
                "combined_repair_within_5s": int(first_repair_delay is not None and first_repair_delay <= 5000),
                "combined_repair_within_15s": int(first_repair_delay is not None and first_repair_delay <= 15000),
                "combined_repair_within_30s": int(first_repair_delay is not None and first_repair_delay <= 30000),
                "combined_repair_within_60s": int(first_repair_delay is not None and first_repair_delay <= 60000),
                "market_maker_net_pnl_usdt": result_pnl["maker"] if result_pnl["maker"] is not None else "",
                "market_taker_net_pnl_usdt": result_pnl["taker"] if result_pnl["taker"] is not None else "",
                "market_combined_net_pnl_usdt": result_pnl["combined"] if result_pnl["combined"] is not None else "",
                "lifecycle_repair_surplus_usdt": result_pnl["repair_surplus"] if result_pnl["repair_surplus"] is not None else "",
                "lifecycle_repair_success": result_pnl["repair_success"] if result_pnl["repair_success"] is not None else "",
            }
            transition_rows.append(row)

    transition_rows.sort(key=lambda row: (int(row["maker_last_event_ms"]), int(row["market_id"]), str(row["maker_parent_id"])))
    _write_csv(args.transitions, transition_rows)

    by_regime = {regime: [row for row in transition_rows if str(row["regime"]) == regime] for regime in ("ORDINARY_CONTROL", "STRESS_2026_08_16")}
    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True,
        "noModelFit": True,
        "purpose": "Identify the Target Maker objective/state machine by comparing a matched ordinary control window with the 2026-08-16 Maker-loss/Taker-gain stress window.",
        "coreOutcomeContract": {
            "officialSettlementUsedForStrategyClassification": False,
            "officialDrawUsedForStrategyClassification": False,
            "lifecycleRepairSuccess": "maker_net_pnl_usdt < 0 AND taker_net_pnl_usdt > abs(maker_net_pnl_usdt)",
            "note": "Official 0/1/non-binary settlement semantics are deliberately kept separate from the Target Maker/Taker lifecycle objective.",
        },
        "windows": {
            "ordinaryControl": {"start": ordinary_start_dt.isoformat(), "end": ordinary_end_dt.isoformat(), "startMs": ordinary[0], "endMs": ordinary[1], "definition": "same wall-clock span exactly 24h before stress unless explicitly overridden"},
            "stress": {"start": stress_start_dt.isoformat(), "end": stress_end_dt.isoformat(), "startMs": stress[0], "endMs": stress[1], "definition": "bounded 8/16 pre-noon mechanism-identification window; only observed Maker completions inside the window are labeled stress"},
        },
        "sources": {
            "targetDb": str(args.db),
            "eventTable": "wallet_shadow_target_events via existing analyze_target_maker_taker_inventory_lifecycle_v1 loader",
            "marketResultTable": "target_market_results when present",
            "publicDataset": str(args.public_dataset),
            "publicDatasetUse": "TTE/phase/Predict mid context only; no stale orderbook snapshots are used",
            "eventAudit": event_audit,
        },
        "analysisBins": {
            "residualShares": ["BALANCED_LE_1", "LE_18", "LE_36", "LE_54", "GT_54"],
            "note": "18-share multiples are analysis bins motivated by prior observed parent sizing, not assumed exact requested order size.",
        },
        "overall": _summary_rows(transition_rows),
        "byRegime": {regime: _summary_rows(rows) for regime, rows in by_regime.items()},
        "byRegimeResidualBucket": {regime: _grouped_summary(rows, lambda row: row["residual_bucket"]) for regime, rows in by_regime.items()},
        "byRegimePhase": {regime: _grouped_summary(rows, lambda row: row["phase"]) for regime, rows in by_regime.items()},
        "byRegimeCurrentMakerEffect": {regime: _grouped_summary(rows, lambda row: row["current_maker_effect"]) for regime, rows in by_regime.items()},
        "marketPnlByRegime": {regime: _market_pnl_summary(rows, market_results) for regime, rows in by_regime.items()},
        "diagnostics": {
            "transitionRows": len(transition_rows),
            "ordinaryRows": len(by_regime["ORDINARY_CONTROL"]),
            "stressRows": len(by_regime["STRESS_2026_08_16"]),
            "unknownPhaseRows": sum(str(row["phase"]) == "UNKNOWN" for row in transition_rows),
            "overlapTakerActiveRows": sum(int(row["overlap_taker_active"]) for row in transition_rows),
            "markets": len({int(row["market_id"]) for row in transition_rows}),
        },
        "interpretationGuide": {
            "toleranceBandEvidence": "Small residual buckets with low short-horizon repair rates and non-trivial residual ages support deliberate residual tolerance rather than forced 50/50 balancing.",
            "passiveRepairEvidence": "MAKER_REPAIR rates rising with residual size/age or tail pressure support an inventory-aware Maker controller.",
            "continuedAcquisitionEvidence": "MAKER_ADD or ADD_EXPOSURE despite existing residual supports an acquisition/edge objective rather than strict balancing.",
            "takerHandoffEvidence": "TAKER_REPAIR probability/delay rising in large residual or tail states supports conditional insurance handoff.",
            "stressIdentification": "A stress-vs-control shift in these transitions is more informative about the objective than raw Maker side frequency.",
        },
        "caveats": [
            "This V1 is fill/lifecycle-only. It does not infer unfilled/cancelled resting quotes or 8778 quote-band geometry; those belong in V2 after the lifecycle mechanism is measured.",
            "Parent overlap is explicitly reported because distinct parents can fill concurrently; the next-action field uses the first newly observed parent onset strictly after the current Maker parent completion.",
            "The maker worst-case PnL proxy is emitted only when observed Maker UP and DOWN signed inventories are both non-negative; it is not the official settlement accounting.",
            "No EBM fitting, threshold tuning, or paper/live strategy promotion occurs in this test.",
        ],
    }
    _write_json(args.report, report)

    print(REPORT_VERSION)
    print(f"ordinary_rows={len(by_regime['ORDINARY_CONTROL'])} stress_rows={len(by_regime['STRESS_2026_08_16'])}")
    for regime in ("ORDINARY_CONTROL", "STRESS_2026_08_16"):
        summary = report["byRegime"][regime]
        pnl = report["marketPnlByRegime"][regime]
        print(
            f"{regime}: rows={summary.get('rows')} maker_repair={summary.get('makerNextRepairRate')} "
            f"maker_add={summary.get('makerNextAddRate')} taker_handoff={summary.get('takerHandoffRate')} "
            f"repair15s={summary.get('combinedRepairWithin15sRate')} maker_pnl={pnl.get('totalMakerNetPnlUsdt')} "
            f"taker_pnl={pnl.get('totalTakerNetPnlUsdt')}"
        )
    print(f"Report: {args.report.expanduser().resolve()}")
    print(f"Transitions: {args.transitions.expanduser().resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
