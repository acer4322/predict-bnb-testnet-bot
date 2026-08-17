from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import analyze_target_controller_parameter_extraction_v1 as core

ROOT = Path(__file__).resolve().parents[1]
TAIPEI = ZoneInfo("Asia/Taipei")
TARGET_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"
REPORT_VERSION = "TARGET_CONTROLLER_COMPLETE_HISTORY_V2_VERSION_AWARE_BURSTS"
DEFAULT_LEGACY_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_OFFICIAL_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_CUTOVER = "2026-08-15T00:00:00+08:00"
EPS = 1e-9

BURST_FIELDS = [
    "market_id", "segment_id", "source_version", "burst_id", "burst_index_market",
    "first_event_ms", "last_event_ms", "duration_ms", "parent_count", "mixed_sides",
    "clean_side", "bid_parent_count", "ask_parent_count", "shares", "notional_usdt", "vwap",
    "min_price", "max_price", "pre_risk_deficit", "pre_abs_payoff_gap",
    "pre_maker_abs_payoff_gap", "pre_worst_case_pnl", "post_risk_deficit",
    "post_abs_payoff_gap", "post_worst_case_pnl", "delta_worst_case_pnl",
    "risk_deficit_reduction", "abs_gap_reduction", "portfolio_effect", "repair_fraction",
    "share_to_gap_ratio", "cash_cost", "worst_case_gain_per_cost", "cheap_lock_candidate",
    "expensive_correction_candidate",
]
TRANSITION_FIELDS = [
    "market_id", "segment_id", "source_version", "maker_parent_id", "maker_completed_ms",
    "risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap", "worst_case_pnl",
    "next_actor", "next_transition", "next_delay_ms", "next_parent_id", "next_burst_id",
    "next_effect", "next_shares", "next_notional_usdt",
]
MARKET_FIELDS = [
    "market_id", "segment_id", "source_version", "valid_lifecycle", "invalid_reason",
    "first_event_ms", "last_event_ms", "maker_parents", "taker_parents", "taker_bursts",
    "final_risk_deficit", "final_abs_payoff_gap", "final_worst_case_pnl",
]


def _parse_ms(text: str) -> int:
    dt = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TAIPEI)
    return int(dt.timestamp() * 1000)


def _day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TAIPEI).date().isoformat()


def _fmt(ms: int | None) -> str | None:
    if ms is None:
        return None
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(TAIPEI).isoformat()


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    tmp.replace(path)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_source(path: Path, source: str, asset: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    db = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    try:
        table = "wallet_shadow_target_events"
        cols = {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}
        required = {"leg_id", "wallet", "asset", "market_id", "role", "side", "quote_type", "order_hash", "event_ms", "price", "shares"}
        missing = sorted(required - cols)
        if missing:
            raise RuntimeError(f"{resolved}: {table} missing columns: {', '.join(missing)}")
        rows: list[dict[str, Any]] = []
        sql = """
            SELECT leg_id,wallet,asset,market_id,role,side,quote_type,order_hash,event_ms,price,shares
              FROM wallet_shadow_target_events
             WHERE asset=? AND role IN ('MAKER','TAKER') AND side IN ('UP','DOWN')
                   AND quote_type IN ('BID','ASK') AND shares>0
             ORDER BY event_ms,leg_id
        """
        for raw in db.execute(sql, (asset,)):
            row = dict(raw)
            row["wallet"] = str(row.get("wallet") or "").lower()
            row["market_id"] = int(row["market_id"])
            row["event_ms"] = int(row["event_ms"])
            row["price"] = float(row["price"])
            row["shares"] = float(row["shares"])
            row["source_version"] = source
            rows.append(row)
        return rows, {
            "path": str(resolved), "sourceVersion": source, "rows": len(rows),
            "markets": len({r['market_id'] for r in rows}),
            "firstEventMs": min((r['event_ms'] for r in rows), default=None),
            "lastEventMs": max((r['event_ms'] for r in rows), default=None),
        }
    finally:
        db.close()


def _stitch(legacy: list[dict[str, Any]], official: list[dict[str, Any]], cutoff_ms: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    chosen = [r for r in legacy if r["event_ms"] < cutoff_ms] + [r for r in official if r["event_ms"] >= cutoff_ms]
    seen: set[tuple[str, str, int]] = set()
    output: list[dict[str, Any]] = []
    duplicate = wrong_wallet = 0
    for row in sorted(chosen, key=lambda r: (r["event_ms"], r["source_version"], str(r["leg_id"]))):
        if row["wallet"] and row["wallet"] != TARGET_WALLET:
            wrong_wallet += 1
            continue
        key = (row["source_version"], str(row["leg_id"]), row["market_id"])
        if key in seen:
            duplicate += 1
            continue
        seen.add(key)
        output.append(row)
    return output, {
        "cutoverMs": cutoff_ms, "cutoverTaipei": _fmt(cutoff_ms),
        "policy": "event_ms < cutover => LEGACY only; event_ms >= cutover => OFFICIAL only; no post-cutover legacy fallback",
        "selectedRows": len(output), "legacyRowsSelected": sum(r["source_version"] == "LEGACY" for r in output),
        "officialRowsSelected": sum(r["source_version"] == "OFFICIAL" for r in output),
        "duplicateRowsRemoved": duplicate, "wrongWalletRowsExcluded": wrong_wallet,
    }


def _assign_segments(events: list[dict[str, Any]], gap_ms: int) -> tuple[dict[tuple[str, str], int], list[dict[str, Any]]]:
    by_source: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in events:
        by_source[row["source_version"]].append(row)
    mapping: dict[tuple[str, str], int] = {}
    gaps: list[dict[str, Any]] = []
    segment = 0
    for source in ("LEGACY", "OFFICIAL"):
        rows = sorted(by_source.get(source, []), key=lambda r: (r["event_ms"], str(r["leg_id"])))
        if not rows:
            continue
        segment += 1
        prev: int | None = None
        for row in rows:
            now = row["event_ms"]
            if prev is not None and now - prev >= gap_ms:
                gaps.append({
                    "sourceVersion": source, "gapStartMs": prev, "gapEndMs": now,
                    "gapMinutes": (now - prev) / 60000.0, "gapStartTaipei": _fmt(prev), "gapEndTaipei": _fmt(now),
                })
                segment += 1
            mapping[(source, str(row["leg_id"]))] = segment
            prev = now
    return mapping, gaps


def _parent_id(row: dict[str, Any]) -> str:
    identity = str(row.get("order_hash") or row["leg_id"])
    return f"{row['source_version']}:{row['asset']}:{row['role']}:{identity}:{row['side']}:{row['quote_type']}"


def _build_parents(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        pid = _parent_id(event)
        event["parent_id"] = pid
        grouped[pid].append(event)
    parents: list[dict[str, Any]] = []
    for pid, legs in grouped.items():
        legs.sort(key=lambda r: (r["event_ms"], str(r["leg_id"])))
        first = legs[0]
        shares = sum(r["shares"] for r in legs)
        notional = sum(r["price"] * r["shares"] for r in legs)
        parents.append({
            "parent_id": pid, "source_version": first["source_version"], "market_id": first["market_id"],
            "role": str(first["role"]).upper(), "side": str(first["side"]).upper(),
            "quote_type": str(first["quote_type"]).upper(), "first_event_ms": legs[0]["event_ms"],
            "last_event_ms": legs[-1]["event_ms"], "shares": shares,
            "average_price": notional / shares if shares else 0.0, "notional_usdt": notional,
        })
    return sorted(parents, key=lambda r: (r["first_event_ms"], r["market_id"], r["parent_id"]))


def _market_segment(events: list[dict[str, Any]], mapping: dict[tuple[str, str], int]) -> tuple[bool, int | None, str, str]:
    sources = {r["source_version"] for r in events}
    segments = {mapping.get((r["source_version"], str(r["leg_id"]))) for r in events}
    segments.discard(None)
    if len(sources) != 1:
        return False, None, "MIXED", "market crosses source-version boundary"
    source = next(iter(sources))
    if len(segments) != 1:
        return False, None, source, "market crosses observed-activity hard gap"
    return True, int(next(iter(segments))), source, ""


def _burst_groups(parents: list[dict[str, Any]], idle_gap_ms: int, cap_ms: int) -> list[list[dict[str, Any]]]:
    groups: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    onset = prev_end = 0
    for parent in parents:
        if parent["role"] != "TAKER":
            if current:
                groups.append(current)
                current = []
            continue
        first, last = parent["first_event_ms"], parent["last_event_ms"]
        if current and first - prev_end <= idle_gap_ms and first - onset <= cap_ms:
            current.append(parent)
        else:
            if current:
                groups.append(current)
            current = [parent]
            onset = first
        prev_end = last
    if current:
        groups.append(current)
    return groups


def _replay(market_id: int, parents: list[dict[str, Any]], segment_id: int, source: str, idle_gap_ms: int, cap_ms: int) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    parents = sorted(parents, key=lambda r: (r["first_event_ms"], r["last_event_ms"], r["parent_id"]))
    start_times = [int(r["first_event_ms"]) for r in parents]
    state = core.PortfolioState()
    before_state: dict[str, core.PortfolioState] = {}
    after_state: dict[str, core.PortfolioState] = {}
    for parent in parents:
        before_state[parent["parent_id"]] = state.copy()
        core._apply_leg(state, parent["role"], parent["side"], parent["quote_type"], parent["shares"], parent["average_price"])
        after_state[parent["parent_id"]] = state.copy()

    groups = _burst_groups(parents, idle_gap_ms, cap_ms)
    bursts: list[dict[str, Any]] = []
    burst_by_parent: dict[str, dict[str, Any]] = {}
    for index, group in enumerate(groups, 1):
        first, last = group[0], group[-1]
        pre_state = before_state[first["parent_id"]].copy()
        post_state = pre_state.copy()
        for parent in group:
            core._apply_leg(post_state, parent["role"], parent["side"], parent["quote_type"], parent["shares"], parent["average_price"])
        pre, post = core._portfolio_metrics(pre_state), core._portfolio_metrics(post_state)
        effect = core._portfolio_effect(pre, post)
        shares = sum(p["shares"] for p in group)
        notional = sum(p["notional_usdt"] for p in group)
        prices = [p["average_price"] for p in group]
        sides = {p["side"] for p in group}
        cash_cost = pre["cash"] - post["cash"]
        wc_gain = post["worst_case_pnl"] - pre["worst_case_pnl"]
        gap_red = pre["abs_payoff_gap"] - post["abs_payoff_gap"]
        correction = effect in {"RISK_REDUCING", "TAIL_IMPROVING", "GAP_REDUCING_EXPENSIVE"}
        bid_only = all(p["quote_type"] == "BID" for p in group)
        row = {
            "market_id": market_id, "segment_id": segment_id, "source_version": source,
            "burst_id": f"{market_id}:{segment_id}:B{index}", "burst_index_market": index,
            "first_event_ms": first["first_event_ms"], "last_event_ms": last["last_event_ms"],
            "duration_ms": last["last_event_ms"] - first["first_event_ms"], "parent_count": len(group),
            "mixed_sides": int(len(sides) != 1), "clean_side": next(iter(sides)) if len(sides) == 1 else "",
            "bid_parent_count": sum(p["quote_type"] == "BID" for p in group),
            "ask_parent_count": sum(p["quote_type"] == "ASK" for p in group),
            "shares": shares, "notional_usdt": notional, "vwap": notional / shares if shares else 0.0,
            "min_price": min(prices), "max_price": max(prices),
            "pre_risk_deficit": pre["risk_deficit"], "pre_abs_payoff_gap": pre["abs_payoff_gap"],
            "pre_maker_abs_payoff_gap": pre["maker_abs_payoff_gap"], "pre_worst_case_pnl": pre["worst_case_pnl"],
            "post_risk_deficit": post["risk_deficit"], "post_abs_payoff_gap": post["abs_payoff_gap"],
            "post_worst_case_pnl": post["worst_case_pnl"], "delta_worst_case_pnl": wc_gain,
            "risk_deficit_reduction": pre["risk_deficit"] - post["risk_deficit"], "abs_gap_reduction": gap_red,
            "portfolio_effect": effect, "repair_fraction": max(0.0, gap_red) / pre["abs_payoff_gap"] if pre["abs_payoff_gap"] > EPS else 0.0,
            "share_to_gap_ratio": shares / pre["abs_payoff_gap"] if pre["abs_payoff_gap"] > EPS else "",
            "cash_cost": cash_cost, "worst_case_gain_per_cost": wc_gain / cash_cost if cash_cost > EPS else "",
            "cheap_lock_candidate": int(correction and bid_only and max(prices) < 0.10),
            "expensive_correction_candidate": int(correction and bid_only and min(prices) > 0.90),
        }
        bursts.append(row)
        for parent in group:
            burst_by_parent[parent["parent_id"]] = row

    transitions: list[dict[str, Any]] = []
    for parent in parents:
        if parent["role"] != "MAKER":
            continue
        metrics = core._portfolio_metrics(after_state[parent["parent_id"]])
        completed_ms = int(parent["last_event_ms"])
        next_index = bisect.bisect_right(start_times, completed_ms)
        future = parents[next_index] if next_index < len(parents) else None
        row = {
            "market_id": market_id, "segment_id": segment_id, "source_version": source,
            "maker_parent_id": parent["parent_id"], "maker_completed_ms": completed_ms,
            "risk_deficit": metrics["risk_deficit"], "abs_payoff_gap": metrics["abs_payoff_gap"],
            "maker_abs_payoff_gap": metrics["maker_abs_payoff_gap"], "worst_case_pnl": metrics["worst_case_pnl"],
            "next_actor": "NONE", "next_transition": "END", "next_delay_ms": "", "next_parent_id": "",
            "next_burst_id": "", "next_effect": "", "next_shares": "", "next_notional_usdt": "",
        }
        if future is not None:
            row["next_actor"] = future["role"]
            row["next_delay_ms"] = future["first_event_ms"] - completed_ms
            row["next_parent_id"] = future["parent_id"]
            if future["role"] == "TAKER":
                burst = burst_by_parent[future["parent_id"]]
                row["next_burst_id"] = burst["burst_id"]
                row["next_effect"] = burst["portfolio_effect"]
                row["next_shares"] = burst["shares"]
                row["next_notional_usdt"] = burst["notional_usdt"]
                row["next_transition"] = "TAKER_BURST_" + burst["portfolio_effect"]
            else:
                signed = core._signed_shares(future["quote_type"], future["shares"])
                direction = signed if future["side"] == "UP" else -signed
                after_gap = metrics["payoff_gap"] + direction
                action = "REPAIR" if abs(after_gap) < abs(metrics["payoff_gap"]) - EPS else "ADD" if abs(after_gap) > abs(metrics["payoff_gap"]) + EPS else "NEUTRAL"
                row["next_effect"] = action
                row["next_shares"] = future["shares"]
                row["next_notional_usdt"] = future["notional_usdt"]
                row["next_transition"] = "MAKER_CONTINUE_" + action
        transitions.append(row)

    final = core._portfolio_metrics(state)
    market_row = {
        "market_id": market_id, "segment_id": segment_id, "source_version": source, "valid_lifecycle": 1,
        "invalid_reason": "", "first_event_ms": parents[0]["first_event_ms"], "last_event_ms": parents[-1]["last_event_ms"],
        "maker_parents": sum(p["role"] == "MAKER" for p in parents), "taker_parents": sum(p["role"] == "TAKER" for p in parents),
        "taker_bursts": len(bursts), "final_risk_deficit": final["risk_deficit"],
        "final_abs_payoff_gap": final["abs_payoff_gap"], "final_worst_case_pnl": final["worst_case_pnl"],
    }
    return bursts, transitions, market_row


def _burst_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"bursts": 0}
    effects = Counter(r["portfolio_effect"] for r in rows)
    risk = [r for r in rows if r["portfolio_effect"] in {"RISK_REDUCING", "TAIL_IMPROVING", "GAP_REDUCING_EXPENSIVE"}]
    cheap = [r for r in rows if int(r["cheap_lock_candidate"]) == 1]
    expensive = [r for r in rows if int(r["expensive_correction_candidate"]) == 1]
    rx = [float(r["pre_risk_deficit"]) for r in risk]
    gx = [float(r["pre_abs_payoff_gap"]) for r in risk]
    sy = [float(r["shares"]) for r in risk]
    return {
        "bursts": len(rows), "markets": len({r['market_id'] for r in rows}),
        "parents": sum(int(r["parent_count"]) for r in rows),
        "parentsPerBurst": sum(int(r["parent_count"]) for r in rows) / len(rows),
        "mixedSideRate": sum(int(r["mixed_sides"]) for r in rows) / len(rows),
        "portfolioEffects": dict(effects), "portfolioEffectRates": {k: v / len(rows) for k, v in effects.items()},
        "shares": core._distribution(float(r["shares"]) for r in rows),
        "durationMs": core._distribution(float(r["duration_ms"]) for r in rows),
        "riskReducing": {
            "bursts": len(risk), "preRiskDeficit": core._distribution(rx),
            "preAbsPayoffGap": core._distribution(gx), "shares": core._distribution(sy),
            "riskDeficitReduction": core._distribution(float(r["risk_deficit_reduction"]) for r in risk),
            "absGapReduction": core._distribution(float(r["abs_gap_reduction"]) for r in risk),
        },
        "sizeController": {
            "burstSharesVsPreRiskDeficit": core._ols(rx, sy),
            "burstSharesVsPreAbsPayoffGap": core._ols(gx, sy),
            "shareToGapRatio": core._distribution(float(r["share_to_gap_ratio"]) for r in risk if r["share_to_gap_ratio"] != ""),
        },
        "cheapPayoffLock": {
            "bursts": len(cheap), "shares": core._distribution(float(r["shares"]) for r in cheap),
            "preRiskDeficit": core._distribution(float(r["pre_risk_deficit"]) for r in cheap),
            "worstCaseGainPerCost": core._distribution(float(r["worst_case_gain_per_cost"]) for r in cheap if r["worst_case_gain_per_cost"] != ""),
        },
        "expensiveCorrection": {
            "bursts": len(expensive), "shares": core._distribution(float(r["shares"]) for r in expensive),
            "preRiskDeficit": core._distribution(float(r["pre_risk_deficit"]) for r in expensive),
            "deltaWorstCasePnl": core._distribution(float(r["delta_worst_case_pnl"]) for r in expensive),
        },
    }


def _transition_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"states": 0}
    taker = [r for r in rows if r["next_actor"] == "TAKER"]
    maker = [r for r in rows if r["next_actor"] == "MAKER"]
    return {
        "states": len(rows), "markets": len({r['market_id'] for r in rows}),
        "takerHandoffRate": len(taker) / len(rows), "makerContinueRate": len(maker) / len(rows),
        "nextDelayMs": core._distribution(float(r["next_delay_ms"]) for r in rows if r["next_delay_ms"] != ""),
        "takerNext": {
            "states": len(taker), "riskDeficit": core._distribution(float(r["risk_deficit"]) for r in taker),
            "absPayoffGap": core._distribution(float(r["abs_payoff_gap"]) for r in taker),
            "makerAbsPayoffGap": core._distribution(float(r["maker_abs_payoff_gap"]) for r in taker),
        },
        "noTakerNext": {
            "states": len(rows) - len(taker),
            "riskDeficit": core._distribution(float(r["risk_deficit"]) for r in rows if r["next_actor"] != "TAKER"),
            "absPayoffGap": core._distribution(float(r["abs_payoff_gap"]) for r in rows if r["next_actor"] != "TAKER"),
            "makerAbsPayoffGap": core._distribution(float(r["maker_abs_payoff_gap"]) for r in rows if r["next_actor"] != "TAKER"),
        },
    }


def _surface(rows: list[dict[str, Any]], feature: str, bins: int = 5) -> list[dict[str, Any]]:
    ordered = sorted((float(r[feature]), r) for r in rows if math.isfinite(float(r[feature])))
    if not ordered:
        return []
    out: list[dict[str, Any]] = []
    for i in range(bins):
        lo, hi = len(ordered) * i // bins, len(ordered) * (i + 1) // bins
        chunk = [r for _, r in ordered[lo:hi]]
        if not chunk:
            continue
        out.append({
            "bin": i + 1, "rows": len(chunk), "min": ordered[lo][0], "max": ordered[hi - 1][0],
            "median": statistics.median(float(r[feature]) for r in chunk),
            "takerHandoffRate": sum(r["next_actor"] == "TAKER" for r in chunk) / len(chunk),
        })
    return out


def _group(bursts: list[dict[str, Any]], transitions: list[dict[str, Any]], key_fn) -> dict[str, Any]:
    bg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    tg: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in bursts:
        bg[str(key_fn(r))].append(r)
    for r in transitions:
        tg[str(key_fn(r))].append(r)
    return {k: {"bursts": _burst_summary(bg.get(k, [])), "transitions": _transition_summary(tg.get(k, []))} for k in sorted(set(bg) | set(tg))}


def main() -> int:
    parser = argparse.ArgumentParser(description="Version-aware complete-history Target controller + Taker burst audit")
    parser.add_argument("--legacy-db", type=Path, default=DEFAULT_LEGACY_DB)
    parser.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_DB)
    parser.add_argument("--cutover", default=DEFAULT_CUTOVER)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--gap-minutes", type=float, default=30.0)
    parser.add_argument("--idle-gap-ms", type=int, default=1000)
    parser.add_argument("--burst-cap-ms", type=int, default=3000)
    parser.add_argument("--report", type=Path, default=ROOT / "data/research/target_controller_complete_history_v2_report.json")
    parser.add_argument("--bursts", type=Path, default=ROOT / "data/research/target_controller_complete_history_v2_bursts.csv")
    parser.add_argument("--transitions", type=Path, default=ROOT / "data/research/target_controller_complete_history_v2_transitions.csv")
    parser.add_argument("--markets", type=Path, default=ROOT / "data/research/target_controller_complete_history_v2_markets.csv")
    args = parser.parse_args()

    cutoff_ms = _parse_ms(args.cutover)
    legacy, legacy_audit = _load_source(args.legacy_db, "LEGACY", str(args.asset).upper())
    official, official_audit = _load_source(args.official_db, "OFFICIAL", str(args.asset).upper())
    events, stitch = _stitch(legacy, official, cutoff_ms)
    if not events:
        raise SystemExit("no selected events after version policy")
    mapping, gaps = _assign_segments(events, max(1, int(args.gap_minutes * 60000)))
    parents = _build_parents(events)
    events_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    parents_by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for r in events:
        events_by_market[r["market_id"]].append(r)
    for r in parents:
        parents_by_market[r["market_id"]].append(r)

    bursts: list[dict[str, Any]] = []
    transitions: list[dict[str, Any]] = []
    markets: list[dict[str, Any]] = []
    for market_id in sorted(events_by_market):
        market_events = events_by_market[market_id]
        valid, segment_id, source, reason = _market_segment(market_events, mapping)
        if not valid or segment_id is None:
            markets.append({
                "market_id": market_id, "segment_id": "", "source_version": source, "valid_lifecycle": 0,
                "invalid_reason": reason, "first_event_ms": min(r["event_ms"] for r in market_events),
                "last_event_ms": max(r["event_ms"] for r in market_events), "maker_parents": 0, "taker_parents": 0,
                "taker_bursts": 0, "final_risk_deficit": "", "final_abs_payoff_gap": "", "final_worst_case_pnl": "",
            })
            continue
        b, t, m = _replay(market_id, parents_by_market[market_id], segment_id, source, max(0, args.idle_gap_ms), max(0, args.burst_cap_ms))
        bursts.extend(b)
        transitions.extend(t)
        markets.append(m)

    bursts.sort(key=lambda r: (r["first_event_ms"], r["market_id"], r["burst_id"]))
    transitions.sort(key=lambda r: (r["maker_completed_ms"], r["market_id"], r["maker_parent_id"]))
    markets.sort(key=lambda r: (r["first_event_ms"], r["market_id"]))
    _write_csv(args.bursts, bursts, BURST_FIELDS)
    _write_csv(args.transitions, transitions, TRANSITION_FIELDS)
    _write_csv(args.markets, markets, MARKET_FIELDS)

    report = {
        "reportVersion": REPORT_VERSION, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True, "noModelFit": True, "noStrategyPromotion": True,
        "purpose": "Infer a stable inventory-aware Decision/Portfolio Controller from all complete Target lifecycle segments while respecting collector-version boundaries and irrecoverable gaps.",
        "sourcePolicy": {
            "cutover": args.cutover, "cutoverMs": cutoff_ms,
            "beforeCutover": "LEGACY predict_wallet_shadow.db only",
            "atOrAfterCutover": "OFFICIAL target_wallet_official_v1.db only",
            "postCutoverLegacyFallback": False,
            "gapPolicy": f"Observed source-local inactivity >= {args.gap_minutes:g} minutes starts a hard lifecycle segment; no interpolation or inferred fills.",
        },
        "sources": {"legacy": legacy_audit, "official": official_audit, "stitch": stitch},
        "burstDefinition": {
            "idleGapMs": max(0, args.idle_gap_ms), "maxDurationFromOnsetMs": max(0, args.burst_cap_ms),
            "makerBreaksBurst": True,
            "rule": "Consecutive Taker parents are one candidate intervention only when no Maker intervenes, adjacent Taker gap <= idleGapMs, and elapsed time from burst onset <= cap.",
        },
        "transitionDefinition": {
            "strictPostMaker": True,
            "rule": "Next actor must have first_event_ms > maker_completed_ms; already-active/overlapping parents are not counted as a new Maker-to-Taker handoff.",
        },
        "coverage": {
            "selectedFillLegs": len(events), "parents": len(parents), "markets": len(markets),
            "validLifecycleMarkets": sum(int(r["valid_lifecycle"]) for r in markets),
            "invalidLifecycleMarkets": sum(not int(r["valid_lifecycle"]) for r in markets),
            "firstEventMs": min(r["event_ms"] for r in events), "lastEventMs": max(r["event_ms"] for r in events),
            "firstEventTaipei": _fmt(min(r["event_ms"] for r in events)), "lastEventTaipei": _fmt(max(r["event_ms"] for r in events)),
            "detectedHardGaps": gaps,
        },
        "overall": {"bursts": _burst_summary(bursts), "makerDecisionStates": _transition_summary(transitions)},
        "bySourceVersion": _group(bursts, transitions, lambda r: r["source_version"]),
        "byTaipeiDay": _group(bursts, transitions, lambda r: _day(int(r["first_event_ms"] if "first_event_ms" in r else r["maker_completed_ms"]))),
        "decisionSurface": {f: _surface(transitions, f) for f in ("risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap")},
        "falsificationTests": {
            "burstSizing": "Burst-level size correlation should materially exceed parent-level correlation if Decision sizing is split across execution parents.",
            "conditionalTolerance": "Monotonic Taker handoff across payoff-gap bins supports an inventory tolerance surface; flat bins weaken a simple gap-trigger controller.",
            "cheapPayoffLock": "<0.10 BID-only correction bursts are judged by worst-case gain per cash cost, not just riskDeficitReduction.",
            "expensiveCorrection": ">0.90 BID-only correction bursts are high-urgency candidates; no emergency label is assumed a priori.",
            "versionStability": "Compare LEGACY/OFFICIAL and day-level summaries before treating any parameter as stable.",
        },
        "caveats": [
            "Public Binance/Predict logic features are intentionally excluded in V2; portfolio/execution controller is tested first.",
            "Gap detection uses observed Target inactivity conservatively and never fabricates missing fills.",
            "Markets crossing source boundary or hard gap are excluded from lifecycle state analysis.",
            "Burst portfolio effects use parent-level observed averages; overlapping fill legs remain an execution-granularity caveat and should be audited before live promotion.",
            "Observed fills do not reveal unfilled/cancelled Maker quotes; explicit fees are not deducted.",
        ],
        "outputs": {"report": str(args.report), "burstsCsv": str(args.bursts), "transitionsCsv": str(args.transitions), "marketsCsv": str(args.markets)},
    }
    _write_json(args.report, report)
    print(REPORT_VERSION)
    print(f"selected legs={len(events):,} parents={len(parents):,} markets={len(markets):,}")
    print(f"bursts={len(bursts):,} maker_states={len(transitions):,} hard_gaps={len(gaps):,}")
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
