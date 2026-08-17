from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import analyze_target_maker_heavy_survival_v3_3 as v33

MIN_ORDINARY_COVERAGE = 0.90
MIN_SPECIAL_COVERAGE = 0.60
MODE = "EXPLORATORY_CANONICAL_SUBSET"


def main() -> int:
    original_guard = v33.v31._coverage_guard
    original_write = v33._write_json_v33

    def exploratory_guard(
        cohort: dict[int, str],
        settlements: dict[int, str],
        **_kwargs: Any,
    ) -> dict[str, Any]:
        return original_guard(
            cohort,
            settlements,
            min_ordinary=MIN_ORDINARY_COVERAGE,
            min_special=MIN_SPECIAL_COVERAGE,
        )

    def exploratory_write(path: Path, payload: dict[str, Any]) -> None:
        if isinstance(payload, dict):
            payload["analysisMode"] = MODE
            payload["automaticStrategyPromotion"] = False
            payload["exploratoryCoveragePolicy"] = {
                "ordinaryCanonicalSettlementMinimum": MIN_ORDINARY_COVERAGE,
                "specialCanonicalSettlementMinimum": MIN_SPECIAL_COVERAGE,
                "strictPromotionGateUnchanged": 0.95,
                "interpretation": (
                    "Exploratory continuation only. Ordinary walk-forward may be used for mechanism discovery; "
                    "SPECIAL metrics are incomplete-subset stress diagnostics and must not set live cutoffs or promotion decisions."
                ),
            }
            guardrails = list(payload.get("interpretationGuardrails") or [])
            note = (
                "EXPLORATORY_CANONICAL_SUBSET: settlement coverage is intentionally below the strict 95% promotion gate. "
                "Do not promote a survival cutoff or repair rule from this report."
            )
            if note not in guardrails:
                guardrails.append(note)
            payload["interpretationGuardrails"] = guardrails
        original_write(path, payload)

    try:
        v33.v31._coverage_guard = exploratory_guard
        v33._write_json_v33 = exploratory_write
        print(
            f"{MODE}: ordinary canonical coverage gate >= {MIN_ORDINARY_COVERAGE:.0%}; "
            f"SPECIAL >= {MIN_SPECIAL_COVERAGE:.0%}; strict 95% promotion gate remains unchanged.",
            flush=True,
        )
        return int(v33.main())
    finally:
        v33.v31._coverage_guard = original_guard
        v33._write_json_v33 = original_write


if __name__ == "__main__":
    raise SystemExit(main())
