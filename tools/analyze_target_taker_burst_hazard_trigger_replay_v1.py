from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_fast_v2_scores.csv"
DEFAULT_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_burst_hazard_trigger_replay_v1_report.json"
REPORT_VERSION = "TARGET_TAKER_BURST_HAZARD_TRIGGER_REPLAY_V1_PHASE_ADAPTIVE_RAW_RANK"
FRACTIONS = (0.10, 0.20, 0.30)
POLICIES = (
    ("LEVEL_CD2", "LEVEL", 2),
    ("LEVEL_CD5", "LEVEL", 5),
    ("RISING_CD2", "RISING", 2),
    ("RISING_CD5", "RISING", 5),
)
PHASES = ("OPEN", "MID", "TAIL")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temp = resolved.with_suffix(resolved.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(resolved)


def _read_csv(path: Path) -> list[dict[str, str]]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    with resolved.open(encoding="utf-8", newline="") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


def _float(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


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


def _dataset_index(path: Path) -> dict[tuple[int, int], dict[str, str]]:
    result: dict[tuple[int, int], dict[str, str]] = {}
    for row in _read_csv(path):
        market_id = _int(row.get("market_id"))
        sampled = _int(row.get("decision_sampled_at_ms"))
        if market_id is None or sampled is None:
            continue
        result[(market_id, sampled)] = row
    return result


def _prepare(scores_path: Path, dataset_path: Path) -> list[dict[str, Any]]:
    source = _dataset_index(dataset_path)
    rows: list[dict[str, Any]] = []
    for raw in _read_csv(scores_path):
        market_id = _int(raw.get("market_id"))
        sampled = _int(raw.get("decision_sampled_at_ms"))
        horizon = _int(raw.get("horizon_seconds"))
        score = _float(raw.get("raw_probability"))
        if market_id is None or sampled is None or horizon not in (2, 5) or score is None:
            continue
        joined = source.get((market_id, sampled))
        if joined is None:
            continue
        delta = _int(joined.get("cap2_next_burst_delta_ms"))
        label = _int(joined.get(f"cap2_label_next_burst_{horizon}s")) or 0
        phase = str(raw.get("macro_phase") or joined.get("macro_phase") or "UNKNOWN").upper()
        rows.append(
            {
                "experiment": str(raw.get("experiment") or ""),
                "horizon": int(horizon),
                "market_id": int(market_id),
                "sampled_ms": int(sampled),
                "phase": phase,
                "score": float(score),
                "label": int(label),
                "next_delta_ms": delta,
                "next_onset_ms": (int(sampled) + int(delta)) if delta is not None and delta > 0 else None,
            }
        )
    rows.sort(key=lambda row: (row["experiment"], row["horizon"], row["sampled_ms"], row["market_id"]))
    return rows


def _adaptive_marks(
    rows: list[dict[str, Any]],
    *,
    fraction: float,
    window_rows: int,
    min_history_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    histories: dict[str, deque[float]] = {phase: deque(maxlen=window_rows) for phase in PHASES}
    marked: list[dict[str, Any]] = []
    threshold_rows = 0
    warmup_rows = 0
    by_phase = {phase: {"rows": 0, "thresholdRows": 0, "warmupRows": 0} for phase in PHASES}

    for row in sorted(rows, key=lambda item: (item["sampled_ms"], item["market_id"])):
        phase = row["phase"]
        if phase not in histories:
            continue
        by_phase[phase]["rows"] += 1
        history = histories[phase]
        threshold: float | None = None
        above = False
        if len(history) >= int(min_history_rows):
            threshold = _quantile(list(history), 1.0 - float(fraction))
            above = float(row["score"]) >= threshold
            threshold_rows += 1
            by_phase[phase]["thresholdRows"] += 1
        else:
            warmup_rows += 1
            by_phase[phase]["warmupRows"] += 1

        enriched = dict(row)
        enriched["adaptive_threshold"] = threshold
        enriched["above_threshold"] = bool(above)
        marked.append(enriched)
        history.append(float(row["score"]))

    return marked, {
        "rows": len(marked),
        "thresholdRows": threshold_rows,
        "warmupRows": warmup_rows,
        "thresholdCoverage": threshold_rows / len(marked) if marked else None,
        "byMacroPhase": by_phase,
    }


def _eligible_onsets(rows: list[dict[str, Any]]) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for row in rows:
        if row.get("adaptive_threshold") is None or int(row["label"]) != 1:
            continue
        onset = row.get("next_onset_ms")
        if onset is not None:
            result.add((int(row["market_id"]), int(onset)))
    return result


def _run_policy(
    marked: list[dict[str, Any]],
    *,
    mode: str,
    cooldown_s: int,
) -> dict[str, Any]:
    last_trigger_by_market: dict[int, int] = {}
    prev_above_by_market: dict[int, bool] = {}
    triggers: list[dict[str, Any]] = []

    for row in marked:
        market_id = int(row["market_id"])
        sampled = int(row["sampled_ms"])
        threshold_ready = row.get("adaptive_threshold") is not None
        above = bool(row.get("above_threshold")) if threshold_ready else False
        prev_above = bool(prev_above_by_market.get(market_id, False))
        cooldown_ok = sampled - last_trigger_by_market.get(market_id, -10**18) >= int(cooldown_s) * 1000

        should_trigger = False
        if threshold_ready and cooldown_ok:
            if mode == "LEVEL":
                should_trigger = above
            elif mode == "RISING":
                should_trigger = above and not prev_above
            else:
                raise ValueError(f"unknown mode: {mode}")

        if should_trigger:
            triggers.append(dict(row))
            last_trigger_by_market[market_id] = sampled

        prev_above_by_market[market_id] = above

    eligible = _eligible_onsets(marked)
    successful = [row for row in triggers if int(row["label"]) == 1 and row.get("next_onset_ms") is not None]
    captured = {(int(row["market_id"]), int(row["next_onset_ms"])) for row in successful}
    markets = {int(row["market_id"]) for row in marked}

    def phase_payload(phase: str) -> dict[str, Any]:
        phase_rows = [row for row in marked if row["phase"] == phase and row.get("adaptive_threshold") is not None]
        phase_triggers = [row for row in triggers if row["phase"] == phase]
        phase_success = [row for row in successful if row["phase"] == phase]
        phase_eligible = _eligible_onsets(phase_rows)
        phase_captured = {(int(row["market_id"]), int(row["next_onset_ms"])) for row in phase_success}
        return {
            "thresholdRows": len(phase_rows),
            "triggers": len(phase_triggers),
            "triggersPer100Rows": len(phase_triggers) / len(phase_rows) * 100.0 if phase_rows else None,
            "successfulTriggers": len(phase_success),
            "triggerPrecision": len(phase_success) / len(phase_triggers) if phase_triggers else None,
            "eligibleUniqueBursts": len(phase_eligible),
            "capturedUniqueBursts": len(phase_captured & phase_eligible),
            "uniqueBurstCaptureRate": len(phase_captured & phase_eligible) / len(phase_eligible) if phase_eligible else None,
        }

    duplicate_successes = max(0, len(successful) - len(captured & eligible))
    return {
        "mode": mode,
        "cooldownSeconds": int(cooldown_s),
        "markets": len(markets),
        "triggers": len(triggers),
        "triggersPerMarket": len(triggers) / len(markets) if markets else None,
        "successfulTriggers": len(successful),
        "triggerPrecision": len(successful) / len(triggers) if triggers else None,
        "eligibleUniqueBursts": len(eligible),
        "capturedUniqueBursts": len(captured & eligible),
        "uniqueBurstCaptureRate": len(captured & eligible) / len(eligible) if eligible else None,
        "duplicateSuccessfulTriggersToSameBurst": duplicate_successes,
        "duplicateSuccessfulTriggerRate": duplicate_successes / len(successful) if successful else None,
        "byMacroPhase": {phase: phase_payload(phase) for phase in PHASES},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay frozen16 burst-hazard scores as causal phase-adaptive trigger events. "
            "Thresholds use only PRIOR unlabeled raw scores in the same macro phase."
        )
    )
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--window-rows", type=int, default=600)
    parser.add_argument("--min-history-rows", type=int, default=120)
    args = parser.parse_args()

    window_rows = max(120, int(args.window_rows))
    min_history_rows = max(30, min(int(args.min_history_rows), window_rows))
    rows = _prepare(args.scores, args.dataset)
    if not rows:
        raise SystemExit("no score rows could be joined to burst-hazard dataset")

    groups: dict[tuple[str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["experiment"]), int(row["horizon"]))].append(row)

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "scoreField": "raw_probability",
        "thresholdPolicy": (
            "For each OPEN/MID/TAIL phase separately, threshold at time t is the requested quantile of only PRIOR "
            "unlabeled raw scores in a rolling window. Current/future scores and Target labels never set thresholds."
        ),
        "triggerPolicy": (
            "LEVEL fires whenever score is above the adaptive threshold and cooldown is clear. RISING fires only on "
            "a below->above threshold transition, also subject to cooldown. Trigger success means a strict-future "
            "cap2 burst onset occurs within the model horizon. Unique-burst capture deduplicates repeated successful triggers."
        ),
        "config": {
            "fractions": list(FRACTIONS),
            "windowRowsPerPhase": window_rows,
            "minHistoryRowsPerPhase": min_history_rows,
            "policies": [name for name, _mode, _cooldown in POLICIES],
        },
        "groups": {},
    }

    for (experiment, horizon), group in sorted(groups.items(), key=lambda item: (item[0][1], item[0][0]), reverse=True):
        key = f"{horizon}s::{experiment}"
        payload: dict[str, Any] = {
            "rows": len(group),
            "markets": len({int(row["market_id"]) for row in group}),
            "fractions": {},
        }
        report["groups"][key] = payload
        print(f"\n{key} | rows={len(group):,} markets={payload['markets']}", flush=True)

        for fraction in FRACTIONS:
            marked, coverage = _adaptive_marks(
                group,
                fraction=float(fraction),
                window_rows=window_rows,
                min_history_rows=min_history_rows,
            )
            policies: dict[str, Any] = {}
            for name, mode, cooldown in POLICIES:
                result = _run_policy(marked, mode=mode, cooldown_s=cooldown)
                policies[name] = result
            payload["fractions"][f"TOP_{int(round(fraction * 100)):02d}PCT"] = {
                "targetFraction": float(fraction),
                "coverage": coverage,
                "policies": policies,
            }
            best = policies["LEVEL_CD5"]
            tail = best["byMacroPhase"]["TAIL"]
            print(
                f"  top{int(round(fraction*100)):02d}% LEVEL_CD5 | triggers/market={best['triggersPerMarket']:.2f} "
                f"precision={best['triggerPrecision']:.3f} capture={best['uniqueBurstCaptureRate']:.3f} | "
                f"TAIL precision={tail['triggerPrecision'] if tail['triggerPrecision'] is not None else 'n/a'} "
                f"capture={tail['uniqueBurstCaptureRate'] if tail['uniqueBurstCaptureRate'] is not None else 'n/a'}",
                flush=True,
            )

    _write_json(args.report, report)
    print(f"\nreport: {args.report.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
