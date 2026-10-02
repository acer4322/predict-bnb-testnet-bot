from __future__ import annotations

import analyze_target_controller_fragment_ebm_v27 as base

ORIGINAL_BUILD = base.build_training_rows


def build_training_rows(states, snapshots, prediction_events):
    rows, audit = ORIGINAL_BUILD(states, snapshots, prediction_events)
    for row in rows:
        age = base._num(row.get("micro_prediction_event_age_ms"))
        if age is not None and age <= base.PREDICTION_FRESHNESS_MS:
            continue
        for history_ms in base.HISTORY_MS:
            suffix = f"{history_ms // 1000}s"
            row[f"micro_prediction_delta_{suffix}"] = None
            row[f"micro_prediction_range_{suffix}"] = None
            row[f"micro_prediction_pressure_aligned_delta_{suffix}"] = None
    return rows, audit


base.build_training_rows = build_training_rows
base.REPORT_VERSION = "TARGET_CONTROLLER_FRAGMENT_EBM_V271_PREDICTION_FRESHNESS"


if __name__ == "__main__":
    base.main()
