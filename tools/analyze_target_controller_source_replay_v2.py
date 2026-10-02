from __future__ import annotations

import argparse
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import analyze_target_controller_complete_history_v2_compat as compat

v2 = compat.v2
ROOT = Path(__file__).resolve().parents[1]
REPORT_VERSION = "TARGET_CONTROLLER_SOURCE_REPLAY_V2"


def _select_rows(rows: list[dict], source: str, cutoff_ms: int) -> tuple[list[dict], dict]:
    selected = []
    wrong_wallet = 0
    duplicates = 0
    seen: set[tuple[str, int]] = set()
    for row in rows:
        in_window = int(row["event_ms"]) < cutoff_ms if source == "LEGACY" else int(row["event_ms"]) >= cutoff_ms
        if not in_window:
            continue
        if row.get("wallet") and str(row["wallet"]).lower() != v2.TARGET_WALLET:
            wrong_wallet += 1
            continue
        key = (str(row["leg_id"]), int(row["market_id"]))
        if key in seen:
            duplicates += 1
            continue
        seen.add(key)
        selected.append(row)
    selected.sort(key=lambda r: (int(r["event_ms"]), int(r["market_id"]), str(r["leg_id"])))
    return selected, {
        "sourceVersion": source,
        "selectedRows": len(selected),
        "wrongWalletRowsExcluded": wrong_wallet,
        "duplicateRowsRemoved": duplicates,
        "cutoverRule": "event_ms < cutover" if source == "LEGACY" else "event_ms >= cutover",
    }


def _run_source(
    events: list[dict],
    source: str,
    gap_ms: int,
    idle_gap_ms: int,
    burst_cap_ms: int,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    mapping, gaps = v2._assign_segments(events, gap_ms)
    parents = v2._build_parents(events)
    events_by_market: dict[int, list[dict]] = defaultdict(list)
    parents_by_market: dict[int, list[dict]] = defaultdict(list)
    for row in events:
        events_by_market[int(row["market_id"])].append(row)
    for row in parents:
        parents_by_market[int(row["market_id"])].append(row)

    bursts: list[dict] = []
    transitions: list[dict] = []
    markets: list[dict] = []
    for market_id in sorted(events_by_market):
        market_events = events_by_market[market_id]
        valid, segment_id, detected_source, reason = v2._market_segment(market_events, mapping)
        if not valid or segment_id is None:
            markets.append({
                "market_id": market_id,
                "segment_id": "",
                "source_version": detected_source,
                "valid_lifecycle": 0,
                "invalid_reason": reason,
                "first_event_ms": min(int(r["event_ms"]) for r in market_events),
                "last_event_ms": max(int(r["event_ms"]) for r in market_events),
                "maker_parents": 0,
                "taker_parents": 0,
                "taker_bursts": 0,
                "final_risk_deficit": "",
                "final_abs_payoff_gap": "",
                "final_worst_case_pnl": "",
            })
            continue
        b, t, m = v2._replay(
            market_id,
            parents_by_market[market_id],
            segment_id,
            source,
            idle_gap_ms,
            burst_cap_ms,
        )
        bursts.extend(b)
        transitions.extend(t)
        markets.append(m)

    bursts.sort(key=lambda r: (int(r["first_event_ms"]), int(r["market_id"]), str(r["burst_id"])))
    transitions.sort(key=lambda r: (int(r["maker_completed_ms"]), int(r["market_id"]), str(r["maker_parent_id"])))
    markets.sort(key=lambda r: (int(r["first_event_ms"]), int(r["market_id"])))
    return bursts, transitions, markets, gaps


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay one Target collector source in isolation and emit normalized controller rows.")
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--source-version", choices=("LEGACY", "OFFICIAL"), required=True)
    parser.add_argument("--cutover", default=v2.DEFAULT_CUTOVER)
    parser.add_argument("--asset", default="BTC")
    parser.add_argument("--gap-minutes", type=float, default=30.0)
    parser.add_argument("--idle-gap-ms", type=int, default=1000)
    parser.add_argument("--burst-cap-ms", type=int, default=3000)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bursts", type=Path, required=True)
    parser.add_argument("--transitions", type=Path, required=True)
    parser.add_argument("--markets", type=Path, required=True)
    args = parser.parse_args()

    source = str(args.source_version).upper()
    cutoff_ms = v2._parse_ms(args.cutover)
    raw, source_audit = compat._load_source_compat(args.db, source, str(args.asset).upper())
    events, selection_audit = _select_rows(raw, source, cutoff_ms)
    if not events:
        raise SystemExit(f"{source}: no selected events after source/cutover policy")

    bursts, transitions, markets, gaps = _run_source(
        events,
        source,
        max(1, int(args.gap_minutes * 60000)),
        max(0, int(args.idle_gap_ms)),
        max(0, int(args.burst_cap_ms)),
    )

    v2._write_csv(args.bursts, bursts, v2.BURST_FIELDS)
    v2._write_csv(args.transitions, transitions, v2.TRANSITION_FIELDS)
    v2._write_csv(args.markets, markets, v2.MARKET_FIELDS)

    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True,
        "sourceVersion": source,
        "cutover": args.cutover,
        "sourceAudit": source_audit,
        "selectionAudit": selection_audit,
        "coverage": {
            "selectedFillLegs": len(events),
            "parents": len(v2._build_parents(events)),
            "markets": len(markets),
            "validLifecycleMarkets": sum(int(r["valid_lifecycle"]) for r in markets),
            "invalidLifecycleMarkets": sum(not int(r["valid_lifecycle"]) for r in markets),
            "firstEventMs": min(int(r["event_ms"]) for r in events),
            "lastEventMs": max(int(r["event_ms"]) for r in events),
            "firstEventTaipei": v2._fmt(min(int(r["event_ms"]) for r in events)),
            "lastEventTaipei": v2._fmt(max(int(r["event_ms"]) for r in events)),
            "detectedHardGaps": gaps,
        },
        "summary": {
            "bursts": v2._burst_summary(bursts),
            "makerDecisionStates": v2._transition_summary(transitions),
            "decisionSurface": {
                feature: v2._surface(transitions, feature)
                for feature in ("risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap")
            },
        },
        "outputs": {
            "report": str(args.report),
            "burstsCsv": str(args.bursts),
            "transitionsCsv": str(args.transitions),
            "marketsCsv": str(args.markets),
        },
    }
    v2._write_json(args.report, report)

    print(f"{REPORT_VERSION} {source}")
    print(
        f"selected legs={len(events):,} markets={len(markets):,} "
        f"valid={sum(int(r['valid_lifecycle']) for r in markets):,} "
        f"bursts={len(bursts):,} maker_states={len(transitions):,} hard_gaps={len(gaps):,}"
    )
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
