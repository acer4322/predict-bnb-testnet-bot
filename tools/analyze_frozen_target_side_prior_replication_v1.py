from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

import joblib
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "target_public_side_prior_replication5_v1_preregistered.json"
SOURCE = BASE / "hft_native_constrained_rank_unused90_v4.json"
MODEL = BASE / "target_public_side_prior_hft_pilot_v1.joblib"
OUTPUT = BASE / "target_public_side_prior_replication5_v1_report.json"
TARGET_NAMES = ["secondsLeft", "directionScore", "up_bid", "up_ask", "down_bid", "down_ask"]
PUBLIC_NAMES = ["secondsLeft", "directionScore", "upBid", "upAsk", "downBid", "downAsk"]
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except Exception:
        return 0.0


def sha256(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return digest.upper()


def action(row: dict[str, Any], side: str, offset: int) -> dict[str, Any]:
    for candidate in row.get("actions") or []:
        if str(candidate.get("side") or "").upper() == side and int(candidate.get("offset")) == offset:
            return candidate
    raise RuntimeError(f"missing {side}_{offset} at {row.get('marketId')}:{row.get('checkpointMs')}")


def oracle(row: dict[str, Any], side: str | None = None) -> float:
    rewards = [
        finite(candidate.get("mtm1sUsdt"))
        for candidate in row.get("actions") or []
        if side is None or str(candidate.get("side") or "").upper() == side
    ]
    return max([0.0, *rewards])


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    expected_hash = str(prereg["frozenPolicy"]["modelSha256"]).upper()
    actual_hash = sha256(MODEL)
    if actual_hash != expected_hash:
        raise RuntimeError(f"frozen model hash mismatch: {actual_hash}")
    artifact = joblib.load(MODEL)
    if artifact.get("targetFeatureNames") != TARGET_NAMES or artifact.get("runtimePublicFeatureNames") != PUBLIC_NAMES:
        raise RuntimeError("frozen model feature contract mismatch")
    model = artifact["model"]
    market_ids = [int(value) for value in prereg["cohort"]["markets"]]
    dataset = json.loads(SOURCE.read_text(encoding="utf-8"))
    source_rows = [row for row in dataset.get("rows") or [] if int(row["marketId"]) in set(market_ids)]
    if len(source_rows) != 15 or sorted({int(row["marketId"]) for row in source_rows}) != sorted(market_ids):
        raise RuntimeError("replication cohort coverage mismatch")
    audit_rows: list[dict[str, Any]] = []
    for row in sorted(source_rows, key=lambda value: (int(value["checkpointMs"]), int(value["marketId"]))):
        public = row.get("features") or {}
        matrix = pd.DataFrame(
            [{target: finite(public.get(runtime)) for target, runtime in zip(TARGET_NAMES, PUBLIC_NAMES)}]
        )
        probability_up = float(model.predict_proba(matrix)[0, 1])
        predicted_side = "UP" if probability_up >= 0.5 else "DOWN"
        opposite_side = "DOWN" if predicted_side == "UP" else "UP"
        confidence = max(probability_up, 1.0 - probability_up)
        acts = confidence >= 0.60
        chosen = action(row, predicted_side, 0)
        direction_side = "UP" if finite(public.get("directionScore")) >= 0.0 else "DOWN"
        direction = action(row, direction_side, 0)
        audit_rows.append(
            {
                "marketId": int(row["marketId"]),
                "checkpointMs": int(row["checkpointMs"]),
                "probabilityUp": probability_up,
                "confidence": confidence,
                "predictedSide": predicted_side,
                "policyAction": f"{predicted_side}_0" if acts else "WAIT",
                "chosenRewardMtm1sUsdt": finite(chosen.get("mtm1sUsdt")) if acts else 0.0,
                "chosenFilledShares5s": finite(chosen.get("filledShares5s")) if acts else 0.0,
                "matchedDirectionRewardMtm1sUsdt": finite(direction.get("mtm1sUsdt")) if acts else 0.0,
                "alwaysPredictedSideOffset0RewardMtm1sUsdt": finite(chosen.get("mtm1sUsdt")),
                "freeOracleRewardMtm1sUsdt": oracle(row),
                "predictedSideOracleRewardMtm1sUsdt": oracle(row, predicted_side),
                "oppositeSideOracleRewardMtm1sUsdt": oracle(row, opposite_side),
            }
        )
    acted = [row for row in audit_rows if row["policyAction"] != "WAIT"]
    fills = [row for row in acted if row["chosenFilledShares5s"] > EPS]
    summary = {
        "markets": 5,
        "checkpoints": len(audit_rows),
        "waits": len(audit_rows) - len(acted),
        "acts": len(acted),
        "actRate": len(acted) / len(audit_rows),
        "filledActs": len(fills),
        "filledShares5s": sum(row["chosenFilledShares5s"] for row in acted),
        "positiveActs": sum(row["chosenRewardMtm1sUsdt"] > EPS for row in acted),
        "negativeActs": sum(row["chosenRewardMtm1sUsdt"] < -EPS for row in acted),
        "policyMtm1sUsdt": sum(row["chosenRewardMtm1sUsdt"] for row in audit_rows),
        "matchedDirectionMtm1sUsdt": sum(row["matchedDirectionRewardMtm1sUsdt"] for row in audit_rows),
        "alwaysPredictedSideOffset0Mtm1sUsdt": sum(
            row["alwaysPredictedSideOffset0RewardMtm1sUsdt"] for row in audit_rows
        ),
        "freeOracleMtm1sUsdt": sum(row["freeOracleRewardMtm1sUsdt"] for row in audit_rows),
        "predictedSideOracleMtm1sUsdt": sum(row["predictedSideOracleRewardMtm1sUsdt"] for row in audit_rows),
        "oppositeSideOracleMtm1sUsdt": sum(row["oppositeSideOracleRewardMtm1sUsdt"] for row in audit_rows),
    }
    sufficient_acts = summary["acts"] >= 5
    sufficient_fills = summary["filledActs"] >= 2
    sufficient_oracle = summary["freeOracleMtm1sUsdt"] > EPS
    conditions = {
        "sufficientActs": sufficient_acts,
        "sufficientActualFills": sufficient_fills,
        "sufficientFreeOracle": sufficient_oracle,
        "atLeastTwoPositiveActs": summary["positiveActs"] >= 2,
        "policyMtmPositive": summary["policyMtm1sUsdt"] > EPS,
        "policyBeatsMatchedDirection": summary["policyMtm1sUsdt"] > summary["matchedDirectionMtm1sUsdt"] + EPS,
        "predictedSideOracleBeatsOpposite": (
            summary["predictedSideOracleMtm1sUsdt"] > summary["oppositeSideOracleMtm1sUsdt"] + EPS
        ),
    }
    if not sufficient_acts or not sufficient_fills or not sufficient_oracle:
        decision = "NEED_MORE_DATA"
    elif all(conditions.values()):
        decision = "KEEP_COMPONENT_REPLICATED"
    else:
        decision = "REJECT"
    report = {
        "version": "TARGET_PUBLIC_SIDE_PRIOR_REPLICATION5_V1_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "frozenModelSha256": actual_hash,
        "sourceDataset": SOURCE.name,
        "execution": prereg["execution"],
        "cohort": prereg["cohort"],
        "summary": summary,
        "gateConditions": conditions,
        "decision": decision,
        "rows": audit_rows,
        "guards": prereg["guards"],
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(OUTPUT), "decision": decision, "summary": summary, "conditions": conditions}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
