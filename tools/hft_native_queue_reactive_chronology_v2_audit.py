from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_native_queue_reactive_chronology_v2 as qr
from tools import hft_native_queue_regime_value_v1 as v1


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "fillAuc",
        "checkpoints",
        "oracleReward",
        "oracleActs",
        "oracleActRate",
        "policyReward",
        "policyActs",
        "policyActRate",
        "waits",
        "positiveActs",
        "negativeActs",
        "zeroActs",
        "oracleCapture",
    )
    return {key: metrics.get(key) for key in keys}


def fit_and_evaluate(
    frames: dict[str, pd.DataFrame], features: list[str]
) -> dict[str, dict[str, Any]]:
    original_features = qr.FEATURES
    qr.FEATURES = features
    try:
        training = frames["train"]
        fill_model = v1.classifier()
        fill_model.fit(qr.matrix(training), training.filled)
        filled_training = training[training.filled > 0].copy()
        size_model = v1.regressor()
        size_model.fit(qr.matrix(filled_training), filled_training.filled_shares)
        markout_model = v1.regressor()
        markout_model.fit(
            qr.matrix(filled_training),
            filled_training.markout_per_share,
            sample_weight=filled_training.filled_shares,
        )
        return {
            split: qr.evaluate(split, frame, fill_model, size_model, markout_model)
            for split, frame in frames.items()
        }
    finally:
        qr.FEATURES = original_features


def chosen_breakdown(
    primary: dict[str, Any], frames: dict[str, pd.DataFrame]
) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    summaries: dict[str, Any] = {}
    details: dict[str, list[dict[str, Any]]] = {}
    for split, frame in frames.items():
        rows: list[dict[str, Any]] = []
        for choice in primary["evaluations"][split]["rows"]:
            action = str(choice["action"])
            if action == "WAIT":
                continue
            side, offset_text = action.split("_")
            selected = frame[
                (frame.market_id == int(choice["marketId"]))
                & (frame.checkpoint_ms == int(choice["checkpointMs"]))
                & (frame.side == side)
                & (frame.action_offset == float(offset_text))
            ]
            if len(selected) != 1:
                raise RuntimeError(f"cannot uniquely resolve chosen action: {choice}")
            row = selected.iloc[0]
            rows.append(
                {
                    "marketId": int(row.market_id),
                    "checkpointMs": int(row.checkpoint_ms),
                    "action": action,
                    "actionPrice": float(row.action_price),
                    "filledShares5s": float(row.filled_shares),
                    "mtm1sUsdt": float(row.mtm),
                    "portfolioFloorDelta": float(row.delta_floor_realized),
                    "portfolioReward": float(row.portfolio_reward),
                    "predictedValue": float(choice["predictedValue"]),
                    "oracleAction": str(choice["oracleAction"]),
                    "oracleReward": float(choice["oracleReward"]),
                    "regime": choice.get("regime"),
                }
            )
        mtm = float(sum(row["mtm1sUsdt"] for row in rows))
        floor_delta = float(sum(row["portfolioFloorDelta"] for row in rows))
        reward = float(sum(row["portfolioReward"] for row in rows))
        reported = float(primary["evaluations"][split]["policyReward"])
        if abs(reward - reported) > 1e-8 or abs(reward - (mtm + floor_delta)) > 1e-8:
            raise RuntimeError(f"reward decomposition mismatch for {split}")
        summaries[split] = {
            "acts": len(rows),
            "filledActs": sum(row["filledShares5s"] > v1.EPS for row in rows),
            "zeroFillActs": sum(row["filledShares5s"] <= v1.EPS for row in rows),
            "mtm1sUsdt": mtm,
            "portfolioFloorDelta": floor_delta,
            "portfolioReward": reward,
        }
        details[split] = rows
    return summaries, details


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="hft_native_unused_chronology_v2_preregistered.json")
    parser.add_argument("--collector", default="hft_native_unused120_collector_v2.json")
    parser.add_argument("--sweep", default="hft_native_unused120_bothsides_v2.json")
    parser.add_argument("--primary", default="hft_native_queue_reactive_chronology_v2_report.json")
    parser.add_argument("--output", default="hft_native_queue_reactive_chronology_v2_audit.json")
    args = parser.parse_args()

    contract_path = BASE / args.contract
    primary_path = BASE / args.primary
    policy_path = ROOT / "tools" / "hft_native_queue_reactive_chronology_v2.py"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    all_rows = qr.load_rows(args.collector, args.sweep)
    ids = {
        split: {int(row["marketId"]) for row in contract[split]}
        for split in ("train", "validation", "holdout")
    }
    frames = {
        split: all_rows[all_rows.market_id.isin(market_ids)].copy()
        for split, market_ids in ids.items()
    }
    chronology_strict = (
        int(frames["train"].checkpoint_ms.max())
        < int(frames["validation"].checkpoint_ms.min())
        < int(frames["holdout"].checkpoint_ms.min())
    )
    if not chronology_strict:
        raise RuntimeError("chronological split is not strict")

    reward_summaries, chosen_details = chosen_breakdown(primary, frames)
    static_evaluations = fit_and_evaluate(frames, list(v1.FEATURES))
    static_compact = {split: compact(metrics) for split, metrics in static_evaluations.items()}
    reactive_compact = {
        split: compact(primary["evaluations"][split])
        for split in ("train", "validation", "holdout")
    }
    report = {
        "version": "HFT_NATIVE_QUEUE_REACTIVE_CHRONOLOGY_V2_AUDIT",
        "researchOnly": True,
        "auditOnlyNotUsedForTuning": True,
        "winnerSettlementPnlRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "contract": args.contract,
        "primary": args.primary,
        "lockedArtifactHashes": {
            "contractSha256": sha256(contract_path),
            "policyToolSha256": sha256(policy_path),
            "primaryReportSha256": sha256(primary_path),
        },
        "checks": {
            "strictChronology": chronology_strict,
            "cohortMarkets": {split: len(market_ids) for split, market_ids in ids.items()},
            "cohortCheckpoints": {
                split: int(frame[["market_id", "checkpoint_ms"]].drop_duplicates().shape[0])
                for split, frame in frames.items()
            },
            "cohortActionRows": {split: int(len(frame)) for split, frame in frames.items()},
        },
        "queueReactivePrimary": reactive_compact,
        "chosenRewardDecomposition": reward_summaries,
        "chosenActionDetails": chosen_details,
        "staticQueueAblation": {
            "description": "Same chronological cohorts, targets, hurdle models, hyperparameters, score and WAIT rule; remove only the new strict-past queue-reactive flow features.",
            "features": list(v1.FEATURES),
            "evaluations": static_compact,
        },
        "holdoutComparison": {
            "queueReactiveReward": reactive_compact["holdout"]["policyReward"],
            "staticQueueReward": static_compact["holdout"]["policyReward"],
            "queueReactiveActs": reactive_compact["holdout"]["policyActs"],
            "staticQueueActs": static_compact["holdout"]["policyActs"],
        },
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "checks": report["checks"],
                "rewardDecomposition": reward_summaries,
                "holdoutComparison": report["holdoutComparison"],
                "staticQueueAblation": static_compact,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
