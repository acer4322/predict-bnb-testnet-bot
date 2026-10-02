from __future__ import annotations

from pathlib import Path
from typing import Any

import joblib
import numpy as np

VERSION = "R4_SAFE_BASE_PRESERVATION_GATE_V1"


class R4SafeBasePreservationGateV1:
    """Research-only strict-past preservation warning head.

    Uses the Target-trained preservation teacher to estimate the minimum
    normalized floor over the next 30 seconds from current observable state.
    A non-positive prediction is a semantic warning that additional expansion
    may consume the safe base. This module has no Echtgeld authority.
    """

    def __init__(self, research_dir: Path) -> None:
        art = joblib.load(Path(research_dir) / "r4_base_preservation_teacher_v1.joblib")
        self.features = list(art["features"])
        self.model = art["model"]
        self.teacher_version = str(art.get("version") or "")

    @staticmethod
    def _finite(value: Any) -> float:
        try:
            x = float(value)
            return x if np.isfinite(x) else 0.0
        except Exception:
            return 0.0

    def score(self, state: dict[str, Any]) -> dict[str, Any]:
        x = np.asarray([[self._finite(state.get(f, 0.0)) for f in self.features]], dtype=float)
        pred = float(self.model.predict(x)[0])
        current_floor = self._finite(state.get("floor", state.get("worst_case_floor", 0.0)))
        current_floor_per_gross = self._finite(state.get("floor_per_gross", 0.0))
        warn = bool(current_floor > 0.0 and pred <= 0.0)
        return {
            "version": VERSION,
            "strictPast": True,
            "teacherVersion": self.teacher_version,
            "currentFloor": current_floor,
            "currentFloorPerGross": current_floor_per_gross,
            "predictedMinFloorPerGross30s": pred,
            "preserveBaseWarning": warn,
            "recommendedResearchAction": "STOP_EXPANSION" if warn else "NO_PRESERVATION_VETO",
            "liveAuthority": False,
        }
