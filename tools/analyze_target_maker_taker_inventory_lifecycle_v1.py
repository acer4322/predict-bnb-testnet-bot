from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TARGET_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_PUBLIC_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_maker_taker_inventory_lifecycle_v1_report.json"
DEFAULT_ACTIONS_CSV = ROOT / "data" / "research" / "target_maker_taker_inventory_lifecycle_v1.csv"
REPORT_VERSION = "TARGET_MAKER_TAKER_INVENTORY_LIFECYCLE_V1_SIGNED_OFFICIAL"
DEFAULT_SPECIAL_START = "2026-08-16T12:00:00+08:00"
DEFAULT_MAX_PHASE_LAG_MS = 2000
EPS = 1e-9

STATE_KEYS = (
    "maker_up",
    "maker_down",
    "taker_up",
    "taker_down",
)

ACTION_FIELDS = [
    "market_id",
    "regime",
    "phase",
    "phase_lag_ms",
    "parent_id",
    "role",
    "side",
    "quote_type",
    "order_hash",
    "first_event_ms",
    "last_event_ms",
    "duration_ms",
    "fill_legs",
    "shares",
    "average_price",
    "notional_usdt",
    "taker_index_market",
    "is_first_taker",
    "has_prior_maker_fill",
    "prior_maker_parent_count",
    "prior_taker_parent_count",
    "parent_effect_directional",
    "parent_effect_up",
    "parent_effect_down",
    "maker_up_before",
    "maker_down_before",
    "taker_up_before",
    "taker_down_before",
    "maker_delta_before",
    "taker_delta_before",
    "combined_delta_before",
    "combined_abs_before",
    "gross_before",
    "paired_long_before",
    "combined_delta_counterfactual_after",
    "combined_abs_counterfactual_after",
    "gross_counterfactual_after",
    "paired_long_counterfactual_after",
    "combined_abs_reduction",
    "gross_reduction",
    "paired_long_change",
    "balance_component_shares",
    "maker_offset_component_shares",
    "semantic_primary",
    "maker_relation",
    "pair_completion_flag",
    "reduce_gross_flag",
    "overlap_parent_count",
    "maker_net_pnl_usdt",
    "taker_net_pnl_usdt",
    "combined_net_pnl_usdt",
    "winner",
]


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10.0)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


def _has_table(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
        (table,),
    ).fetchone() is not None


def _columns(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    with temp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=ACTION_FIELDS, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    temp.replace(resolved)


def _epoch_ms(text: str) -> int:
    raw = str(text).strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(raw)
    if dt.tzinfo is None:
        raise ValueError("timestamp must include timezone offset")
    return int(dt.timestamp() * 1000)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _signed_token_shares(quote_type: str, shares: float) -> float:
    qtype = str(quote_type or "").upper()
    if qtype == "BID":
        return float(shares)
    if qtype == "ASK":
        return -float(shares)
    raise ValueError(f"unsupported quote_type: {quote_type!r}")


def _directional_effect(side: str, quote_type: str, shares: float) -> float:
    signed = _signed_token_shares(quote_type, shares)
    outcome = str(side or "").upper()
    if outcome == "UP":
        return signed
    if outcome == "DOWN":
        return -signed
    raise ValueError(f"unsupported side: {side!r}")


def _empty_state() -> dict[str, float]:
    return {key: 0.0 for key in STATE_KEYS}


def _copy_state(state: dict[str, float]) -> dict[str, float]:
    return {key: float(state.get(key, 0.0)) for key in STATE_KEYS}


def _apply_flow(state: dict[str, float], role: str, side: str, quote_type: str, shares: float) -> None:
    role_key = str(role or "").lower()
    side_key = str(side or "").lower()
    if role_key not in {"maker", "taker"} or side_key not in {"up", "down"}:
        return
    state[f"{role_key}_{side_key}"] += _signed_token_shares(quote_type, shares)


def _state_metrics(state: dict[str, float]) -> dict[str, float]:
    maker_up = float(state.get("maker_up", 0.0))
    maker_down = float(state.get("maker_down", 0.0))
    taker_up = float(state.get("taker_up", 0.0))
    taker_down = float(state.get("taker_down", 0.0))
    up = maker_up + taker_up
    down = maker_down + taker_down
    maker_delta = maker_up - maker_down
    taker_delta = taker_up - taker_down
    combined_delta = up - down
    gross = abs(up) + abs(down)
    paired_long = max(0.0, min(up, down))
    return {
        "maker_up": maker_up,
        "maker_down": maker_down,
        "taker_up": taker_up,
        "taker_down": taker_down,
        "up": up,
        "down": down,
        "maker_delta": maker_delta,
        "taker_delta": taker_delta,
        "combined_delta": combined_delta,
        "combined_abs": abs(combined_delta),
        "gross": gross,
        "paired_long": paired_long,
    }


def _counterfactual_after(
    before: dict[str, float],
    role: str,
    side: str,
    quote_type: str,
    shares: float,
) -> dict[str, float]:
    result = _copy_state(before)
    _apply_flow(result, role, side, quote_type, shares)
    return result


def _opposite_sign(a: float, b: float) -> bool:
    return abs(a) > EPS and abs(b) > EPS and (a > 0) != (b > 0)


def _semantic(before_delta: float, after_delta: float) -> str:
    before_abs = abs(float(before_delta))
    after_abs = abs(float(after_delta))
    if _opposite_sign(before_delta, after_delta):
        return "FLIP"
    if after_abs < before_abs - EPS:
        return "BALANCE"
    if after_abs > before_abs + EPS:
        return "ADD"
    return "NEUTRAL"


def _maker_relation(maker_delta: float, parent_directional_effect: float) -> str:
    if abs(maker_delta) <= EPS or abs(parent_directional_effect) <= EPS:
        return "MAKER_BALANCED_OR_UNKNOWN"
    if _opposite_sign(maker_delta, parent_directional_effect):
        return "OPPOSE_MAKER_HEAVY"
    return "WITH_MAKER_HEAVY"


def _repair_component(prior_delta: float, effect: float) -> float:
    if not _opposite_sign(prior_delta, effect):
        return 0.0
    return min(abs(float(prior_delta)), abs(float(effect)))


def _parent_id_for_event(row: dict[str, Any]) -> str:
    asset = str(row.get("asset") or "")
    role = str(row.get("role") or "")
    side = str(row.get("side") or "")
    quote_type = str(row.get("quote_type") or "")
    identity = str(row.get("order_hash") or row.get("leg_id") or "")
    return f"{asset}:{role}:{identity}:{side}:{quote_type}"


def _load_official_events(
    db: sqlite3.Connection,
    *,
    asset: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    table = "wallet_shadow_target_events"
    required = {
        "leg_id", "wallet", "asset", "market_id", "role", "side", "quote_type",
        "order_hash", "event_ms", "price", "shares",
    }
    if not _has_table(db, table):
        raise RuntimeError(f"missing table: {table}")
    missing = sorted(required - _columns(db, table))
    if missing:
        raise RuntimeError("wallet_shadow_target_events missing columns: " + ", ".join(missing))

    wallet_row = db.execute(
        "SELECT wallet,COUNT(*) n FROM wallet_shadow_target_events WHERE asset=? GROUP BY wallet ORDER BY n DESC LIMIT 1",
        (asset,),
    ).fetchone()
    target_wallet = str(wallet_row["wallet"]).lower() if wallet_row is not None else None

    rows: list[dict[str, Any]] = []
    query = """
        SELECT leg_id,wallet,asset,market_id,role,side,quote_type,order_hash,
               event_ms,price,shares
          FROM wallet_shadow_target_events
         WHERE asset=? AND role IN ('MAKER','TAKER') AND side IN ('UP','DOWN')
               AND quote_type IN ('BID','ASK') AND shares>0
         ORDER BY market_id,event_ms,id
    """
    for raw in db.execute(query, (asset,)):
        row = dict(raw)
        row["parent_id"] = _parent_id_for_event(row)
        row["market_id"] = int(row["market_id"])
        row["event_ms"] = int(row["event_ms"])
        row["price"] = float(row["price"])
        row["shares"] = float(row["shares"])
        rows.append(row)

    return rows, {
        "targetWallet": target_wallet,
        "fillLegs": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "firstEventMs": min((int(row["event_ms"]) for row in rows), default=None),
        "lastEventMs": max((int(row["event_ms"]) for row in rows), default=None),
    }


def _build_parents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        grouped[str(row["parent_id"])].append(row)
    parents: list[dict[str, Any]] = []
    for parent_id, legs in grouped.items():
        ordered = sorted(legs, key=lambda item: (int(item["event_ms"]), str(item["leg_id"])))
        first = ordered[0]
        shares = sum(float(item["shares"]) for item in ordered)
        weighted = sum(float(item["price"]) * float(item["shares"]) for item in ordered)
        parents.append(
            {
                "parent_id": parent_id,
                "market_id": int(first["market_id"]),
                "asset": str(first["asset"]),
                "role": str(first["role"]),
                "side": str(first["side"]),
                "quote_type": str(first["quote_type"]),
                "order_hash": first.get("order_hash"),
                "first_event_ms": int(ordered[0]["event_ms"]),
                "last_event_ms": int(ordered[-1]["event_ms"]),
                "average_price": weighted / shares if shares > 0 else 0.0,
                "shares": shares,
                "fill_legs": len(ordered),
            }
        )
    parents.sort(key=lambda row: (int(row["market_id"]), int(row["first_event_ms"]), int(row["last_event_ms"]), str(row["parent_id"])))
    return parents


def _parent_table_audit(db: sqlite3.Connection, parents: list[dict[str, Any]], asset: str) -> dict[str, Any]:
    if not _has_table(db, "target_parent_orders"):
        return {"tablePresent": False}
    required = {"parent_id", "asset", "role", "side", "quote_type", "shares", "first_event_ms", "last_event_ms"}
    if not required.issubset(_columns(db, "target_parent_orders")):
        return {"tablePresent": True, "compatible": False}
    official = {
        str(row["parent_id"]): dict(row)
        for row in db.execute(
            "SELECT parent_id,shares,first_event_ms,last_event_ms FROM target_parent_orders WHERE asset=?",
            (asset,),
        )
    }
    derived = {str(row["parent_id"]): row for row in parents}
    matched = set(official) & set(derived)
    share_errors = [
        abs(float(derived[key]["shares"]) - float(official[key]["shares"]))
        for key in matched
    ]
    return {
        "tablePresent": True,
        "compatible": True,
        "officialParents": len(official),
        "derivedParents": len(derived),
        "matchedParents": len(matched),
        "matchRateVsDerived": len(matched) / len(derived) if derived else None,
        "maxAbsoluteShareDifference": max(share_errors) if share_errors else None,
    }


def _load_public_cohorts(
    path: Path,
    *,
    special_start_ms: int,
) -> tuple[dict[int, str], dict[int, dict[str, Any]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = set(reader.fieldnames or [])
        time_col = "decision_sampled_at_ms" if "decision_sampled_at_ms" in fields else "sampled_at_ms"
        if "market_id" not in fields or time_col not in fields:
            raise RuntimeError("public dataset must contain market_id and decision_sampled_at_ms/sample_at_ms")
        for raw in reader:
            try:
                market_id = int(float(raw["market_id"]))
                sampled_ms = int(float(raw[time_col]))
            except (TypeError, ValueError):
                continue
            phase = str(raw.get("macro_phase") or "").upper()
            if phase not in {"OPEN", "MID", "TAIL"}:
                seconds = _finite(raw.get("seconds_left"))
                if seconds is not None:
                    phase = "OPEN" if seconds > 180 else "MID" if seconds > 60 else "TAIL"
                else:
                    phase = "UNKNOWN"
            grouped[market_id].append({"sampled_ms": sampled_ms, "phase": phase})

    special_ids = {
        market_id
        for market_id, rows in grouped.items()
        if any(int(row["sampled_ms"]) >= special_start_ms for row in rows)
    }
    ordinary_ids = {
        market_id
        for market_id, rows in grouped.items()
        if market_id not in special_ids and any(int(row["sampled_ms"]) < special_start_ms for row in rows)
    }
    cohort: dict[int, str] = {}
    for market_id in ordinary_ids:
        cohort[market_id] = "ORDINARY_PRE_SPECIAL"
    for market_id in special_ids:
        cohort[market_id] = "SPECIAL"

    phase_index: dict[int, dict[str, Any]] = {}
    for market_id, rows in grouped.items():
        ordered = sorted(rows, key=lambda row: int(row["sampled_ms"]))
        phase_index[market_id] = {
            "rows": ordered,
            "times": [int(row["sampled_ms"]) for row in ordered],
        }
    all_samples = [int(row["sampled_ms"]) for rows in grouped.values() for row in rows]
    return cohort, phase_index, {
        "path": str(resolved),
        "markets": len(grouped),
        "ordinaryMarkets": len(ordinary_ids),
        "specialMarkets": len(special_ids),
        "firstSampleMs": min(all_samples) if all_samples else None,
        "lastSampleMs": max(all_samples) if all_samples else None,
        "specialStartMs": special_start_ms,
        "specialDefinition": "market has at least one row at/after specialStartMs in the frozen public research dataset",
    }


def _phase_asof(index: dict[int, dict[str, Any]], market_id: int, at_ms: int, max_lag_ms: int) -> tuple[str, int | None]:
    entry = index.get(int(market_id))
    if entry is None:
        return "UNKNOWN", None
    times = entry["times"]
    pos = bisect.bisect_right(times, int(at_ms)) - 1
    if pos < 0:
        return "UNKNOWN", None
    row = entry["rows"][pos]
    lag = int(at_ms) - int(row["sampled_ms"])
    if lag < 0 or lag > int(max_lag_ms):
        return "UNKNOWN", lag
    return str(row["phase"]), lag


def _load_market_results(db: sqlite3.Connection, asset: str) -> dict[int, dict[str, Any]]:
    if not _has_table(db, "target_market_results"):
        return {}
    required = {
        "market_id", "asset", "winner", "net_pnl_usdt", "maker_net_pnl_usdt", "taker_net_pnl_usdt"
    }
    if not required.issubset(_columns(db, "target_market_results")):
        return {}
    result: dict[int, dict[str, Any]] = {}
    for raw in db.execute(
        """SELECT market_id,winner,net_pnl_usdt,net_roi,maker_net_pnl_usdt,taker_net_pnl_usdt,
                  buy_notional_usdt,sell_proceeds_usdt,payout_usdt,accounting_version
             FROM target_market_results WHERE asset=?""",
        (asset,),
    ):
        row = dict(raw)
        result[int(row["market_id"])] = row
    return result


def _overlap_counts(parents: list[dict[str, Any]]) -> dict[str, int]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in parents:
        by_market[int(row["market_id"])].append(row)
    result: dict[str, int] = {}
    for rows in by_market.values():
        ordered = sorted(rows, key=lambda item: (int(item["first_event_ms"]), int(item["last_event_ms"])))
        for row in ordered:
            start = int(row["first_event_ms"])
            end = int(row["last_event_ms"])
            result[str(row["parent_id"])] = sum(
                1
                for other in ordered
                if str(other["parent_id"]) != str(row["parent_id"])
                and int(other["first_event_ms"]) <= end
                and int(other["last_event_ms"]) >= start
            )
    return result


def _replay(
    events: list[dict[str, Any]],
    parents: list[dict[str, Any]],
    *,
    cohort: dict[int, str],
    phase_index: dict[int, dict[str, Any]],
    market_results: dict[int, dict[str, Any]],
    max_phase_lag_ms: int,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    parent_map = {str(row["parent_id"]): row for row in parents}
    first_leg = {str(row["parent_id"]): int(row["first_event_ms"]) for row in parents}
    last_leg = {str(row["parent_id"]): int(row["last_event_ms"]) for row in parents}
    overlap = _overlap_counts(parents)
    by_market_events: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_market_events[int(event["market_id"])].append(event)

    action_rows: list[dict[str, Any]] = []
    market_payloads: dict[int, dict[str, Any]] = {}

    for market_id, market_events in sorted(by_market_events.items()):
        ordered = sorted(market_events, key=lambda item: (int(item["event_ms"]), str(item["leg_id"])))
        state = _empty_state()
        before_by_parent: dict[str, dict[str, float]] = {}
        maker_parent_seen: set[str] = set()
        taker_parent_seen: set[str] = set()
        first_seen_parent: set[str] = set()
        taker_index = 0
        market_taker_rows: list[dict[str, Any]] = []

        for event in ordered:
            parent_id = str(event["parent_id"])
            if parent_id not in first_seen_parent:
                before_by_parent[parent_id] = _copy_state(state)
                first_seen_parent.add(parent_id)
            _apply_flow(state, str(event["role"]), str(event["side"]), str(event["quote_type"]), float(event["shares"]))
            if int(event["event_ms"]) != last_leg.get(parent_id):
                continue
            parent = parent_map[parent_id]
            role = str(parent["role"])
            if role == "MAKER":
                maker_parent_seen.add(parent_id)
                continue
            if role != "TAKER":
                continue

            taker_index += 1
            before = before_by_parent[parent_id]
            before_m = _state_metrics(before)
            counter = _counterfactual_after(
                before,
                role,
                str(parent["side"]),
                str(parent["quote_type"]),
                float(parent["shares"]),
            )
            after_m = _state_metrics(counter)
            effect = _directional_effect(str(parent["side"]), str(parent["quote_type"]), float(parent["shares"]))
            semantic = _semantic(before_m["combined_delta"], after_m["combined_delta"])
            relation = _maker_relation(before_m["maker_delta"], effect)
            phase, phase_lag = _phase_asof(
                phase_index,
                market_id,
                int(parent["first_event_ms"]),
                max_phase_lag_ms,
            )
            result = market_results.get(market_id, {})
            row = {
                "market_id": market_id,
                "regime": cohort.get(market_id, "OUTSIDE_PUBLIC_COHORT"),
                "phase": phase,
                "phase_lag_ms": phase_lag if phase_lag is not None else "",
                "parent_id": parent_id,
                "role": role,
                "side": str(parent["side"]),
                "quote_type": str(parent["quote_type"]),
                "order_hash": parent.get("order_hash") or "",
                "first_event_ms": int(parent["first_event_ms"]),
                "last_event_ms": int(parent["last_event_ms"]),
                "duration_ms": int(parent["last_event_ms"]) - int(parent["first_event_ms"]),
                "fill_legs": int(parent["fill_legs"]),
                "shares": float(parent["shares"]),
                "average_price": float(parent["average_price"]),
                "notional_usdt": float(parent["average_price"]) * float(parent["shares"]),
                "taker_index_market": taker_index,
                "is_first_taker": int(taker_index == 1),
                "has_prior_maker_fill": int(abs(before_m["maker_up"]) > EPS or abs(before_m["maker_down"]) > EPS),
                "prior_maker_parent_count": len(maker_parent_seen),
                "prior_taker_parent_count": len(taker_parent_seen),
                "parent_effect_directional": effect,
                "parent_effect_up": _signed_token_shares(str(parent["quote_type"]), float(parent["shares"])) if str(parent["side"]) == "UP" else 0.0,
                "parent_effect_down": _signed_token_shares(str(parent["quote_type"]), float(parent["shares"])) if str(parent["side"]) == "DOWN" else 0.0,
                "maker_up_before": before_m["maker_up"],
                "maker_down_before": before_m["maker_down"],
                "taker_up_before": before_m["taker_up"],
                "taker_down_before": before_m["taker_down"],
                "maker_delta_before": before_m["maker_delta"],
                "taker_delta_before": before_m["taker_delta"],
                "combined_delta_before": before_m["combined_delta"],
                "combined_abs_before": before_m["combined_abs"],
                "gross_before": before_m["gross"],
                "paired_long_before": before_m["paired_long"],
                "combined_delta_counterfactual_after": after_m["combined_delta"],
                "combined_abs_counterfactual_after": after_m["combined_abs"],
                "gross_counterfactual_after": after_m["gross"],
                "paired_long_counterfactual_after": after_m["paired_long"],
                "combined_abs_reduction": before_m["combined_abs"] - after_m["combined_abs"],
                "gross_reduction": before_m["gross"] - after_m["gross"],
                "paired_long_change": after_m["paired_long"] - before_m["paired_long"],
                "balance_component_shares": _repair_component(before_m["combined_delta"], effect),
                "maker_offset_component_shares": _repair_component(before_m["maker_delta"], effect),
                "semantic_primary": semantic,
                "maker_relation": relation,
                "pair_completion_flag": int(after_m["paired_long"] > before_m["paired_long"] + EPS),
                "reduce_gross_flag": int(after_m["gross"] < before_m["gross"] - EPS),
                "overlap_parent_count": int(overlap.get(parent_id, 0)),
                "maker_net_pnl_usdt": result.get("maker_net_pnl_usdt", ""),
                "taker_net_pnl_usdt": result.get("taker_net_pnl_usdt", ""),
                "combined_net_pnl_usdt": result.get("net_pnl_usdt", ""),
                "winner": result.get("winner", ""),
            }
            action_rows.append(row)
            market_taker_rows.append(row)
            taker_parent_seen.add(parent_id)

        final_m = _state_metrics(state)
        result = market_results.get(market_id)
        market_payloads[market_id] = {
            "marketId": market_id,
            "regime": cohort.get(market_id, "OUTSIDE_PUBLIC_COHORT"),
            "makerParents": len({str(row["parent_id"]) for row in parents if int(row["market_id"]) == market_id and str(row["role"]) == "MAKER"}),
            "takerParents": len(market_taker_rows),
            "finalMakerDelta": final_m["maker_delta"],
            "finalTakerDelta": final_m["taker_delta"],
            "finalCombinedDelta": final_m["combined_delta"],
            "finalCombinedAbs": final_m["combined_abs"],
            "finalPairedLong": final_m["paired_long"],
            "takerBalanceActions": sum(str(row["semantic_primary"]) == "BALANCE" for row in market_taker_rows),
            "takerFlipActions": sum(str(row["semantic_primary"]) == "FLIP" for row in market_taker_rows),
            "takerAddActions": sum(str(row["semantic_primary"]) == "ADD" for row in market_taker_rows),
            "takerOpposeMakerHeavyActions": sum(str(row["maker_relation"]) == "OPPOSE_MAKER_HEAVY" for row in market_taker_rows),
            "takerBalanceComponentShares": sum(float(row["balance_component_shares"]) for row in market_taker_rows),
            "takerMakerOffsetComponentShares": sum(float(row["maker_offset_component_shares"]) for row in market_taker_rows),
            "takerRepairNotionalUsdt": sum(
                float(row["notional_usdt"])
                for row in market_taker_rows
                if str(row["maker_relation"]) == "OPPOSE_MAKER_HEAVY"
            ),
            "settled": int(result is not None),
            "winner": result.get("winner") if result else None,
            "makerNetPnlUsdt": float(result["maker_net_pnl_usdt"]) if result is not None else None,
            "takerNetPnlUsdt": float(result["taker_net_pnl_usdt"]) if result is not None else None,
            "combinedNetPnlUsdt": float(result["net_pnl_usdt"]) if result is not None else None,
            "pnlReconciliationError": (
                float(result["net_pnl_usdt"]) - float(result["maker_net_pnl_usdt"]) - float(result["taker_net_pnl_usdt"])
                if result is not None else None
            ),
        }

    action_rows.sort(key=lambda row: (int(row["first_event_ms"]), int(row["market_id"]), str(row["parent_id"])))
    return action_rows, market_payloads


def _rate(count: int, total: int) -> float | None:
    return count / total if total else None


def _mean(values: list[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _median(values: list[float]) -> float | None:
    return statistics.median(values) if values else None


def _event_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"takerActions": 0}
    classifications = Counter(str(row["semantic_primary"]) for row in rows)
    relations = Counter(str(row["maker_relation"]) for row in rows)
    quotes = Counter(str(row["quote_type"]) for row in rows)
    first = [row for row in rows if int(row["is_first_taker"]) == 1]
    later = [row for row in rows if int(row["is_first_taker"]) == 0]
    with_maker = [row for row in rows if int(row["has_prior_maker_fill"]) == 1]

    def subset_payload(subset: list[dict[str, Any]]) -> dict[str, Any]:
        if not subset:
            return {"actions": 0}
        cls = Counter(str(row["semantic_primary"]) for row in subset)
        rel = Counter(str(row["maker_relation"]) for row in subset)
        return {
            "actions": len(subset),
            "balanceRate": _rate(int(cls.get("BALANCE", 0)), len(subset)),
            "flipRate": _rate(int(cls.get("FLIP", 0)), len(subset)),
            "addRate": _rate(int(cls.get("ADD", 0)), len(subset)),
            "opposesMakerHeavyRate": _rate(int(rel.get("OPPOSE_MAKER_HEAVY", 0)), len(subset)),
            "pairCompletionRate": _rate(sum(int(row["pair_completion_flag"]) for row in subset), len(subset)),
            "reduceGrossRate": _rate(sum(int(row["reduce_gross_flag"]) for row in subset), len(subset)),
            "meanCombinedAbsReduction": _mean([float(row["combined_abs_reduction"]) for row in subset]),
            "medianCombinedAbsReduction": _median([float(row["combined_abs_reduction"]) for row in subset]),
            "totalBalanceComponentShares": sum(float(row["balance_component_shares"]) for row in subset),
            "totalMakerOffsetComponentShares": sum(float(row["maker_offset_component_shares"]) for row in subset),
            "totalShares": sum(float(row["shares"]) for row in subset),
            "totalNotionalUsdt": sum(float(row["notional_usdt"]) for row in subset),
        }

    by_phase: dict[str, Any] = {}
    for phase in ("OPEN", "MID", "TAIL", "UNKNOWN"):
        subset = [row for row in rows if str(row["phase"]) == phase]
        if subset:
            by_phase[phase] = subset_payload(subset)

    return {
        "takerActions": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "withPriorMakerActions": len(with_maker),
        "withPriorMakerRate": _rate(len(with_maker), len(rows)),
        "classifications": dict(classifications),
        "classificationRates": {key: value / len(rows) for key, value in classifications.items()},
        "makerRelations": dict(relations),
        "makerRelationRates": {key: value / len(rows) for key, value in relations.items()},
        "quoteTypes": dict(quotes),
        "pairCompletionRate": _rate(sum(int(row["pair_completion_flag"]) for row in rows), len(rows)),
        "reduceGrossRate": _rate(sum(int(row["reduce_gross_flag"]) for row in rows), len(rows)),
        "overlappingParentRate": _rate(sum(int(row["overlap_parent_count"]) > 0 for row in rows), len(rows)),
        "shares": {
            "total": sum(float(row["shares"]) for row in rows),
            "balanceComponent": sum(float(row["balance_component_shares"]) for row in rows),
            "makerOffsetComponent": sum(float(row["maker_offset_component_shares"]) for row in rows),
        },
        "combinedAbsReduction": {
            "mean": _mean([float(row["combined_abs_reduction"]) for row in rows]),
            "median": _median([float(row["combined_abs_reduction"]) for row in rows]),
            "positiveRate": _rate(sum(float(row["combined_abs_reduction"]) > EPS for row in rows), len(rows)),
        },
        "firstTaker": subset_payload(first),
        "laterTaker": subset_payload(later),
        "withPriorMaker": subset_payload(with_maker),
        "byPhase": by_phase,
        "byQuoteType": {
            qtype: subset_payload([row for row in rows if str(row["quote_type"]) == qtype])
            for qtype in ("BID", "ASK")
            if any(str(row["quote_type"]) == qtype for row in rows)
        },
    }


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx = statistics.fmean(xs)
    my = statistics.fmean(ys)
    dx = [x - mx for x in xs]
    dy = [y - my for y in ys]
    denom = math.sqrt(sum(v * v for v in dx) * sum(v * v for v in dy))
    if denom <= EPS:
        return None
    return sum(a * b for a, b in zip(dx, dy)) / denom


def _market_summary(markets: list[dict[str, Any]]) -> dict[str, Any]:
    if not markets:
        return {"markets": 0}
    settled = [row for row in markets if int(row.get("settled") or 0) == 1]
    maker_losses = [row for row in settled if float(row["makerNetPnlUsdt"]) < -EPS]
    maker_wins = [row for row in settled if float(row["makerNetPnlUsdt"]) > EPS]
    rescued = [row for row in maker_losses if float(row["combinedNetPnlUsdt"]) > EPS]
    improved = [row for row in settled if float(row["combinedNetPnlUsdt"]) > float(row["makerNetPnlUsdt"]) + EPS]
    worsened = [row for row in settled if float(row["combinedNetPnlUsdt"]) < float(row["makerNetPnlUsdt"]) - EPS]
    loss_magnitudes = [max(0.0, -float(row["makerNetPnlUsdt"])) for row in settled]
    repair_shares = [float(row["takerMakerOffsetComponentShares"]) for row in settled]
    repair_notional = [float(row["takerRepairNotionalUsdt"]) for row in settled]
    return {
        "markets": len(markets),
        "settledMarkets": len(settled),
        "settlementCoverage": _rate(len(settled), len(markets)),
        "makerLosingMarkets": len(maker_losses),
        "makerWinningMarkets": len(maker_wins),
        "makerLosingMarketRate": _rate(len(maker_losses), len(settled)),
        "makerLossRescuedToCombinedPositive": len(rescued),
        "makerLossRescueRate": _rate(len(rescued), len(maker_losses)),
        "takerImprovedMakerPnlMarkets": len(improved),
        "takerImprovedMakerPnlRate": _rate(len(improved), len(settled)),
        "takerWorsenedMakerPnlMarkets": len(worsened),
        "takerWorsenedMakerPnlRate": _rate(len(worsened), len(settled)),
        "pnl": {
            "makerTotal": sum(float(row["makerNetPnlUsdt"]) for row in settled),
            "takerTotal": sum(float(row["takerNetPnlUsdt"]) for row in settled),
            "combinedTotal": sum(float(row["combinedNetPnlUsdt"]) for row in settled),
            "makerMeanPerMarket": _mean([float(row["makerNetPnlUsdt"]) for row in settled]),
            "takerMeanPerMarket": _mean([float(row["takerNetPnlUsdt"]) for row in settled]),
            "combinedMeanPerMarket": _mean([float(row["combinedNetPnlUsdt"]) for row in settled]),
            "makerMedianPerMarket": _median([float(row["makerNetPnlUsdt"]) for row in settled]),
            "combinedMedianPerMarket": _median([float(row["combinedNetPnlUsdt"]) for row in settled]),
            "maxAbsoluteReconciliationError": max((abs(float(row["pnlReconciliationError"])) for row in settled), default=None),
        },
        "inventory": {
            "meanAbsFinalMakerDelta": _mean([abs(float(row["finalMakerDelta"])) for row in markets]),
            "meanAbsFinalCombinedDelta": _mean([abs(float(row["finalCombinedDelta"])) for row in markets]),
            "medianAbsFinalMakerDelta": _median([abs(float(row["finalMakerDelta"])) for row in markets]),
            "medianAbsFinalCombinedDelta": _median([abs(float(row["finalCombinedDelta"])) for row in markets]),
        },
        "repair": {
            "totalMakerOffsetComponentShares": sum(float(row["takerMakerOffsetComponentShares"]) for row in markets),
            "totalRepairNotionalUsdt": sum(float(row["takerRepairNotionalUsdt"]) for row in markets),
            "makerLossMagnitudeVsRepairSharesPearson": _pearson(loss_magnitudes, repair_shares),
            "makerLossMagnitudeVsRepairNotionalPearson": _pearson(loss_magnitudes, repair_notional),
        },
    }


def _safe_get(payload: dict[str, Any], *path: str) -> float | None:
    current: Any = payload
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return None
        current = current[key]
    return _finite(current)


def _difference(a: float | None, b: float | None) -> float | None:
    return a - b if a is not None and b is not None else None


def _special_minus_ordinary(ordinary: dict[str, Any], special: dict[str, Any]) -> dict[str, Any]:
    oe = ordinary.get("events", {})
    se = special.get("events", {})
    om = ordinary.get("markets", {})
    sm = special.get("markets", {})
    return {
        "balanceRate": _difference(
            _safe_get(se, "classificationRates", "BALANCE"),
            _safe_get(oe, "classificationRates", "BALANCE"),
        ),
        "flipRate": _difference(
            _safe_get(se, "classificationRates", "FLIP"),
            _safe_get(oe, "classificationRates", "FLIP"),
        ),
        "opposesMakerHeavyRate": _difference(
            _safe_get(se, "makerRelationRates", "OPPOSE_MAKER_HEAVY"),
            _safe_get(oe, "makerRelationRates", "OPPOSE_MAKER_HEAVY"),
        ),
        "laterTakerBalanceRate": _difference(
            _safe_get(se, "laterTaker", "balanceRate"),
            _safe_get(oe, "laterTaker", "balanceRate"),
        ),
        "makerLosingMarketRate": _difference(
            _safe_get(sm, "makerLosingMarketRate"),
            _safe_get(om, "makerLosingMarketRate"),
        ),
        "makerLossRescueRate": _difference(
            _safe_get(sm, "makerLossRescueRate"),
            _safe_get(om, "makerLossRescueRate"),
        ),
        "makerMeanPnlPerMarket": _difference(
            _safe_get(sm, "pnl", "makerMeanPerMarket"),
            _safe_get(om, "pnl", "makerMeanPerMarket"),
        ),
        "combinedMeanPnlPerMarket": _difference(
            _safe_get(sm, "pnl", "combinedMeanPerMarket"),
            _safe_get(om, "pnl", "combinedMeanPerMarket"),
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Reconstruct signed Target Maker/Taker outcome inventory from official BID/ASK fill legs, "
            "then classify Taker parents as ADD/BALANCE/FLIP and test whether Taker repairs Maker losses."
        )
    )
    parser.add_argument("--target-db", type=Path, default=DEFAULT_TARGET_DB)
    parser.add_argument("--public-dataset", type=Path, default=DEFAULT_PUBLIC_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--actions-csv", type=Path, default=DEFAULT_ACTIONS_CSV)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--special-start", default=DEFAULT_SPECIAL_START)
    parser.add_argument("--max-phase-lag-ms", type=int, default=DEFAULT_MAX_PHASE_LAG_MS)
    args = parser.parse_args()

    asset = str(args.asset).upper().strip()
    if asset not in {"BTC", "ETH"}:
        raise SystemExit("--asset must be BTC or ETH")
    special_start_ms = _epoch_ms(args.special_start)
    max_phase_lag_ms = max(0, int(args.max_phase_lag_ms))

    print(REPORT_VERSION, flush=True)
    print("[1/5] Load official signed Maker/Taker fill legs...", flush=True)
    db = _connect_ro(args.target_db)
    try:
        events, event_meta = _load_official_events(db, asset=asset)
        if not events:
            raise SystemExit(f"no official {asset} Maker/Taker fill legs")
        parents = _build_parents(events)
        parent_audit = _parent_table_audit(db, parents, asset)
        market_results = _load_market_results(db, asset)
    finally:
        db.close()
    print(
        f"      fillLegs={len(events):,} parents={len(parents):,} markets={len({int(r['market_id']) for r in events}):,}",
        flush=True,
    )

    print("[2/5] Resolve ordinary vs special research cohorts + phase as-of index...", flush=True)
    cohort, phase_index, public_meta = _load_public_cohorts(
        args.public_dataset,
        special_start_ms=special_start_ms,
    )
    print(
        f"      ordinaryMarkets={public_meta['ordinaryMarkets']:,} specialMarkets={public_meta['specialMarkets']:,}",
        flush=True,
    )

    print("[3/5] Replay signed inventory at fill-leg resolution and classify Taker parent semantics...", flush=True)
    actions, market_payloads = _replay(
        events,
        parents,
        cohort=cohort,
        phase_index=phase_index,
        market_results=market_results,
        max_phase_lag_ms=max_phase_lag_ms,
    )
    _write_csv(args.actions_csv, actions)
    print(f"      Taker parent actions={len(actions):,}", flush=True)

    print("[4/5] Compare ordinary vs special lifecycle + Maker/Taker PnL...", flush=True)
    segments: dict[str, Any] = {}
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        event_rows = [row for row in actions if str(row["regime"]) == regime]
        markets = [row for row in market_payloads.values() if str(row["regime"]) == regime]
        segments[regime] = {
            "events": _event_summary(event_rows),
            "markets": _market_summary(markets),
        }

    special_delta = _special_minus_ordinary(
        segments["ORDINARY_PRE_SPECIAL"],
        segments["SPECIAL"],
    )

    cohort_market_ids = set(cohort)
    official_market_ids = {int(row["market_id"]) for row in events}
    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Test the inventory-lifecycle hypothesis that later Target Taker actions are partly position management "
            "of Maker inventory, with the 2026-08-16 special regime used as a stress-test microscope."
        ),
        "inventorySemantics": {
            "source": "official wallet_shadow_target_events",
            "signedOutcomePosition": "BID adds shares; ASK subtracts shares for the stated UP/DOWN outcome",
            "directionalDelta": "UP_position - DOWN_position",
            "parentActionUnit": "order-hash parent reconstructed from official fill legs",
            "stateBoundary": (
                "state-before is captured immediately before the parent's first observed fill leg; classification uses "
                "a counterfactual application of only that parent's total signed shares so overlapping parents do not "
                "get falsely attributed to the current action"
            ),
            "fillTimeline": "inventory itself is replayed at individual official fill-leg timestamps",
            "primarySemanticHierarchy": "FLIP > BALANCE > ADD > NEUTRAL; pair completion and gross reduction are orthogonal flags",
        },
        "pnlSemantics": {
            "source": "target_market_results",
            "important": "official collector accounting uses filled cashflow plus winner payout and has no explicit fee deduction",
            "combinedIdentityExpected": "net_pnl_usdt = maker_net_pnl_usdt + taker_net_pnl_usdt",
        },
        "specialRegime": {
            "specialStart": args.special_start,
            "specialStartMs": special_start_ms,
            "definition": public_meta["specialDefinition"],
            "important": "SPECIAL is defined only by the frozen public research dataset, not by every later market after the timestamp.",
        },
        "sources": {
            "targetDb": str(args.target_db.expanduser().resolve()),
            "publicDataset": str(args.public_dataset.expanduser().resolve()),
            "actionsCsv": str(args.actions_csv.expanduser().resolve()),
        },
        "coverage": {
            "officialEvents": event_meta,
            "derivedParents": len(parents),
            "derivedMakerParents": sum(str(row["role"]) == "MAKER" for row in parents),
            "derivedTakerParents": sum(str(row["role"]) == "TAKER" for row in parents),
            "parentTableAudit": parent_audit,
            "publicResearch": public_meta,
            "officialMarketsInPublicCohort": len(official_market_ids & cohort_market_ids),
            "officialMarketsOutsidePublicCohort": len(official_market_ids - cohort_market_ids),
            "settledMarketResults": len(market_results),
            "classifiedTakerActions": len(actions),
            "classifiedTakerActionsInPrimaryCohorts": sum(str(row["regime"]) in {"ORDINARY_PRE_SPECIAL", "SPECIAL"} for row in actions),
            "phaseKnownRate": _rate(sum(str(row["phase"]) in {"OPEN", "MID", "TAIL"} for row in actions), len(actions)),
        },
        "segments": segments,
        "specialMinusOrdinary": special_delta,
        "interpretationGuardrails": [
            "BALANCE/FLIP describes signed inventory effect, not the wallet's private intent.",
            "A Taker action can both increase paired-long coverage and reduce directional imbalance; orthogonal flags preserve this overlap.",
            "Special-regime differences are descriptive stress-test evidence and must not be promoted as ordinary-market rules without new ordinary holdout validation.",
            "PnL is the official collector's no-explicit-fee accounting and should not be compared directly with fee-inclusive paper strategy PnL without adjustment.",
        ],
    }
    _write_json(args.report, report)

    print("[5/5] Summary", flush=True)
    for regime in ("ORDINARY_PRE_SPECIAL", "SPECIAL"):
        ev = segments[regime]["events"]
        mk = segments[regime]["markets"]
        print(
            f"  {regime}: actions={ev.get('takerActions', 0):,} "
            f"balance={ev.get('classificationRates', {}).get('BALANCE')} "
            f"opposeMaker={ev.get('makerRelationRates', {}).get('OPPOSE_MAKER_HEAVY')} "
            f"makerPnl={mk.get('pnl', {}).get('makerTotal')} "
            f"takerPnl={mk.get('pnl', {}).get('takerTotal')} "
            f"combinedPnl={mk.get('pnl', {}).get('combinedTotal')} "
            f"rescueRate={mk.get('makerLossRescueRate')}",
            flush=True,
        )
    print(f"Report: {args.report.expanduser().resolve()}", flush=True)
    print(f"Actions CSV: {args.actions_csv.expanduser().resolve()}", flush=True)
    print("No Maker/Taker lifecycle rule was promoted to paper or live trading.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
