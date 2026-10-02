from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .schema import canonical_event, trace_document


def _phase(time_ms: int | None, market_window_ms: int = 300_000) -> tuple[float | None, float | None]:
    if time_ms is None:
        return None, None
    elapsed = int(time_ms) % market_window_ms
    return elapsed / float(market_window_ms), (market_window_ms - elapsed) / 1000.0


def _ordered(events: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    priority = {
        "MANAGEMENT_DECISION": 0,
        "ROUTER_DECISION": 1,
        "CARRIER_SUBMITTED": 2,
        "PHYSICAL_FILL_ALLOCATED": 3,
        "REPAIR_PAYMENT": 4,
        "RESPONSIBILITY_BIRTH": 5,
        "RESPONSIBILITY_COMPLETED": 6,
        "COMPOSITE_CARRIER": 7,
        "MARKET_SUMMARY": 9,
    }
    rows = list(events)
    rows.sort(
        key=lambda row: (
            row.get("timeMs") is None,
            int(row.get("timeMs") or 0),
            priority.get(str(row.get("eventType")), 8),
            int(row.get("sequence") or 0),
        )
    )
    for sequence, row in enumerate(rows):
        row["sequence"] = sequence
    return rows


def _summary_event(
    market_id: int,
    *,
    time_ms: int | None,
    responsibility_role: str = "UNKNOWN",
    metadata: dict[str, Any],
) -> dict[str, Any]:
    phase, seconds_left = _phase(time_ms)
    return canonical_event(
        source="OUR",
        evidence_class="SUMMARY_DERIVED",
        market_id=market_id,
        sequence=0,
        event_type="MARKET_SUMMARY",
        time_ms=time_ms,
        seconds_left=seconds_left,
        normalized_phase=phase,
        responsibility={"role": responsibility_role},
        metadata=metadata,
    )


def from_same_parent_parallel_result(
    payload: dict[str, Any], *, source_artifact: str
) -> dict[str, Any]:
    market_id = int(payload["marketId"])
    events: list[dict[str, Any]] = []
    seen_router: set[tuple[Any, ...]] = set()
    for row in payload.get("parallelRepairEvents") or []:
        key = (
            row.get("t"),
            row.get("event"),
            row.get("parentId"),
            row.get("epoch"),
            row.get("reason"),
            bool(row.get("allow")),
            row.get("managerDebt"),
            row.get("legalPhysicalQty"),
        )
        if key in seen_router:
            continue
        seen_router.add(key)
        time_ms = int(row["t"]) if row.get("t") is not None else None
        phase, seconds_left = _phase(time_ms)
        if row.get("secondsLeft") is not None:
            seconds_left = float(row["secondsLeft"])
            phase = min(1.0, max(0.0, 1.0 - seconds_left / 300.0))
        allow = bool(row.get("allow"))
        events.append(
            canonical_event(
                source="OUR",
                evidence_class="AUTHORITATIVE_EVENT",
                market_id=market_id,
                sequence=len(events),
                event_type="ROUTER_DECISION",
                time_ms=time_ms,
                seconds_left=seconds_left,
                normalized_phase=phase,
                portfolio={"floor": row.get("floor")},
                responsibility={
                    "responsibilityId": row.get("parentId"),
                    "objectiveId": f"OUR_PARENT:{row.get('parentId')}",
                    "generationId": row.get("epoch"),
                    "role": "REPAIR",
                    "side": row.get("parentSide"),
                    "parentId": row.get("parentId"),
                    "stateBefore": "OPEN",
                    "stateAfter": "OPEN",
                },
                manager_debt={"before": row.get("managerDebt"), "after": row.get("managerDebt")},
                execution={
                    "route": "ACTIVE",
                    "price": row.get("liveAsk"),
                    "quantity": row.get("legalPhysicalQty"),
                    "passiveLive": row.get("passiveLive"),
                    "activeLive": allow,
                },
                decision={
                    "module": "RepairExecutionRouter.SameParentAggregateParallelRepairPolicyV1",
                    "allow": allow,
                    "reason": row.get("reason"),
                    "terminalBlocker": None if allow else row.get("reason"),
                },
                recoverability={
                    "recoverable": (
                        float(row["projectedFloorAfterActive"]) + 1e-9 >= float(row["floor"])
                        if row.get("projectedFloorAfterActive") is not None and row.get("floor") is not None
                        else None
                    ),
                    "reason": "PROJECTED_ACTIVE_FLOOR_NON_DAMAGE",
                },
                metadata={
                    "postEpochChurn": row.get("postEpochChurn"),
                    "paymentProgress": row.get("paymentProgress"),
                    "paidSinceEpoch": row.get("paidSinceEpoch"),
                    "lineageAnchorExists": row.get("lineageAnchorExists"),
                    "projectedFloorAfterActive": row.get("projectedFloorAfterActive"),
                },
            )
        )

    for row in payload.get("allocationEvents") or []:
        time_ms = int(row["t"]) if row.get("t") is not None else None
        phase, seconds_left = _phase(time_ms)
        physical = float(row.get("fillInc") or 0.0)
        repair = float(row.get("repairInc") or 0.0)
        overflow = float(row.get("overflowInc") or 0.0)
        events.append(
            canonical_event(
                source="OUR",
                evidence_class="AUTHORITATIVE_EVENT",
                market_id=market_id,
                sequence=len(events),
                event_type="PHYSICAL_FILL_ALLOCATED",
                time_ms=time_ms,
                seconds_left=seconds_left,
                normalized_phase=phase,
                responsibility={
                    "responsibilityId": row.get("parentId"),
                    "objectiveId": f"OUR_PARENT:{row.get('parentId')}",
                    "role": "REPAIR",
                    "parentId": row.get("parentId"),
                    "stateBefore": "OPEN",
                    "stateAfter": "PAID" if float(row.get("parentDebtAfter") or 0.0) <= 1e-9 else "OPEN",
                },
                manager_debt={
                    "before": row.get("parentDebtBefore"),
                    "after": row.get("parentDebtAfter"),
                },
                execution={"carrierId": row.get("key"), "route": "UNKNOWN", "quantity": physical},
                allocation={
                    "physicalFillQty": physical,
                    "confirmedPaidQty": repair,
                    "repairAllocation": repair,
                    "overflowAllocation": overflow,
                },
                metadata={
                    "repairCumulative": row.get("repairCum"),
                    "overflowCumulative": row.get("overflowCum"),
                    "transitionOverflowTotal": row.get("transitionOverflowTotal"),
                },
            )
        )
    max_time = max((event.get("timeMs") or 0 for event in events), default=0) or None
    events.append(
        _summary_event(
            market_id,
            time_ms=max_time,
            responsibility_role="REPAIR",
            metadata={
                "decision": payload.get("decision"),
                "candidate": payload.get("candidate"),
                "control": payload.get("frozenControl"),
                "safety": payload.get("safety"),
                "gates": payload.get("gates"),
            },
        )
    )
    events = _ordered(events)
    candidate = payload.get("candidate") or {}
    return trace_document(
        source="OUR",
        market_id=market_id,
        events=events,
        provenance={
            "sourceArtifact": source_artifact,
            "adapter": "our_same_parent_parallel_result_v1",
            "winnerUsedForRuntime": False,
        },
        coverage={
            "routerDecisions": "AUTHORITATIVE_RECORDED_SUBSET",
            "allocationEvents": "AUTHORITATIVE_RECORDED_SUBSET",
            "allPhysicalFillClocks": "SUMMARY_ONLY",
            "portfolioTrajectory": "PARTIAL",
        },
        summary={
            "decision": payload.get("decision"),
            "fills": candidate.get("fills"),
            "repairExpandRepairRounds": candidate.get("rounds"),
            "terminalFloor": candidate.get("floor"),
            "pnlDiagnosticOnly": candidate.get("pnlDiagnosticOnly"),
            "terminalOutstandingDebt": candidate.get("overflowRemaining"),
            "safety": payload.get("safety"),
        },
    )


def from_v90d_result(payload: dict[str, Any], *, source_artifact: str) -> dict[str, Any]:
    market_id = int(payload["marketId"])
    raw_events = payload.get("events") or []
    submit = next((row for row in raw_events if row.get("t") is not None), {})
    time_ms = int(submit["t"]) if submit.get("t") is not None else None
    phase, seconds_left = _phase(time_ms)
    events: list[dict[str, Any]] = []
    for row in raw_events:
        row_time = int(row["t"]) if row.get("t") is not None else time_ms
        row_phase, row_seconds_left = _phase(row_time)
        is_submit = row.get("event") == "V90D_SUBMIT_RESULT"
        manager_debt = row.get("managerDebtAtSubmit", row.get("managerDebt"))
        events.append(
            canonical_event(
                source="OUR",
                evidence_class=("AUTHORITATIVE_EVENT" if row.get("t") is not None else "SUMMARY_DERIVED"),
                market_id=market_id,
                sequence=len(events),
                event_type="CARRIER_SUBMITTED" if is_submit else "MANAGEMENT_DECISION",
                time_ms=row_time,
                seconds_left=row_seconds_left,
                normalized_phase=row_phase,
                portfolio={"floor": row.get("floorBefore")},
                responsibility={
                    "responsibilityId": row.get("parentId"),
                    "objectiveId": f"OUR_PARENT:{row.get('parentId')}",
                    "role": "REPAIR",
                    "side": row.get("side"),
                    "parentId": row.get("parentId"),
                    "stateBefore": "OPEN",
                    "stateAfter": "OPEN",
                },
                manager_debt={"before": manager_debt, "after": manager_debt},
                execution={
                    "carrierId": row.get("key"),
                    "route": "PASSIVE",
                    "price": row.get("price"),
                    "quantity": row.get("candidateQty", row.get("venueMinQty")),
                },
                decision={
                    "module": "CarrierBudget.MinimumLegalIncrementalRepairV90D",
                    "allow": bool(row.get("return", True)),
                    "reason": row.get("event"),
                },
                recoverability={
                    "recoverable": (
                        float(row["hypFloorAfter"]) + 1e-9 >= float(row["floorBefore"])
                        if row.get("hypFloorAfter") is not None and row.get("floorBefore") is not None
                        else None
                    ),
                    "reason": "INCREMENTAL_REPAIR_FLOOR_IMPROVEMENT",
                },
                metadata={
                    "venueMinQty": row.get("venueMinQty"),
                    "floorAfter": row.get("hypFloorAfter"),
                    "floorDelta": row.get("hypFloorDelta"),
                },
            )
        )
    candidate = payload.get("candidateV90D") or {}
    fill_qty = float(candidate.get("fillQty") or 0.0)
    debt_before = candidate.get("managerDebtAtSubmit")
    debt_after = candidate.get("residualDebtAfterObservedFill")
    if fill_qty > 0:
        events.append(
            canonical_event(
                source="OUR",
                evidence_class="SUMMARY_DERIVED",
                market_id=market_id,
                sequence=len(events),
                event_type="REPAIR_PAYMENT",
                time_ms=time_ms,
                seconds_left=seconds_left,
                normalized_phase=phase,
                portfolio={"floor": candidate.get("terminalFloor")},
                responsibility={
                    "responsibilityId": raw_events[0].get("parentId") if raw_events else None,
                    "objectiveId": (
                        f"OUR_PARENT:{raw_events[0].get('parentId')}" if raw_events else None
                    ),
                    "role": "REPAIR",
                    "side": raw_events[0].get("side") if raw_events else None,
                    "parentId": raw_events[0].get("parentId") if raw_events else None,
                    "stateBefore": "OPEN",
                    "stateAfter": "OPEN" if float(debt_after or 0.0) > 1e-9 else "COMPLETED",
                },
                manager_debt={"before": debt_before, "after": debt_after},
                execution={
                    "carrierId": (candidate.get("submitKeys") or [None])[0],
                    "route": "PASSIVE",
                    "quantity": fill_qty,
                },
                allocation={
                    "physicalFillQty": fill_qty,
                    "confirmedPaidQty": fill_qty,
                    "repairAllocation": fill_qty,
                    "overflowAllocation": 0.0,
                },
                metadata={
                    "clockLimitation": "fill clock absent from compact result; submit clock retained",
                    "actualFillConfirmedByResultGate": bool((payload.get("gates") or {}).get("oneMinLegalActualFill")),
                },
            )
        )
    events.append(
        _summary_event(
            market_id,
            time_ms=time_ms,
            responsibility_role="REPAIR",
            metadata={
                "decision": payload.get("decision"),
                "candidate": candidate,
                "control": payload.get("baselineV90A"),
                "safety": payload.get("safety"),
                "gates": payload.get("gates"),
            },
        )
    )
    return trace_document(
        source="OUR",
        market_id=market_id,
        events=_ordered(events),
        provenance={
            "sourceArtifact": source_artifact,
            "adapter": "our_v90d_min_legal_incremental_v1",
            "winnerUsedForRuntime": False,
        },
        coverage={
            "admissionAndSubmit": "AUTHORITATIVE_EVENT",
            "actualFill": "CONFIRMED_SUMMARY_WITHOUT_DISTINCT_FILL_CLOCK",
            "secondPaymentOpportunity": "NOT_EXERCISED_BY_PREREGISTERED_DESIGN",
            "portfolioTrajectory": "TERMINAL_AND_WORST_ONLY",
        },
        summary={
            "decision": payload.get("decision"),
            "fills": candidate.get("fills"),
            "terminalFloor": candidate.get("terminalFloor"),
            "worstObservedFloor": candidate.get("worstObservedFloor"),
            "pnlDiagnosticOnly": candidate.get("pnl"),
            "terminalOutstandingDebt": candidate.get("residualDebtAfterObservedFill"),
            "safety": payload.get("safety"),
        },
    )


def from_scheduler_funnel_result(
    payload: dict[str, Any], *, source_artifact: str
) -> dict[str, Any]:
    market_id = int(payload["marketId"])
    events: list[dict[str, Any]] = []
    for row in payload.get("rows") or []:
        time_ms = int(row["t"]) if row.get("t") is not None else None
        phase, seconds_left = _phase(time_ms)
        reason = str(row.get("reason") or "UNKNOWN")
        allow = bool(row.get("submit"))
        events.append(
            canonical_event(
                source="OUR",
                evidence_class="AUTHORITATIVE_EVENT",
                market_id=market_id,
                sequence=len(events),
                event_type="MANAGEMENT_DECISION",
                time_ms=time_ms,
                seconds_left=seconds_left,
                normalized_phase=phase,
                opportunity={
                    "thesisSide": row.get("thesisSideBefore"),
                    "score": row.get("pExpand"),
                    "state": "FAVORABLE_EXPAND_CANDIDATE",
                },
                responsibility={
                    "responsibilityId": row.get("parentId"),
                    "objectiveId": row.get("objectiveId"),
                    "generationId": row.get("generationAuthorizedBefore"),
                    "role": "EXPAND",
                    "side": row.get("side"),
                    "parentId": row.get("parentId"),
                    "stateBefore": "OPEN" if row.get("parentId") is not None else None,
                    "stateAfter": "OPEN" if row.get("parentId") is not None else None,
                },
                manager_debt={"before": row.get("coordDebtBefore"), "after": row.get("coordDebtBefore")},
                execution={"carrierId": row.get("key"), "route": "UNKNOWN"},
                decision={
                    "module": "ExpandAdmission.V83ViaContinuousResponsibilityScheduler",
                    "allow": allow,
                    "reason": reason,
                    "terminalBlocker": None if allow else reason,
                },
                recoverability={
                    "recoverable": row.get("recoverable"),
                    "reason": row.get("recoverabilityReason"),
                },
                metadata={
                    "clockSource": row.get("clockSource"),
                    "afterKind": row.get("afterKind"),
                    "expandOccupiedBefore": row.get("expandOccupiedBefore"),
                    "recoverability": row.get("recoverability"),
                },
            )
        )
    max_time = max((event.get("timeMs") or 0 for event in events), default=0) or None
    events.append(
        _summary_event(
            market_id,
            time_ms=max_time,
            responsibility_role="UNKNOWN",
            metadata={
                "decision": "FUNNEL_DIAGNOSTIC",
                "fills": payload.get("fills"),
                "rounds": payload.get("rounds"),
                "pnlDiagnosticOnly": payload.get("pnlDiagnosticOnly"),
                "terminalFloor": payload.get("floor"),
                "scheduler": payload.get("scheduler"),
                "reasonCounts": payload.get("reasonCounts"),
                "safety": payload.get("safety"),
            },
        )
    )
    return trace_document(
        source="OUR",
        market_id=market_id,
        events=_ordered(events),
        provenance={
            "sourceArtifact": source_artifact,
            "adapter": "our_continuous_scheduler_funnel_v1",
            "winnerUsedForRuntime": False,
        },
        coverage={
            "managementDecisionClocks": "AUTHORITATIVE",
            "physicalFillClocks": "SUMMARY_ONLY",
            "responsibilityLifecycle": "PARTIAL",
            "portfolioTrajectory": "TERMINAL_ONLY",
        },
        summary={
            "fills": payload.get("fills"),
            "repairExpandRepairRounds": payload.get("rounds"),
            "terminalFloor": payload.get("floor"),
            "pnlDiagnosticOnly": payload.get("pnlDiagnosticOnly"),
            "schedulerChecks": payload.get("schedulerChecks"),
            "scheduler": payload.get("scheduler"),
            "reasonCounts": payload.get("reasonCounts"),
            "safety": payload.get("safety"),
        },
    )


def load_our_trace(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    payload = json.loads(source.read_text(encoding="utf-8"))
    version = str(payload.get("version") or "")
    artifact = source.as_posix()
    if "SAME_PARENT_PARALLEL_REPAIR_HFT_SMOKE" in version:
        return from_same_parent_parallel_result(payload, source_artifact=artifact)
    if "V90D_MIN_LEGAL_INCREMENTAL" in version:
        return from_v90d_result(payload, source_artifact=artifact)
    if "GUARDED_CYCLE" in version and isinstance(payload.get("rows"), list):
        return from_scheduler_funnel_result(payload, source_artifact=artifact)
    raise ValueError(f"no OUR trace adapter for {version!r} ({source})")
