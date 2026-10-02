from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OOF = ROOT / "data" / "research" / "target_taker_trigger_conditioned_action_v1_oof.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_trigger_conditioned_selective_replay_v1_report.json"
REPORT_VERSION = "TARGET_TAKER_TRIGGER_CONDITIONED_SELECTIVE_REPLAY_V1_CAUSAL_RAW_RANK"
PHASES = ("OPEN", "MID", "TAIL")
CANDIDATES = ("TOP10_LEVEL_CD5", "TOP20_LEVEL_CD5")
SIDE_FRACTIONS = (0.10, 0.20, 0.30)
MIXED_VETOES: tuple[tuple[str, float | None], ...] = (("NO_VETO", None), ("TOP20_MIXED_VETO", 0.20))
LEAD_BINS = (
    ("LE_1S", 0, 1000),
    ("1_TO_2S", 1000, 2000),
    ("2_TO_3S", 2000, 3000),
    ("3_TO_5S", 3000, 5000),
)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    tmp = resolved.with_suffix(resolved.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(resolved)


def _read_csv(path: Path) -> list[dict[str, str]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _quantile(values: list[float], q: float) -> float:
    if not values:
        raise ValueError("quantile requires values")
    ordered = sorted(float(v) for v in values)
    if len(ordered) == 1:
        return ordered[0]
    pos = min(1.0, max(0.0, float(q))) * (len(ordered) - 1)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return ordered[lo]
    weight = pos - lo
    return ordered[lo] * (1.0 - weight) + ordered[hi] * weight


def _prepare(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _read_csv(path):
        candidate = str(raw.get("candidate") or "")
        fold = _int(raw.get("fold"))
        market_id = _int(raw.get("market_id"))
        sampled_ms = _int(raw.get("sampled_ms"))
        side_score = _float(raw.get("side_score"))
        stage1_score = _float(raw.get("stage1_score"))
        if candidate not in CANDIDATES or None in (fold, market_id, sampled_ms, side_score, stage1_score):
            continue
        phase = str(raw.get("phase") or "UNKNOWN").upper()
        if phase not in PHASES:
            continue
        rows.append(
            {
                "candidate": candidate,
                "fold": int(fold),
                "market_id": int(market_id),
                "sampled_ms": int(sampled_ms),
                "phase": phase,
                "side_score": float(side_score),
                "stage1_score": float(stage1_score),
                "success": int(_int(raw.get("success")) or 0),
                "next_delta_ms": _int(raw.get("next_delta_ms")),
                "next_onset_ms": _int(raw.get("next_onset_ms")),
                "next_is_subsequent": int(_int(raw.get("next_is_subsequent")) or 0),
                "next_clean_mixed": str(raw.get("next_clean_mixed") or "UNKNOWN").upper(),
                "next_burst_side": str(raw.get("next_burst_side") or "").upper(),
            }
        )
    rows.sort(key=lambda row: (row["candidate"], row["fold"], row["sampled_ms"], row["market_id"]))
    return rows


def _causal_marks(
    rows: list[dict[str, Any]],
    *,
    side_fraction: float,
    mixed_veto_fraction: float | None,
    window_rows: int,
    min_history_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    side_hist: dict[tuple[int, str], deque[float]] = defaultdict(lambda: deque(maxlen=window_rows))
    mixed_hist: dict[tuple[int, str], deque[float]] = defaultdict(lambda: deque(maxlen=window_rows))
    marked: list[dict[str, Any]] = []
    ready_rows = 0
    selected_rows = 0
    warmup_rows = 0

    for row in sorted(rows, key=lambda item: (item["fold"], item["sampled_ms"], item["market_id"])):
        key = (int(row["fold"]), str(row["phase"]))
        side_history = side_hist[key]
        mixed_history = mixed_hist[key]
        ready = len(side_history) >= min_history_rows and (
            mixed_veto_fraction is None or len(mixed_history) >= min_history_rows
        )
        enriched = dict(row)
        enriched.update(
            {
                "threshold_ready": bool(ready),
                "side_low_threshold": None,
                "side_high_threshold": None,
                "mixed_veto_threshold": None,
                "predicted_side": "",
                "mixed_vetoed": False,
                "selected": False,
            }
        )

        if ready:
            ready_rows += 1
            low = _quantile(list(side_history), float(side_fraction))
            high = _quantile(list(side_history), 1.0 - float(side_fraction))
            enriched["side_low_threshold"] = low
            enriched["side_high_threshold"] = high
            score = float(row["side_score"])
            if score <= low:
                enriched["predicted_side"] = "DOWN"
            elif score >= high:
                enriched["predicted_side"] = "UP"

            if mixed_veto_fraction is not None:
                veto_threshold = _quantile(list(mixed_history), 1.0 - float(mixed_veto_fraction))
                enriched["mixed_veto_threshold"] = veto_threshold
                enriched["mixed_vetoed"] = float(row["stage1_score"]) >= veto_threshold

            enriched["selected"] = bool(enriched["predicted_side"]) and not bool(enriched["mixed_vetoed"])
            if enriched["selected"]:
                selected_rows += 1
        else:
            warmup_rows += 1

        marked.append(enriched)
        # Critical causal boundary: current scores enter history only AFTER the current decision.
        side_history.append(float(row["side_score"]))
        mixed_history.append(float(row["stage1_score"]))

    return marked, {
        "rows": len(marked),
        "thresholdReadyRows": ready_rows,
        "warmupRows": warmup_rows,
        "thresholdCoverage": ready_rows / len(marked) if marked else None,
        "selectedRows": selected_rows,
        "selectionRateAmongReady": selected_rows / ready_rows if ready_rows else None,
        "selectionRateAmongAllTriggers": selected_rows / len(marked) if marked else None,
    }


def _is_clean_subsequent(row: dict[str, Any]) -> bool:
    return (
        int(row.get("success") or 0) == 1
        and int(row.get("next_is_subsequent") or 0) == 1
        and str(row.get("next_clean_mixed") or "") == "CLEAN"
        and str(row.get("next_burst_side") or "") in {"UP", "DOWN"}
    )


def _row_correct(row: dict[str, Any]) -> bool:
    return _is_clean_subsequent(row) and str(row.get("predicted_side") or "") == str(row.get("next_burst_side") or "")


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    selected = [row for row in rows if bool(row.get("selected"))]
    if not selected:
        return {"selectedActions": 0}
    any_burst = [row for row in selected if int(row.get("success") or 0) == 1]
    subsequent = [row for row in any_burst if int(row.get("next_is_subsequent") or 0) == 1]
    clean = [row for row in selected if _is_clean_subsequent(row)]
    mixed = [
        row
        for row in subsequent
        if str(row.get("next_clean_mixed") or "") == "MIXED"
    ]
    correct = [row for row in clean if _row_correct(row)]
    wrong = [row for row in clean if not _row_correct(row)]
    markets = {int(row["market_id"]) for row in selected}
    up_actions = [row for row in selected if str(row.get("predicted_side")) == "UP"]
    down_actions = [row for row in selected if str(row.get("predicted_side")) == "DOWN"]
    return {
        "selectedActions": len(selected),
        "selectedMarkets": len(markets),
        "selectedActionsPerMarket": len(selected) / len(markets) if markets else None,
        "anyBurstWithin5s": len(any_burst),
        "anyBurstPrecision": len(any_burst) / len(selected),
        "subsequentBurstWithin5s": len(subsequent),
        "cleanSubsequentBursts": len(clean),
        "mixedSubsequentBursts": len(mixed),
        "cleanSubsequentRatePerSelected": len(clean) / len(selected),
        "mixedRateGivenSubsequent": len(mixed) / len(subsequent) if subsequent else None,
        "correctSide": len(correct),
        "wrongSideOnClean": len(wrong),
        "sideAccuracyGivenCleanSubsequent": len(correct) / len(clean) if clean else None,
        "endToEndCorrectSidePerSelectedAction": len(correct) / len(selected),
        "predictedUPActions": len(up_actions),
        "predictedDOWNActions": len(down_actions),
        "upCorrectRatePerSelected": sum(_row_correct(row) for row in up_actions) / len(up_actions) if up_actions else None,
        "downCorrectRatePerSelected": sum(_row_correct(row) for row in down_actions) / len(down_actions) if down_actions else None,
    }


def _breakdown(rows: list[dict[str, Any]]) -> dict[str, Any]:
    phase_payload = {
        phase: _metrics([row for row in rows if str(row.get("phase")) == phase])
        for phase in PHASES
    }
    lead_payload: dict[str, Any] = {}
    for name, low, high in LEAD_BINS:
        selected = []
        for row in rows:
            delta = _int(row.get("next_delta_ms"))
            if delta is not None and low < delta <= high:
                selected.append(row)
        lead_payload[name] = _metrics(selected)
    return {"byMacroPhase": phase_payload, "byLeadMs": lead_payload}


def _dedupe_selected_target_bursts(rows: list[dict[str, Any]]) -> dict[str, Any]:
    chosen: dict[tuple[int, int], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (int(item["sampled_ms"]), int(item["market_id"]))):
        if not bool(row.get("selected")) or not _is_clean_subsequent(row):
            continue
        onset = _int(row.get("next_onset_ms"))
        if onset is None:
            continue
        chosen.setdefault((int(row["market_id"]), int(onset)), row)
    unique = list(chosen.values())
    correct = sum(_row_correct(row) for row in unique)
    return {
        "uniqueCleanTargetBursts": len(unique),
        "correctUniqueCleanTargetBursts": correct,
        "sideAccuracyOnUniqueCleanTargetBursts": correct / len(unique) if unique else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay trigger-conditioned action OOF scores with causal phase-local raw-rank side gates. "
            "Thresholds use only prior unlabeled OOF trigger scores within the same fold and macro phase."
        )
    )
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--window-rows", type=int, default=300)
    parser.add_argument("--min-history-rows", type=int, default=40)
    args = parser.parse_args()

    rows = _prepare(args.oof)
    if not rows:
        raise SystemExit(
            f"no trigger-conditioned OOF rows: {args.oof.expanduser().resolve()}\n"
            "Run .\\run-target-taker-trigger-conditioned-action-v1.ps1 first."
        )
    window_rows = max(80, int(args.window_rows))
    min_history_rows = max(20, min(int(args.min_history_rows), window_rows))

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "source": str(args.oof.expanduser().resolve()),
        "purpose": (
            "Convert the deployment-clean hazard->absolute-side bridge into an abstaining action policy. "
            "Only score extremeness and an optional high-MIXED-risk veto decide whether to emit UP/DOWN."
        ),
        "thresholdPolicy": (
            "For each candidate/fold/OPEN-MID-TAIL stream, side and MIXED thresholds at time t use only prior "
            "unlabeled trigger scores. Current scores are appended to history only after the decision."
        ),
        "successSemantics": (
            "Primary imitation success is: selected trigger -> within 5s a subsequent CLEAN Target burst occurs -> "
            "predicted absolute side matches that burst. This is imitation precision, not trading PnL."
        ),
        "config": {
            "candidates": list(CANDIDATES),
            "sideTailFractions": list(SIDE_FRACTIONS),
            "mixedVetoes": [name for name, _fraction in MIXED_VETOES],
            "windowRowsPerFoldPhase": window_rows,
            "minHistoryRowsPerFoldPhase": min_history_rows,
        },
        "candidates": {},
    }

    for candidate in CANDIDATES:
        source_rows = [row for row in rows if row["candidate"] == candidate]
        payload: dict[str, Any] = {
            "sourceTriggers": len(source_rows),
            "policies": {},
        }
        report["candidates"][candidate] = payload
        print(f"\n{candidate}: sourceTriggers={len(source_rows):,}", flush=True)
        for side_fraction in SIDE_FRACTIONS:
            for veto_name, veto_fraction in MIXED_VETOES:
                marked, coverage = _causal_marks(
                    source_rows,
                    side_fraction=float(side_fraction),
                    mixed_veto_fraction=veto_fraction,
                    window_rows=window_rows,
                    min_history_rows=min_history_rows,
                )
                metrics = _metrics(marked)
                policy_name = f"SIDE_TAIL_{int(side_fraction * 100):02d}__{veto_name}"
                payload["policies"][policy_name] = {
                    "sideTailFractionEachSide": float(side_fraction),
                    "mixedVetoFraction": veto_fraction,
                    "coverage": coverage,
                    "metrics": metrics,
                    **_breakdown(marked),
                    "uniqueTargetBurstAudit": _dedupe_selected_target_bursts(marked),
                }
                print(
                    f"  {policy_name} selected={metrics.get('selectedActions', 0):,} "
                    f"e2e={metrics.get('endToEndCorrectSidePerSelectedAction')} "
                    f"side|clean={metrics.get('sideAccuracyGivenCleanSubsequent')}",
                    flush=True,
                )

    _write_json(args.report, report)
    print(f"\nReport: {args.report.expanduser().resolve()}", flush=True)
    print("No strategy was promoted.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
