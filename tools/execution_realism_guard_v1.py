from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "data" / "research" / "simulation_execution_realism_contract_v1.json"
DIAGNOSTIC_ENV = "BTC5M_DIAGNOSTIC_OPTIMISTIC_EXECUTION"

OPTIMISTIC_MAKER_MODES = {
    "QUEUECLEAR_PASS",
    "DEPLETE_PASS",
    "PASS_THROUGH_FULL_ORDER",
    "INSTANT_FULL_FILL",
    "FILL_ON_TOUCH",
}
OPTIMISTIC_TAKER_MODES = {
    "INSTANT",
    "INSTANT_OBSERVED_ASK",
    "PAPER_IMMEDIATE_FILL",
}


def load_contract() -> dict[str, Any]:
    if not CONTRACT_PATH.exists():
        raise RuntimeError(f"execution-realism contract missing: {CONTRACT_PATH}")
    data = json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))
    if data.get("status") != "ACTIVE_CANONICAL_RESEARCH_GUARD":
        raise RuntimeError("execution-realism contract is not active")
    return data


def diagnostic_override_enabled() -> bool:
    return os.environ.get(DIAGNOSTIC_ENV, "").strip() == "1"


def require_performance_grade_execution(
    *,
    test_name: str,
    maker_mode: str,
    taker_mode: str,
    closed_loop: bool,
    uses_predict_execution_tape: bool,
    uses_hftbacktest: bool,
    diagnostic_only: bool = False,
) -> dict[str, Any]:
    """Enforce realistic execution for any performance-grade simulation.

    Optimistic execution remains available only for explicit diagnostic/reproducibility
    work. The function returns metadata suitable for embedding in reports.
    """
    contract = load_contract()
    maker = str(maker_mode or "").upper()
    taker = str(taker_mode or "").upper()
    optimistic = maker in OPTIMISTIC_MAKER_MODES or taker in OPTIMISTIC_TAKER_MODES
    diag = bool(diagnostic_only or diagnostic_override_enabled())

    if optimistic and not diag:
        raise RuntimeError(
            f"{test_name}: optimistic execution is forbidden for performance-grade simulation "
            f"(maker={maker}, taker={taker}). Use HftBacktest + Predict Execution Tape V1, "
            f"or explicitly set {DIAGNOSTIC_ENV}=1 for DIAGNOSTIC_ONLY reproduction."
        )
    if not diag:
        missing: list[str] = []
        if not uses_hftbacktest:
            missing.append("HftBacktest")
        if not uses_predict_execution_tape:
            missing.append("Predict Execution Tape V1")
        if not closed_loop:
            missing.append("own-state closed-loop feedback")
        if missing:
            raise RuntimeError(
                f"{test_name}: performance-grade simulation missing required execution realism: "
                + ", ".join(missing)
            )

    return {
        "contractVersion": contract.get("contractVersion"),
        "testName": test_name,
        "makerMode": maker,
        "takerMode": taker,
        "closedLoop": bool(closed_loop),
        "usesHftBacktest": bool(uses_hftbacktest),
        "usesPredictExecutionTapeV1": bool(uses_predict_execution_tape),
        "diagnosticOnly": diag,
        "executionEvidenceLabel": (
            "DIAGNOSTIC_ONLY_OPTIMISTIC_EXECUTION"
            if diag and optimistic
            else "HFTBACKTEST_PREDICT_TAPE_CLOSED_LOOP"
        ),
        "knownRealismGaps": list(contract.get("knownRemainingRealismGaps") or []),
    }


def require_legacy_optimistic_diagnostic(*, test_name: str, fill_proxy: str) -> dict[str, Any]:
    """Hard-block legacy dream-fill runners unless explicitly invoked as diagnostic."""
    mode = str(fill_proxy or "").upper()
    if mode not in OPTIMISTIC_MAKER_MODES:
        # Legacy runners are not automatically performance-grade just because a
        # different proxy was chosen. They must use the canonical HFT runner for that.
        raise RuntimeError(
            f"{test_name}: legacy closed-loop runner is diagnostic-only. "
            "Use tools/hftbacktest_cap100_closed_loop_pilot_audit_v0.py for performance-grade simulation."
        )
    if not diagnostic_override_enabled():
        raise RuntimeError(
            f"{test_name}: {mode} is a dream-fill proxy and is disabled by default. "
            f"Set {DIAGNOSTIC_ENV}=1 only for historical DIAGNOSTIC_ONLY reproduction; "
            "performance tests must use HftBacktest + Predict Execution Tape V1."
        )
    return {
        "executionEvidenceLabel": "DIAGNOSTIC_ONLY_OPTIMISTIC_EXECUTION",
        "fillProxy": mode,
        "diagnosticOnly": True,
    }
