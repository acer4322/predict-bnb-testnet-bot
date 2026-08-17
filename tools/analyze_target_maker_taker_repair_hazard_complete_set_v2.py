from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

import analyze_target_maker_taker_inventory_lifecycle_v1 as lifecycle
import analyze_target_maker_taker_inventory_lifecycle_v1_1 as lifecycle_v11

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_SIGNAL_DBS = [
    ROOT / "data" / "wallet_taker_signals.db",
    ROOT / "data" / "public_research_archive_v1.db",
]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_complete_set_v2_report.json"
DEFAULT_RISK_CSV = ROOT / "data" / "research" / "target_maker_taker_repair_hazard_v2_risk.csv"
DEFAULT_PAIRS_CSV = ROOT / "data" / "research" / "target_maker_taker_complete_set_v2_pairs.csv"
REPORT_VERSION = "TARGET_MAKER_TAKER_REPAIR_HAZARD_COMPLETE_SET_V2"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
HORIZONS_MS = (1000, 2000, 5000)
EPS = 1e-9
PAIR_NEAR_PAR_CENTS = 0.01

RISK_FIELDS = [
    "market_id", "regime", "sampled_ms", "seconds_left", "phase", "lifecycle_state",
    "maker_delta", "maker_abs_delta", "combined_delta", "maker_heavy_side",
    "predict_up_mid", "predict_down_mid", "heavy_win_probability", "heavy_probability_bucket",
    "heavy_side_won", "signal_source", "signal_source_priority",
    "any_taker_1s", "repair_taker_1s", "repair_shares_1s", "repair_notional_1s",
    "any_taker_2s", "repair_taker_2s", "repair_shares_2s", "repair_notional_2s",
    "any_taker_5s", "repair_taker_5s", "repair_shares_5s", "repair_notional_5s",
    "first_repair_delay_ms_5s",
]

PAIR_FIELDS = [
    "market_id", "regime", "taker_parent_id", "taker_first_event_ms", "taker_is_first",
    "phase", "seconds_left", "heavy_win_probability", "heavy_probability_bucket",
    "maker_side", "maker_price", "maker_event_ms", "taker_side", "taker_price", "taker_event_ms",
    "paired_shares", "pair_cost", "raw_edge_per_share", "raw_locked_edge_usdt",
    "fee_adjusted_edge_per_share", "fee_adjusted_locked_edge_usdt", "pair_class_raw",
    "pair_class_fee_adjusted", "maker_to_taker_delay_ms", "repair_against_maker_heavy",
]


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(resolved)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
    ).fetchone() is not None


def _phase(seconds_left: float | None) -> str:
    if seconds_left is None:
        return "UNKNOWN"
    if seconds_left > 180:
        return "OPEN"
    if seconds_left > 60:
        return "MID"
    return "TAIL"


def _prob_bucket(value: float | None) -> str:
    if value is None:
        return "UNKNOWN"
    if value < 0.20:
        return "LT_020"
    if value < 0.40:
        return "020_040"
    if value < 0.60:
        return "040_060"
    if value < 0.80:
        return "060_080"
    return "GE_080"


def _mid(up: Any, down: Any, up_bid: Any = None, up_ask: Any = None, down_bid: Any = None, down_ask: Any = None) -> tuple[float | None, float | None]:
    up_mid = _finite(up)
    down_mid = _finite(down)
    if up_mid is None:
        bid = _finite(up_bid)
        ask = _finite(up_ask)
        if bid is not None and ask is not None:
            up_mid = (bid + ask) / 2.0
    if down_mid is None:
        bid = _finite(down_bid)
        ask = _finite(down_ask)
        if bid is not None and ask is not None:
            down_mid = (bid + ask) / 2.0
    if up_mid is None and down_mid is not None:
        up_mid = 1.0 - down_mid
    if down_mid is None and up_mid is not None:
        down_mid = 1.0 - up_mid
    if up_mid is not None and not 0 <= up_mid <= 1:
        up_mid = None
    if down_mid is not None and not 0 <= down_mid <= 1:
        down_mid = None
    return up_mid, down_mid


def _generic_fee_per_share(price: float, fee_bps: int) -> float:
    if not 0 <= price <= 1:
        return 0.0
    return min(price, 1.0 - price) * float(fee_bps) / 10_000.0


def _pair_class(edge: float, near_par: float = PAIR_NEAR_PAR_CENTS) -> str:
    if edge > near_par:
        return "LOCKED_POSITIVE"
    if edge < -near_par:
        return "INSURANCE_COST"
    return "NEAR_PAR"


class SignalArchive:
    def __init__(self, paths: Iterable[Path]) -> None:
        self.sources: list[dict[str, Any]] = []
        for priority, raw in enumerate(paths):
            path = Path(raw).expanduser().resolve()
            if not path.exists():
                continue
            db = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=10.0)
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA busy_timeout=10000")
            table = "wallet_taker_signal_snapshots"
            if not _has_table(db, table):
                db.close()
                continue
            cols = _columns(db, table)
            required = {"market_id", "sampled_at_ms", "seconds_left"}
            if not required.issubset(cols):
                db.close()
                continue
            if not ({"predict_up_mid", "predict_down_mid"} <= cols or {"predict_up_bid", "predict_up_ask", "predict_down_bid", "predict_down_ask"} <= cols):
                db.close()
                continue
            self.sources.append({"path": path, "db": db, "priority": priority, "columns": cols})

    def close(self) -> None:
        for source in self.sources:
            source["db"].close()

    def one_second_rows(self, market_ids: set[int]) -> tuple[dict[int, list[dict[str, Any]]], dict[str, Any]]:
        merged: dict[tuple[int, int], dict[str, Any]] = {}
        source_counts: Counter[str] = Counter()
        raw_rows = 0
        for source in self.sources:
            cols: set[str] = source["columns"]
            select = ["market_id", "sampled_at_ms", "seconds_left"]
            for name in ("predict_up_mid", "predict_down_mid", "predict_up_bid", "predict_up_ask", "predict_down_bid", "predict_down_ask"):
                if name in cols:
                    select.append(name)
            placeholders = ",".join("?" for _ in market_ids)
            if not market_ids:
                continue
            query = f"SELECT {','.join(select)} FROM wallet_taker_signal_snapshots WHERE market_id IN ({placeholders}) ORDER BY market_id,sampled_at_ms"
            for raw in source["db"].execute(query, tuple(sorted(market_ids))):
                raw_rows += 1
                row = dict(raw)
                market_id = int(row["market_id"])
                sampled_ms = int(row["sampled_at_ms"])
                second_key = sampled_ms // 1000
                up_mid, down_mid = _mid(
                    row.get("predict_up_mid"), row.get("predict_down_mid"),
                    row.get("predict_up_bid"), row.get("predict_up_ask"),
                    row.get("predict_down_bid"), row.get("predict_down_ask"),
                )
                if up_mid is None or down_mid is None:
                    continue
                candidate = {
                    "market_id": market_id,
                    "sampled_at_ms": sampled_ms,
                    "seconds_left": _finite(row.get("seconds_left")),
                    "predict_up_mid": up_mid,
                    "predict_down_mid": down_mid,
                    "source": str(source["path"]),
                    "source_priority": int(source["priority"]),
                }
                key = (market_id, second_key)
                previous = merged.get(key)
                if previous is None or (sampled_ms, int(source["priority"])) >= (int(previous["sampled_at_ms"]), int(previous["source_priority"])):
                    merged[key] = candidate
        by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in merged.values():
            by_market[int(row["market_id"])].append(row)
            source_counts[str(row["source"])] += 1
        for rows in by_market.values():
            rows.sort(key=lambda item: int(item["sampled_at_ms"]))
        return by_market, {
            "configuredSources": [str(source["path"]) for source in self.sources],
            "rawRowsRead": raw_rows,
            "oneSecondRows": len(merged),
            "markets": len(by_market),
            "selectedSourceRows": dict(source_counts),
        }

    def asof(self, market_id: int, at_ms: int, max_lag_ms: int = 1500) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        for source in self.sources:
            cols: set[str] = source["columns"]
            select = ["sampled_at_ms", "seconds_left"]
            for name in ("predict_up_mid", "predict_down_mid", "predict_up_bid", "predict_up_ask", "predict_down_bid", "predict_down_ask"):
                if name in cols:
                    select.append(name)
            row = source["db"].execute(
                f"SELECT {','.join(select)} FROM wallet_taker_signal_snapshots WHERE market_id=? AND sampled_at_ms<? AND sampled_at_ms>=? ORDER BY sampled_at_ms DESC LIMIT 1",
                (int(market_id), int(at_ms), int(at_ms) - int(max_lag_ms)),
            ).fetchone()
            if row is None:
                continue
            item = dict(row)
            up_mid, down_mid = _mid(
                item.get("predict_up_mid"), item.get("predict_down_mid"),
                item.get("predict_up_bid"), item.get("predict_up_ask"),
                item.get("predict_down_bid"), item.get("predict_down_ask"),
            )
            if up_mid is None or down_mid is None:
                continue
            candidate = {
                "sampled_at_ms": int(item["sampled_at_ms"]),
                "seconds_left": _finite(item.get("seconds_left")),
                "predict_up_mid": up_mid,
                "predict_down_mid": down_mid,
                "source": str(source["path"]),
                "source_priority": int(source["priority"]),
            }
            if best is None or (candidate["sampled_at_ms"], candidate["source_priority"]) >= (best["sampled_at_ms"], best["source_priority"]):
                best = candidate
        return best


def _effect_against_delta(delta: float, side: str, quote_type: str, shares: float) -> bool:
    if abs(delta) <= EPS:
        return False
    effect = lifecycle._directional_effect(side, quote_type, shares)
    return lifecycle._opposite_sign(delta, effect)


def _future_labels(sampled_ms: int, maker_delta: float, actions: list[dict[str, Any]], times: list[int]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for horizon_ms in HORIZONS_MS:
        start = bisect.bisect_right(times, int(sampled_ms))
        end = bisect.bisect_right(times, int(sampled_ms) + int(horizon_ms))
        future = actions[start:end]
        repair = [
            row for row in future
            if _effect_against_delta(
                maker_delta,
                str(row["side"]),
                str(row["quote_type"]),
                float(row["shares"]),
            )
        ]
        suffix = f"{horizon_ms // 1000}s"
        result[f"any_taker_{suffix}"] = int(bool(future))
        result[f"repair_taker_{suffix}"] = int(bool(repair))
        result[f"repair_shares_{suffix}"] = sum(float(row["shares"]) for row in repair)
        result[f"repair_notional_{suffix}"] = sum(float(row["notional_usdt"]) for row in repair)
        if horizon_ms == 5000:
            result["first_repair_delay_ms_5s"] = (
                min(int(row["first_event_ms"]) - int(sampled_ms) for row in repair) if repair else ""
            )
    return result


def _build_risk_rows(
    events: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    snapshots: dict[int, list[dict[str, Any]]],
    cohort: dict[int, str],
    market_results: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    events_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    actions_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        events_by_market[int(row["market_id"])].append(row)
    for row in actions:
        actions_by_market[int(row["market_id"])].append(row)
    output: list[dict[str, Any]] = []

    for market_id, market_snapshots in snapshots.items():
        regime = cohort.get(market_id)
        if regime not in {"ORDINARY_PRE_SPECIAL", "SPECIAL"}:
            continue
        market_events = sorted(events_by_market.get(market_id, []), key=lambda row: (int(row["event_ms"]), str(row["leg_id"])))
        market_actions = sorted(actions_by_market.get(market_id, []), key=lambda row: (int(row["first_event_ms"]), str(row["parent_id"])))
        if not market_events or not market_actions:
            continue
        action_times = [int(row["first_event_ms"]) for row in market_actions]
        first_taker_ms = action_times[0]
        state = lifecycle._empty_state()
        event_pos = 0
        winner = str(market_results.get(market_id, {}).get("winner") or "")

        for snapshot in market_snapshots:
            sampled_ms = int(snapshot["sampled_at_ms"])
            while event_pos < len(market_events) and int(market_events[event_pos]["event_ms"]) < sampled_ms:
                event = market_events[event_pos]
                lifecycle._apply_flow(
                    state,
                    str(event["role"]),
                    str(event["side"]),
                    str(event["quote_type"]),
                    float(event["shares"]),
                )
                event_pos += 1
            metrics = lifecycle._state_metrics(state)
            maker_delta = float(metrics["maker_delta"])
            if abs(maker_delta) <= EPS:
                continue
            heavy_side = "UP" if maker_delta > 0 else "DOWN"
            heavy_prob = float(snapshot["predict_up_mid"] if heavy_side == "UP" else snapshot["predict_down_mid"])
            seconds_left = _finite(snapshot.get("seconds_left"))
            row = {
                "market_id": market_id,
                "regime": regime,
                "sampled_ms": sampled_ms,
                "seconds_left": seconds_left if seconds_left is not None else "",
                "phase": _phase(seconds_left),
                "lifecycle_state": "PRE_FIRST_TAKER" if sampled_ms < first_taker_ms else "POST_FIRST_TAKER",
                "maker_delta": maker_delta,
                "maker_abs_delta": abs(maker_delta),
                "combined_delta": float(metrics["combined_delta"]),
                "maker_heavy_side": heavy_side,
                "predict_up_mid": float(snapshot["predict_up_mid"]),
                "predict_down_mid": float(snapshot["predict_down_mid"]),
                "heavy_win_probability": heavy_prob,
                "heavy_probability_bucket": _prob_bucket(heavy_prob),
                "heavy_side_won": int(winner == heavy_side) if winner in {"UP", "DOWN"} else "",
                "signal_source": str(snapshot["source"]),
                "signal_source_priority": int(snapshot["source_priority"]),
            }
            row.update(_future_labels(sampled_ms, maker_delta, market_actions, action_times))
            output.append(row)
    output.sort(key=lambda row: (int(row["sampled_ms"]), int(row["market_id"])))
    return output


def _consume_lots(lots: deque[dict[str, Any]], shares: float) -> float:
    remaining = max(0.0, float(shares))
    consumed = 0.0
    while remaining > EPS and lots:
        lot = lots[0]
        take = min(remaining, float(lot["remaining"]))
        lot["remaining"] -= take
        remaining -= take
        consumed += take
        if float(lot["remaining"]) <= EPS:
            lots.popleft()
    return consumed


def _match_taker_leg(
    lots: deque[dict[str, Any]],
    taker_event: dict[str, Any],
    action: dict[str, Any],
    *,
    fee_bps: int,
    regime: str,
    signal: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    remaining = float(taker_event["shares"])
    output: list[dict[str, Any]] = []
    taker_price = float(taker_event["price"])
    maker_delta_before = float(action.get("maker_delta_before") or 0.0)
    repair_flag = int(_effect_against_delta(
        maker_delta_before,
        str(action["side"]),
        str(action["quote_type"]),
        float(action["shares"]),
    ))
    heavy_prob: float | None = None
    seconds_left: float | None = None
    if signal is not None:
        heavy_side = "UP" if maker_delta_before > EPS else "DOWN" if maker_delta_before < -EPS else None
        if heavy_side:
            heavy_prob = float(signal["predict_up_mid"] if heavy_side == "UP" else signal["predict_down_mid"])
        seconds_left = _finite(signal.get("seconds_left"))

    while remaining > EPS and lots:
        lot = lots[0]
        qty = min(remaining, float(lot["remaining"]))
        maker_price = float(lot["price"])
        raw_edge = 1.0 - maker_price - taker_price
        fee_per_share = _generic_fee_per_share(maker_price, fee_bps) + _generic_fee_per_share(taker_price, fee_bps)
        fee_edge = raw_edge - fee_per_share
        output.append({
            "market_id": int(taker_event["market_id"]),
            "regime": regime,
            "taker_parent_id": str(action["parent_id"]),
            "taker_first_event_ms": int(action["first_event_ms"]),
            "taker_is_first": int(action["is_first_taker"]),
            "phase": _phase(seconds_left),
            "seconds_left": seconds_left if seconds_left is not None else "",
            "heavy_win_probability": heavy_prob if heavy_prob is not None else "",
            "heavy_probability_bucket": _prob_bucket(heavy_prob),
            "maker_side": str(lot["side"]),
            "maker_price": maker_price,
            "maker_event_ms": int(lot["event_ms"]),
            "taker_side": str(taker_event["side"]),
            "taker_price": taker_price,
            "taker_event_ms": int(taker_event["event_ms"]),
            "paired_shares": qty,
            "pair_cost": maker_price + taker_price,
            "raw_edge_per_share": raw_edge,
            "raw_locked_edge_usdt": raw_edge * qty,
            "fee_adjusted_edge_per_share": fee_edge,
            "fee_adjusted_locked_edge_usdt": fee_edge * qty,
            "pair_class_raw": _pair_class(raw_edge),
            "pair_class_fee_adjusted": _pair_class(fee_edge),
            "maker_to_taker_delay_ms": int(taker_event["event_ms"]) - int(lot["event_ms"]),
            "repair_against_maker_heavy": repair_flag,
        })
        lot["remaining"] -= qty
        remaining -= qty
        if float(lot["remaining"]) <= EPS:
            lots.popleft()
    return output


def _build_complete_set_pairs(
    events: list[dict[str, Any]],
    actions: list[dict[str, Any]],
    cohort: dict[int, str],
    signals: SignalArchive,
    *,
    fee_bps: int,
    max_signal_lag_ms: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    action_by_parent = {str(row["parent_id"]): row for row in actions}
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_market[int(event["market_id"])].append(event)
    output: list[dict[str, Any]] = []
    maker_bid_shares = 0.0
    taker_bid_shares = 0.0
    maker_ask_consumed = 0.0

    for market_id, rows in by_market.items():
        regime = cohort.get(market_id)
        if regime not in {"ORDINARY_PRE_SPECIAL", "SPECIAL"}:
            continue
        available = {"UP": deque(), "DOWN": deque()}
        for event in sorted(rows, key=lambda row: (int(row["event_ms"]), str(row["leg_id"]))):
            role = str(event["role"])
            side = str(event["side"])
            qtype = str(event["quote_type"])
            shares = float(event["shares"])
            if qtype == "ASK":
                maker_ask_consumed += _consume_lots(available[side], shares)
                continue
            if qtype != "BID":
                continue
            if role == "MAKER":
                maker_bid_shares += shares
                available[side].append({
                    "side": side,
                    "price": float(event["price"]),
                    "event_ms": int(event["event_ms"]),
                    "remaining": shares,
                })
                continue
            if role != "TAKER":
                continue
            taker_bid_shares += shares
            parent_id = str(event["parent_id"])
            action = action_by_parent.get(parent_id)
            if action is None:
                continue
            opposite = "DOWN" if side == "UP" else "UP"
            signal = signals.asof(market_id, int(event["event_ms"]), max_signal_lag_ms)
            output.extend(_match_taker_leg(
                available[opposite], event, action,
                fee_bps=fee_bps,
                regime=regime,
                signal=signal,
            ))
    output.sort(key=lambda row: (int(row["taker_event_ms"]), int(row["market_id"]), str(row["taker_parent_id"])))
    return output, {
        "makerBidShares": maker_bid_shares,
        "takerBidShares": taker_bid_shares,
        "makerOriginSharesConsumedByAnyAskBeforePairing": maker_ask_consumed,
        "pairedFragments": len(output),
        "pairedShares": sum(float(row["paired_shares"]) for row in output),
    }


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _risk_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    payload: dict[str, Any] = {
        "rows": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "meanMakerAbsDelta": _mean([float(row["maker_abs_delta"]) for row in rows]),
        "heavySideWinRateAudit": _rate(sum(int(row["heavy_side_won"]) for row in rows if row["heavy_side_won"] != ""), sum(row["heavy_side_won"] != "" for row in rows)),
    }
    for horizon in (1, 2, 5):
        any_key = f"any_taker_{horizon}s"
        repair_key = f"repair_taker_{horizon}s"
        shares_key = f"repair_shares_{horizon}s"
        notional_key = f"repair_notional_{horizon}s"
        any_count = sum(int(row[any_key]) for row in rows)
        repair_count = sum(int(row[repair_key]) for row in rows)
        positive_shares = [float(row[shares_key]) for row in rows if float(row[shares_key]) > EPS]
        positive_notional = [float(row[notional_key]) for row in rows if float(row[notional_key]) > EPS]
        payload[f"horizon{horizon}s"] = {
            "anyTakerRate": _rate(any_count, len(rows)),
            "repairRate": _rate(repair_count, len(rows)),
            "repairGivenTakerRate": _rate(repair_count, any_count),
            "meanRepairSharesAllRows": _mean([float(row[shares_key]) for row in rows]),
            "meanRepairSharesGivenRepair": _mean(positive_shares),
            "meanRepairNotionalGivenRepair": _mean(positive_notional),
        }
    return payload


def _market_blocked_low_high(rows: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [
        row for row in rows
        if str(row["lifecycle_state"]) == "POST_FIRST_TAKER"
        and str(row["phase"]) in {"MID", "TAIL"}
    ]
    low = [row for row in relevant if float(row["heavy_win_probability"]) < 0.40]
    high = [row for row in relevant if float(row["heavy_win_probability"]) >= 0.60]

    def blocked(subset: list[dict[str, Any]]) -> dict[str, Any]:
        grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in subset:
            grouped[int(row["market_id"])].append(row)
        per_market = [statistics.fmean(int(row["repair_taker_5s"]) for row in market_rows) for market_rows in grouped.values()]
        return {
            "rows": len(subset),
            "markets": len(grouped),
            "rawRepairRate5s": _rate(sum(int(row["repair_taker_5s"]) for row in subset), len(subset)),
            "marketBlockedMeanRepairRate5s": _mean(per_market),
            "marketBlockedMedianRepairRate5s": _median(per_market),
        }

    low_payload = blocked(low)
    high_payload = blocked(high)
    low_rate = _finite(low_payload.get("marketBlockedMeanRepairRate5s"))
    high_rate = _finite(high_payload.get("marketBlockedMeanRepairRate5s"))
    return {
        "definition": "POST_FIRST_TAKER + MID/TAIL; LOW heavyProb<0.40 vs HIGH heavyProb>=0.60",
        "LOW": low_payload,
        "HIGH": high_payload,
        "marketBlockedDifferenceLowMinusHigh": (low_rate - high_rate) if low_rate is not None and high_rate is not None else None,
        "marketBlockedRatioLowVsHigh": (low_rate / high_rate) if low_rate is not None and high_rate not in (None, 0) else None,
    }


def _risk_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        subset = [row for row in rows if str(row["regime"]) == regime]
        by_bucket = {
            bucket: _risk_metrics([row for row in subset if str(row["heavy_probability_bucket"]) == bucket])
            for bucket in ("LT_020", "020_040", "040_060", "060_080", "GE_080")
        }
        by_phase_bucket: dict[str, Any] = {}
        for phase in ("OPEN", "MID", "TAIL"):
            phase_rows = [row for row in subset if str(row["phase"]) == phase and str(row["lifecycle_state"]) == "POST_FIRST_TAKER"]
            by_phase_bucket[phase] = {
                bucket: _risk_metrics([row for row in phase_rows if str(row["heavy_probability_bucket"]) == bucket])
                for bucket in ("LT_020", "020_040", "040_060", "060_080", "GE_080")
            }
        output[regime] = {
            "overall": _risk_metrics(subset),
            "postFirst": _risk_metrics([row for row in subset if str(row["lifecycle_state"]) == "POST_FIRST_TAKER"]),
            "byHeavyProbability": by_bucket,
            "postFirstByPhaseAndHeavyProbability": by_phase_bucket,
            "primaryHypothesis": _market_blocked_low_high(subset),
        }
    return output


def _pair_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"pairFragments": 0}
    total_shares = sum(float(row["paired_shares"]) for row in rows)
    raw_classes = Counter(str(row["pair_class_raw"]) for row in rows for _ in [0])
    fee_classes = Counter(str(row["pair_class_fee_adjusted"]) for row in rows for _ in [0])
    share_by_raw = defaultdict(float)
    share_by_fee = defaultdict(float)
    for row in rows:
        share_by_raw[str(row["pair_class_raw"])] += float(row["paired_shares"])
        share_by_fee[str(row["pair_class_fee_adjusted"])] += float(row["paired_shares"])
    return {
        "pairFragments": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "pairedShares": total_shares,
        "meanPairCostShareWeighted": (
            sum(float(row["pair_cost"]) * float(row["paired_shares"]) for row in rows) / total_shares if total_shares > EPS else None
        ),
        "rawLockedEdgeUsdt": sum(float(row["raw_locked_edge_usdt"]) for row in rows),
        "feeAdjustedLockedEdgeUsdt": sum(float(row["fee_adjusted_locked_edge_usdt"]) for row in rows),
        "rawClassFragments": dict(raw_classes),
        "feeAdjustedClassFragments": dict(fee_classes),
        "rawClassShareRates": {key: value / total_shares for key, value in share_by_raw.items()} if total_shares > EPS else {},
        "feeAdjustedClassShareRates": {key: value / total_shares for key, value in share_by_fee.items()} if total_shares > EPS else {},
        "repairShareRate": (
            sum(float(row["paired_shares"]) for row in rows if int(row["repair_against_maker_heavy"]) == 1) / total_shares if total_shares > EPS else None
        ),
        "medianMakerToTakerDelayMs": _median([float(row["maker_to_taker_delay_ms"]) for row in rows]),
    }


def _pair_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        subset = [row for row in rows if str(row["regime"]) == regime]
        output[regime] = {
            "overall": _pair_metrics(subset),
            "firstTaker": _pair_metrics([row for row in subset if int(row["taker_is_first"]) == 1]),
            "laterTaker": _pair_metrics([row for row in subset if int(row["taker_is_first"]) == 0]),
            "repairOnly": _pair_metrics([row for row in subset if int(row["repair_against_maker_heavy"]) == 1]),
            "postFirstMidTailLowHeavyProbability": _pair_metrics([
                row for row in subset
                if int(row["taker_is_first"]) == 0
                and str(row["phase"]) in {"MID", "TAIL"}
                and row["heavy_win_probability"] != ""
                and float(row["heavy_win_probability"]) < 0.40
            ]),
            "postFirstMidTailHighHeavyProbability": _pair_metrics([
                row for row in subset
                if int(row["taker_is_first"]) == 0
                and str(row["phase"]) in {"MID", "TAIL"}
                and row["heavy_win_probability"] != ""
                and float(row["heavy_win_probability"]) >= 0.60
            ]),
        }
    return output


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Test whether Target Taker repair intensity rises when Maker-heavy inventory becomes unlikely to win, "
            "and whether complementary Maker->Taker pairs are locked-edge arbitrage or paid insurance."
        )
    )
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--signal-db", type=Path, action="append", default=None)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--risk-csv", type=Path, default=DEFAULT_RISK_CSV)
    parser.add_argument("--pairs-csv", type=Path, default=DEFAULT_PAIRS_CSV)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--fee-bps", type=int, default=200)
    parser.add_argument("--max-signal-lag-ms", type=int, default=1500)
    args = parser.parse_args()

    asset = str(args.asset).upper().strip()
    special_start_ms = lifecycle._epoch_ms(args.special_start)
    signal_paths = args.signal_db if args.signal_db else DEFAULT_SIGNAL_DBS

    print(REPORT_VERSION, flush=True)
    print("[1/6] Load official signed fill legs + deduped Taker parent lifecycle...", flush=True)
    db = lifecycle._connect_ro(args.target_db)
    try:
        events, event_meta = lifecycle._load_official_events(db, asset=asset)
        parents = lifecycle._build_parents(events)
        market_results = lifecycle._load_market_results(db, asset)
    finally:
        db.close()
    cohort, phase_index, public_meta = lifecycle._load_public_cohorts(
        args.public_dataset, special_start_ms=special_start_ms
    )
    actions, _ = lifecycle_v11._replay(
        events,
        parents,
        cohort=cohort,
        phase_index=phase_index,
        market_results=market_results,
        max_phase_lag_ms=2000,
    )
    if len(actions) != sum(str(row["role"]) == "TAKER" for row in parents):
        raise RuntimeError("deduped Taker parent invariant failed before V2 analysis")
    print(f"      fillLegs={len(events):,} parents={len(parents):,} TakerActions={len(actions):,}", flush=True)

    print("[2/6] Load target-blind prediction snapshots and collapse to one risk row/second...", flush=True)
    signal_archive = SignalArchive(signal_paths)
    try:
        primary_market_ids = {
            int(row["market_id"]) for row in events
            if cohort.get(int(row["market_id"])) in {"ORDINARY_PRE_SPECIAL", "SPECIAL"}
        }
        snapshots, signal_meta = signal_archive.one_second_rows(primary_market_ids)
        risk_rows = _build_risk_rows(events, actions, snapshots, cohort, market_results)
        _write_csv(args.risk_csv, risk_rows, RISK_FIELDS)
        print(f"      riskRows={len(risk_rows):,} markets={len({int(r['market_id']) for r in risk_rows}):,}", flush=True)

        print("[3/6] Measure loss-probability -> future repair hazard at 1/2/5s...", flush=True)
        risk_summary = _risk_summary(risk_rows)

        print("[4/6] FIFO-match Maker BID inventory to later complementary Taker BID fills...", flush=True)
        pair_rows, pair_coverage = _build_complete_set_pairs(
            events,
            actions,
            cohort,
            signal_archive,
            fee_bps=max(0, int(args.fee_bps)),
            max_signal_lag_ms=max(0, int(args.max_signal_lag_ms)),
        )
        _write_csv(args.pairs_csv, pair_rows, PAIR_FIELDS)
    finally:
        signal_archive.close()
    print(f"      pairFragments={len(pair_rows):,} pairedShares={pair_coverage['pairedShares']:.2f}", flush=True)

    print("[5/6] Compare locked-edge complete sets vs paid-insurance repairs...", flush=True)
    pair_summary = _pair_summary(pair_rows)

    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "hypothesis": (
            "When Maker-heavy inventory is becoming unlikely to win, especially MID/TAIL and after the first Taker, "
            "Target should use complementary Taker BID more often and/or at larger size; when the heavy side is very "
            "likely to win it should repair less to preserve one-sided upside."
        ),
        "fixedDefinitions": {
            "riskSampling": "latest target-blind prediction snapshot per market-second",
            "stateBoundary": "official fill legs strictly earlier than sampled_ms",
            "makerHeavySide": "sign(maker_up_position - maker_down_position)",
            "heavyWinProbability": "target-blind Predict midpoint of the current Maker-heavy outcome",
            "probabilityBuckets": ["<0.20", "0.20-0.40", "0.40-0.60", "0.60-0.80", ">=0.80"],
            "repairLabel": "future Taker parent signed directional effect opposes Maker-heavy delta at the risk snapshot",
            "primaryHypothesisSlice": "POST_FIRST_TAKER + MID/TAIL; LOW <0.40 vs HIGH >=0.60; future repair within 5s",
            "completeSetPairing": "FIFO Maker BID acquisition lots matched once to later complementary Taker BID fill legs",
            "pairCost": "maker acquisition price + complementary Taker acquisition price",
            "pairClasses": "LOCKED_POSITIVE if edge>+0.01; NEAR_PAR within +/-0.01; INSURANCE_COST if edge<-0.01",
            "feeAudit": f"generic {max(0, int(args.fee_bps))} bps formula on each acquisition leg; not a claim about official Maker rebates/fees",
        },
        "coverage": {
            "officialEvents": event_meta,
            "derivedParents": len(parents),
            "dedupedTakerActions": len(actions),
            "publicResearch": public_meta,
            "signalArchive": signal_meta,
            "riskRows": len(risk_rows),
            "riskMarkets": len({int(row["market_id"]) for row in risk_rows}),
            "completeSet": pair_coverage,
        },
        "repairHazard": risk_summary,
        "completeSetEconomics": pair_summary,
        "interpretationGuardrails": [
            "Heavy-win probability is public market-implied probability, not the Target wallet's private belief.",
            "Future repair labels are used only for offline hypothesis testing; no future Target event may be used by a live strategy.",
            "Per-second risk rows are serially correlated, so the primary comparison also reports market-blocked means.",
            "Complete-set pairing is an attribution model. It does not prove the wallet internally linked those exact FIFO lots.",
            "Pair-cost >1 can be rational as paid insurance if it reduces larger directional risk; pair-cost <1 is consistent with locked-edge completion.",
            "The generic fee-adjusted pair audit is not official fee accounting and must not be treated as realized wallet PnL.",
        ],
    }
    _write_json(args.report, report)

    print("[6/6] Primary hypothesis summary", flush=True)
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        primary = risk_summary.get(regime, {}).get("primaryHypothesis", {})
        low = primary.get("LOW", {}).get("marketBlockedMeanRepairRate5s")
        high = primary.get("HIGH", {}).get("marketBlockedMeanRepairRate5s")
        ratio = primary.get("marketBlockedRatioLowVsHigh")
        pairs = pair_summary.get(regime, {}).get("laterTaker", {})
        print(
            f"  {regime}: lowProbRepair5s={low} highProbRepair5s={high} ratio={ratio} "
            f"laterPairedShares={pairs.get('pairedShares')} rawLockedEdge={pairs.get('rawLockedEdgeUsdt')}",
            flush=True,
        )
    print(f"Report: {args.report.expanduser().resolve()}", flush=True)
    print(f"Risk rows: {args.risk_csv.expanduser().resolve()}", flush=True)
    print(f"Complete-set pairs: {args.pairs_csv.expanduser().resolve()}", flush=True)
    print("No repair or complete-set rule was promoted to paper/live trading.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
