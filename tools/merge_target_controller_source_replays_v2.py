from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import analyze_target_controller_complete_history_v2_compat as compat

v2 = compat.v2
REPORT_VERSION = "TARGET_CONTROLLER_COMPLETE_HISTORY_V2_SPLIT_SOURCE_MERGE"


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.expanduser().resolve().open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.expanduser().resolve().read_text(encoding="utf-8"))


def _market_ids(rows: list[dict[str, Any]]) -> set[int]:
    return {int(float(row["market_id"])) for row in rows if row.get("market_id") not in (None, "")}


def _filter_market_rows(rows: list[dict[str, Any]], excluded: set[int]) -> list[dict[str, Any]]:
    return [row for row in rows if int(float(row["market_id"])) not in excluded]


def _invalid_boundary_market(market_id: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    firsts = [int(float(r["first_event_ms"])) for r in rows if r.get("first_event_ms") not in (None, "")]
    lasts = [int(float(r["last_event_ms"])) for r in rows if r.get("last_event_ms") not in (None, "")]
    return {
        "market_id": market_id,
        "segment_id": "",
        "source_version": "MIXED",
        "valid_lifecycle": 0,
        "invalid_reason": "market crosses source-version boundary",
        "first_event_ms": min(firsts) if firsts else "",
        "last_event_ms": max(lasts) if lasts else "",
        "maker_parents": 0,
        "taker_parents": 0,
        "taker_bursts": 0,
        "final_risk_deficit": "",
        "final_abs_payoff_gap": "",
        "final_worst_case_pnl": "",
    }


def _merge_markets(legacy: list[dict[str, Any]], official: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], set[int]]:
    legacy_ids = _market_ids(legacy)
    official_ids = _market_ids(official)
    overlap = legacy_ids & official_ids
    merged = _filter_market_rows(legacy, overlap) + _filter_market_rows(official, overlap)
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in legacy + official:
        mid = int(float(row["market_id"]))
        if mid in overlap:
            by_market[mid].append(row)
    for market_id in sorted(overlap):
        merged.append(_invalid_boundary_market(market_id, by_market[market_id]))
    merged.sort(key=lambda r: (
        int(float(r["first_event_ms"])) if r.get("first_event_ms") not in (None, "") else 0,
        int(float(r["market_id"])),
    ))
    return merged, overlap


def main() -> int:
    parser = argparse.ArgumentParser(description="Merge normalized LEGACY/OFFICIAL controller replay outputs without reopening SQLite DBs.")
    parser.add_argument("--legacy-report", type=Path, required=True)
    parser.add_argument("--legacy-bursts", type=Path, required=True)
    parser.add_argument("--legacy-transitions", type=Path, required=True)
    parser.add_argument("--legacy-markets", type=Path, required=True)
    parser.add_argument("--official-report", type=Path, required=True)
    parser.add_argument("--official-bursts", type=Path, required=True)
    parser.add_argument("--official-transitions", type=Path, required=True)
    parser.add_argument("--official-markets", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--bursts", type=Path, required=True)
    parser.add_argument("--transitions", type=Path, required=True)
    parser.add_argument("--markets", type=Path, required=True)
    args = parser.parse_args()

    legacy_report = _read_json(args.legacy_report)
    official_report = _read_json(args.official_report)
    legacy_bursts = _read_csv(args.legacy_bursts)
    official_bursts = _read_csv(args.official_bursts)
    legacy_transitions = _read_csv(args.legacy_transitions)
    official_transitions = _read_csv(args.official_transitions)
    legacy_markets = _read_csv(args.legacy_markets)
    official_markets = _read_csv(args.official_markets)

    merged_markets, boundary_overlap = _merge_markets(legacy_markets, official_markets)
    bursts = _filter_market_rows(legacy_bursts + official_bursts, boundary_overlap)
    transitions = _filter_market_rows(legacy_transitions + official_transitions, boundary_overlap)

    bursts.sort(key=lambda r: (int(float(r["first_event_ms"])), int(float(r["market_id"])), str(r["burst_id"])))
    transitions.sort(key=lambda r: (int(float(r["maker_completed_ms"])), int(float(r["market_id"])), str(r["maker_parent_id"])))

    v2._write_csv(args.bursts, bursts, v2.BURST_FIELDS)
    v2._write_csv(args.transitions, transitions, v2.TRANSITION_FIELDS)
    v2._write_csv(args.markets, merged_markets, v2.MARKET_FIELDS)

    gaps = []
    for source_report in (legacy_report, official_report):
        gaps.extend(source_report.get("coverage", {}).get("detectedHardGaps", []))

    selected_legs = int(legacy_report.get("coverage", {}).get("selectedFillLegs", 0)) + int(
        official_report.get("coverage", {}).get("selectedFillLegs", 0)
    )
    source_parents = int(legacy_report.get("coverage", {}).get("parents", 0)) + int(
        official_report.get("coverage", {}).get("parents", 0)
    )

    valid_markets = [row for row in merged_markets if int(float(row.get("valid_lifecycle") or 0)) == 1]
    report = {
        "reportVersion": REPORT_VERSION,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "paperResearchOnly": True,
        "noModelFit": True,
        "noStrategyPromotion": True,
        "purpose": "Replay LEGACY and OFFICIAL collector histories in isolated processes, then merge only normalized controller outputs.",
        "sourceIsolation": {
            "sqliteOpenedDuringMerge": False,
            "legacyReplayIndependent": True,
            "officialReplayIndependent": True,
            "boundaryOverlapMarketsExcluded": sorted(boundary_overlap),
            "boundaryOverlapMarketCount": len(boundary_overlap),
        },
        "sourceReports": {
            "LEGACY": legacy_report,
            "OFFICIAL": official_report,
        },
        "coverage": {
            "sourceStageSelectedFillLegsBeforeBoundaryExclusion": selected_legs,
            "sourceStageParentsBeforeBoundaryExclusion": source_parents,
            "markets": len(merged_markets),
            "validLifecycleMarkets": len(valid_markets),
            "invalidLifecycleMarkets": len(merged_markets) - len(valid_markets),
            "boundaryOverlapMarketsExcluded": len(boundary_overlap),
            "detectedHardGaps": gaps,
        },
        "overall": {
            "bursts": v2._burst_summary(bursts),
            "makerDecisionStates": v2._transition_summary(transitions),
        },
        "bySourceVersion": v2._group(bursts, transitions, lambda r: r["source_version"]),
        "byTaipeiDay": v2._group(
            bursts,
            transitions,
            lambda r: v2._day(int(float(r["first_event_ms"] if "first_event_ms" in r else r["maker_completed_ms"]))),
        ),
        "decisionSurface": {
            feature: v2._surface(transitions, feature)
            for feature in ("risk_deficit", "abs_payoff_gap", "maker_abs_payoff_gap")
        },
        "falsificationTests": {
            "burstSizing": "Burst-level size correlation should materially exceed parent-level correlation if Decision sizing is split across execution parents.",
            "conditionalTolerance": "Monotonic Taker handoff across payoff-gap bins supports an inventory tolerance surface; flat bins weaken a simple gap-trigger controller.",
            "cheapPayoffLock": "<0.10 BID-only correction bursts are judged by worst-case gain per cash cost.",
            "expensiveCorrection": ">0.90 BID-only correction bursts are high-urgency candidates; no emergency label is assumed a priori.",
            "versionStability": "Compare independent LEGACY/OFFICIAL source-stage summaries before treating any parameter as stable.",
        },
        "caveats": [
            "LEGACY and OFFICIAL SQLite schemas are normalized independently before merge.",
            "A market observed on both sides of the cutover is invalidated in the merged lifecycle analysis.",
            "Public Binance/Predict logic features remain intentionally excluded in this V2.",
            "Observed fills do not reveal unfilled/cancelled Maker quotes; explicit fees are not deducted.",
        ],
        "outputs": {
            "report": str(args.report),
            "burstsCsv": str(args.bursts),
            "transitionsCsv": str(args.transitions),
            "marketsCsv": str(args.markets),
        },
    }
    v2._write_json(args.report, report)

    print(REPORT_VERSION)
    print(
        f"merged markets={len(merged_markets):,} valid={len(valid_markets):,} "
        f"bursts={len(bursts):,} maker_states={len(transitions):,} "
        f"boundary_overlap={len(boundary_overlap):,}"
    )
    print(f"report: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
