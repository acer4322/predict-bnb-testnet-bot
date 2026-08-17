from __future__ import annotations

import argparse
import bisect
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import build_target_taker_action_burst_hazard_dataset_v1 as hazard

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_action_onset_preflight_v1_report.json"
DEFAULT_OUTPUT = ROOT / "data" / "research" / "target_taker_action_onset_preflight_v1.csv"
REPORT_VERSION = "TARGET_TAKER_ACTION_ONSET_PREFLIGHT_V1_STRICT_PAST_CAP2"
PRIMARY_CAP_SECONDS = 2
AUDIT_CAP_SECONDS = 3
MAX_MODEL_LEAD_MS = 2000
FROZEN16 = (
    "seconds_left",
    "predict_up_mid",
    "predict_up_spread",
    "predict_down_spread",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "direction_score",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "signal_age_ms",
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _read_csv(path: Path) -> tuple[list[dict[str, str]], list[str]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        rows = [dict(row) for row in reader]
    return rows, fields


def _index_public(rows: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        try:
            market_id = int(row["market_id"])
            sampled_ms = int(float(row["decision_sampled_at_ms"]))
        except (KeyError, TypeError, ValueError):
            continue
        copied = dict(row)
        copied["market_id"] = market_id
        copied["decision_sampled_at_ms"] = sampled_ms
        grouped[market_id].append(copied)
    result: dict[int, dict[str, Any]] = {}
    for market_id, market_rows in grouped.items():
        market_rows.sort(key=lambda row: int(row["decision_sampled_at_ms"]))
        result[market_id] = {
            "rows": market_rows,
            "times": [int(row["decision_sampled_at_ms"]) for row in market_rows],
        }
    return result


def _strict_past_join(
    index: dict[int, dict[str, Any]], market_id: int, onset_ms: int
) -> tuple[dict[str, Any] | None, int | None]:
    entry = index.get(int(market_id))
    if entry is None:
        return None, None
    times = entry["times"]
    # bisect_left is intentional: a snapshot at exactly onset_ms is forbidden.
    pos = bisect.bisect_left(times, int(onset_ms)) - 1
    if pos < 0:
        return None, None
    row = entry["rows"][pos]
    sampled_ms = int(row["decision_sampled_at_ms"])
    if sampled_ms >= int(onset_ms):
        raise AssertionError("strict-past alignment violated")
    return row, int(onset_ms) - sampled_ms


def _labels(burst: dict[str, Any]) -> dict[str, Any]:
    burst_type = str(burst.get("burst_type") or "")
    side = str(burst.get("side") or "").upper()
    mixed = bool(burst.get("mixed_sides")) or burst_type in {"MIXED", "FIRST_MIXED"}
    is_first = burst_type.startswith("FIRST_")
    transition = ""
    if burst_type == "SAME_SIDE_REENTRY":
        transition = "SAME"
    elif burst_type == "SIDE_FLIP":
        transition = "FLIP"
    return {
        "is_first_burst": int(is_first),
        "clean_mixed_label": "MIXED" if mixed else "CLEAN",
        "transition_label": transition,
        "clean_side_label": side if (not mixed and side in {"UP", "DOWN"}) else "",
    }


def _quantiles(values: list[float]) -> dict[str, float | None]:
    clean = sorted(float(v) for v in values if math.isfinite(float(v)))
    if not clean:
        return {"min": None, "p10": None, "p25": None, "p50": None, "p75": None, "p90": None, "max": None}

    def q(frac: float) -> float:
        if len(clean) == 1:
            return clean[0]
        pos = frac * (len(clean) - 1)
        lo = int(math.floor(pos))
        hi = int(math.ceil(pos))
        if lo == hi:
            return clean[lo]
        w = pos - lo
        return clean[lo] * (1.0 - w) + clean[hi] * w

    return {
        "min": clean[0],
        "p10": q(0.10),
        "p25": q(0.25),
        "p50": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "max": clean[-1],
    }


def _region_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    matched = [row for row in rows if int(row.get("strict_past_matched") or 0) == 1]
    model_rows = [row for row in matched if int(row.get("strict_past_within_2s") or 0) == 1]
    subsequent = [row for row in model_rows if int(row.get("is_first_burst") or 0) == 0]
    transition = [row for row in subsequent if str(row.get("transition_label") or "") in {"SAME", "FLIP"}]
    clean = [row for row in subsequent if str(row.get("clean_mixed_label") or "") == "CLEAN"]
    leads = [float(row["feature_lead_ms"]) for row in matched if row.get("feature_lead_ms") not in (None, "")]
    return {
        "bursts": len(rows),
        "markets": len({int(row["market_id"]) for row in rows}),
        "strictPastMatched": len(matched),
        "strictPastCoverage": len(matched) / len(rows) if rows else None,
        "strictPastWithin2s": len(model_rows),
        "strictPastWithin2sCoverage": len(model_rows) / len(rows) if rows else None,
        "featureLeadMs": _quantiles(leads),
        "leadCoverage": {
            f"le{limit}ms": sum(float(v) <= limit for v in leads) / len(rows) if rows else None
            for limit in (250, 500, 1000, 1500, 2000, 5000)
        },
        "burstTypes": dict(Counter(str(row.get("burst_type") or "") for row in model_rows)),
        "firstBursts": sum(int(row.get("is_first_burst") or 0) for row in model_rows),
        "subsequentBursts": len(subsequent),
        "cleanVsMixedSubsequent": dict(Counter(str(row.get("clean_mixed_label") or "") for row in subsequent)),
        "primaryTransitionRows": len(transition),
        "transitionLabels": dict(Counter(str(row.get("transition_label") or "") for row in transition)),
        "cleanSideRows": len(clean),
        "cleanSideLabels": dict(Counter(str(row.get("clean_side_label") or "") for row in clean)),
        "byMacroPhase": {
            phase: {
                "rows": len([row for row in model_rows if str(row.get("macro_phase") or "") == phase]),
                "transitionRows": len([row for row in transition if str(row.get("macro_phase") or "") == phase]),
                "transitionLabels": dict(Counter(
                    str(row.get("transition_label") or "")
                    for row in transition
                    if str(row.get("macro_phase") or "") == phase
                )),
                "cleanMixed": dict(Counter(
                    str(row.get("clean_mixed_label") or "")
                    for row in subsequent
                    if str(row.get("macro_phase") or "") == phase
                )),
            }
            for phase in ("OPEN", "MID", "TAIL")
        },
    }


def _cap3_exact_audit(cap2: list[dict[str, Any]], cap3: list[dict[str, Any]]) -> dict[str, Any]:
    cap3_map = {(int(row["market_id"]), int(row["burst_onset_ms"])): row for row in cap3}
    matched = 0
    type_same = 0
    clean_mixed_same = 0
    side_comparable = 0
    side_same = 0
    transition_comparable = 0
    transition_same = 0
    for row in cap2:
        other = cap3_map.get((int(row["market_id"]), int(row["burst_onset_ms"])))
        if other is None:
            continue
        matched += 1
        if str(row.get("burst_type") or "") == str(other.get("burst_type") or ""):
            type_same += 1
        a = _labels(row)
        b = _labels(other)
        clean_mixed_same += int(a["clean_mixed_label"] == b["clean_mixed_label"])
        if a["clean_side_label"] and b["clean_side_label"]:
            side_comparable += 1
            side_same += int(a["clean_side_label"] == b["clean_side_label"])
        if a["transition_label"] and b["transition_label"]:
            transition_comparable += 1
            transition_same += int(a["transition_label"] == b["transition_label"])
    return {
        "cap2Bursts": len(cap2),
        "cap3Bursts": len(cap3),
        "exactOnsetMatches": matched,
        "cap2ExactOnsetMatchRate": matched / len(cap2) if cap2 else None,
        "burstTypeAgreementOnExactOnset": type_same / matched if matched else None,
        "cleanMixedAgreementOnExactOnset": clean_mixed_same / matched if matched else None,
        "cleanSideComparable": side_comparable,
        "cleanSideAgreement": side_same / side_comparable if side_comparable else None,
        "transitionComparable": transition_comparable,
        "transitionAgreement": transition_same / transition_comparable if transition_comparable else None,
    }


def _write_output(path: Path, rows: list[dict[str, Any]], public_fields: list[str]) -> None:
    fields = [
        "market_id", "burst_onset_ms", "burst_end_ms", "burst_index", "burst_type", "side",
        "mixed_sides", "parent_count", "region", "feature_sample_ms", "feature_lead_ms",
        "strict_past_matched", "strict_past_within_2s", "macro_phase", "is_first_burst",
        "clean_mixed_label", "transition_label", "clean_side_label",
    ]
    fields.extend(feature for feature in FROZEN16 if feature not in fields)
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build one strict-pre-onset row per Target cap2 action burst. No EBM is fit. "
            "The same-second public snapshot is forbidden to prevent action leakage."
        )
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--legacy-db", type=Path, default=hazard.DEFAULT_SHADOW_DB)
    parser.add_argument("--official-db", type=Path, default=hazard.DEFAULT_OFFICIAL_TARGET_DB)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--special-start", default="2026-08-16T12:00:00+08:00")
    parser.add_argument("--special-end", default=None)
    args = parser.parse_args()

    special_start = hazard._epoch_ms(args.special_start)
    special_end = hazard._epoch_ms(args.special_end)
    if special_start is None:
        raise SystemExit("--special-start is required")
    if special_end is not None and special_end <= special_start:
        raise SystemExit("--special-end must be after --special-start")

    print(REPORT_VERSION, flush=True)
    print("[1/5] Load public feature timeline...", flush=True)
    public_rows, public_fields = _read_csv(args.dataset)
    missing = sorted({"market_id", "decision_sampled_at_ms", "macro_phase", *FROZEN16} - set(public_fields))
    if missing:
        raise SystemExit("dataset missing columns: " + ", ".join(missing))
    public_index = _index_public(public_rows)
    print(f"      rows={len(public_rows):,} markets={len(public_index):,}", flush=True)

    print("[2/5] Rebuild full Target parent truth and cap2/cap3 action bursts...", flush=True)
    parents, source_stats, _deployed, _excluded = hazard._merge_labels(args.legacy_db, args.official_db)
    cap2 = hazard._build_bursts(parents, PRIMARY_CAP_SECONDS)
    cap3 = hazard._build_bursts(parents, AUDIT_CAP_SECONDS)
    print(f"      parents={len(parents):,} cap2={len(cap2):,} cap3={len(cap3):,}", flush=True)

    # A market touching/starting after the boundary is not allowed into the ordinary pre-special pool.
    post_start_markets = {
        int(row["market_id"])
        for row in public_rows
        if int(float(row["decision_sampled_at_ms"])) >= int(special_start)
    }

    print("[3/5] Align each cap2 onset to the latest STRICTLY PRIOR public snapshot...", flush=True)
    output_rows: list[dict[str, Any]] = []
    for burst in cap2:
        market_id = int(burst["market_id"])
        onset_ms = int(burst["burst_onset_ms"])
        public, lead_ms = _strict_past_join(public_index, market_id, onset_ms)
        if onset_ms < int(special_start) and market_id not in post_start_markets:
            region = "ORDINARY_PRE_SPECIAL"
        elif onset_ms >= int(special_start) and (special_end is None or onset_ms < int(special_end)):
            region = "POST_SPECIAL_START_AUDIT" if special_end is None else "SPECIAL_WINDOW_AUDIT"
        else:
            region = "OUTSIDE_ANALYSIS_WINDOW"

        row: dict[str, Any] = {
            "market_id": market_id,
            "burst_onset_ms": onset_ms,
            "burst_end_ms": int(burst["burst_end_ms"]),
            "burst_index": int(burst.get("burst_index") or 0),
            "burst_type": str(burst.get("burst_type") or ""),
            "side": str(burst.get("side") or ""),
            "mixed_sides": int(bool(burst.get("mixed_sides"))),
            "parent_count": int(burst.get("parent_count") or 0),
            "region": region,
            "feature_sample_ms": int(public["decision_sampled_at_ms"]) if public is not None else "",
            "feature_lead_ms": int(lead_ms) if lead_ms is not None else "",
            "strict_past_matched": int(public is not None),
            "strict_past_within_2s": int(public is not None and lead_ms is not None and 0 < lead_ms <= MAX_MODEL_LEAD_MS),
            "macro_phase": str(public.get("macro_phase") or "UNKNOWN") if public is not None else "UNKNOWN",
        }
        row.update(_labels(burst))
        if public is not None:
            for feature in FROZEN16:
                row[feature] = public.get(feature, "")
        output_rows.append(row)

    print("[4/5] Summarize ordinary vs post-special-start action labels and cap3 robustness...", flush=True)
    regions = {
        "ordinaryPreSpecial": [row for row in output_rows if row["region"] == "ORDINARY_PRE_SPECIAL"],
        "postSpecialStartAudit": [
            row for row in output_rows if row["region"] in {"POST_SPECIAL_START_AUDIT", "SPECIAL_WINDOW_AUDIT"}
        ],
    }
    report = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Preflight action classification labels at NEW Target action-burst onsets. Ordinary pre-special data is the "
            "primary strategy-recovery population; post-special-start data is audit/magnifier only."
        ),
        "primaryBurstDefinition": {"idleGapSeconds": 1, "maxDurationSeconds": PRIMARY_CAP_SECONDS},
        "auditBurstDefinition": {"idleGapSeconds": 1, "maxDurationSeconds": AUDIT_CAP_SECONDS},
        "strictPastPolicy": (
            "Feature row must satisfy decision_sampled_at_ms < burst_onset_ms. A same-timestamp snapshot is forbidden. "
            f"Rows within {MAX_MODEL_LEAD_MS}ms are flagged as primary model-ready coverage."
        ),
        "labelHierarchy": {
            "stage1": "subsequent burst CLEAN vs MIXED",
            "stage2": "among clean subsequent bursts with known prior clean side: SAME vs FLIP",
            "sideAudit": "clean burst UP vs DOWN; compare later with existing side/public-actor models",
            "excludedFromPrimaryTransition": ["FIRST_ENTRY", "FIRST_MIXED", "MIXED", "STATE_UNKNOWN"],
        },
        "specialBoundary": {"startMs": int(special_start), "endMs": int(special_end) if special_end is not None else None},
        "sourceStats": source_stats,
        "counts": {"parents": len(parents), "cap2Bursts": len(cap2), "cap3Bursts": len(cap3)},
        "regions": {name: _region_stats(rows) for name, rows in regions.items()},
        "cap2VsCap3ExactOnsetAudit": _cap3_exact_audit(cap2, cap3),
    }
    _write_json(args.report, report)

    print("[5/5] Write action-onset dataset + report...", flush=True)
    _write_output(args.output, output_rows, public_fields)
    ordinary = report["regions"]["ordinaryPreSpecial"]
    audit = report["regions"]["postSpecialStartAudit"]
    print(
        f"      ordinary model-ready={ordinary['strictPastWithin2s']:,}/{ordinary['bursts']:,} "
        f"transitionRows={ordinary['primaryTransitionRows']:,} labels={ordinary['transitionLabels']}",
        flush=True,
    )
    print(
        f"      post-start model-ready={audit['strictPastWithin2s']:,}/{audit['bursts']:,} "
        f"transitionRows={audit['primaryTransitionRows']:,} labels={audit['transitionLabels']}",
        flush=True,
    )
    print(f"report: {args.report.expanduser().resolve()}", flush=True)
    print(f"dataset: {args.output.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
