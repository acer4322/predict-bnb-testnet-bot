from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable, Mapping

TRACE_SCHEMA_VERSION = "OUR_TARGET_SEMANTIC_TRACE_SCHEMA_V1"

EVIDENCE_CLASSES = {
    "AUTHORITATIVE_EVENT",
    "POST_MARKET_RECONSTRUCTION",
    "SUMMARY_DERIVED",
}

EVENT_TYPES = {
    "MARKET_SUMMARY",
    "MANAGEMENT_DECISION",
    "ROUTER_DECISION",
    "CARRIER_SUBMITTED",
    "PHYSICAL_FILL_ALLOCATED",
    "RESPONSIBILITY_BIRTH",
    "REPAIR_PAYMENT",
    "RESPONSIBILITY_COMPLETED",
    "DIRECT_PAIR_FILL",
    "COMPOSITE_CARRIER",
}

ROLES = {"REPAIR", "EXPAND", "DIRECT_PAIR", "NONE", "UNKNOWN"}
ROUTES = {"PASSIVE", "ACTIVE", "MIXED", "UNKNOWN", None}


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def canonical_event(
    *,
    source: str,
    evidence_class: str,
    market_id: int,
    sequence: int,
    event_type: str,
    time_ms: int | None = None,
    seconds_left: float | None = None,
    normalized_phase: float | None = None,
    portfolio: Mapping[str, Any] | None = None,
    opportunity: Mapping[str, Any] | None = None,
    responsibility: Mapping[str, Any] | None = None,
    manager_debt: Mapping[str, Any] | None = None,
    execution: Mapping[str, Any] | None = None,
    allocation: Mapping[str, Any] | None = None,
    ownership: Mapping[str, Any] | None = None,
    decision: Mapping[str, Any] | None = None,
    recoverability: Mapping[str, Any] | None = None,
    risk_capacity: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one stable semantic event with explicit unknowns.

    Adapters must not silently upgrade reconstructed or aggregate evidence to an
    authoritative physical clock.  Missing values stay ``None`` instead of
    being guessed from Target behavior or post-market outcome.
    """
    if source not in {"OUR", "TARGET"}:
        raise ValueError(f"invalid source: {source}")
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"invalid evidence_class: {evidence_class}")
    if event_type not in EVENT_TYPES:
        raise ValueError(f"invalid event_type: {event_type}")

    out = {
        "source": source,
        "evidenceClass": evidence_class,
        "marketId": int(market_id),
        "sequence": int(sequence),
        "eventType": event_type,
        "timeMs": int(time_ms) if time_ms is not None else None,
        "secondsLeft": _float_or_none(seconds_left),
        "normalizedPhase": _float_or_none(normalized_phase),
        "portfolio": {
            "upShares": None,
            "downShares": None,
            "upCost": None,
            "downCost": None,
            "totalCost": None,
            "floor": None,
            "bestPayoff": None,
        },
        "opportunity": {
            "thesisSide": None,
            "score": None,
            "state": None,
        },
        "responsibility": {
            "responsibilityId": None,
            "objectiveId": None,
            "generationId": None,
            "role": "UNKNOWN",
            "side": None,
            "parentId": None,
            "childId": None,
            "stateBefore": None,
            "stateAfter": None,
        },
        "managerDebt": {"before": None, "after": None},
        "execution": {
            "carrierId": None,
            "route": None,
            "price": None,
            "quantity": None,
            "passiveReservedQty": None,
            "activeReservedQty": None,
            "passiveLive": None,
            "activeLive": None,
        },
        "allocation": {
            "physicalFillQty": 0.0,
            "confirmedPaidQty": 0.0,
            "repairAllocation": 0.0,
            "overflowAllocation": 0.0,
            "newResponsibilityId": None,
        },
        "ownership": {"before": None, "after": None},
        "decision": {
            "module": None,
            "allow": None,
            "reason": None,
            "terminalBlocker": None,
        },
        "recoverability": {
            "recoverable": None,
            "prospectiveRepairCapacity": None,
            "reason": None,
        },
        "riskCapacity": {"before": None, "after": None},
        "metadata": {},
    }
    for key, values in (
        ("portfolio", portfolio),
        ("opportunity", opportunity),
        ("responsibility", responsibility),
        ("managerDebt", manager_debt),
        ("execution", execution),
        ("allocation", allocation),
        ("ownership", ownership),
        ("decision", decision),
        ("recoverability", recoverability),
        ("riskCapacity", risk_capacity),
    ):
        if values:
            out[key].update(dict(values))
    if metadata:
        out["metadata"].update(dict(metadata))
    return out


def trace_document(
    *,
    source: str,
    market_id: int,
    events: Iterable[Mapping[str, Any]],
    provenance: Mapping[str, Any],
    coverage: Mapping[str, Any] | None = None,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schemaVersion": TRACE_SCHEMA_VERSION,
        "researchOnly": True,
        "runtimeAuthority": False,
        "source": source,
        "marketId": int(market_id),
        "provenance": dict(provenance),
        "coverage": dict(coverage or {}),
        "summary": dict(summary or {}),
        "events": [deepcopy(dict(event)) for event in events],
    }


def validate_trace_document(document: Mapping[str, Any]) -> list[str]:
    errors: list[str] = []
    if document.get("schemaVersion") != TRACE_SCHEMA_VERSION:
        errors.append("schemaVersion mismatch")
    if document.get("source") not in {"OUR", "TARGET"}:
        errors.append("source must be OUR or TARGET")
    try:
        market_id = int(document.get("marketId"))
    except (TypeError, ValueError):
        errors.append("marketId must be an integer")
        market_id = None
    events = document.get("events")
    if not isinstance(events, list):
        return errors + ["events must be a list"]

    previous_sequence = -1
    previous_time: int | None = None
    for index, event in enumerate(events):
        prefix = f"events[{index}]"
        if event.get("source") != document.get("source"):
            errors.append(f"{prefix}.source mismatch")
        if market_id is not None and event.get("marketId") != market_id:
            errors.append(f"{prefix}.marketId mismatch")
        if event.get("evidenceClass") not in EVIDENCE_CLASSES:
            errors.append(f"{prefix}.evidenceClass invalid")
        if event.get("eventType") not in EVENT_TYPES:
            errors.append(f"{prefix}.eventType invalid")
        sequence = event.get("sequence")
        if not isinstance(sequence, int) or sequence <= previous_sequence:
            errors.append(f"{prefix}.sequence must be strictly increasing")
        elif isinstance(sequence, int):
            previous_sequence = sequence
        time_ms = event.get("timeMs")
        if time_ms is not None:
            if not isinstance(time_ms, int):
                errors.append(f"{prefix}.timeMs must be int or null")
            elif previous_time is not None and time_ms < previous_time:
                errors.append(f"{prefix}.timeMs is not chronological")
            else:
                previous_time = time_ms
        phase = event.get("normalizedPhase")
        if phase is not None and not (0.0 <= float(phase) <= 1.0):
            errors.append(f"{prefix}.normalizedPhase outside [0,1]")
        responsibility = event.get("responsibility") or {}
        if responsibility.get("role") not in ROLES:
            errors.append(f"{prefix}.responsibility.role invalid")
        execution = event.get("execution") or {}
        if execution.get("route") not in ROUTES:
            errors.append(f"{prefix}.execution.route invalid")
        allocation = event.get("allocation") or {}
        physical = float(allocation.get("physicalFillQty") or 0.0)
        repair = float(allocation.get("repairAllocation") or 0.0)
        overflow = float(allocation.get("overflowAllocation") or 0.0)
        if min(physical, repair, overflow) < -1e-9:
            errors.append(f"{prefix}.allocation has negative quantity")
        if physical > 1e-9 and abs(physical - repair - overflow) > 1e-7:
            errors.append(f"{prefix}.allocation violates physical conservation")
        debt = event.get("managerDebt") or {}
        before = debt.get("before")
        if before is not None and repair > float(before) + 1e-7:
            errors.append(f"{prefix}.repairAllocation exceeds manager debt")
    return errors


def trace_json_schema() -> dict[str, Any]:
    """Machine-readable structural schema for stored trace documents."""
    nullable_number = {"type": ["number", "null"]}
    nullable_integer = {"type": ["integer", "null"]}
    nullable_string = {"type": ["string", "null"]}
    event_properties = {
        "source": {"enum": ["OUR", "TARGET"]},
        "evidenceClass": {"enum": sorted(EVIDENCE_CLASSES)},
        "marketId": {"type": "integer"},
        "sequence": {"type": "integer", "minimum": 0},
        "eventType": {"enum": sorted(EVENT_TYPES)},
        "timeMs": nullable_integer,
        "secondsLeft": nullable_number,
        "normalizedPhase": {"type": ["number", "null"], "minimum": 0, "maximum": 1},
        "portfolio": {"type": "object"},
        "opportunity": {"type": "object"},
        "responsibility": {"type": "object"},
        "managerDebt": {"type": "object"},
        "execution": {"type": "object"},
        "allocation": {"type": "object"},
        "ownership": {"type": "object"},
        "decision": {"type": "object"},
        "recoverability": {"type": "object"},
        "riskCapacity": {"type": "object"},
        "metadata": {"type": "object"},
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": TRACE_SCHEMA_VERSION,
        "title": "OUR x Target Behavioral Semantic Trace V1",
        "description": (
            "Research-only normalized lifecycle evidence. Target post-market fields "
            "are never runtime authority. Null means unavailable, not zero."
        ),
        "type": "object",
        "required": [
            "schemaVersion",
            "researchOnly",
            "runtimeAuthority",
            "source",
            "marketId",
            "provenance",
            "coverage",
            "summary",
            "events",
        ],
        "properties": {
            "schemaVersion": {"const": TRACE_SCHEMA_VERSION},
            "researchOnly": {"const": True},
            "runtimeAuthority": {"const": False},
            "source": {"enum": ["OUR", "TARGET"]},
            "marketId": {"type": "integer"},
            "provenance": {"type": "object"},
            "coverage": {"type": "object"},
            "summary": {"type": "object"},
            "events": {
                "type": "array",
                "items": {
                    "type": "object",
                    "required": list(event_properties),
                    "properties": event_properties,
                    "additionalProperties": False,
                },
            },
        },
        "additionalProperties": False,
        "$defs": {"nullableString": nullable_string},
        "x-invariants": [
            "physicalFillQty = repairAllocation + overflowAllocation when physicalFillQty > 0",
            "repairAllocation <= managerDebt.before when managerDebt.before is known",
            "event sequence is strictly increasing and timeMs is nondecreasing when present",
            "SUMMARY_DERIVED and POST_MARKET_RECONSTRUCTION must never be treated as runtime authority",
        ],
    }
