from __future__ import annotations

from typing import Any, Iterable, Mapping

ALLOWED_STATUSES = {
    "IMPLEMENTED_CORRECT",
    "PARTIAL",
    "ABSENT",
    "UNKNOWN_NEEDS_MINIMAL_TEST",
}


def valid_status(status: str) -> bool:
    return status in ALLOWED_STATUSES or status.startswith("BLOCKED_BY_")


def build_capability_matrix(
    rows: Iterable[Mapping[str, Any]], *, evidence_cutoff: str
) -> dict[str, Any]:
    normalized = [dict(row) for row in rows]
    errors: list[str] = []
    required = {
        "capability",
        "status",
        "targetEvidence",
        "ourEvidence",
        "ourImplementation",
        "primaryModule",
        "blocker",
        "nextMinimalTest",
    }
    for index, row in enumerate(normalized):
        missing = sorted(required - set(row))
        if missing:
            errors.append(f"rows[{index}] missing {missing}")
        if not valid_status(str(row.get("status") or "")):
            errors.append(f"rows[{index}] invalid status {row.get('status')!r}")
    if errors:
        raise ValueError("; ".join(errors))
    return {
        "version": "BEHAVIORAL_ALIGNMENT_CAPABILITY_MATRIX_V1",
        "date": evidence_cutoff,
        "researchOnly": True,
        "runtimeAuthority": False,
        "allowedStatuses": sorted(ALLOWED_STATUSES) + ["BLOCKED_BY_<MODULE>"],
        "rows": normalized,
        "summary": {
            "capabilities": len(normalized),
            "byStatus": {
                status: sum(row["status"] == status for row in normalized)
                for status in sorted({str(row["status"]) for row in normalized})
            },
        },
    }
