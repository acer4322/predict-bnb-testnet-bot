from __future__ import annotations

from typing import Any, Mapping

from .aligner import align_by_semantic_state
from .economic_cycle_metrics import economic_cycle_metrics


def _late_live_debt_blocks(document: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in document.get("events") or []:
        decision = event.get("decision") or {}
        debt = event.get("managerDebt") or {}
        seconds_left = event.get("secondsLeft")
        reason = str(decision.get("terminalBlocker") or decision.get("reason") or "")
        if (
            seconds_left is not None
            and float(seconds_left) <= 180.0
            and float(debt.get("before") or 0.0) > 1e-9
            and "LATE" in reason.upper()
        ):
            rows.append(
                {
                    "sequence": event.get("sequence"),
                    "timeMs": event.get("timeMs"),
                    "secondsLeft": seconds_left,
                    "managerDebt": debt.get("before"),
                    "reason": reason,
                }
            )
    return rows


def compare_market(
    our_document: Mapping[str, Any], target_document: Mapping[str, Any]
) -> dict[str, Any]:
    if int(our_document["marketId"]) != int(target_document["marketId"]):
        raise ValueError("cannot compare different markets")
    our_metrics = economic_cycle_metrics(our_document)
    target_metrics = economic_cycle_metrics(target_document)
    gaps: list[dict[str, Any]] = []
    if target_metrics["responsibilitiesCompleted"] > our_metrics["responsibilitiesCompleted"]:
        gaps.append(
            {
                "capability": "Persistent Responsibility / Completion Density",
                "primaryModule": "Completion",
                "evidence": {
                    "targetCompleted": target_metrics["responsibilitiesCompleted"],
                    "ourCompleted": our_metrics["responsibilitiesCompleted"],
                },
            }
        )
    if target_metrics["multiPaymentCompletedResponsibilities"] > our_metrics["multiPaymentCompletedResponsibilities"]:
        gaps.append(
            {
                "capability": "Multi-payment Repair Continuation",
                "primaryModule": "RepairExecutionRouter",
                "evidence": {
                    "targetMultiPaymentCompleted": target_metrics[
                        "multiPaymentCompletedResponsibilities"
                    ],
                    "ourMultiPaymentCompleted": our_metrics[
                        "multiPaymentCompletedResponsibilities"
                    ],
                    "ourTerminalOutstandingDebt": our_metrics["terminalOutstandingDebt"],
                },
            }
        )
    if target_metrics["compositeRepairThenExpandClocks"] > our_metrics["compositeRepairThenExpandClocks"]:
        gaps.append(
            {
                "capability": "Composite Repair-first / Overflow-second Relay",
                "primaryModule": "Allocation Ledger",
                "evidence": {
                    "targetCompositeClocks": target_metrics["compositeRepairThenExpandClocks"],
                    "ourCompositeClocks": our_metrics["compositeRepairThenExpandClocks"],
                },
            }
        )
    late_blocks = _late_live_debt_blocks(our_document)
    if late_blocks:
        gaps.append(
            {
                "capability": "Late Existing-debt Repair Payment",
                "primaryModule": "RepairExecutionRouter",
                "evidence": {
                    "ourLateBlocksWithLiveDebt": len(late_blocks),
                    "sample": late_blocks[:5],
                },
            }
        )
    return {
        "version": "OUR_TARGET_BEHAVIORAL_DIFF_V1",
        "researchOnly": True,
        "runtimeAuthority": False,
        "marketId": int(our_document["marketId"]),
        "ourMetrics": our_metrics,
        "targetMetrics": target_metrics,
        "gaps": gaps,
        "alignment": align_by_semantic_state(our_document, target_document),
        "interpretationBoundary": [
            "Target trace is post-market reconstruction and cannot authorize runtime action",
            "coverage differences are explicit; missing OUR event detail is not interpreted as a factual zero",
            "alignment uses responsibility semantics and normalized phase, not exact timestamps",
        ],
    }
