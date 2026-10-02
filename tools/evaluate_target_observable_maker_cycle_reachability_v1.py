from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from typing import Any

import evaluate_target_replay_fidelity_gate_v1 as fidelity


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT / "target_observable_maker_cycle_reachability_v1_preregistered.json"
SOURCE_ROWS = OUT / "target_replay_fidelity_gate_v1_parents.csv"
SOURCE_REPORT = OUT / "target_replay_fidelity_gate_v1_report.json"
REPORT = OUT / "target_observable_maker_cycle_reachability_v1_report.json"
ROWS = OUT / "target_observable_maker_cycle_reachability_v1_patient_parents.csv"
MARKETS = [1569361, 1571387, 1572594]
PRIMARY_MARKET = 1572594
EPS = 1e-9


def finite(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    return float(value)


def load_source_rows() -> list[dict[str, Any]]:
    with SOURCE_ROWS.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def portfolio_metrics(rows: list[dict[str, Any]], fill_field: str) -> dict[str, Any]:
    target = {"UP": 0.0, "DOWN": 0.0}
    actual = {"UP": 0.0, "DOWN": 0.0}
    for row in rows:
        side = str(row["targetSide"]).upper()
        if side not in target:
            continue
        target[side] += finite(row["targetShares"])
        actual[side] += min(finite(row["targetShares"]), finite(row[fill_field]))
    target_gross = target["UP"] + target["DOWN"]
    target_pair = min(target.values())
    actual_pair = min(actual.values())
    l1_gap = abs(actual["UP"] - target["UP"]) + abs(actual["DOWN"] - target["DOWN"])
    return {
        "parents": len(rows),
        "targetShares": target,
        "hftShares": actual,
        "sideRecall": {
            side: actual[side] / target[side] if target[side] > EPS else None
            for side in ("UP", "DOWN")
        },
        "targetPairedShares": target_pair,
        "hftPairedShares": actual_pair,
        "pairedShareReachability": actual_pair / target_pair if target_pair > EPS else None,
        "targetAbsNetShares": abs(target["UP"] - target["DOWN"]),
        "hftAbsNetShares": abs(actual["UP"] - actual["DOWN"]),
        "signedNetGapShares": abs(
            (actual["UP"] - actual["DOWN"]) - (target["UP"] - target["DOWN"])
        ),
        "portfolioL1GapShares": l1_gap,
        "normalizedPortfolioL1Gap": l1_gap / target_gross if target_gross > EPS else None,
    }


def grouped_metrics(rows: list[dict[str, Any]], variant: str, fill_field: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for market_id in MARKETS:
        chosen = [
            row
            for row in rows
            if int(row["marketId"]) == market_id and str(row["variant"]) == variant
        ]
        output[str(market_id)] = portfolio_metrics(chosen, fill_field)
    return output


def replay_patient() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    fidelity.VARIANTS["ARRIVAL_PATIENT_RAW_RISK"] = {
        "arrivalAligned": True,
        "stripped": False,
        "queue": "risk",
    }
    summaries: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    source_report = json.loads(SOURCE_REPORT.read_text(encoding="utf-8"))
    last_received = {
        int(summary["marketId"]): int(summary["feed"]["lastReceivedMs"])
        for summary in source_report["marketSummaries"]
        if summary["variant"] == "ARRIVAL_RAW_RISK"
    }
    for market_id in MARKETS:
        parents = copy.deepcopy(fidelity.load_parents(market_id))
        original_last = {str(parent["parent_id"]): int(parent["last_target_ms"]) for parent in parents}
        # replay_variant derives cancel_request_ms as last_target_ms + 1000.
        # Push that request beyond the archive so the patient ceiling is truly GTC
        # for every executable Tape event, while restoring the real Target window
        # in the exported score rows below.
        patient_last_ms = int(last_received[market_id]) + fidelity.ENTRY_MS + fidelity.RESPONSE_MS + 5000
        for parent in parents:
            parent["last_target_ms"] = max(int(parent["first_target_ms"]), patient_last_ms)
        summary, market_rows = fidelity.replay_variant(
            market_id, parents, "ARRIVAL_PATIENT_RAW_RISK"
        )
        for row in market_rows:
            row["variant"] = "ARRIVAL_PATIENT_GTC"
            row["targetLastFillSecondMs"] = original_last[str(row["parentId"])]
            row["patientNoCancelBeforeTapeEnd"] = True
        summary["variant"] = "ARRIVAL_PATIENT_GTC"
        summary["offlineTeacherWindowExtendedOnlyToSuppressCancellation"] = True
        summaries.append(summary)
        rows.extend(market_rows)
    return summaries, rows


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    source_rows = load_source_rows()
    patient_summaries, patient_rows = replay_patient()
    if patient_rows:
        with ROWS.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(patient_rows[0]))
            writer.writeheader()
            writer.writerows(patient_rows)

    architectures = {
        "FOLLOWER_TARGET_WINDOW": grouped_metrics(
            source_rows, "FOLLOWER_RAW_RISK", "hftFilledByTargetWindow"
        ),
        "FOLLOWER_TERMINAL": grouped_metrics(
            source_rows, "FOLLOWER_RAW_RISK", "hftTerminalFilled"
        ),
        "ARRIVAL_TARGET_WINDOW": grouped_metrics(
            source_rows, "ARRIVAL_RAW_RISK", "hftFilledByTargetWindow"
        ),
        "ARRIVAL_TERMINAL": grouped_metrics(
            source_rows, "ARRIVAL_RAW_RISK", "hftTerminalFilled"
        ),
        "ARRIVAL_PATIENT_GTC_TERMINAL": {
            str(market_id): portfolio_metrics(
                [row for row in patient_rows if int(row["marketId"]) == market_id],
                "hftTerminalFilled",
            )
            for market_id in MARKETS
        },
    }
    key = str(PRIMARY_MARKET)
    follower_gap = finite(architectures["FOLLOWER_TARGET_WINDOW"][key]["normalizedPortfolioL1Gap"])
    arrival_window_gap = finite(architectures["ARRIVAL_TARGET_WINDOW"][key]["normalizedPortfolioL1Gap"])
    arrival_terminal_gap = finite(architectures["ARRIVAL_TERMINAL"][key]["normalizedPortfolioL1Gap"])
    patient_gap = finite(architectures["ARRIVAL_PATIENT_GTC_TERMINAL"][key]["normalizedPortfolioL1Gap"])
    arrival_gain = follower_gap - arrival_window_gap
    patient_gain = arrival_terminal_gap - patient_gap
    if patient_gap <= 0.15 + EPS and patient_gain >= 0.15 - EPS:
        decision = "PATIENT_PERSISTENCE_RECOVERS"
    elif arrival_gain >= 0.20 - EPS and patient_gain < 0.10 - EPS:
        decision = "CLOCK_ALIGNMENT_DOMINANT"
    elif patient_gap > 0.20 + EPS:
        decision = "OBSERVABLE_MAKER_SURFACE_STILL_UNREACHABLE"
    else:
        decision = "MIXED_NEED_CYCLE_PHASE_DECOMPOSITION"

    report = {
        "reportVersion": "TARGET_OBSERVABLE_MAKER_CYCLE_REACHABILITY_V1",
        "researchOnly": True,
        "performanceClaim": False,
        "preregisteredContract": PREREG.name,
        "question": "Does a patient persistent passive architecture restore the observable Target Maker portfolio manifold better than HFT-style follower/short-window execution?",
        "cohort": {
            "markets": MARKETS,
            "primaryChronologicalMarket": PRIMARY_MARKET,
            "openedDevelopmentOnly": True,
            "officialHftForward": False,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": fidelity.ENTRY_MS,
            "responseLatencyMs": fidelity.RESPONSE_MS,
            "pollMs": fidelity.POLL_MS,
            "partialFill": True,
            "dreamFill": False,
            "patientDifference": "GTC orders are not cancelled before Tape end; all fills still require real Tape/queue execution.",
        },
        "architectures": architectures,
        "primaryComparison": {
            "followerTargetWindowNormalizedL1Gap": follower_gap,
            "arrivalTargetWindowNormalizedL1Gap": arrival_window_gap,
            "arrivalTerminalNormalizedL1Gap": arrival_terminal_gap,
            "arrivalPatientTerminalNormalizedL1Gap": patient_gap,
            "arrivalAlignmentGapReduction": arrival_gain,
            "patientPersistenceGapReduction": patient_gain,
        },
        "patientReplaySummaries": patient_summaries,
        "decision": decision,
        "waitActOracleValue": "N/A: offline observable-state reachability audit, not a policy/value experiment",
        "limitations": [
            "Only fully-filled, high-confidence, single-placement Target Maker parents are observable; precision against Target-private unfilled/cancelled orders is unknowable.",
            "Patient GTC uses exact offline Target placements and is an architecture ceiling, not a runtime policy.",
            "Taker intervention, fees, terminal PnL and the complete Target economic cycle are intentionally outside this first small preflight.",
            "A negative result cannot distinguish hidden Target orders from private queue priority until own-wallet lifecycle calibration exists.",
        ],
        "artifacts": {
            "sourceFidelityReport": str(SOURCE_REPORT.resolve()),
            "patientParentRows": str(ROWS.resolve()),
        },
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "report": str(REPORT),
                "primaryComparison": report["primaryComparison"],
                "decision": decision,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
