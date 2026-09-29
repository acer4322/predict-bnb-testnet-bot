from __future__ import annotations

import collections
from typing import Any, Mapping


def _role(event: Mapping[str, Any]) -> str:
    return str((event.get("responsibility") or {}).get("role") or "UNKNOWN")


def economic_cycle_metrics(document: Mapping[str, Any]) -> dict[str, Any]:
    events = list(document.get("events") or [])
    births = [event for event in events if event.get("eventType") == "RESPONSIBILITY_BIRTH"]
    completed = [event for event in events if event.get("eventType") == "RESPONSIBILITY_COMPLETED"]
    payments = [
        event
        for event in events
        if event.get("eventType") in {"REPAIR_PAYMENT", "PHYSICAL_FILL_ALLOCATED"}
        and float((event.get("allocation") or {}).get("repairAllocation") or 0.0) > 1e-9
    ]
    action_events = [
        event
        for event in events
        if event.get("eventType")
        in {
            "CARRIER_SUBMITTED",
            "PHYSICAL_FILL_ALLOCATED",
            "REPAIR_PAYMENT",
            "RESPONSIBILITY_BIRTH",
            "DIRECT_PAIR_FILL",
        }
    ]
    physical_events = [
        event
        for event in events
        if float((event.get("allocation") or {}).get("physicalFillQty") or 0.0) > 1e-9
        or event.get("eventType") == "DIRECT_PAIR_FILL"
    ]
    decision_events = [
        event
        for event in events
        if event.get("eventType") in {"MANAGEMENT_DECISION", "ROUTER_DECISION"}
    ]
    allows = [event for event in decision_events if (event.get("decision") or {}).get("allow") is True]
    blockers = collections.Counter(
        str((event.get("decision") or {}).get("terminalBlocker"))
        for event in decision_events
        if (event.get("decision") or {}).get("terminalBlocker")
    )

    payments_by_responsibility: dict[str, set[int | None]] = collections.defaultdict(set)
    routes_by_responsibility: dict[str, set[str]] = collections.defaultdict(set)
    for event in payments:
        responsibility_id = (event.get("responsibility") or {}).get("responsibilityId")
        if responsibility_id is None:
            responsibility_id = (event.get("responsibility") or {}).get("parentId")
        key = str(responsibility_id)
        payments_by_responsibility[key].add(event.get("timeMs"))
        route = (event.get("execution") or {}).get("route")
        if route:
            routes_by_responsibility[key].add(str(route))

    completed_ids = {
        str((event.get("responsibility") or {}).get("responsibilityId")) for event in completed
    }
    multi_payment_completed = sum(
        len(payments_by_responsibility.get(responsibility_id, set())) >= 2
        for responsibility_id in completed_ids
    )
    shared_route_responsibilities = sum(
        (
            "MIXED" in routes
            or ({"PASSIVE", "ACTIVE"}.issubset(routes))
        )
        for routes in routes_by_responsibility.values()
    )

    # Economic role cycle count is semantic, not exact timestamp imitation.
    role_sequence: list[str] = []
    for event in events:
        event_type = event.get("eventType")
        role = _role(event)
        if event_type == "RESPONSIBILITY_BIRTH" and role == "EXPAND":
            token = "EXPAND"
        elif event_type in {"REPAIR_PAYMENT", "PHYSICAL_FILL_ALLOCATED"} and role == "REPAIR":
            token = "REPAIR"
        else:
            continue
        if not role_sequence or role_sequence[-1] != token:
            role_sequence.append(token)
    repair_expand_repair_rounds = sum(
        role_sequence[index : index + 3] == ["REPAIR", "EXPAND", "REPAIR"]
        for index in range(max(0, len(role_sequence) - 2))
    )
    expand_repair_expand_repair_cycles = sum(
        role_sequence[index : index + 4] == ["EXPAND", "REPAIR", "EXPAND", "REPAIR"]
        for index in range(max(0, len(role_sequence) - 3))
    )

    terminal_debt = document.get("summary", {}).get("terminalOutstandingDebt")
    if terminal_debt is None:
        debt_after = [
            (event.get("managerDebt") or {}).get("after")
            for event in events
            if (event.get("managerDebt") or {}).get("after") is not None
        ]
        terminal_debt = debt_after[-1] if debt_after else None
    summary = document.get("summary") or {}
    reported_rounds = summary.get("repairExpandRepairRounds")
    return {
        "marketId": document.get("marketId"),
        "source": document.get("source"),
        "responsibilitiesBorn": len(births),
        "responsibilitiesCompleted": len(completed),
        "repairPaymentClocks": len({event.get("timeMs") for event in payments}),
        "multiPaymentCompletedResponsibilities": multi_payment_completed,
        "multiPaymentCompletedShare": (
            multi_payment_completed / len(completed_ids) if completed_ids else None
        ),
        "compositeRepairThenExpandClocks": len(
            {event.get("timeMs") for event in events if event.get("eventType") == "COMPOSITE_CARRIER"}
        ),
        "activePassiveSharedResponsibilityCount": shared_route_responsibilities,
        "repairExpandRepairRoundsFromTrace": repair_expand_repair_rounds,
        "expandRepairExpandRepairCyclesFromTrace": expand_repair_expand_repair_cycles,
        "reportedRepairExpandRepairRounds": reported_rounds,
        "managementChecks": len(decision_events),
        "managementAllows": len(allows),
        "physicalActionEvents": len(physical_events),
        "responsibilityValidActionEvents": len(action_events),
        "checksPerPhysicalAction": (
            len(decision_events) / len(physical_events) if physical_events else None
        ),
        "terminalOutstandingDebt": terminal_debt,
        "terminalFloor": summary.get("terminalFloor"),
        "worstObservedFloor": summary.get("worstObservedFloor"),
        "pnlDiagnosticOnly": summary.get("pnlDiagnosticOnly"),
        "terminalBlockers": dict(blockers),
        "roleSequence": role_sequence,
        "coverage": document.get("coverage") or {},
    }
