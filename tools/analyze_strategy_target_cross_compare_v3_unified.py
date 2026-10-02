from __future__ import annotations

import argparse
import bisect
import csv
import json
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import analyze_strategy_target_cross_compare_v1 as v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUR_DB = ROOT / "data" / "strategy_target_compare_v1.db"
DEFAULT_OFFICIAL_DB = ROOT / "data" / "target_wallet_official_v1.db"
DEFAULT_REPORT = ROOT / "data" / "research" / "strategy_target_cross_compare_v3_unified_v0_report.json"
DEFAULT_WINDOWS = ROOT / "data" / "research" / "strategy_target_cross_compare_v3_unified_v0_windows.csv"
DEFAULT_MARKETS = ROOT / "data" / "research" / "strategy_target_cross_compare_v3_unified_v0_markets.csv"
VERSION = "STRATEGY_TARGET_CROSS_COMPARE_V3_UNIFIED_V0"
BASE_PREFIX = "UNIFIED_CONTROLLER_PAPER_V1"
MARKET_MS = 300_000
BIN_MS = 5_000


def _json(value: Any) -> dict[str, Any]:
    if not value:
        return {}
    try:
        obj = json.loads(str(value))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["market_id"]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _load_decisions(con: sqlite3.Connection, prefix: str) -> dict[int, list[tuple[int, dict[str, Any]]]]:
    out: dict[int, list[tuple[int, dict[str, Any]]]] = defaultdict(list)
    sql = """
        SELECT decision_id,strategy_version,market_id,decision_ms,source_snapshot_ms,seconds_left,phase,
               desired_portfolio_action,execution_choice,side,size,primary_reason,
               supporting_reasons_json,veto_reasons_json,direction_state_json,portfolio_state_json,
               economics_state_json,arbitration_state_json,public_state_json,payload_json
          FROM our_decisions
         WHERE strategy_version LIKE ?
         ORDER BY market_id,decision_ms,decision_id
    """
    for raw in con.execute(sql, (f"{prefix}%",)):
        row = dict(raw)
        state = {
            "decisionId": row["decision_id"],
            "strategyVersion": row["strategy_version"],
            "decisionMs": int(row["decision_ms"]),
            "sourceSnapshotMs": row["source_snapshot_ms"],
            "secondsLeft": row["seconds_left"],
            "phase": row["phase"],
            "desiredPortfolioAction": row["desired_portfolio_action"],
            "executionChoice": row["execution_choice"],
            "side": row["side"],
            "size": row["size"],
            "primaryReason": row["primary_reason"],
            "supportingReasons": _json(row["supporting_reasons_json"]),
            "vetoReasons": json.loads(row["veto_reasons_json"]) if row["veto_reasons_json"] else [],
            "direction": _json(row["direction_state_json"]),
            "portfolio": _json(row["portfolio_state_json"]),
            "economics": _json(row["economics_state_json"]),
            "arbitration": _json(row["arbitration_state_json"]),
            "public": _json(row["public_state_json"]),
            "payload": _json(row["payload_json"]),
        }
        out[int(row["market_id"])].append((int(row["decision_ms"]), state))
    return out


def _latest_state(timeline: dict[int, list[tuple[int, dict[str, Any]]]], market_id: int, at_ms: int) -> dict[str, Any]:
    rows = timeline.get(int(market_id), [])
    if not rows:
        return {}
    times = [x[0] for x in rows]
    idx = bisect.bisect_right(times, int(at_ms)) - 1
    if idx < 0:
        return {}
    state = dict(rows[idx][1])
    state["stateAgeMs"] = max(0, int(at_ms) - int(rows[idx][0]))
    return state


def _load_our_actions(con: sqlite3.Connection, prefix: str) -> list[v1.Action]:
    actions: list[v1.Action] = []
    sql = """
        SELECT fill_id,strategy_version,market_id,decision_id,order_id,channel,purpose,side,quote_type,
               price,shares,filled_at_ms,decision_state_json,fill_state_json,payload_json
          FROM our_fills
         WHERE strategy_version LIKE ?
         ORDER BY market_id,filled_at_ms,fill_id
    """
    for raw in con.execute(sql, (f"{prefix}%",)):
        row = dict(raw)
        decision_state = _json(row["decision_state_json"])
        fill_state = _json(row["fill_state_json"])
        payload = _json(row["payload_json"])
        actions.append(v1.Action(
            action_id=str(row["fill_id"]),
            owner="OUR",
            channel=str(row["channel"]).upper(),
            market_id=int(row["market_id"]),
            event_ms=int(row["filled_at_ms"]),
            side=str(row["side"]).upper(),
            quote_type=str(row["quote_type"]).upper(),
            price=float(row["price"]),
            shares=float(row["shares"]),
            source=str(row["strategy_version"]),
            reason=str(decision_state.get("primaryReason") or row.get("purpose") or ""),
            decision=str(decision_state.get("executionChoice") or ""),
            factors={
                "purpose": row.get("purpose"),
                "decisionId": row.get("decision_id"),
                "orderId": row.get("order_id"),
                "decisionState": decision_state,
                "fillState": fill_state,
                "payload": payload,
            },
        ))
    return actions


def _market_bounds(official: sqlite3.Connection, our_markets: set[int], finalized_before_ms: int) -> dict[int, tuple[int, int, str]]:
    if not our_markets:
        return {}
    marks = ",".join("?" for _ in our_markets)
    out: dict[int, tuple[int, int, str]] = {}
    for raw in official.execute(
        f"""SELECT market_id,window_end_ms,winner FROM target_markets
              WHERE asset='BTC' AND market_id IN ({marks}) AND window_end_ms<=?
              ORDER BY window_end_ms""",
        (*tuple(sorted(our_markets)), int(finalized_before_ms)),
    ):
        mid = int(raw["market_id"])
        end_ms = int(raw["window_end_ms"])
        winner = str(raw["winner"] or "").upper()
        out[mid] = (end_ms - MARKET_MS, end_ms, winner if winner in {"UP", "DOWN"} else "")
    return out


def _slice(actions: list[v1.Action], times: list[int], start: int, end: int) -> list[v1.Action]:
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_left(times, end)
    return actions[lo:hi]


def _signature(action: v1.Action) -> str:
    return f"{action.channel}:{action.side}:{action.quote_type}"


def _classify(ours: list[v1.Action], target: list[v1.Action]) -> tuple[str, bool, bool, bool]:
    if not ours and not target:
        return "BOTH_IDLE", False, False, False
    if ours and not target:
        return "OUR_ONLY", False, False, False
    if target and not ours:
        return "TARGET_ONLY", False, False, False
    our_sigs = {_signature(a) for a in ours}
    target_sigs = {_signature(a) for a in target}
    exact = bool(our_sigs & target_sigs)
    same_side = any(a.side == b.side for a in ours for b in target)
    same_channel = any(a.channel == b.channel for a in ours for b in target)
    if exact:
        return "BOTH_ACTIVE_SAME", True, same_channel, same_side
    if same_side:
        return "BOTH_ACTIVE_CHANNEL_DIFF", False, same_channel, True
    return "BOTH_ACTIVE_SIDE_DIFF", False, same_channel, False


def _contribution(actions: list[v1.Action], winner: str) -> float | None:
    if winner not in {"UP", "DOWN"} or not actions:
        return None
    vals = [v1._standalone_contribution(a, winner) for a in actions]
    return sum(float(x) for x in vals if x is not None)


def _market_pnl(actions: list[v1.Action], winner: str) -> float | None:
    return _contribution(actions, winner)


def _action_json(actions: list[v1.Action]) -> str:
    return json.dumps([
        {
            "id": a.action_id,
            "channel": a.channel,
            "side": a.side,
            "quoteType": a.quote_type,
            "eventMs": a.event_ms,
            "price": a.price,
            "shares": a.shares,
            "purpose": (a.factors or {}).get("purpose"),
            "source": a.source,
        }
        for a in actions
    ], ensure_ascii=False, separators=(",", ":"), default=str)


def _state_fields(state: dict[str, Any]) -> dict[str, Any]:
    direction = state.get("direction") if isinstance(state.get("direction"), dict) else {}
    portfolio = state.get("portfolio") if isinstance(state.get("portfolio"), dict) else {}
    economics = state.get("economics") if isinstance(state.get("economics"), dict) else {}
    arbitration = state.get("arbitration") if isinstance(state.get("arbitration"), dict) else {}
    return {
        "our_decision": state.get("executionChoice") or "",
        "our_desired_action": state.get("desiredPortfolioAction") or "",
        "our_reason": state.get("primaryReason") or "",
        "our_phase": state.get("phase") or "",
        "our_seconds_left": state.get("secondsLeft") if state.get("secondsLeft") is not None else "",
        "our_state_age_ms": state.get("stateAgeMs") if state.get("stateAgeMs") is not None else "",
        "our_direction": direction.get("side") or "",
        "our_direction_strength": direction.get("strength") if direction.get("strength") is not None else "",
        "our_alignment": portfolio.get("alignment") or "",
        "our_net_shares": portfolio.get("netShares") if portfolio.get("netShares") is not None else "",
        "our_risk_deficit_usdt": portfolio.get("riskDeficitUsdt") if portfolio.get("riskDeficitUsdt") is not None else "",
        "our_worst_case_pnl_usdt": portfolio.get("worstCasePnlUsdt") if portfolio.get("worstCasePnlUsdt") is not None else "",
        "our_repair_side": economics.get("repairSide") or "",
        "our_repair_ask": economics.get("repairAsk") if economics.get("repairAsk") is not None else "",
        "our_maker_decision": arbitration.get("makerDecision") or "",
        "our_maker_reason": arbitration.get("makerReason") or "",
    }


def _group(rows: list[dict[str, Any]], classification: str) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["classification"] != classification:
            continue
        key = (
            str(row.get("our_decision") or "NONE"),
            str(row.get("our_desired_action") or "NONE"),
            str(row.get("our_reason") or "NONE"),
            str(row.get("target_channels") or "NONE"),
        )
        grouped[key].append(row)
    out: list[dict[str, Any]] = []
    for key, xs in grouped.items():
        target_vals = [float(x["target_contribution_usdt"]) for x in xs if x["target_contribution_usdt"] != ""]
        our_vals = [float(x["our_contribution_usdt"]) for x in xs if x["our_contribution_usdt"] != ""]
        out.append({
            "ourDecision": key[0],
            "ourDesiredAction": key[1],
            "ourReason": key[2],
            "targetChannels": key[3],
            "windows": len(xs),
            "markets": len({int(x["market_id"]) for x in xs}),
            "targetContributionUsdt": sum(target_vals) if target_vals else None,
            "targetPositiveRate": sum(v > 0 for v in target_vals) / len(target_vals) if target_vals else None,
            "ourContributionUsdt": sum(our_vals) if our_vals else None,
            "ourPositiveRate": sum(v > 0 for v in our_vals) / len(our_vals) if our_vals else None,
        })
    out.sort(key=lambda r: (-int(r["windows"]), str(r["ourReason"])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--our-db", type=Path, default=DEFAULT_OUR_DB)
    ap.add_argument("--official-db", type=Path, default=DEFAULT_OFFICIAL_DB)
    ap.add_argument("--strategy-prefix", default=BASE_PREFIX)
    ap.add_argument("--bin-ms", type=int, default=BIN_MS)
    ap.add_argument("--absence-guard-ms", type=int, default=max(v1.ABSENCE_FINALIZATION_MS, 15_000))
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--windows", type=Path, default=DEFAULT_WINDOWS)
    ap.add_argument("--markets", type=Path, default=DEFAULT_MARKETS)
    args = ap.parse_args()

    our = v1._connect_ro(args.our_db)
    official = v1._connect_ro(args.official_db)
    try:
        decisions = _load_decisions(our, args.strategy_prefix)
        our_actions = _load_our_actions(our, args.strategy_prefix)
        our_markets = set(decisions)
        finalized_before = int(time.time() * 1000) - max(0, int(args.absence_guard_ms))
        bounds = _market_bounds(official, our_markets, finalized_before)
        markets = sorted(bounds)

        target_actions = v1._official_target_actions(official, set(markets))
        target_actions = v1._coalesce_target_actions(target_actions)
        target_market_pnl: dict[int, float] = {}
        if markets:
            marks = ",".join("?" for _ in markets)
            for raw in official.execute(
                f"SELECT market_id,net_pnl_usdt FROM target_market_results WHERE asset='BTC' AND market_id IN ({marks})",
                tuple(markets),
            ):
                target_market_pnl[int(raw["market_id"])] = float(raw["net_pnl_usdt"])

        our_by: dict[int, list[v1.Action]] = defaultdict(list)
        target_by: dict[int, list[v1.Action]] = defaultdict(list)
        for a in our_actions:
            if a.market_id in bounds:
                our_by[a.market_id].append(a)
        for a in target_actions:
            if a.market_id in bounds:
                target_by[a.market_id].append(a)
        for mid in markets:
            our_by[mid].sort(key=lambda a: (a.event_ms, a.action_id))
            target_by[mid].sort(key=lambda a: (a.event_ms, a.action_id))
        our_times = {mid: [a.event_ms for a in rows] for mid, rows in our_by.items()}
        target_times = {mid: [a.event_ms for a in rows] for mid, rows in target_by.items()}

        rows: list[dict[str, Any]] = []
        market_rows: list[dict[str, Any]] = []
        bin_ms = max(1000, int(args.bin_ms))
        for mid in markets:
            start_ms, end_ms, winner = bounds[mid]
            at = start_ms
            market_class = Counter()
            while at < end_ms:
                nxt = min(end_ms, at + bin_ms)
                ours = _slice(our_by.get(mid, []), our_times.get(mid, []), at, nxt)
                theirs = _slice(target_by.get(mid, []), target_times.get(mid, []), at, nxt)
                classification, exact, same_channel, same_side = _classify(ours, theirs)
                market_class[classification] += 1
                if theirs:
                    state_anchor_ms = min(a.event_ms for a in theirs)
                elif ours:
                    state_anchor_ms = min(a.event_ms for a in ours)
                else:
                    state_anchor_ms = at
                state = _latest_state(decisions, mid, state_anchor_ms)
                row = {
                    "market_id": mid,
                    "winner": winner,
                    "window_start_ms": at,
                    "window_end_ms": nxt,
                    "seconds_left_at_start": max(0.0, (end_ms - at) / 1000.0),
                    "classification": classification,
                    "exact_signature_match": int(exact),
                    "same_channel_any": int(same_channel),
                    "same_side_any": int(same_side),
                    "our_action_count": len(ours),
                    "target_action_count": len(theirs),
                    "our_channels": "|".join(sorted({a.channel for a in ours})),
                    "target_channels": "|".join(sorted({a.channel for a in theirs})),
                    "our_sides": "|".join(sorted({a.side for a in ours})),
                    "target_sides": "|".join(sorted({a.side for a in theirs})),
                    "our_signatures": "|".join(sorted({_signature(a) for a in ours})),
                    "target_signatures": "|".join(sorted({_signature(a) for a in theirs})),
                    "our_contribution_usdt": _contribution(ours, winner) if winner and ours else "",
                    "target_contribution_usdt": _contribution(theirs, winner) if winner and theirs else "",
                    **_state_fields(state),
                    "our_actions_json": _action_json(ours),
                    "target_actions_json": _action_json(theirs),
                    "our_state_json": json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=str),
                }
                rows.append(row)
                at = nxt

            our_market_actions = our_by.get(mid, [])
            target_market_actions = target_by.get(mid, [])
            market_rows.append({
                "market_id": mid,
                "winner": winner,
                "our_actions": len(our_market_actions),
                "target_actions": len(target_market_actions),
                "our_maker_actions": sum(a.channel == "MAKER" for a in our_market_actions),
                "our_taker_actions": sum(a.channel == "TAKER" for a in our_market_actions),
                "target_maker_actions": sum(a.channel == "MAKER" for a in target_market_actions),
                "target_taker_actions": sum(a.channel == "TAKER" for a in target_market_actions),
                "our_market_pnl_usdt": _market_pnl(our_market_actions, winner) if winner else "",
                "target_action_standalone_sum_usdt": _market_pnl(target_market_actions, winner) if winner else "",
                "target_official_net_pnl_usdt": target_market_pnl.get(mid, ""),
                "both_idle_windows": market_class["BOTH_IDLE"],
                "both_active_same_windows": market_class["BOTH_ACTIVE_SAME"],
                "channel_diff_windows": market_class["BOTH_ACTIVE_CHANNEL_DIFF"],
                "side_diff_windows": market_class["BOTH_ACTIVE_SIDE_DIFF"],
                "our_only_windows": market_class["OUR_ONLY"],
                "target_only_windows": market_class["TARGET_ONLY"],
            })

        counts = Counter(str(r["classification"]) for r in rows)
        action_windows = [r for r in rows if r["classification"] != "BOTH_IDLE"]
        target_only = [r for r in rows if r["classification"] == "TARGET_ONLY"]
        our_only = [r for r in rows if r["classification"] == "OUR_ONLY"]
        channel_diff = [r for r in rows if r["classification"] == "BOTH_ACTIVE_CHANNEL_DIFF"]
        side_diff = [r for r in rows if r["classification"] == "BOTH_ACTIVE_SIDE_DIFF"]

        target_only_channels = Counter()
        for r in target_only:
            for ch in str(r["target_channels"]).split("|"):
                if ch:
                    target_only_channels[ch] += 1
        our_only_channels = Counter()
        for r in our_only:
            for ch in str(r["our_channels"]).split("|"):
                if ch:
                    our_only_channels[ch] += 1

        settled_market_rows = [r for r in market_rows if r["winner"] in {"UP", "DOWN"}]
        our_market_pnls = [float(r["our_market_pnl_usdt"]) for r in settled_market_rows if r["our_market_pnl_usdt"] != ""]
        target_official_pnls = [float(r["target_official_net_pnl_usdt"]) for r in settled_market_rows if r["target_official_net_pnl_usdt"] != ""]

        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "strategyPrefix": args.strategy_prefix,
            "method": {
                "ourSource": str(args.our_db),
                "targetSource": str(args.official_db),
                "stateWindowMs": bin_ms,
                "marketWindowMs": MARKET_MS,
                "marketBoundary": "target_markets.window_end_ms",
                "targetClock": "true parent first_event_ms derived from official event_ms",
                "targetParentCoalescing": {"idleGapMs": v1.TARGET_BURST_IDLE_MS, "capMs": v1.TARGET_BURST_CAP_MS},
                "absenceFinalizationMs": int(args.absence_guard_ms),
                "ourActionDefinition": "actual OUR fills from unified recorder (MAKER fills + TAKER fills)",
                "targetActionDefinition": "official Target parent-order actions coalesced into <=3s same-signature bursts",
                "stateAnchor": "latest strict-past OUR decision at first Target action in window; window-start state when no Target action",
                "warning": "Window standalone contribution is diagnostic only; path-dependent strategy judgment must use full-market portfolio replay.",
            },
            "coverage": {
                "ourDecisionMarkets": len(our_markets),
                "finalizedOverlapMarkets": len(markets),
                "settledOverlapMarkets": sum(bool(bounds[m][2]) for m in markets),
                "windows": len(rows),
                "actionWindows": len(action_windows),
                "ourActions": sum(len(our_by.get(m, [])) for m in markets),
                "targetActionsAfterCoalescing": sum(len(target_by.get(m, [])) for m in markets),
            },
            "classification": dict(counts),
            "rates": {k: (v / len(rows) if rows else None) for k, v in counts.items()},
            "wholeMarketOutcome": {
                "ourV0PnlUsdt": sum(our_market_pnls) if our_market_pnls else None,
                "ourV0PositiveMarkets": sum(v > 0 for v in our_market_pnls),
                "ourV0PositiveRate": sum(v > 0 for v in our_market_pnls) / len(our_market_pnls) if our_market_pnls else None,
                "targetOfficialNetPnlUsdt": sum(target_official_pnls) if target_official_pnls else None,
                "targetOfficialPositiveMarkets": sum(v > 0 for v in target_official_pnls),
                "targetOfficialPositiveRate": sum(v > 0 for v in target_official_pnls) / len(target_official_pnls) if target_official_pnls else None,
                "warning": "Target official net PnL is full wallet accounting; window contributions remain diagnostic action attribution."
            },
            "activityAudit": {
                "targetOnlyWindowsByChannel": dict(target_only_channels),
                "ourOnlyWindowsByChannel": dict(our_only_channels),
                "targetOnlyMarkets": len({int(r["market_id"]) for r in target_only}),
                "ourOnlyMarkets": len({int(r["market_id"]) for r in our_only}),
                "channelDiffMarkets": len({int(r["market_id"]) for r in channel_diff}),
                "sideDiffMarkets": len({int(r["market_id"]) for r in side_diff}),
            },
            "targetOnlyStateGroups": _group(rows, "TARGET_ONLY")[:40],
            "ourOnlyStateGroups": _group(rows, "OUR_ONLY")[:40],
            "channelDiffStateGroups": _group(rows, "BOTH_ACTIVE_CHANNEL_DIFF")[:30],
            "sideDiffStateGroups": _group(rows, "BOTH_ACTIVE_SIDE_DIFF")[:30],
            "collectorLag": v1._collector_lag_audit(official),
            "outputs": {"reportJson": str(args.report), "windowsCsv": str(args.windows), "marketsCsv": str(args.markets)},
        }

        _write_csv(args.windows, rows)
        _write_csv(args.markets, market_rows)
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    finally:
        our.close()
        official.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
