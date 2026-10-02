from __future__ import annotations

import argparse
import bisect
import csv
import json
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import analyze_strategy_target_cross_compare_v1 as v1

ROOT = Path(__file__).resolve().parents[1]
VERSION = "STRATEGY_TARGET_CROSS_COMPARE_V2_STATE_WINDOWS"
DEFAULT_REPORT = ROOT / "data/research/strategy_target_cross_compare_v2_windows_report.json"
DEFAULT_ROWS = ROOT / "data/research/strategy_target_cross_compare_v2_windows.csv"
BIN_MS = 5_000
MARKET_MS = 300_000


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0]) if rows else ["market_id"]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def _market_end_estimates(*timelines: dict[int, list[tuple[int, dict[str, Any]]]]) -> dict[int, int]:
    values: dict[int, list[int]] = defaultdict(list)
    for timeline in timelines:
        for market_id, rows in timeline.items():
            for decision_ms, payload in rows:
                cols = payload.get("_columns") if isinstance(payload.get("_columns"), dict) else {}
                seconds = v1._finite(cols.get("seconds_left", payload.get("secondsLeft")))
                if seconds is None or not (0 <= seconds <= 330):
                    continue
                values[int(market_id)].append(int(round(decision_ms + seconds * 1000.0)))
    return {mid: int(statistics.median(xs)) for mid, xs in values.items() if xs}


def _slice(actions: list[v1.Action], times: list[int], start: int, end: int) -> list[v1.Action]:
    lo = bisect.bisect_left(times, start)
    hi = bisect.bisect_left(times, end)
    return actions[lo:hi]


def _signatures(actions: list[v1.Action]) -> set[str]:
    return {f"{a.channel}:{a.side}:{a.quote_type}" for a in actions}


def _state_snapshot(
    maker_timeline: dict[int, list[tuple[int, dict[str, Any]]]],
    taker_timeline: dict[int, list[tuple[int, dict[str, Any]]]],
    market_id: int,
    at_ms: int,
) -> dict[str, Any]:
    maker_decision, maker_reason, maker_factors = v1._nearest_decision(maker_timeline, market_id, at_ms)
    taker_decision, taker_reason, taker_factors = v1._nearest_decision(taker_timeline, market_id, at_ms)
    maker_action = v1.Action("", "OUR", "MAKER", market_id, at_ms, "", "", 0, 0, "", maker_reason, maker_decision, maker_factors)
    taker_action = v1.Action("", "OUR", "TAKER", market_id, at_ms, "", "", 0, 0, "", taker_reason, taker_decision, taker_factors)
    return {
        "maker": v1._flatten_factor_summary(maker_action),
        "taker": v1._flatten_factor_summary(taker_action),
    }


def _group_reason(rows: list[dict[str, Any]], classification: str) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["classification"] != classification:
            continue
        key = f"M={row['maker_reason'] or 'NONE'} | T={row['taker_reason'] or 'NONE'}"
        groups[key].append(row)
    out: list[dict[str, Any]] = []
    for reason, xs in groups.items():
        our = [float(x["our_contribution_usdt"]) for x in xs if x["our_contribution_usdt"] != ""]
        target = [float(x["target_contribution_usdt"]) for x in xs if x["target_contribution_usdt"] != ""]
        out.append({
            "stateReasons": reason,
            "windows": len(xs),
            "markets": len({int(x["market_id"]) for x in xs}),
            "ourContributionUsdt": sum(our) if our else None,
            "targetContributionUsdt": sum(target) if target else None,
            "targetPositiveRate": sum(v > 0 for v in target) / len(target) if target else None,
            "ourPositiveRate": sum(v > 0 for v in our) / len(our) if our else None,
        })
    out.sort(key=lambda x: (-x["windows"], x["stateReasons"]))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--shadow-db", type=Path, default=v1.DEFAULT_SHADOW_DB)
    ap.add_argument("--official-db", type=Path, default=v1.DEFAULT_OFFICIAL_DB)
    ap.add_argument("--bin-ms", type=int, default=BIN_MS)
    ap.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    ap.add_argument("--rows", type=Path, default=DEFAULT_ROWS)
    args = ap.parse_args()

    shadow = v1._connect_ro(args.shadow_db)
    official = v1._connect_ro(args.official_db) if args.official_db.exists() else None
    try:
        our_actions, _ = v1._load_our_actions(shadow)
        maker_timeline = v1._load_decision_timeline(shadow, "wallet_maker_ebm_v1_decisions", v1.MAKER_COHORT)
        taker_timeline = v1._load_decision_timeline(shadow, "wallet_target_taker_public_side_v1_decisions", v1.TAKER_COHORT)
        ends = _market_end_estimates(maker_timeline, taker_timeline)
        our_markets = set(ends)
        target = v1._legacy_target_actions(shadow, our_markets)
        if official is not None:
            target += v1._official_target_actions(official, our_markets)
        target = v1._coalesce_target_actions(target)
        target_markets = {a.market_id for a in target}
        markets = sorted(our_markets & target_markets)
        winners = v1._winner_maps(shadow, official)

        our_by: dict[int, list[v1.Action]] = defaultdict(list)
        target_by: dict[int, list[v1.Action]] = defaultdict(list)
        for a in our_actions:
            if a.market_id in markets:
                our_by[a.market_id].append(a)
        for a in target:
            if a.market_id in markets:
                target_by[a.market_id].append(a)
        our_times = {m: [a.event_ms for a in sorted(xs, key=lambda a: a.event_ms)] for m, xs in our_by.items()}
        target_times = {m: [a.event_ms for a in sorted(xs, key=lambda a: a.event_ms)] for m, xs in target_by.items()}
        for m in markets:
            our_by[m].sort(key=lambda a: a.event_ms)
            target_by[m].sort(key=lambda a: a.event_ms)

        rows: list[dict[str, Any]] = []
        for market_id in markets:
            end_ms = int(ends[market_id])
            start_ms = end_ms - MARKET_MS
            winner = winners.get(market_id)
            at = start_ms
            while at < end_ms:
                nxt = min(end_ms, at + max(1000, int(args.bin_ms)))
                ours = _slice(our_by.get(market_id, []), our_times.get(market_id, []), at, nxt)
                theirs = _slice(target_by.get(market_id, []), target_times.get(market_id, []), at, nxt)
                if ours and theirs:
                    classification = "BOTH_ACTIVE"
                elif ours:
                    classification = "OUR_ONLY"
                elif theirs:
                    classification = "TARGET_ONLY"
                else:
                    classification = "BOTH_IDLE"
                our_sig = _signatures(ours)
                target_sig = _signatures(theirs)
                same_signature = bool(our_sig & target_sig)
                our_contribs = [v1._standalone_contribution(a, winner) for a in ours]
                target_contribs = [v1._standalone_contribution(a, winner) for a in theirs]
                our_contrib = sum(float(x) for x in our_contribs if x is not None)
                target_contrib = sum(float(x) for x in target_contribs if x is not None)
                state = _state_snapshot(maker_timeline, taker_timeline, market_id, nxt - 1)
                maker_state = state["maker"]
                taker_state = state["taker"]
                rows.append({
                    "market_id": market_id,
                    "winner": winner or "",
                    "window_start_ms": at,
                    "window_end_ms": nxt,
                    "seconds_left_at_end": max(0.0, (end_ms - nxt) / 1000.0),
                    "classification": classification,
                    "same_signature": int(same_signature),
                    "our_action_count": len(ours),
                    "target_action_count": len(theirs),
                    "our_signatures": "|".join(sorted(our_sig)),
                    "target_signatures": "|".join(sorted(target_sig)),
                    "our_contribution_usdt": our_contrib if ours and winner else "",
                    "target_contribution_usdt": target_contrib if theirs and winner else "",
                    "maker_decision": maker_state.get("decision") or "",
                    "maker_reason": maker_state.get("reason") or "",
                    "maker_hazard_probability": maker_state.get("hazardProbability") if maker_state.get("hazardProbability") is not None else "",
                    "maker_inventory_delta_shares": maker_state.get("inventoryDeltaShares") if maker_state.get("inventoryDeltaShares") is not None else "",
                    "taker_decision": taker_state.get("decision") or "",
                    "taker_reason": taker_state.get("reason") or "",
                    "taker_side_score": taker_state.get("sideScore") if taker_state.get("sideScore") is not None else "",
                    "taker_side_confidence": taker_state.get("sideConfidence") if taker_state.get("sideConfidence") is not None else "",
                    "state_json": json.dumps(state, ensure_ascii=False, separators=(",", ":"), default=str),
                })
                at = nxt

        counts = Counter(r["classification"] for r in rows)
        action_rows = [r for r in rows if r["classification"] != "BOTH_IDLE"]
        both = [r for r in rows if r["classification"] == "BOTH_ACTIVE"]
        ours_only = [r for r in rows if r["classification"] == "OUR_ONLY"]
        target_only = [r for r in rows if r["classification"] == "TARGET_ONLY"]
        both_idle = [r for r in rows if r["classification"] == "BOTH_IDLE"]

        def contrib(xs: list[dict[str, Any]], field: str) -> list[float]:
            return [float(r[field]) for r in xs if r[field] != ""]

        our_only_pnl = contrib(ours_only, "our_contribution_usdt")
        target_only_pnl = contrib(target_only, "target_contribution_usdt")
        report = {
            "reportVersion": VERSION,
            "researchOnly": True,
            "liveTradingChanges": False,
            "method": {
                "stateWindowMs": int(args.bin_ms),
                "marketWindowMs": MARKET_MS,
                "targetParentsCoalesced": {"idleGapMs": v1.TARGET_BURST_IDLE_MS, "capMs": v1.TARGET_BURST_CAP_MS},
                "postHocTargetClock": "true event_ms",
                "liveTargetAbsenceGuardMs": v1.ABSENCE_FINALIZATION_MS,
                "interpretation": "Compare controller state/activity in fixed time windows instead of one-to-one fill counts.",
            },
            "coverage": {
                "markets": len(markets), "windows": len(rows), "actionWindows": len(action_rows),
                "withWinner": len({int(r['market_id']) for r in rows if r['winner']}),
            },
            "classification": dict(counts),
            "rates": {
                "bothIdle": len(both_idle) / len(rows) if rows else None,
                "ourOnly": len(ours_only) / len(rows) if rows else None,
                "targetOnly": len(target_only) / len(rows) if rows else None,
                "bothActive": len(both) / len(rows) if rows else None,
                "sameSignatureGivenBothActive": sum(int(r["same_signature"]) for r in both) / len(both) if both else None,
            },
            "economicAttribution": {
                "ourOnlyContributionUsdt": sum(our_only_pnl) if our_only_pnl else None,
                "ourOnlyPositiveRate": sum(v > 0 for v in our_only_pnl) / len(our_only_pnl) if our_only_pnl else None,
                "targetOnlyContributionUsdt": sum(target_only_pnl) if target_only_pnl else None,
                "targetOnlyPositiveRate": sum(v > 0 for v in target_only_pnl) / len(target_only_pnl) if target_only_pnl else None,
                "warning": "Standalone action contribution, not a full path-dependent portfolio counterfactual.",
            },
            "ourOnlyStateReasons": _group_reason(rows, "OUR_ONLY")[:30],
            "targetOnlyStateReasons": _group_reason(rows, "TARGET_ONLY")[:30],
            "collectorLag": v1._collector_lag_audit(official),
            "outputs": {"rowsCsv": str(args.rows), "reportJson": str(args.report)},
        }
        _write_csv(args.rows, rows)
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
