from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import analyze_target_taker_actor_state_substitution_replay_v1 as actor
import train_target_taker_action_model_v1 as onset

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HAZARD_SCORES = (
    ROOT
    / "data"
    / "research"
    / "target_taker_post_first_idle_burst_hazard_ordinary_fulltimeline_v1_scores.csv"
)
DEFAULT_HAZARD_DATASET = ROOT / "data" / "research" / "target_taker_action_burst_hazard_v1.csv"
DEFAULT_ONSET_DATASET = ROOT / "data" / "research" / "target_taker_action_onset_preflight_v1.csv"
DEFAULT_REPORT = ROOT / "data" / "research" / "target_taker_trigger_conditioned_action_v1_report.json"
DEFAULT_OOF = ROOT / "data" / "research" / "target_taker_trigger_conditioned_action_v1_oof.csv"
REPORT_VERSION = "TARGET_TAKER_TRIGGER_CONDITIONED_ACTION_V1_ORDINARY_OOF"

PHASES = ("OPEN", "MID", "TAIL")
CANDIDATES = (
    ("TOP10_LEVEL_CD5", 0.10, 5),
    ("TOP20_LEVEL_CD5", 0.20, 5),
)
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
    tmp.write_text(json.dumps(onset._clean(payload), ensure_ascii=False, indent=2), encoding="utf-8")
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


def _hazard_index(path: Path) -> dict[tuple[int, int], dict[str, str]]:
    result: dict[tuple[int, int], dict[str, str]] = {}
    for row in _read_csv(path):
        market_id = _int(row.get("market_id"))
        sampled = _int(row.get("decision_sampled_at_ms"))
        if market_id is None or sampled is None:
            continue
        result[(market_id, sampled)] = row
    return result


def _next_action_labels(row: dict[str, Any]) -> dict[str, Any]:
    success = int(_int(row.get("cap2_label_next_burst_5s")) or 0)
    delta = _int(row.get("cap2_next_burst_delta_ms"))
    burst_type = str(row.get("cap2_next_burst_type") or "").upper()
    side = str(row.get("cap2_next_burst_side") or "").upper()
    subsequent = not burst_type.startswith("FIRST_") if burst_type else False

    if side in {"UP", "DOWN"}:
        clean_mixed = "CLEAN"
    elif burst_type in {"MIXED", "FIRST_MIXED"}:
        clean_mixed = "MIXED"
    else:
        clean_mixed = "UNKNOWN"

    return {
        "success": success,
        "next_delta_ms": delta,
        "next_burst_type": burst_type,
        "next_burst_side": side if side in {"UP", "DOWN"} else "",
        "next_is_subsequent": int(subsequent),
        "next_clean_mixed": clean_mixed,
    }


def _extract_triggers(
    *,
    marked: list[dict[str, Any]],
    hazard_index: dict[tuple[int, int], dict[str, str]],
    entry_delay_s: int,
    cooldown_s: int,
) -> list[dict[str, Any]]:
    starts = actor._market_starts(marked)
    entry_ms = {
        int(market_id): int(start) + int(entry_delay_s) * 1000
        for market_id, start in starts.items()
    }
    last_action = dict(entry_ms)
    triggers: list[dict[str, Any]] = []

    for row in marked:
        market_id = int(row["market_id"])
        sampled = int(row["sampled_ms"])
        if sampled < entry_ms[market_id]:
            continue
        if row.get("adaptive_threshold") is None or not bool(row.get("above_threshold")):
            continue
        if sampled - last_action[market_id] < int(cooldown_s) * 1000:
            continue

        source = hazard_index.get((market_id, sampled))
        if source is None:
            continue

        enriched: dict[str, Any] = {
            "fold": int(row["fold"]),
            "market_id": market_id,
            "sampled_ms": sampled,
            "seconds_left": _float(source.get("seconds_left")),
            "phase": str(row.get("phase") or source.get("macro_phase") or "UNKNOWN").upper(),
            "hazard_score": float(row["score"]),
            "hazard_threshold": float(row["adaptive_threshold"]),
        }
        enriched.update(_next_action_labels(source))
        delta = enriched.get("next_delta_ms")
        enriched["next_onset_ms"] = (
            sampled + int(delta)
            if int(enriched["success"]) == 1 and delta is not None and 0 < int(delta) <= 5000
            else None
        )
        for feature in onset.FROZEN16:
            enriched[feature] = source.get(feature, "")
        triggers.append(enriched)
        last_action[market_id] = sampled

    return triggers


def _dedupe_successful(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[tuple[int, int], dict[str, Any]] = {}
    for row in sorted(rows, key=lambda item: (int(item["sampled_ms"]), int(item["market_id"]))):
        if int(row.get("success") or 0) != 1 or row.get("next_onset_ms") is None:
            continue
        key = (int(row["market_id"]), int(row["next_onset_ms"]))
        chosen.setdefault(key, row)
    return sorted(chosen.values(), key=lambda item: (int(item["sampled_ms"]), int(item["market_id"])))


def _task_training_frame(
    frame: Any,
    *,
    task: str,
    test_start_ms: int,
    test_markets: set[int],
    pd: Any,
) -> Any:
    base = frame[
        (frame["region"] == "ORDINARY_PRE_SPECIAL")
        & (pd.to_numeric(frame["strict_past_within_2s"], errors="coerce") == 1)
        & (pd.to_numeric(frame["is_first_burst"], errors="coerce") == 0)
        & (pd.to_numeric(frame["burst_onset_ms"], errors="coerce") < int(test_start_ms))
        & (~frame["market_id"].isin(test_markets))
    ].copy()

    if task == "stage1":
        base = base[base["clean_mixed_label"].isin(["CLEAN", "MIXED"])].copy()
        base["label"] = (base["clean_mixed_label"] == "MIXED").astype(int)
    elif task == "side":
        base = base[
            (base["clean_mixed_label"] == "CLEAN")
            & base["clean_side_label"].isin(["UP", "DOWN"])
        ].copy()
        base["label"] = (base["clean_side_label"] == "UP").astype(int)
    else:
        raise ValueError(task)
    return base


def _fit_model(
    *,
    shared: Any,
    deps: dict[str, Any],
    frame: Any,
    features: list[str],
    seed: int,
    interactions: int,
    max_rounds: int,
    outer_bags: int,
) -> Any:
    pd = deps["pd"]
    y = frame["label"].astype(int)
    if len(frame) < 80 or set(int(v) for v in y.unique()) != {0, 1}:
        raise RuntimeError(
            f"insufficient training data: rows={len(frame)} classes={sorted(set(int(v) for v in y.unique()))}"
        )
    return shared._fit_classifier(
        deps,
        shared._numeric(pd, frame, features),
        y,
        interactions=min(max(0, int(interactions)), max(0, len(features) // 2)),
        max_rounds=max_rounds,
        outer_bags=outer_bags,
        seed=seed,
    )


def _phase_metrics(
    *,
    deps: dict[str, Any],
    rows: list[dict[str, Any]],
    label_key: str,
    score_key: str,
) -> dict[str, Any]:
    np = deps["np"]
    result: dict[str, Any] = {}
    for phase in PHASES:
        selected = [row for row in rows if str(row.get("phase") or "") == phase]
        if not selected:
            result[phase] = {"rows": 0}
            continue
        y = np.asarray([int(row[label_key]) for row in selected], dtype=int)
        p = np.asarray([float(row[score_key]) for row in selected], dtype=float)
        result[phase] = onset._rank_metrics(deps, y, p)
    return result


def _lead_metrics(
    *,
    deps: dict[str, Any],
    rows: list[dict[str, Any]],
    label_key: str,
    score_key: str,
) -> dict[str, Any]:
    np = deps["np"]
    result: dict[str, Any] = {}
    for name, low, high in LEAD_BINS:
        selected = []
        for row in rows:
            delta = _int(row.get("next_delta_ms"))
            if delta is None:
                continue
            if low < delta <= high:
                selected.append(row)
        if not selected:
            result[name] = {"rows": 0}
            continue
        y = np.asarray([int(row[label_key]) for row in selected], dtype=int)
        p = np.asarray([float(row[score_key]) for row in selected], dtype=float)
        result[name] = onset._rank_metrics(deps, y, p)
    return result


def _hard_accuracy(
    deps: dict[str, Any],
    rows: list[dict[str, Any]],
    label_key: str,
    score_key: str,
) -> dict[str, Any]:
    if not rows:
        return {"rows": 0}
    np = deps["np"]
    y = np.asarray([int(row[label_key]) for row in rows], dtype=int)
    score = np.asarray([float(row[score_key]) for row in rows], dtype=float)
    pred = (score >= 0.5).astype(int)
    return {
        "rows": int(len(rows)),
        "accuracyAt0p5Audit": float((pred == y).mean()),
        "balancedAccuracyAt0p5Audit": float(deps["balanced_accuracy_score"](y, pred)),
    }


def _trigger_base(rows: list[dict[str, Any]]) -> dict[str, Any]:
    markets = {int(row["market_id"]) for row in rows}
    success = [
        row
        for row in rows
        if int(row.get("success") or 0) == 1 and row.get("next_onset_ms") is not None
    ]
    unique = _dedupe_successful(rows)
    subsequent = [row for row in unique if int(row.get("next_is_subsequent") or 0) == 1]
    clean_mixed = Counter(str(row.get("next_clean_mixed") or "UNKNOWN") for row in subsequent)
    side = Counter(str(row.get("next_burst_side") or "UNKNOWN") for row in subsequent)

    def phase_payload(phase: str) -> dict[str, Any]:
        phase_rows = [row for row in rows if str(row.get("phase") or "") == phase]
        phase_success = [
            row
            for row in phase_rows
            if int(row.get("success") or 0) == 1 and row.get("next_onset_ms") is not None
        ]
        return {
            "triggers": len(phase_rows),
            "successfulTriggers": len(phase_success),
            "triggerPrecision": len(phase_success) / len(phase_rows) if phase_rows else None,
        }

    return {
        "triggers": len(rows),
        "markets": len(markets),
        "triggersPerMarket": len(rows) / len(markets) if markets else None,
        "successfulTriggers": len(success),
        "triggerPrecision": len(success) / len(rows) if rows else None,
        "uniqueSuccessfulBursts": len(unique),
        "duplicateSuccessfulTriggers": max(0, len(success) - len(unique)),
        "uniqueSubsequentBursts": len(subsequent),
        "nextCleanMixed": dict(clean_mixed),
        "nextCleanSide": dict(side),
        "byMacroPhase": {phase: phase_payload(phase) for phase in PHASES},
    }


def _evaluate_task(
    *,
    deps: dict[str, Any],
    rows: list[dict[str, Any]],
    task: str,
) -> dict[str, Any]:
    np = deps["np"]
    if task == "stage1":
        eligible = [
            row
            for row in rows
            if int(row.get("next_is_subsequent") or 0) == 1
            and str(row.get("next_clean_mixed") or "") in {"CLEAN", "MIXED"}
            and row.get("stage1_score") is not None
        ]
        for row in eligible:
            row["stage1_label"] = int(str(row["next_clean_mixed"]) == "MIXED")
        label_key = "stage1_label"
        score_key = "stage1_score"
    elif task == "side":
        eligible = [
            row
            for row in rows
            if int(row.get("next_is_subsequent") or 0) == 1
            and str(row.get("next_clean_mixed") or "") == "CLEAN"
            and str(row.get("next_burst_side") or "") in {"UP", "DOWN"}
            and row.get("side_score") is not None
        ]
        for row in eligible:
            row["side_label"] = int(str(row["next_burst_side"]) == "UP")
        label_key = "side_label"
        score_key = "side_score"
    else:
        raise ValueError(task)

    if not eligible:
        return {"rows": 0}
    y = np.asarray([int(row[label_key]) for row in eligible], dtype=int)
    p = np.asarray([float(row[score_key]) for row in eligible], dtype=float)
    payload = onset._rank_metrics(deps, y, p)
    payload["byMacroPhase"] = _phase_metrics(
        deps=deps, rows=eligible, label_key=label_key, score_key=score_key
    )
    payload["byLeadMs"] = _lead_metrics(
        deps=deps, rows=eligible, label_key=label_key, score_key=score_key
    )
    payload["hardDecisionAudit"] = _hard_accuracy(deps, eligible, label_key, score_key)
    return payload


def _write_oof(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "candidate",
        "fold",
        "market_id",
        "sampled_ms",
        "seconds_left",
        "phase",
        "hazard_score",
        "hazard_threshold",
        "success",
        "next_delta_ms",
        "next_onset_ms",
        "next_burst_type",
        "next_is_subsequent",
        "next_clean_mixed",
        "next_burst_side",
        "stage1_score",
        "side_score",
    ]
    resolved = path.expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    with resolved.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows([{field: row.get(field, "") for field in fields} for row in rows])


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Train ordinary strict-past action-onset EBMs only on historical markets, then score the SAME "
            "live-compatible phase-adaptive hazard triggers produced from full-timeline OOF hazard scores. "
            "Primary question: does absolute action side remain predictable at the actual hazard-trigger time?"
        )
    )
    parser.add_argument("--hazard-scores", type=Path, default=DEFAULT_HAZARD_SCORES)
    parser.add_argument("--hazard-dataset", type=Path, default=DEFAULT_HAZARD_DATASET)
    parser.add_argument("--onset-dataset", type=Path, default=DEFAULT_ONSET_DATASET)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--oof", type=Path, default=DEFAULT_OOF)
    parser.add_argument("--entry-delay-seconds", type=int, default=3)
    parser.add_argument("--window-rows", type=int, default=600)
    parser.add_argument("--min-history-rows", type=int, default=120)
    parser.add_argument("--interactions", type=int, default=10)
    parser.add_argument("--max-rounds", type=int, default=600)
    parser.add_argument("--outer-bags", type=int, default=3)
    args = parser.parse_args()

    shared = onset._shared()
    deps = shared._imports()
    pd = deps["pd"]

    hazard_rows = actor._prepare(args.hazard_scores)
    if not hazard_rows:
        raise SystemExit("no ordinary full-timeline hazard OOF rows")
    hazard_index = _hazard_index(args.hazard_dataset)

    onset_frame = pd.read_csv(args.onset_dataset)
    required = {
        "market_id",
        "burst_onset_ms",
        "region",
        "strict_past_within_2s",
        "is_first_burst",
        "clean_mixed_label",
        "clean_side_label",
        *onset.FROZEN16,
    }
    missing = sorted(required - set(onset_frame.columns))
    if missing:
        raise SystemExit("onset dataset missing columns: " + ", ".join(missing))
    onset_frame["market_id"] = pd.to_numeric(onset_frame["market_id"], errors="raise").astype(int)
    onset_frame["burst_onset_ms"] = pd.to_numeric(
        onset_frame["burst_onset_ms"], errors="raise"
    ).astype("int64")

    entry_delay = max(0, min(60, int(args.entry_delay_seconds)))
    window_rows = max(120, int(args.window_rows))
    min_history_rows = max(30, min(int(args.min_history_rows), window_rows))
    interactions = max(0, int(args.interactions))
    max_rounds = max(100, int(args.max_rounds))
    outer_bags = max(2, int(args.outer_bags))

    by_fold_score_rows: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in hazard_rows:
        by_fold_score_rows[int(row["fold"])].append(row)

    candidate_rows: dict[str, list[dict[str, Any]]] = {}
    threshold_audit: dict[str, Any] = {}
    for name, fraction, cooldown in CANDIDATES:
        marked, fold_thresholds = actor._adaptive_marks_by_fold(
            hazard_rows,
            fraction=float(fraction),
            window_rows=window_rows,
            min_history_rows=min_history_rows,
        )
        rows = _extract_triggers(
            marked=marked,
            hazard_index=hazard_index,
            entry_delay_s=entry_delay,
            cooldown_s=cooldown,
        )
        for row in rows:
            row["candidate"] = name
        candidate_rows[name] = rows
        threshold_audit[name] = fold_thresholds

    report: dict[str, Any] = {
        "reportVersion": REPORT_VERSION,
        "paperResearchOnly": True,
        "automaticStrategyPromotion": False,
        "causalClaim": False,
        "purpose": (
            "Bridge the validated 5s hazard trigger to an actionable next-burst decision. "
            "Hazard trigger eligibility is OOF, causal, phase-adaptive, and uses only our own entry/cooldown state. "
            "Action EBMs are trained only on strictly earlier ordinary action-onset rows."
        ),
        "trainingPopulation": "ORDINARY_PRE_SPECIAL strict-past action-onset rows before each OOF hazard fold",
        "testPopulation": "live-compatible ordinary full-timeline OOF hazard triggers",
        "primaryDecision": "absolute UP/DOWN side of the next CLEAN subsequent Target burst within 5s",
        "secondaryDecision": "CLEAN vs MIXED of the next subsequent Target burst within 5s",
        "whyNoTransitionModelYet": (
            "On action-onset OOF, raw SAME/FLIP was near random while absolute side was stronger. "
            "This bridge first tests the deployment-clean absolute-side path before adding self-state transition recursion."
        ),
        "config": {
            "entryDelaySeconds": entry_delay,
            "windowRowsPerPhasePerFold": window_rows,
            "minHistoryRowsPerPhasePerFold": min_history_rows,
            "candidates": [
                {"name": name, "fraction": fraction, "cooldownSeconds": cooldown}
                for name, fraction, cooldown in CANDIDATES
            ],
            "features": list(onset.FROZEN16),
            "interactions": interactions,
            "maxRounds": max_rounds,
            "outerBags": outer_bags,
        },
        "thresholdAudit": threshold_audit,
        "folds": [],
        "aggregate": {},
    }

    all_output_rows: list[dict[str, Any]] = []
    fold_numbers = sorted(by_fold_score_rows)
    print(REPORT_VERSION, flush=True)
    print(
        f"folds={len(fold_numbers)} entryDelay={entry_delay}s "
        f"candidates={','.join(name for name, _fraction, _cooldown in CANDIDATES)}",
        flush=True,
    )
    print("Special/post-special data is not used here.", flush=True)

    for fold in fold_numbers:
        score_group = by_fold_score_rows[fold]
        test_markets = {int(row["market_id"]) for row in score_group}
        test_start_ms = min(int(row["sampled_ms"]) for row in score_group)

        stage1_train = _task_training_frame(
            onset_frame,
            task="stage1",
            test_start_ms=test_start_ms,
            test_markets=test_markets,
            pd=pd,
        )
        side_train = _task_training_frame(
            onset_frame,
            task="side",
            test_start_ms=test_start_ms,
            test_markets=test_markets,
            pd=pd,
        )
        print(
            f"[FOLD {fold}] testMarkets={len(test_markets)} "
            f"stage1Train={len(stage1_train):,} sideTrain={len(side_train):,}",
            flush=True,
        )

        stage1_model = _fit_model(
            shared=shared,
            deps=deps,
            frame=stage1_train,
            features=onset.FROZEN16,
            seed=31000 + fold,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
        )
        side_model = _fit_model(
            shared=shared,
            deps=deps,
            frame=side_train,
            features=onset.FROZEN16,
            seed=32000 + fold,
            interactions=interactions,
            max_rounds=max_rounds,
            outer_bags=outer_bags,
        )

        fold_payload: dict[str, Any] = {
            "fold": fold,
            "testStartMs": test_start_ms,
            "testMarkets": len(test_markets),
            "stage1TrainRows": int(len(stage1_train)),
            "stage1TrainPositiveRate": float(stage1_train["label"].mean()),
            "sideTrainRows": int(len(side_train)),
            "sideTrainUpRate": float(side_train["label"].mean()),
            "candidates": {},
        }

        for candidate, _fraction, _cooldown in CANDIDATES:
            rows = [row for row in candidate_rows[candidate] if int(row["fold"]) == fold]
            if rows:
                X = pd.DataFrame(
                    [{feature: row.get(feature, "") for feature in onset.FROZEN16} for row in rows]
                )
                stage1_score = stage1_model.predict_proba(
                    shared._numeric(pd, X, onset.FROZEN16)
                )[:, 1]
                side_score = side_model.predict_proba(
                    shared._numeric(pd, X, onset.FROZEN16)
                )[:, 1]
                for row, mixed_score, up_score in zip(rows, stage1_score, side_score):
                    row["stage1_score"] = float(mixed_score)
                    row["side_score"] = float(up_score)
            unique_success = _dedupe_successful(rows)
            fold_payload["candidates"][candidate] = {
                "triggerBase": _trigger_base(rows),
                "stage1CleanVsMixed": _evaluate_task(deps=deps, rows=unique_success, task="stage1"),
                "absoluteSideUpVsDown": _evaluate_task(deps=deps, rows=unique_success, task="side"),
            }
            all_output_rows.extend(rows)

        report["folds"].append(fold_payload)
        _write_json(args.report, report)
        _write_oof(args.oof, all_output_rows)

    for candidate, _fraction, _cooldown in CANDIDATES:
        rows = [row for row in all_output_rows if row["candidate"] == candidate]
        unique_success = _dedupe_successful(rows)
        side_eval_rows = [
            row
            for row in unique_success
            if int(row.get("next_is_subsequent") or 0) == 1
            and str(row.get("next_clean_mixed") or "") == "CLEAN"
            and str(row.get("next_burst_side") or "") in {"UP", "DOWN"}
            and row.get("side_score") is not None
        ]
        side_correct = sum(
            int((float(row["side_score"]) >= 0.5) == (str(row["next_burst_side"]) == "UP"))
            for row in side_eval_rows
        )
        report["aggregate"][candidate] = {
            "triggerBase": _trigger_base(rows),
            "stage1CleanVsMixed": _evaluate_task(deps=deps, rows=unique_success, task="stage1"),
            "absoluteSideUpVsDown": _evaluate_task(deps=deps, rows=unique_success, task="side"),
            "endToEndAuditAt0p5": {
                "correctSideOnUniqueSubsequentCleanBursts": side_correct,
                "uniqueSubsequentCleanBursts": len(side_eval_rows),
                "correctSideGivenUniqueSubsequentCleanBurst": (
                    side_correct / len(side_eval_rows) if side_eval_rows else None
                ),
                "correctSidePerAllTrigger": side_correct / len(rows) if rows else None,
                "warning": (
                    "0.5 is an audit decision threshold on balanced EBM scores, not a promoted live threshold."
                ),
            },
        }

    _write_json(args.report, report)
    _write_oof(args.oof, all_output_rows)
    print(f"Report: {args.report.expanduser().resolve()}", flush=True)
    print(f"OOF:    {args.oof.expanduser().resolve()}", flush=True)
    print("No strategy was promoted.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
