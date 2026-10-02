from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import balanced_accuracy_score, brier_score_loss, log_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot import unified_controller_paper_v2 as mod  # noqa: E402
from tools.evaluate_r2_pending_management_closed_loop_v0 import winners  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "hft_taker_terminal_outcome_value_v1_preregistered.json"
V4_CONTRACT = BASE / "hft_native_constrained_rank_v4_preregistered.json"
V4_DATASET = BASE / "hft_native_constrained_rank_unused90_v4.json"
V3_CONTRACT = BASE / "hft_native_timegrid_inventory_chronology_v3_preregistered.json"
V3_DATASET = BASE / "hft_native_timegrid_inventory_unused80_v3.json"
PILOT_ACTIONS = BASE / "hft_taker_terminal_outcome_value_v1_pilot_actions.json"
MODEL = BASE / "hft_taker_terminal_outcome_value_v1_model.joblib"
TRAIN_REPORT = BASE / "hft_taker_terminal_outcome_value_v1_train_report.json"
PILOT_REPORT = BASE / "hft_taker_terminal_outcome_value_v1_pilot_report.json"
SEED = 20260823
QTY = float(mod.SHARES)
EPS = 1e-9


FEATURES = [
    "seconds_left",
    "direction_score",
    "spot_return_1s_bps",
    "spot_return_3s_bps",
    "spot_return_5s_bps",
    "spot_queue_imbalance",
    "spot_taker_imbalance_1s",
    "futures_return_1s_bps",
    "futures_return_3s_bps",
    "futures_return_5s_bps",
    "futures_queue_imbalance",
    "futures_taker_imbalance_1s",
    "spot_minus_strike_bps",
    "chainlink_minus_strike_bps",
    "perp_spot_basis_bps",
    "predict_up_mid",
    "predict_up_bid",
    "predict_up_ask",
    "predict_down_bid",
    "predict_down_ask",
]


RAW_NAMES = {
    "seconds_left": ("secondsLeft",),
    "direction_score": ("directionScore",),
    "spot_return_1s_bps": ("spotReturn1sBps",),
    "spot_return_3s_bps": ("spotReturn3sBps",),
    "spot_return_5s_bps": ("spotReturn5sBps",),
    "spot_queue_imbalance": ("spotQueueImbalance",),
    "spot_taker_imbalance_1s": ("spotTakerImbalance1s",),
    "futures_return_1s_bps": ("futuresReturn1sBps",),
    "futures_return_3s_bps": ("futuresReturn3sBps",),
    "futures_return_5s_bps": ("futuresReturn5sBps",),
    "futures_queue_imbalance": ("futuresQueueImbalance",),
    "futures_taker_imbalance_1s": ("futuresTakerImbalance1s",),
    "spot_minus_strike_bps": ("spotMinusStrikeBps",),
    "chainlink_minus_strike_bps": ("chainlinkMinusStrikeBps",),
    "perp_spot_basis_bps": ("perpSpotBasisBps",),
    "predict_up_mid": ("predictUpMid",),
    "predict_up_bid": ("upBid", "predictUpBid"),
    "predict_up_ask": ("upAsk", "predictUpAsk"),
    "predict_down_bid": ("downBid", "predictDownBid"),
    "predict_down_ask": ("downAsk", "predictDownAsk"),
}


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else np.nan
    except Exception:
        return np.nan


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest().upper()


def get_value(raw: dict[str, Any], name: str) -> float:
    for raw_name in RAW_NAMES[name]:
        if raw.get(raw_name) is not None:
            return finite(raw.get(raw_name))
    return np.nan


def normalize(raw: dict[str, Any]) -> dict[str, float]:
    return {name: get_value(raw, name) for name in FEATURES}


def contract_ids(path: Path, splits: tuple[str, ...]) -> list[int]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    return [int(row["marketId"]) for split in splits for row in contract[split]]


def checkpoint_frame(dataset_path: Path, wanted: set[int], outcome_map: dict[int, str]) -> pd.DataFrame:
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    seen: set[int] = set()
    for checkpoint in dataset.get("rows") or []:
        market_id = int(checkpoint["marketId"])
        if market_id not in wanted:
            continue
        seen.add(market_id)
        rows.append(
            {
                "market_id": market_id,
                "checkpoint_ms": int(checkpoint["checkpointMs"]),
                "winner_up": float(str(outcome_map[market_id]).upper() == "UP"),
                **normalize(dict(checkpoint.get("features") or {})),
            }
        )
    if seen != wanted:
        raise RuntimeError(f"checkpoint coverage mismatch in {dataset_path.name}")
    return pd.DataFrame(rows)


def pipeline() -> Pipeline:
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", LogisticRegression(C=0.25, max_iter=2000, random_state=SEED)),
        ]
    )


def metrics(frame: pd.DataFrame, probability: np.ndarray) -> dict[str, Any]:
    label = frame.winner_up.to_numpy(dtype=int)
    implied = np.clip(frame.predict_up_mid.to_numpy(dtype=float), 1e-6, 1 - 1e-6)
    prediction = (probability >= 0.5).astype(int)
    return {
        "markets": int(frame.market_id.nunique()),
        "rows": int(len(frame)),
        "balancedAccuracy": float(balanced_accuracy_score(label, prediction)),
        "rocAuc": float(roc_auc_score(label, probability)),
        "brier": float(brier_score_loss(label, probability)),
        "logLoss": float(log_loss(label, probability, labels=[0, 1])),
        "impliedMidBrier": float(brier_score_loss(label, implied)),
        "impliedMidLogLoss": float(log_loss(label, implied, labels=[0, 1])),
        "meanPUp": float(np.mean(probability)),
        "winnerUpRate": float(np.mean(label)),
    }


def train_phase() -> None:
    v4_ids = contract_ids(V4_CONTRACT, ("train", "validation", "holdout"))
    v3_train = contract_ids(V3_CONTRACT, ("train",))
    v3_validation = contract_ids(V3_CONTRACT, ("validation",))
    train = pd.concat(
        [
            checkpoint_frame(V4_DATASET, set(v4_ids), winners(v4_ids)),
            checkpoint_frame(V3_DATASET, set(v3_train), winners(v3_train)),
        ],
        ignore_index=True,
    )
    validation = checkpoint_frame(V3_DATASET, set(v3_validation), winners(v3_validation))
    if int(train.checkpoint_ms.max()) >= int(validation.checkpoint_ms.min()):
        raise RuntimeError("training chronology violation")
    model = pipeline()
    counts = train.groupby("market_id").market_id.transform("size").to_numpy(dtype=float)
    model.fit(train[FEATURES], train.winner_up.astype(int), model__sample_weight=1.0 / counts)
    train_probability = model.predict_proba(train[FEATURES])[:, 1]
    validation_probability = model.predict_proba(validation[FEATURES])[:, 1]
    bundle = {
        "version": "HFT_TAKER_TERMINAL_OUTCOME_VALUE_V1_MODEL",
        "features": FEATURES,
        "model": model,
        "trainingMarketIds": v4_ids + v3_train,
        "winnerRuntimeInput": False,
        "winnerUsedAsOfflineTrainingLabelOnly": True,
        "targetFutureActionInput": False,
        "r2IntentInput": False,
    }
    joblib.dump(bundle, MODEL)
    report = {
        "version": "HFT_TAKER_TERMINAL_OUTCOME_VALUE_V1_TRAIN",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "model": MODEL.name,
        "modelSha256": sha256(MODEL),
        "pilotWinnerLabelsLoaded": False,
        "train": metrics(train, train_probability),
        "chronologicalDiagnostic": metrics(validation, validation_probability),
        "features": FEATURES,
        "thresholdSweep": False,
        "hyperparameterSweep": False,
    }
    TRAIN_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(TRAIN_REPORT), "model": str(MODEL), "modelSha256": report["modelSha256"], "train": report["train"], "chronologicalDiagnostic": report["chronologicalDiagnostic"], "pilotWinnerLabelsLoaded": False}, ensure_ascii=False, indent=2))


def action_terminal(action: dict[str, Any], winner: str) -> float:
    shares = float(action.get("filled") or 0.0)
    price = finite(action.get("fillPrice"))
    fee = float(action.get("fee") or 0.0)
    if shares <= EPS or not math.isfinite(price):
        return 0.0
    return shares * (float(str(action["side"]).upper() == winner) - price) - fee


def evaluate_phase() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    train_report = json.loads(TRAIN_REPORT.read_text(encoding="utf-8"))
    if sha256(MODEL) != str(train_report["modelSha256"]):
        raise RuntimeError("frozen model hash mismatch")
    bundle = joblib.load(MODEL)
    dataset = json.loads(PILOT_ACTIONS.read_text(encoding="utf-8"))
    expected = [int(value) for value in prereg["pilot"]["markets"]]
    if [int(value) for value in dataset.get("markets") or []] != expected:
        raise RuntimeError("pilot market order/coverage mismatch")
    outcome_map = winners(expected)
    rows: list[dict[str, Any]] = []
    for checkpoint in dataset.get("rows") or []:
        market_id = int(checkpoint["marketId"])
        raw = dict(checkpoint.get("features") or {})
        xx = pd.DataFrame([normalize(raw)], columns=FEATURES)
        p_up = float(bundle["model"].predict_proba(xx)[0, 1])
        up_ask = finite(raw.get("predictUpAsk"))
        down_ask = finite(raw.get("predictDownAsk"))
        up_score = QTY * (p_up - up_ask) - mod.taker_fee(QTY, up_ask, mod.FEE_BPS)
        down_score = QTY * ((1.0 - p_up) - down_ask) - mod.taker_fee(QTY, down_ask, mod.FEE_BPS)
        scores = {"UP": float(up_score), "DOWN": float(down_score)}
        winner = str(outcome_map[market_id]).upper()
        actions = {str(action["side"]).upper(): action for action in checkpoint.get("actions") or []}
        selected_side = max(scores, key=scores.get)
        if scores[selected_side] <= EPS:
            selected_side = "WAIT"
            selected_reward = 0.0
            selected_filled = 0.0
            selected_mtm = 0.0
        else:
            selected = actions[selected_side]
            selected_reward = action_terminal(selected, winner)
            selected_filled = float(selected.get("filled") or 0.0)
            selected_mtm = float(selected.get("reward1sNetFee") or 0.0)
        direction_side = "UP" if finite(raw.get("directionScore")) >= 0.0 else "DOWN"
        direction_reward = action_terminal(actions[direction_side], winner)
        oracle_reward, oracle_side = max([(0.0, "WAIT"), *[(action_terminal(action, winner), side) for side, action in actions.items()]])
        rows.append(
            {
                "marketId": market_id,
                "checkpointMs": int(checkpoint["checkpointMs"]),
                "winner": winner,
                "pUp": p_up,
                "upScore": float(up_score),
                "downScore": float(down_score),
                "action": selected_side,
                "filledShares": selected_filled,
                "terminalRewardNetFeeUsdt": selected_reward,
                "mtm1sNetFeeAudit": selected_mtm,
                "directionBaselineAction": direction_side,
                "directionBaselineTerminalRewardNetFeeUsdt": direction_reward,
                "oracleAction": oracle_side,
                "oracleTerminalRewardNetFeeUsdt": float(oracle_reward),
            }
        )
    acts = [row for row in rows if row["action"] != "WAIT"]
    metrics_out = {
        "markets": len(expected),
        "checkpoints": len(rows),
        "waits": len(rows) - len(acts),
        "acts": len(acts),
        "actRate": len(acts) / max(1, len(rows)),
        "filledActs": sum(float(row["filledShares"]) > EPS for row in acts),
        "filledShares": float(sum(float(row["filledShares"]) for row in acts)),
        "terminalRewardNetFeeUsdt": float(sum(float(row["terminalRewardNetFeeUsdt"]) for row in acts)),
        "mtm1sNetFeeAudit": float(sum(float(row["mtm1sNetFeeAudit"]) for row in acts)),
        "positiveActs": sum(float(row["terminalRewardNetFeeUsdt"]) > EPS for row in acts),
        "negativeActs": sum(float(row["terminalRewardNetFeeUsdt"]) < -EPS for row in acts),
        "directionScoreBaselineTerminalRewardNetFeeUsdt": float(sum(float(row["directionBaselineTerminalRewardNetFeeUsdt"]) for row in rows)),
        "oracleTerminalRewardNetFeeUsdt": float(sum(float(row["oracleTerminalRewardNetFeeUsdt"]) for row in rows)),
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in rows),
        "rows": rows,
    }
    conditions = {
        "oraclePositive": metrics_out["oracleTerminalRewardNetFeeUsdt"] > EPS,
        "policyPositive": metrics_out["terminalRewardNetFeeUsdt"] > EPS,
        "minimumFilledActs": metrics_out["filledActs"] >= 2,
        "maximumActRate": metrics_out["actRate"] <= 0.70 + EPS,
        "beatsDirectionScoreSignBaseline": metrics_out["terminalRewardNetFeeUsdt"] > metrics_out["directionScoreBaselineTerminalRewardNetFeeUsdt"] + EPS,
    }
    decision = "KEEP_COMPONENT" if all(conditions.values()) else "REJECT"
    report = {
        "version": "HFT_TAKER_TERMINAL_OUTCOME_VALUE_V1_PILOT",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "trainReport": TRAIN_REPORT.name,
        "model": MODEL.name,
        "modelSha256": sha256(MODEL),
        "winnerRuntimeInput": False,
        "winnerUsedAsOfflineOutcomeOnly": True,
        "metrics": metrics_out,
        "conditions": conditions,
        "decision": decision,
    }
    PILOT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(PILOT_REPORT), "decision": decision, "conditions": conditions, "metrics": {key: value for key, value in metrics_out.items() if key != "rows"}}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["train", "evaluate"], required=True)
    args = parser.parse_args()
    if args.phase == "train":
        train_phase()
    else:
        evaluate_phase()


if __name__ == "__main__":
    main()
