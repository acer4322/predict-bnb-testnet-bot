from __future__ import annotations

import argparse
import csv
import json
import math
from collections import defaultdict, deque
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCORES = ROOT / "data" / "research" / "target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_scores.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_actor_state_substitution_replay_v1_report.json"
REPORT_VERSION = "TARGET_TAKER_ACTOR_STATE_SUBSTITUTION_REPLAY_V1_FULLTIMELINE_OOF"
PHASES = ("OPEN", "MID", "TAIL")
CANDIDATES = (
    ("TOP10_LEVEL_CD5", 0.10, 5),
    ("TOP20_LEVEL_CD5", 0.20, 5),
    ("TOP10_LEVEL_CD2", 0.10, 2),
    ("TOP20_LEVEL_CD2", 0.20, 2),
)


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


def _parse_delays(value: str) -> list[int]:
    result: list[int] = []
    for raw in str(value).split(","):
        raw = raw.strip()
        if not raw:
            continue
        delay = int(raw)
        if delay < 0 or delay > 60:
            raise ValueError("entry delays must be between 0 and 60 seconds")
        if delay not in result:
            result.append(delay)
    if not result:
        raise ValueError("at least one entry delay is required")
    return result


def _prepare(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _read_csv(path):
        fold = _int(raw.get("fold"))
        horizon = _int(raw.get("horizon_seconds"))
        market_id = _int(raw.get("market_id"))
        sampled = _int(raw.get("decision_sampled_at_ms"))
        score = _float(raw.get("raw_probability"))
        label = _int(raw.get("cap2_label"))
        delta = _int(raw.get("cap2_next_burst_delta_ms"))
        active = _int(raw.get("cap2_burst_active"))
        risk = _int(raw.get("cap2_risk_post_first_idle"))
        if None in (fold, horizon, market_id, sampled, score, label):
            continue
        if int(horizon) != 5:
            continue
        phase = str(raw.get("macro_phase") or "UNKNOWN").upper()
        if phase not in PHASES:
            continue
        rows.append({
            "fold": int(fold),
            "market_id": int(market_id),
            "sampled_ms": int(sampled),
            "seconds_left": _float(raw.get("seconds_left")),
            "phase": phase,
            "score": float(score),
            "label": int(label),
            "next_delta_ms": int(delta) if delta is not None else None,
            "next_onset_ms": int(sampled) + int(delta) if delta is not None and delta > 0 else None,
            # These fields are AUDIT ONLY. They must never participate in trigger eligibility.
            "target_position_state_audit": str(raw.get("cap2_position_state") or ""),
            "target_burst_active_audit": int(active or 0),
            "target_post_first_idle_audit": int(risk or 0),
        })
    rows.sort(key=lambda row: (row["fold"], row["sampled_ms"], row["market_id"]))
    return rows


def _adaptive_marks_by_fold(
    rows: list[dict[str, Any]],
    *,
    fraction: float,
    window_rows: int,
    min_history_rows: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    by_fold: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_fold[int(row["fold"])].append(row)

    marked: list[dict[str, Any]] = []
    fold_payload: dict[str, Any] = {}
    for fold in sorted(by_fold):
        histories: dict[str, deque[float]] = {phase: deque(maxlen=window_rows) for phase in PHASES}
        threshold_rows = 0
        warmup_rows = 0
        by_phase = {phase: {"rows": 0, "thresholdRows": 0, "warmupRows": 0} for phase in PHASES}
        for row in sorted(by_fold[fold], key=lambda item: (item["sampled_ms"], item["market_id"])):
            phase = row["phase"]
            history = histories[phase]
            by_phase[phase]["rows"] += 1
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
            # Critical boundary: only AFTER evaluating the current row does it enter threshold history.
            history.append(float(row["score"]))
        fold_payload[str(fold)] = {
            "rows": len(by_fold[fold]),
            "thresholdRows": threshold_rows,
            "warmupRows": warmup_rows,
            "thresholdCoverage": threshold_rows / len(by_fold[fold]) if by_fold[fold] else None,
            "byMacroPhase": by_phase,
        }
    marked.sort(key=lambda row: (row["fold"], row["sampled_ms"], row["market_id"]))
    return marked, fold_payload


def _market_starts(rows: list[dict[str, Any]]) -> dict[int, int]:
    result: dict[int, int] = {}
    for row in rows:
        market_id = int(row["market_id"])
        sampled = int(row["sampled_ms"])
        result[market_id] = min(sampled, result.get(market_id, sampled))
    return result


def _audit_state(row: dict[str, Any]) -> str:
    if int(row.get("target_burst_active_audit") or 0) == 1:
        return "TARGET_BURST_ACTIVE"
    if int(row.get("target_post_first_idle_audit") or 0) == 1:
        return "TARGET_POST_FIRST_IDLE"
    state = str(row.get("target_position_state_audit") or "")
    if state == "PRE_FIRST":
        return "TARGET_PRE_FIRST"
    if state == "NO_TARGET_BURST":
        return "NO_TARGET_BURST"
    return "OTHER_TARGET_STATE"


def _eligible_onsets(
    marked: list[dict[str, Any]],
    *,
    entry_ms_by_market: dict[int, int],
) -> set[tuple[int, int]]:
    result: set[tuple[int, int]] = set()
    for row in marked:
        market_id = int(row["market_id"])
        sampled = int(row["sampled_ms"])
        if sampled < int(entry_ms_by_market[market_id]):
            continue
        if row.get("adaptive_threshold") is None:
            continue
        if int(row["label"]) != 1 or row.get("next_onset_ms") is None:
            continue
        result.add((market_id, int(row["next_onset_ms"])))
    return result


def _run_actor_policy(
    marked: list[dict[str, Any]],
    *,
    entry_delay_s: int,
    cooldown_s: int,
) -> dict[str, Any]:
    starts = _market_starts(marked)
    entry_ms_by_market = {market_id: start + int(entry_delay_s) * 1000 for market_id, start in starts.items()}
    # Runtime state is OUR state only: first-entry timestamp + our own last action timestamp.
    last_action_by_market = dict(entry_ms_by_market)
    triggers: list[dict[str, Any]] = []

    for row in marked:
        market_id = int(row["market_id"])
        sampled = int(row["sampled_ms"])
        if sampled < entry_ms_by_market[market_id]:
            continue
        if row.get("adaptive_threshold") is None or not bool(row.get("above_threshold")):
            continue
        if sampled - last_action_by_market[market_id] < int(cooldown_s) * 1000:
            continue
        triggers.append(dict(row))
        last_action_by_market[market_id] = sampled

    eligible = _eligible_onsets(marked, entry_ms_by_market=entry_ms_by_market)
    successful = [row for row in triggers if int(row["label"]) == 1 and row.get("next_onset_ms") is not None]
    captured = {(int(row["market_id"]), int(row["next_onset_ms"])) for row in successful}
    duplicate_successes = max(0, len(successful) - len(captured & eligible))

    audit_counts: dict[str, int] = defaultdict(int)
    audit_success: dict[str, int] = defaultdict(int)
    for row in triggers:
        state = _audit_state(row)
        audit_counts[state] += 1
        if int(row["label"]) == 1:
            audit_success[state] += 1

    def phase_payload(phase: str) -> dict[str, Any]:
        phase_rows = [row for row in marked if row["phase"] == phase]
        phase_triggers = [row for row in triggers if row["phase"] == phase]
        phase_success = [row for row in successful if row["phase"] == phase]
        phase_eligible = _eligible_onsets(phase_rows, entry_ms_by_market=entry_ms_by_market)
        phase_captured = {(int(row["market_id"]), int(row["next_onset_ms"])) for row in phase_success}
        return {
            "rows": len(phase_rows),
            "triggers": len(phase_triggers),
            "triggersPer100Rows": len(phase_triggers) / len(phase_rows) * 100.0 if phase_rows else None,
            "successfulTriggers": len(phase_success),
            "triggerPrecision": len(phase_success) / len(phase_triggers) if phase_triggers else None,
            "eligibleUniqueBursts": len(phase_eligible),
            "capturedUniqueBursts": len(phase_captured & phase_eligible),
            "uniqueBurstCaptureRate": len(phase_captured & phase_eligible) / len(phase_eligible) if phase_eligible else None,
        }

    audit_payload: dict[str, Any] = {}
    for state in sorted(set(audit_counts) | set(audit_success)):
        count = audit_counts[state]
        success = audit_success[state]
        audit_payload[state] = {
            "triggers": count,
            "share": count / len(triggers) if triggers else None,
            "successfulTriggers": success,
            "precision": success / count if count else None,
        }

    markets = set(starts)
    return {
        "entryDelaySeconds": int(entry_delay_s),
        "cooldownSeconds": int(cooldown_s),
        "runtimeStateInputs": ["market_start_time", "our_first_entry_time", "our_last_action_time", "public_raw_hazard_score"],
        "targetTruthUsedForEligibility": False,
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
        "targetTruthAuditAtTrigger": audit_payload,
        "byMacroPhase": {phase: phase_payload(phase) for phase in PHASES},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Replay ordinary full-timeline OOF hazard scores using only strategy-owned actor state. "
            "Target POST_FIRST/IDLE truth is forbidden from trigger eligibility and appears only in after-the-fact audit tables."
        )
    )
    parser.add_argument("--scores", type=Path, default=DEFAULT_SCORES)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--entry-delays", default="1,3,5,10")
    parser.add_argument("--window-rows", type=int, default=600)
    parser.add_argument("--min-history-rows", type=int, default=120)
    args = parser.parse_args()

    try:
        delays = _parse_delays(args.entry_delays)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    window_rows = max(120, int(args.window_rows))
    min_history_rows = max(30, min(int(args.min_history_rows), window_rows))

    rows = _prepare(args.scores)
    if not rows:
        raise SystemExit("no 5s full-timeline OOF score rows found")
    folds = sorted({int(row["fold"]) for row in rows})
    markets = sorted({int(row["market_id"]) for row in rows})

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Substitute Target-truth runtime state with strategy-owned actor state. Thresholds use prior unlabeled scores only; "
            "entry eligibility uses a fixed public market-start delay; cooldown uses only our own simulated actions."
        ),
        "scores": str(args.scores.expanduser().resolve()),
        "horizonSeconds": 5,
        "runtimeTruthBoundary": (
            "cap2_position_state, cap2_burst_active, and cap2_risk_post_first_idle are audit-only. "
            "Changing those fields must not change trigger timestamps."
        ),
        "thresholdPolicy": (
            "Per fold and OPEN/MID/TAIL phase, each threshold uses only PRIOR raw scores from the same fold/phase rolling window. "
            "The current score enters history only after its threshold decision."
        ),
        "entryPolicy": (
            "For each test market, our simulated first entry occurs at first observed public OOF row + entryDelaySeconds. "
            "No Target first-entry timestamp is consulted. The first hazard action must also clear the candidate cooldown from our entry."
        ),
        "config": {
            "entryDelaysSeconds": delays,
            "windowRowsPerPhasePerFold": window_rows,
            "minHistoryRowsPerPhasePerFold": min_history_rows,
            "candidates": [name for name, _fraction, _cooldown in CANDIDATES],
        },
        "rows": len(rows),
        "markets": len(markets),
        "folds": folds,
        "candidates": {},
    }

    print(REPORT_VERSION, flush=True)
    print(f"rows={len(rows):,} markets={len(markets)} folds={len(folds)}", flush=True)
    print("TARGET POST_FIRST/IDLE TRUTH IS AUDIT-ONLY; IT DOES NOT GATE TRIGGERS.", flush=True)

    marks_cache: dict[float, tuple[list[dict[str, Any]], dict[str, Any]]] = {}
    for name, fraction, cooldown in CANDIDATES:
        if fraction not in marks_cache:
            marks_cache[fraction] = _adaptive_marks_by_fold(
                rows,
                fraction=fraction,
                window_rows=window_rows,
                min_history_rows=min_history_rows,
            )
        marked, coverage = marks_cache[fraction]
        candidate: dict[str, Any] = {
            "targetFraction": fraction,
            "cooldownSeconds": cooldown,
            "coverageByFold": coverage,
            "entryDelaySensitivity": {},
        }
        for delay in delays:
            result = _run_actor_policy(marked, entry_delay_s=delay, cooldown_s=cooldown)
            candidate["entryDelaySensitivity"][f"OPEN_PLUS_{delay:02d}S"] = result
            tail = result["byMacroPhase"]["TAIL"]
            print(
                f"{name} entry+{delay}s | triggers/market={result['triggersPerMarket']:.2f} "
                f"precision={result['triggerPrecision']:.3f} capture={result['uniqueBurstCaptureRate']:.3f} | "
                f"TAIL precision={tail.get('triggerPrecision')} capture={tail.get('uniqueBurstCaptureRate')}",
                flush=True,
            )
        report["candidates"][name] = candidate

    _write_json(args.report, report)
    print(f"report: {args.report.expanduser().resolve()}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
