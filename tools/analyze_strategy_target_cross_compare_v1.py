from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
import sqlite3
import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SHADOW_DB = ROOT / "data" / "predict_wallet_shadow.db"
DEFAULT_OFFICIAL_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "strategy_target_cross_compare_v1_report.json"
DEFAULT_ROWS = ROOT / "data" / "research" / "strategy_target_cross_compare_v1_rows.csv"
DEFAULT_MARKETS = ROOT / "data" / "research" / "strategy_target_cross_compare_v1_markets.csv"
VERSION = "STRATEGY_TARGET_CROSS_COMPARE_V1"

MAKER_COHORT = "TARGET_MAKER_EBM_V1"
TAKER_COHORT = "TARGET_TAKER_PUBLIC_SIDE_V1_SIDE_ONLY"
MATCH_WINDOW_MS = 5_000
TARGET_WALLET = "0x6da6cb464f92ae7ad4ec3d239c81719cb1d0ae03"
TARGET_BURST_IDLE_MS = 1_000
TARGET_BURST_CAP_MS = 3_000
DECISION_LOOKBACK_MS = 15_000
ABSENCE_FINALIZATION_MS = 10_000


def _finite(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return x if math.isfinite(x) else None


def _quantile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    values = sorted(values)
    if len(values) == 1:
        return values[0]
    p = max(0.0, min(1.0, q)) * (len(values) - 1)
    lo = int(math.floor(p))
    hi = int(math.ceil(p))
    if lo == hi:
        return values[lo]
    w = p - lo
    return values[lo] * (1.0 - w) + values[hi] * w


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        out = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return out if isinstance(out, dict) else {}


def _connect_ro(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    con = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


@dataclass
class Action:
    action_id: str
    owner: str
    channel: str
    market_id: int
    event_ms: int
    side: str
    quote_type: str
    price: float
    shares: float
    source: str
    reason: str = ""
    decision: str = ""
    factors: dict[str, Any] | None = None
    observed_at_ms: int | None = None

    @property
    def signed_direction(self) -> int:
        return 1 if self.side == "UP" else -1 if self.side == "DOWN" else 0


def _load_decision_timeline(con: sqlite3.Connection, table: str, cohort: str) -> dict[int, list[tuple[int, dict[str, Any]]]]:
    out: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    if not _table_exists(con, table):
        return out
    cols = {str(row[1]) for row in con.execute(f"PRAGMA table_info({table})")}
    selected = ["market_id", "decision_at_ms", "decision", "reason", "payload_json"]
    if "side" in cols:
        selected.append("side")
    if "hazard_probability" in cols:
        selected += ["hazard_probability", "level_up_probability", "level_down_probability", "inventory_delta_shares", "seconds_left"]
    if "side_score" in cols:
        selected += ["side_score", "side_confidence", "hazard_score", "hazard_reason", "seconds_left"]
    sql = f"SELECT {','.join(selected)} FROM {table} WHERE cohort=? ORDER BY market_id,decision_at_ms"
    for raw in con.execute(sql, (cohort,)):
        row = dict(raw)
        payload = _json(row.pop("payload_json", None))
        factors = {**payload, "_columns": row}
        out[int(row["market_id"])].append((int(row["decision_at_ms"]), factors))
    return out


def _nearest_decision(timeline: dict[int, list[tuple[int, dict[str, Any]]]], market_id: int, event_ms: int) -> tuple[str, str, dict[str, Any]]:
    rows = timeline.get(int(market_id), [])
    if not rows:
        return "", "", {}
    times = [x[0] for x in rows]
    idx = bisect.bisect_right(times, int(event_ms)) - 1
    if idx < 0 or event_ms - rows[idx][0] > DECISION_LOOKBACK_MS:
        return "", "", {}
    payload = rows[idx][1]
    cols = payload.get("_columns") if isinstance(payload.get("_columns"), dict) else {}
    return str(cols.get("decision") or payload.get("decision") or ""), str(cols.get("reason") or payload.get("reason") or ""), payload


def _load_our_actions(con: sqlite3.Connection) -> tuple[list[Action], dict[str, Any]]:
    maker_decisions = _load_decision_timeline(con, "wallet_maker_ebm_v1_decisions", MAKER_COHORT)
    taker_decisions = _load_decision_timeline(con, "wallet_target_taker_public_side_v1_decisions", TAKER_COHORT)
    actions: list[Action] = []

    if _table_exists(con, "wallet_maker_ebm_v1_orders"):
        for raw in con.execute(
            """SELECT id,market_id,side,price,shares,placed_at_ms,closed_at_ms,close_reason,fill_ask
                 FROM wallet_maker_ebm_v1_orders
                WHERE cohort=? AND status='FILLED' AND closed_at_ms IS NOT NULL
                ORDER BY closed_at_ms,id""",
            (MAKER_COHORT,),
        ):
            row = dict(raw)
            decision, reason, factors = _nearest_decision(maker_decisions, int(row["market_id"]), int(row["placed_at_ms"]))
            actions.append(Action(
                action_id=str(row["id"]), owner="OUR", channel="MAKER", market_id=int(row["market_id"]),
                event_ms=int(row["closed_at_ms"]), side=str(row["side"]).upper(), quote_type="BID",
                price=float(row["price"]), shares=float(row["shares"]), source=MAKER_COHORT,
                reason=reason or str(row.get("close_reason") or ""), decision=decision,
                factors={**factors, "placedAtMs": row.get("placed_at_ms"), "filledAtMs": row.get("closed_at_ms"), "fillAsk": row.get("fill_ask"), "fillProxy": row.get("close_reason")},
            ))

    if _table_exists(con, "wallet_target_taker_public_side_v1_events"):
        for raw in con.execute(
            """SELECT id,market_id,decision_at_ms,side,observed_ask,effective_unit_cost,shares,side_score,hazard_score,payload_json
                 FROM wallet_target_taker_public_side_v1_events
                WHERE cohort=? ORDER BY decision_at_ms,id""",
            (TAKER_COHORT,),
        ):
            row = dict(raw)
            decision, reason, factors = _nearest_decision(taker_decisions, int(row["market_id"]), int(row["decision_at_ms"]))
            price = _finite(row.get("effective_unit_cost")) or _finite(row.get("observed_ask")) or 0.0
            event_payload = _json(row.get("payload_json"))
            actions.append(Action(
                action_id=str(row["id"]), owner="OUR", channel="TAKER", market_id=int(row["market_id"]),
                event_ms=int(row["decision_at_ms"]), side=str(row["side"]).upper(), quote_type="BID",
                price=float(price), shares=float(row["shares"]), source=TAKER_COHORT,
                reason=reason, decision=decision,
                factors={**factors, "event": event_payload, "sideScore": row.get("side_score"), "hazardScore": row.get("hazard_score")},
            ))

    actions.sort(key=lambda a: (a.market_id, a.event_ms, a.action_id))
    return actions, {"makerDecisionMarkets": len(maker_decisions), "takerDecisionMarkets": len(taker_decisions)}


def _legacy_target_actions(con: sqlite3.Connection, market_ids: set[int]) -> list[Action]:
    if not market_ids or not _table_exists(con, "wallet_shadow_target_events"):
        return []
    placeholders = ",".join("?" for _ in market_ids)
    rows = [dict(row) for row in con.execute(
        f"""SELECT leg_id,market_id,role,side,quote_type,order_hash,event_ms,price,shares
              FROM wallet_shadow_target_events WHERE wallet=? AND market_id IN ({placeholders})
             ORDER BY market_id,event_ms,leg_id""",
        (TARGET_WALLET, *tuple(sorted(market_ids))),
    )]
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        identity = str(row.get("order_hash") or row["leg_id"])
        key = (int(row["market_id"]), str(row["role"]).upper(), identity, str(row.get("side") or "").upper(), str(row.get("quote_type") or "").upper())
        grouped[key].append(row)

    out: list[Action] = []
    for key, legs in grouped.items():
        market_id, role, identity, side, quote = key
        legs.sort(key=lambda r: (int(r["event_ms"]), str(r["leg_id"])))
        shares = sum(float(r.get("shares") or 0.0) for r in legs)
        notional = sum(float(r.get("shares") or 0.0) * float(r.get("price") or 0.0) for r in legs)
        if shares <= 0:
            continue
        out.append(Action(
            action_id=f"LEGACY:{market_id}:{role}:{identity}:{side}:{quote}", owner="TARGET", channel=role,
            market_id=market_id, event_ms=int(legs[0]["event_ms"]), side=side, quote_type=quote,
            price=notional / shares, shares=shares, source="LEGACY_TARGET",
        ))
    return out


def _official_target_actions(con: sqlite3.Connection, market_ids: set[int]) -> list[Action]:
    if not market_ids or not _table_exists(con, "target_parent_orders"):
        return []
    placeholders = ",".join("?" for _ in market_ids)
    out: list[Action] = []
    for raw in con.execute(
        f"""SELECT parent_id,market_id,role,side,quote_type,first_event_ms,last_event_ms,average_price,shares
              FROM target_parent_orders WHERE asset='BTC' AND market_id IN ({placeholders})
             ORDER BY market_id,first_event_ms,parent_id""",
        tuple(sorted(market_ids)),
    ):
        row = dict(raw)
        out.append(Action(
            action_id=str(row["parent_id"]), owner="TARGET", channel=str(row["role"]).upper(),
            market_id=int(row["market_id"]), event_ms=int(row["first_event_ms"]), side=str(row["side"]).upper(),
            quote_type=str(row["quote_type"]).upper(), price=float(row["average_price"]), shares=float(row["shares"]),
            source="OFFICIAL_TARGET",
        ))
    return out



def _coalesce_target_actions(actions: list[Action]) -> list[Action]:
    """Collapse execution fragmentation into lifecycle-like action bursts."""
    grouped: list[Action] = []
    by_market: dict[int, list[Action]] = defaultdict(list)
    for action in actions:
        by_market[action.market_id].append(action)
    for market_id, rows in by_market.items():
        rows.sort(key=lambda a: (a.event_ms, a.channel, a.side, a.quote_type, a.action_id))
        current: list[Action] = []
        onset = prev = 0
        signature: tuple[str, str, str] | None = None
        def flush() -> None:
            nonlocal current
            if not current:
                return
            shares = sum(a.shares for a in current)
            notional = sum(a.price * a.shares for a in current)
            first = current[0]
            grouped.append(Action(
                action_id=f"TARGET_BURST:{market_id}:{first.channel}:{first.side}:{first.quote_type}:{first.event_ms}:{len(current)}",
                owner="TARGET", channel=first.channel, market_id=market_id, event_ms=first.event_ms,
                side=first.side, quote_type=first.quote_type, price=notional / shares if shares else first.price,
                shares=shares, source=first.source + "_BURST",
                factors={"parentCount": len(current), "lastEventMs": current[-1].event_ms},
            ))
            current = []
        for action in rows:
            sig = (action.channel, action.side, action.quote_type)
            if current and sig == signature and action.event_ms - prev <= TARGET_BURST_IDLE_MS and action.event_ms - onset <= TARGET_BURST_CAP_MS:
                current.append(action)
            else:
                flush()
                current = [action]
                onset = action.event_ms
                signature = sig
            prev = action.event_ms
        flush()
    grouped.sort(key=lambda a: (a.market_id, a.event_ms, a.action_id))
    return grouped

def _winner_maps(shadow: sqlite3.Connection, official: sqlite3.Connection | None) -> dict[int, str]:
    out: dict[int, str] = {}
    for table in ("wallet_shadow_target_market_results", "wallet_maker_ebm_v1_results", "wallet_target_taker_public_side_v1_results"):
        if not _table_exists(shadow, table):
            continue
        cols = {str(r[1]) for r in shadow.execute(f"PRAGMA table_info({table})")}
        if "winner" not in cols:
            continue
        for raw in shadow.execute(f"SELECT market_id,winner FROM {table} WHERE winner IN ('UP','DOWN')"):
            out[int(raw[0])] = str(raw[1]).upper()
    if official is not None and _table_exists(official, "target_market_results"):
        for raw in official.execute("SELECT market_id,winner FROM target_market_results WHERE asset='BTC' AND winner IN ('UP','DOWN')"):
            out[int(raw[0])] = str(raw[1]).upper()
    return out


def _standalone_contribution(action: Action, winner: str | None) -> float | None:
    if winner not in {"UP", "DOWN"}:
        return None
    payout = action.shares if action.side == winner else 0.0
    if action.quote_type == "BID":
        return payout - action.price * action.shares
    if action.quote_type == "ASK":
        return action.price * action.shares - payout
    return None


def _collector_lag_audit(official: sqlite3.Connection | None) -> dict[str, Any]:
    if official is None or not _table_exists(official, "wallet_shadow_target_events"):
        return {"samples": 0, "absenceFinalizationMs": ABSENCE_FINALIZATION_MS}
    lags: list[float] = []
    for raw in official.execute(
        """SELECT observed_at_ms-event_ms AS lag FROM wallet_shadow_target_events
            WHERE asset='BTC' AND observed_at_ms>=event_ms AND observed_at_ms-event_ms BETWEEN 0 AND 60000"""
    ):
        lag = _finite(raw[0])
        if lag is not None:
            lags.append(lag)
    return {
        "samples": len(lags), "p50Ms": _quantile(lags, 0.50), "p90Ms": _quantile(lags, 0.90),
        "p95Ms": _quantile(lags, 0.95), "p99Ms": _quantile(lags, 0.99),
        "absenceFinalizationMs": ABSENCE_FINALIZATION_MS,
        "policy": "post-hoc matching uses Target event_ms; live OUR_ONLY/TARGET_ONLY classification is not final until absenceFinalizationMs has elapsed",
    }


def _candidate_key(our: Action, target: Action) -> tuple[int, int, int]:
    return (
        0 if our.channel == target.channel else 1,
        0 if our.side == target.side else 1,
        abs(our.event_ms - target.event_ms),
    )


def _classify(our: Action, target: Action | None) -> str:
    if target is None:
        return "OUR_ONLY"
    if our.channel == target.channel and our.side == target.side:
        return "MATCH"
    if our.side == target.side:
        return "CHANNEL_DIFF"
    if our.channel == target.channel:
        return "SIDE_DIFF"
    return "SIDE_CHANNEL_DIFF"


def _flatten_factor_summary(action: Action) -> dict[str, Any]:
    f = action.factors or {}
    cols = f.get("_columns") if isinstance(f.get("_columns"), dict) else {}
    inventory = f.get("inventory") if isinstance(f.get("inventory"), dict) else {}
    hazard = f.get("hazard") if isinstance(f.get("hazard"), dict) else {}
    levels = f.get("levels") if isinstance(f.get("levels"), dict) else {}
    signal = f.get("signal") if isinstance(f.get("signal"), dict) else {}
    return {
        "reason": action.reason,
        "decision": action.decision,
        "secondsLeft": cols.get("seconds_left", f.get("secondsLeft")),
        "hazardProbability": cols.get("hazard_probability", hazard.get("probability")),
        "sideScore": cols.get("side_score", f.get("sideScore")),
        "sideConfidence": cols.get("side_confidence"),
        "inventoryDeltaShares": cols.get("inventory_delta_shares", inventory.get("deltaShares")),
        "levelUpProbability": cols.get("level_up_probability", (levels.get("UP") or {}).get("probability") if isinstance(levels.get("UP"), dict) else None),
        "levelDownProbability": cols.get("level_down_probability", (levels.get("DOWN") or {}).get("probability") if isinstance(levels.get("DOWN"), dict) else None),
        "directionScore": signal.get("directionScore", f.get("directionScore")),
    }


def _latest_our_state(decision_timelines: dict[str, dict[int, list[tuple[int, dict[str, Any]]]]], market_id: int, target_ms: int) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for channel, timeline in decision_timelines.items():
        decision, reason, factors = _nearest_decision(timeline, market_id, target_ms)
        if decision or reason:
            out[channel] = {
                "decision": decision, "reason": reason,
                "factorSummary": _flatten_factor_summary(Action("", "OUR", channel, market_id, target_ms, "", "", 0, 0, "", reason, decision, factors)),
            }
    return out


def _group_reason(rows: list[dict[str, Any]], *, kind: str) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["classification"] != kind:
            continue
        reason = str(row.get("our_reason") or "NO_RECENT_DECISION")
        grouped[reason].append(row)
    out = []
    for reason, items in grouped.items():
        contrib = [float(r["our_contribution_usdt"]) for r in items if r.get("our_contribution_usdt") not in {None, ""}]
        out.append({
            "reason": reason, "actions": len(items), "markets": len({int(r["market_id"]) for r in items}),
            "standaloneContributionUsdt": sum(contrib) if contrib else None,
            "meanContributionUsdt": statistics.fmean(contrib) if contrib else None,
            "positiveRate": sum(x > 0 for x in contrib) / len(contrib) if contrib else None,
            "negativeRate": sum(x < 0 for x in contrib) / len(contrib) if contrib else None,
        })
    out.sort(key=lambda r: (-r["actions"], r["reason"]))
    return out


def _write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow-db", type=Path, default=DEFAULT_SHADOW_DB)
    ap.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_DB)
    ap.add_argument("--match-window-ms", type=int, default=MATCH_WINDOW_MS)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    ap.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    args = ap.parse_args()

    shadow = _connect_ro(args.shadow_db)
    official = _connect_ro(args.official_db) if args.official_db.exists() else None
    try:
        our_actions, decision_audit = _load_our_actions(shadow)
        our_markets = {a.market_id for a in our_actions}
        target_actions = _legacy_target_actions(shadow, our_markets)
        if official is not None:
            target_actions += _official_target_actions(official, our_markets)
        raw_target_actions = list(target_actions)
        target_actions = _coalesce_target_actions(target_actions)
        winners = _winner_maps(shadow, official)
        lag_audit = _collector_lag_audit(official)

        maker_timeline = _load_decision_timeline(shadow, "wallet_maker_ebm_v1_decisions", MAKER_COHORT)
        taker_timeline = _load_decision_timeline(shadow, "wallet_target_taker_public_side_v1_decisions", TAKER_COHORT)
        decision_timelines = {"MAKER": maker_timeline, "TAKER": taker_timeline}

        target_by_market: dict[int, list[Action]] = defaultdict(list)
        target_times: dict[int, list[int]] = defaultdict(list)
        for t in target_actions:
            target_by_market[t.market_id].append(t)
            target_times[t.market_id].append(t.event_ms)

        used_target: set[str] = set()
        rows: list[dict[str, Any]] = []
        for our in our_actions:
            arr = target_by_market.get(our.market_id, [])
            times = target_times.get(our.market_id, [])
            lo = bisect.bisect_left(times, our.event_ms - int(args.match_window_ms))
            hi = bisect.bisect_right(times, our.event_ms + int(args.match_window_ms))
            candidates = [t for t in arr[lo:hi] if t.action_id not in used_target]
            target = min(candidates, key=lambda t: _candidate_key(our, t)) if candidates else None
            if target is not None:
                used_target.add(target.action_id)
            winner = winners.get(our.market_id)
            summary = _flatten_factor_summary(our)
            rows.append({
                "market_id": our.market_id, "winner": winner or "", "classification": _classify(our, target),
                "our_action_id": our.action_id, "our_ms": our.event_ms, "our_channel": our.channel, "our_side": our.side,
                "our_quote_type": our.quote_type, "our_price": our.price, "our_shares": our.shares,
                "our_reason": our.reason, "our_decision": our.decision,
                "our_contribution_usdt": _standalone_contribution(our, winner),
                "target_action_id": target.action_id if target else "", "target_ms": target.event_ms if target else "",
                "target_channel": target.channel if target else "", "target_side": target.side if target else "",
                "target_quote_type": target.quote_type if target else "", "target_price": target.price if target else "",
                "target_shares": target.shares if target else "",
                "target_contribution_usdt": _standalone_contribution(target, winner) if target else None,
                "delta_ms_target_minus_our": target.event_ms - our.event_ms if target else "",
                "factor_summary_json": json.dumps(summary, ensure_ascii=False, separators=(",", ":")),
                "our_factors_json": json.dumps(our.factors or {}, ensure_ascii=False, separators=(",", ":"), default=str),
                "our_state_at_target_json": "",
            })

        # Target actions with no paired OUR action are the most useful missing-factor cases.
        our_by_market: dict[int, list[Action]] = defaultdict(list)
        our_times: dict[int, list[int]] = defaultdict(list)
        for a in our_actions:
            our_by_market[a.market_id].append(a)
            our_times[a.market_id].append(a.event_ms)
        for target in target_actions:
            if target.action_id in used_target:
                continue
            arr = our_by_market.get(target.market_id, [])
            times = our_times.get(target.market_id, [])
            lo = bisect.bisect_left(times, target.event_ms - int(args.match_window_ms))
            hi = bisect.bisect_right(times, target.event_ms + int(args.match_window_ms))
            # Any OUR action in the window would have been consumed by a closer target during greedy matching.
            # Keep these as TARGET_ONLY so one burst does not hide extra Target activity.
            winner = winners.get(target.market_id)
            state = _latest_our_state(decision_timelines, target.market_id, target.event_ms)
            rows.append({
                "market_id": target.market_id, "winner": winner or "", "classification": "TARGET_ONLY",
                "our_action_id": "", "our_ms": "", "our_channel": "", "our_side": "", "our_quote_type": "",
                "our_price": "", "our_shares": "", "our_reason": "", "our_decision": "",
                "our_contribution_usdt": None,
                "target_action_id": target.action_id, "target_ms": target.event_ms,
                "target_channel": target.channel, "target_side": target.side, "target_quote_type": target.quote_type,
                "target_price": target.price, "target_shares": target.shares,
                "target_contribution_usdt": _standalone_contribution(target, winner),
                "delta_ms_target_minus_our": "", "factor_summary_json": "", "our_factors_json": "",
                "our_state_at_target_json": json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=str),
            })

        rows.sort(key=lambda r: (int(r["market_id"]), int(r["our_ms"] or r["target_ms"] or 0), str(r["classification"])))

        by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            by_market[int(row["market_id"])].append(row)
        market_rows: list[dict[str, Any]] = []
        for market_id, items in sorted(by_market.items()):
            our_contrib = sum(float(r["our_contribution_usdt"]) for r in items if r.get("our_contribution_usdt") not in {None, ""})
            target_contrib = sum(float(r["target_contribution_usdt"]) for r in items if r.get("target_contribution_usdt") not in {None, ""})
            counts = Counter(str(r["classification"]) for r in items)
            market_rows.append({
                "market_id": market_id, "winner": winners.get(market_id, ""),
                "our_actions": sum(bool(r.get("our_action_id")) for r in items),
                "target_actions": sum(bool(r.get("target_action_id")) for r in items),
                "match": counts.get("MATCH", 0), "our_only": counts.get("OUR_ONLY", 0),
                "target_only": counts.get("TARGET_ONLY", 0), "channel_diff": counts.get("CHANNEL_DIFF", 0),
                "side_diff": counts.get("SIDE_DIFF", 0), "side_channel_diff": counts.get("SIDE_CHANNEL_DIFF", 0),
                "our_standalone_contribution_usdt": our_contrib,
                "target_standalone_contribution_usdt": target_contrib,
                "our_minus_target_contribution_usdt": our_contrib - target_contrib,
            })

        class_counts = Counter(str(r["classification"]) for r in rows)
        our_only = [r for r in rows if r["classification"] == "OUR_ONLY"]
        target_only = [r for r in rows if r["classification"] == "TARGET_ONLY"]
        our_only_contrib = [float(r["our_contribution_usdt"]) for r in our_only if r.get("our_contribution_usdt") not in {None, ""}]
        target_only_contrib = [float(r["target_contribution_usdt"]) for r in target_only if r.get("target_contribution_usdt") not in {None, ""}]

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "purpose": "Post-hoc synchronized replay of OUR paper Maker/Taker actions against Target observed Maker/Taker parents, with factor and standalone settlement attribution.",
            "caveats": [
                "Current OUR inputs are existing forward Maker EBM and public-side Taker paper cohorts, not yet the newly researched unified portfolio controller.",
                "One-to-one greedy event matching is a V1 diagnostic; burst-level matching is the next refinement for dense Target activity.",
                "standaloneContributionUsdt measures each fill/action versus doing nothing at settlement; it is not a full portfolio counterfactual and ignores explicit fees/rebates unless embedded in effective unit cost.",
                "Target natural API delay is never used as strategy evidence. Post-hoc matching uses true Target event_ms. Live absence is finalized only after the delay guard.",
            ],
            "configuration": {
                "makerCohort": MAKER_COHORT, "takerCohort": TAKER_COHORT,
                "eventMatchWindowMs": int(args.match_window_ms), "decisionLookbackMs": DECISION_LOOKBACK_MS,
                "targetBurstIdleMs": TARGET_BURST_IDLE_MS, "targetBurstCapMs": TARGET_BURST_CAP_MS,
            },
            "collectorLag": lag_audit,
            "coverage": {
                "ourActions": len(our_actions), "ourMarkets": len(our_markets),
                "targetRawParentActionsOnOurMarkets": len(raw_target_actions), "targetActionBurstsOnOurMarkets": len(target_actions), "targetMarkets": len({a.market_id for a in target_actions}),
                "marketsWithWinner": len({m for m in our_markets if m in winners}),
                **decision_audit,
            },
            "alignment": {
                "rows": len(rows), "classCounts": dict(class_counts),
                "matchedOrDivergentPairs": sum(v for k, v in class_counts.items() if k not in {"OUR_ONLY", "TARGET_ONLY"}),
                "ourOnly": len(our_only), "targetOnly": len(target_only),
            },
            "economicAttribution": {
                "ourOnlyStandaloneContributionUsdt": sum(our_only_contrib) if our_only_contrib else None,
                "ourOnlyPositiveRate": sum(x > 0 for x in our_only_contrib) / len(our_only_contrib) if our_only_contrib else None,
                "targetOnlyStandaloneContributionUsdt": sum(target_only_contrib) if target_only_contrib else None,
                "targetOnlyPositiveRate": sum(x > 0 for x in target_only_contrib) / len(target_only_contrib) if target_only_contrib else None,
                "interpretation": "OUR_ONLY positive repeated contribution => OUR_EDGE_CANDIDATE; OUR_ONLY repeated negative contribution => candidate factor/action for removal. TARGET_ONLY positive repeated contribution => missing-factor candidate; TARGET_ONLY negative contribution => omission may be beneficial and should not be copied blindly.",
            },
            "ourOnlyByReason": _group_reason(rows, kind="OUR_ONLY"),
            "outputs": {"rowsCsv": str(args.rows), "marketsCsv": str(args.markets), "reportJson": str(args.report)},
        }

        fields = [
            "market_id", "winner", "classification",
            "our_action_id", "our_ms", "our_channel", "our_side", "our_quote_type", "our_price", "our_shares", "our_reason", "our_decision", "our_contribution_usdt",
            "target_action_id", "target_ms", "target_channel", "target_side", "target_quote_type", "target_price", "target_shares", "target_contribution_usdt",
            "delta_ms_target_minus_our", "factor_summary_json", "our_factors_json", "our_state_at_target_json",
        ]
        _write_csv(args.rows, rows, fields)
        _write_csv(args.markets, market_rows, list(market_rows[0].keys()) if market_rows else ["market_id"])
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        shadow.close()
        if official is not None:
            official.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
