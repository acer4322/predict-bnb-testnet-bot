from __future__ import annotations

from typing import Any, Mapping


def semantic_family(event: Mapping[str, Any]) -> str:
    event_type = str(event.get("eventType") or "UNKNOWN")
    role = str((event.get("responsibility") or {}).get("role") or "UNKNOWN")
    if event_type in {"REPAIR_PAYMENT", "PHYSICAL_FILL_ALLOCATED"} and role == "REPAIR":
        return "REPAIR_PAYMENT"
    if event_type == "RESPONSIBILITY_BIRTH" and role == "EXPAND":
        return "EXPAND_BIRTH"
    if event_type in {"MANAGEMENT_DECISION", "ROUTER_DECISION"}:
        return f"{role}_DECISION"
    return event_type


def align_by_semantic_state(
    our_document: Mapping[str, Any],
    target_document: Mapping[str, Any],
    *,
    max_phase_distance: float = 0.20,
) -> dict[str, Any]:
    """Align by lifecycle family and normalized phase, never exact timestamp."""
    our_events = list(our_document.get("events") or [])
    target_events = list(target_document.get("events") or [])
    unused_our = set(range(len(our_events)))
    matches: list[dict[str, Any]] = []
    unmatched_target: list[dict[str, Any]] = []
    for target in target_events:
        target_family = semantic_family(target)
        target_phase = target.get("normalizedPhase")
        candidates: list[tuple[float, int]] = []
        for index in unused_our:
            ours = our_events[index]
            if semantic_family(ours) != target_family:
                continue
            our_phase = ours.get("normalizedPhase")
            if target_phase is None or our_phase is None:
                distance = 1.0
            else:
                distance = abs(float(target_phase) - float(our_phase))
            if distance <= max_phase_distance:
                candidates.append((distance, index))
        if not candidates:
            unmatched_target.append(target)
            continue
        distance, index = min(candidates)
        unused_our.remove(index)
        matches.append(
            {
                "semanticFamily": target_family,
                "phaseDistance": distance,
                "targetSequence": target.get("sequence"),
                "ourSequence": our_events[index].get("sequence"),
            }
        )
    return {
        "marketId": our_document.get("marketId"),
        "alignmentUnit": "portfolio+responsibility+execution+normalizedPhase",
        "exactTimestampAlignment": False,
        "matches": matches,
        "unmatchedTarget": [
            {
                "sequence": event.get("sequence"),
                "semanticFamily": semantic_family(event),
                "normalizedPhase": event.get("normalizedPhase"),
            }
            for event in unmatched_target
        ],
        "unmatchedOur": [
            {
                "sequence": our_events[index].get("sequence"),
                "semanticFamily": semantic_family(our_events[index]),
                "normalizedPhase": our_events[index].get("normalizedPhase"),
            }
            for index in sorted(unused_our)
        ],
    }
