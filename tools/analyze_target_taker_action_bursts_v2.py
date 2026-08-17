from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import analyze_target_taker_action_bursts_v1 as v1

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_action_bursts_v2_report.json"
DEFAULT_BURSTS = ROOT / "data" / "research" / "target_taker_action_bursts_v2.csv"
REPORT_VERSION = "TARGET_TAKER_ACTION_BURSTS_V2_CAPPED_EPISODES"
DEFAULT_MAX_DURATIONS = (1, 2, 3, 5)


def _parse_seconds(text: str) -> list[int]:
    values: list[int] = []
    for raw in str(text).split(","):
        raw = raw.strip()
        if not raw:
            continue
        value = int(raw)
        if value < 0:
            raise ValueError("seconds must be >= 0")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError("at least one duration is required")
    return values


def _split_episode_capped(episode: list[dict[str, Any]], max_duration_ms: int) -> list[list[dict[str, Any]]]:
    if not episode:
        return []
    groups: list[list[dict[str, Any]]] = [[episode[0]]]
    onset = int(episode[0]["target_event_ms"])
    for event in episode[1:]:
        event_ms = int(event["target_event_ms"])
        if event_ms - onset <= int(max_duration_ms):
            groups[-1].append(event)
        else:
            groups.append([event])
            onset = event_ms
    return groups


def _build_capped_bursts(
    events: list[dict[str, Any]],
    *,
    idle_gap_s: int,
    max_duration_s: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_market: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_market[int(event["market_id"])].append(dict(event))

    output: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    idle_gap_ms = int(idle_gap_s) * 1000
    max_duration_ms = int(max_duration_s) * 1000

    for market_id in sorted(by_market):
        market_events = sorted(
            by_market[market_id],
            key=lambda row: (
                int(row["target_event_ms"]),
                int(row.get("event_index") or 0),
                str(row.get("parent_id") or ""),
            ),
        )
        episodes = v1._cluster_market_events(market_events, idle_gap_ms)
        market_bursts: list[dict[str, Any]] = []
        previous_clean_side = ""

        for episode_index, episode in enumerate(episodes, 1):
            episode_onset = int(episode[0]["target_event_ms"])
            episode_end = int(episode[-1]["target_event_ms"])
            capped = _split_episode_capped(episode, max_duration_ms)
            episode_rows.append(
                {
                    "market_id": market_id,
                    "episode_index": episode_index,
                    "parent_count": len(episode),
                    "duration_ms": episode_end - episode_onset,
                    "action_bursts": len(capped),
                }
            )

            for burst_index_in_episode, cluster in enumerate(capped, 1):
                onset_ms = int(cluster[0]["target_event_ms"])
                end_ms = int(cluster[-1]["target_event_ms"])
                side_counts = Counter(str(row.get("side") or "").upper() for row in cluster)
                side_counts.pop("", None)
                clean_sides = sorted(side_counts)
                mixed = len(clean_sides) != 1
                clean_side = clean_sides[0] if len(clean_sides) == 1 else ""

                is_first_market_burst = len(market_bursts) == 0
                if is_first_market_burst:
                    burst_type = "FIRST_MIXED" if mixed else "FIRST_ENTRY"
                elif mixed:
                    burst_type = "MIXED"
                elif clean_side and previous_clean_side and clean_side == previous_clean_side:
                    burst_type = "SAME_SIDE_REENTRY"
                elif clean_side and previous_clean_side:
                    burst_type = "SIDE_FLIP"
                else:
                    burst_type = "STATE_UNKNOWN"

                first = cluster[0]
                row = {
                    "idle_gap_seconds": int(idle_gap_s),
                    "max_duration_seconds": int(max_duration_s),
                    "market_id": market_id,
                    "episode_index": episode_index,
                    "episode_parent_count": len(episode),
                    "episode_duration_ms": episode_end - episode_onset,
                    "burst_index_in_episode": burst_index_in_episode,
                    "burst_onset_ms": onset_ms,
                    "burst_end_ms": end_ms,
                    "burst_duration_ms": end_ms - onset_ms,
                    "parent_count": len(cluster),
                    "unique_event_seconds": len({int(r["target_event_ms"]) for r in cluster}),
                    "side": clean_side,
                    "up_parent_count": int(side_counts.get("UP", 0)),
                    "down_parent_count": int(side_counts.get("DOWN", 0)),
                    "mixed_sides": bool(mixed),
                    "burst_type": burst_type,
                    "phase": str(first.get("phase") or "UNKNOWN"),
                    "macro_phase": str(first.get("macro_phase") or "UNKNOWN"),
                    "first_parent_event_type": str(first.get("event_type") or "UNKNOWN"),
                }
                market_bursts.append(row)
                if clean_side:
                    previous_clean_side = clean_side

        market_count = len(market_bursts)
        for market_index, row in enumerate(market_bursts, 1):
            row["burst_index"] = market_index
            row["market_burst_count"] = market_count
            output.append(row)

    episode_summary = {
        "episodes": len(episode_rows),
        "episodeCountPerMarket": v1._quantiles([
            float(value)
            for value in Counter(int(row["market_id"]) for row in episode_rows).values()
        ]),
        "episodeDurationMs": v1._quantiles([float(row["duration_ms"]) for row in episode_rows]),
        "parentsPerEpisode": v1._quantiles([float(row["parent_count"]) for row in episode_rows]),
        "actionBurstsPerEpisode": v1._quantiles([float(row["action_bursts"]) for row in episode_rows]),
    }
    return output, episode_summary


def _summary_v2(bursts: list[dict[str, Any]], parent_count: int) -> dict[str, Any]:
    base = v1._summary(bursts, parent_count)
    first_total = sum(1 for row in bursts if str(row["burst_type"]).startswith("FIRST_"))
    base["firstBurstCount"] = first_total
    base["marketsWithFirstBurst"] = len({int(row["market_id"]) for row in bursts if str(row["burst_type"]).startswith("FIRST_")})
    return base


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Split Target activity into 1s-idle episodes, then cap action bursts by elapsed time from onset "
            "to prevent adjacent-gap chaining from turning long activity episodes into one decision."
        )
    )
    parser.add_argument("--events", type=Path, default=v1.DEFAULT_EVENTS)
    parser.add_argument("--scores", type=Path, default=v1.DEFAULT_SCORES)
    parser.add_argument("--hazard-report", type=Path, default=v1.DEFAULT_HAZARD_REPORT)
    parser.add_argument("--idle-gap-seconds", type=int, default=1)
    parser.add_argument("--max-duration-seconds", default=",".join(str(v) for v in DEFAULT_MAX_DURATIONS))
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--bursts-csv", type=Path, default=DEFAULT_BURSTS)
    args = parser.parse_args()

    if args.idle_gap_seconds < 0:
        raise SystemExit("--idle-gap-seconds must be >= 0")
    try:
        caps = _parse_seconds(args.max_duration_seconds)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("TARGET_TAKER_ACTION_BURSTS_V2_CAPPED_EPISODES", flush=True)
    print("[1/5] Load Target parents + existing frozen16 scores...", flush=True)
    events = v1._load_events(args.events)
    scores = v1._load_scores(args.scores)
    rules = v1._load_raw_gate_rules(args.hazard_report)
    if not events or not scores:
        raise SystemExit("missing events or hazard scores")
    print(
        f"      parents={len(events):,} markets={len(set(int(r['market_id']) for r in events)):,} "
        f"scoreRows={len(scores):,}",
        flush=True,
    )

    print("[2/5] Build uncapped activity-episode reference...", flush=True)
    reference = v1._build_bursts(events, args.idle_gap_seconds)
    reference_summary = v1._summary(reference, len(events))
    print(
        f"      idle gap<={args.idle_gap_seconds}s -> episodes={reference_summary['bursts']:,} "
        f"maxDurationMs={reference_summary['burstDurationMs'].get('max')}",
        flush=True,
    )

    print("[3/5] Split each activity episode into onset-capped action bursts...", flush=True)
    sensitivity: dict[str, Any] = {}
    all_bursts: list[dict[str, Any]] = []
    for index, cap_s in enumerate(caps, 1):
        bursts, episode_summary = _build_capped_bursts(
            events,
            idle_gap_s=args.idle_gap_seconds,
            max_duration_s=cap_s,
        )
        all_bursts.extend(bursts)
        summary = _summary_v2(bursts, len(events))
        sensitivity[str(cap_s)] = {
            "summary": summary,
            "episodeSummary": episode_summary,
            "hazardReplay": {},
        }
        print(
            f"      [{index}/{len(caps)}] cap<={cap_s}s: actionBursts={summary['bursts']:,} "
            f"parents/burst={summary['compressionParentsPerBurst']:.2f} "
            f"mixed={summary['mixedBurstShare']:.1%} maxDurationMs={summary['burstDurationMs'].get('max')}",
            flush=True,
        )

    print("[4/5] Replay existing frozen16 gates against capped burst onsets...", flush=True)
    for cap_s in caps:
        entry = sensitivity[str(cap_s)]
        bursts = [row for row in all_bursts if int(row["max_duration_seconds"]) == int(cap_s)]
        for horizon in sorted(rules, reverse=True):
            score_index = v1._score_indexes(scores, horizon)
            gate_results: list[dict[str, Any]] = []
            for rule in rules[horizon]:
                threshold = float(rule["threshold"])
                gate_results.append(
                    {
                        **rule,
                        "triggerPrecision": v1._trigger_precision(
                            bursts,
                            score_index,
                            threshold=threshold,
                            horizon_s=horizon,
                        ),
                        "burstCapture": {
                            mode: v1._burst_capture(
                                bursts,
                                score_index,
                                threshold=threshold,
                                horizon_s=horizon,
                                mode=mode,
                            )
                            for mode in v1.CAPTURE_MODES
                        },
                    }
                )
            entry["hazardReplay"][f"hazard_{horizon}s"] = gate_results

    print("[5/5] Write capped-burst report...", flush=True)
    columns = [
        "idle_gap_seconds", "max_duration_seconds", "market_id", "burst_index", "market_burst_count",
        "episode_index", "episode_parent_count", "episode_duration_ms", "burst_index_in_episode",
        "burst_onset_ms", "burst_end_ms", "burst_duration_ms", "parent_count", "unique_event_seconds",
        "side", "up_parent_count", "down_parent_count", "mixed_sides", "burst_type", "phase",
        "macro_phase", "first_parent_event_type",
    ]
    v1._write_csv(args.bursts_csv, all_bursts, columns)
    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Separate long Target activity episodes from bounded action-burst candidates, then replay frozen16 "
            "timing against capped burst onsets without refitting EBM."
        ),
        "clustering": {
            "idleGapSeconds": int(args.idle_gap_seconds),
            "maxDurationSeconds": caps,
            "episodeRule": "Adjacent parents remain in one activity episode while gap <= idleGapSeconds.",
            "actionBurstRule": (
                "Within an episode, a burst may include parents only while event_ms - burst_onset_ms <= maxDuration. "
                "This hard onset cap prevents unlimited single-linkage chaining."
            ),
            "firstMixedRule": "If the first market burst contains both sides it is FIRST_MIXED, not a clean FIRST_ENTRY.",
            "mixedStateRule": "MIXED/FIRST_MIXED bursts do not update clean direction state.",
        },
        "inputs": {
            "events": str(args.events.expanduser().resolve()),
            "scores": str(args.scores.expanduser().resolve()),
            "hazardReport": str(args.hazard_report.expanduser().resolve()),
            "parents": len(events),
            "scoreRows": len(scores),
        },
        "uncappedEpisodeReference": reference_summary,
        "sensitivity": sensitivity,
        "outputs": {
            "report": str(args.report.expanduser().resolve()),
            "burstsCsv": str(args.bursts_csv.expanduser().resolve()),
        },
        "interpretationGuide": {
            "activityEpisode": "A sustained period of Target activity; it can be long and is not assumed to be one decision.",
            "actionBurst": "A bounded candidate execution/decision unit inside an activity episode.",
            "selectionGoal": (
                "Prefer a cap where compression is meaningful, MIXED share remains interpretable, burst-window precision "
                "does not collapse, and OPEN/MID/TAIL capture remains stable."
            ),
        },
    }
    v1._write_json(args.report, report)

    print("\nCAPPED BURST SENSITIVITY COMPLETE", flush=True)
    for cap_s in caps:
        entry = sensitivity[str(cap_s)]
        summary = entry["summary"]
        print(
            f"  cap<={cap_s}s | bursts={summary['bursts']:,} | parents/burst={summary['compressionParentsPerBurst']:.2f} "
            f"| mixed={summary['mixedBurstShare']:.1%}",
            flush=True,
        )
        for gate in entry["hazardReplay"].get("hazard_5s", []):
            if gate.get("name") == "CAL_TOP_20PCT":
                capture = gate["burstCapture"]["CONTINUOUS"]["captureRate"]
                precision = gate["triggerPrecision"]["burstWindowPrecision"]
                print(f"      5s TOP20 capture={capture} precision={precision}", flush=True)
                break
    print(f"report: {args.report.expanduser().resolve()}", flush=True)
    print(f"bursts: {args.bursts_csv.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
