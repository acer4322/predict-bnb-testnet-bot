from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
import torch
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, roc_auc_score
from torch import nn
from torch.utils.data import DataLoader, TensorDataset


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_grouped_fill_support_rank_v1 as grouped  # noqa: E402
from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402
from tools import hft_target_side_prior_value_rank_v1 as hybrid  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "hft_orderflow_sequence_value_v1_preregistered.json"
DATASET = BASE / "hft_orderflow_sequence_value_v1_dataset.npz"
HFT_FRAME = BASE / "hft_orderflow_sequence_value_v1_hft_frame.joblib"
GROUPED_FRAME = BASE / "hft_target_side_prior_value_rank_v1_frame.joblib"
GROUPED_MODEL = BASE / "hft_grouped_fill_support_rank_v1_model.joblib"
EMBEDDED_FRAME = BASE / "hft_orderflow_sequence_value_v1_embedded_frame.joblib"
ENCODER = BASE / "hft_orderflow_sequence_value_v1_encoder.pt"
MODEL = BASE / "hft_orderflow_sequence_value_v1_model.joblib"
REPORT = BASE / "hft_orderflow_sequence_value_v1_train_report.json"
SEED = 20260823
EMBEDDING_DIM = 32
BASE_FEATURES = list(v1.FEATURES)
EMBEDDING_FEATURES = [f"sequence_embedding_{index}" for index in range(EMBEDDING_DIM)]
FEATURES = [*BASE_FEATURES, *EMBEDDING_FEATURES]
QTY = v1.QTY
EPS = v1.EPS


class OrderFlowEncoder(nn.Module):
    def __init__(self, channels: int = 10, embedding_dim: int = EMBEDDING_DIM) -> None:
        super().__init__()
        self.features = nn.Sequential(
            nn.Conv1d(channels, 32, kernel_size=5, padding=2),
            nn.GELU(),
            nn.Conv1d(32, 32, kernel_size=3, padding=1),
            nn.GELU(),
            nn.Conv1d(32, 32, kernel_size=3, padding=2, dilation=2),
            nn.GELU(),
            nn.AdaptiveAvgPool1d(1),
        )
        self.embedding = nn.Linear(32, embedding_dim)
        self.decoder = nn.Linear(embedding_dim, channels)

    def forward(self, inputs: torch.Tensor) -> tuple[torch.Tensor, torch.Tensor]:
        hidden = self.features(inputs).squeeze(-1)
        embedding = self.embedding(hidden)
        return embedding, self.decoder(torch.tanh(embedding))


def set_seed() -> None:
    random.seed(SEED)
    np.random.seed(SEED)
    torch.manual_seed(SEED)
    torch.set_num_threads(6)
    torch.use_deterministic_algorithms(True)


def pretrain_encoder(data: Any) -> tuple[OrderFlowEncoder, dict[str, Any], np.ndarray, np.ndarray]:
    inputs = np.asarray(data["pretrain_x"], dtype=np.float32)
    targets = np.asarray(data["pretrain_y"], dtype=np.float32)
    markets = np.asarray(data["pretrain_market"], dtype=np.int64)
    unique_markets = sorted(int(value) for value in np.unique(markets))
    validation_markets = set(unique_markets[-20:])
    validation_mask = np.asarray([int(value) in validation_markets for value in markets], dtype=bool)
    training_mask = ~validation_mask
    x_mean = inputs[training_mask].mean(axis=(0, 2), keepdims=True)
    x_std = inputs[training_mask].std(axis=(0, 2), keepdims=True)
    x_std = np.maximum(x_std, 1e-6)
    y_mean = targets[training_mask].mean(axis=0, keepdims=True)
    y_std = np.maximum(targets[training_mask].std(axis=0, keepdims=True), 1e-6)
    normalized_x = (inputs - x_mean) / x_std
    normalized_y = (targets - y_mean) / y_std
    train_dataset = TensorDataset(
        torch.from_numpy(normalized_x[training_mask]),
        torch.from_numpy(normalized_y[training_mask]),
    )
    validation_dataset = TensorDataset(
        torch.from_numpy(normalized_x[validation_mask]),
        torch.from_numpy(normalized_y[validation_mask]),
    )
    generator = torch.Generator().manual_seed(SEED)
    train_loader = DataLoader(train_dataset, batch_size=256, shuffle=True, generator=generator)
    validation_loader = DataLoader(validation_dataset, batch_size=256, shuffle=False)
    model = OrderFlowEncoder(channels=inputs.shape[1])
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001)
    criterion = nn.MSELoss()
    history: list[dict[str, float]] = []
    for epoch in range(1, 16):
        model.train()
        training_loss = 0.0
        training_rows = 0
        for batch_x, batch_y in train_loader:
            optimizer.zero_grad(set_to_none=True)
            _embedding, prediction = model(batch_x)
            loss = criterion(prediction, batch_y)
            loss.backward()
            optimizer.step()
            training_loss += float(loss.item()) * len(batch_x)
            training_rows += len(batch_x)
        model.eval()
        validation_loss = 0.0
        validation_rows = 0
        with torch.no_grad():
            for batch_x, batch_y in validation_loader:
                _embedding, prediction = model(batch_x)
                loss = criterion(prediction, batch_y)
                validation_loss += float(loss.item()) * len(batch_x)
                validation_rows += len(batch_x)
        row = {
            "epoch": float(epoch),
            "trainMse": training_loss / max(1, training_rows),
            "validationMse": validation_loss / max(1, validation_rows),
        }
        history.append(row)
        if epoch in {1, 5, 10, 15}:
            print(json.dumps({"pretrainProgress": row}), flush=True)
    metadata = {
        "trainMarkets": len(unique_markets) - len(validation_markets),
        "validationMarkets": len(validation_markets),
        "trainSamples": int(training_mask.sum()),
        "validationSamples": int(validation_mask.sum()),
        "validationMarketIds": sorted(validation_markets),
        "history": history,
        "finalTrainMse": history[-1]["trainMse"],
        "finalValidationMse": history[-1]["validationMse"],
    }
    return model, metadata, x_mean.astype(np.float32), x_std.astype(np.float32)


def embed(model: OrderFlowEncoder, inputs: np.ndarray, mean: np.ndarray, std: np.ndarray) -> np.ndarray:
    normalized = (np.asarray(inputs, dtype=np.float32) - mean) / std
    model.eval()
    result: list[np.ndarray] = []
    loader = DataLoader(TensorDataset(torch.from_numpy(normalized)), batch_size=256, shuffle=False)
    with torch.no_grad():
        for (batch,) in loader:
            embedding, _prediction = model(batch)
            result.append(embedding.numpy())
    return np.concatenate(result, axis=0).astype(np.float32)


def quantile_model() -> HistGradientBoostingRegressor:
    return HistGradientBoostingRegressor(
        loss="quantile",
        quantile=0.25,
        max_depth=3,
        learning_rate=0.045,
        max_iter=180,
        l2_regularization=5.0,
        min_samples_leaf=12,
        random_state=SEED,
    )


def feature_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    return frame[FEATURES].replace([np.inf, -np.inf], np.nan)


def fit_value_model(training: pd.DataFrame) -> dict[str, Any]:
    fill_model = v1.classifier()
    fill_model.fit(feature_matrix(training), training.filled)
    filled = training[training.filled > 0].copy()
    size_model = v1.regressor()
    size_model.fit(feature_matrix(filled), filled.filled_shares)
    markout_model = quantile_model()
    markout_model.fit(
        feature_matrix(filled),
        filled.markout_per_share,
        sample_weight=filled.filled_shares,
    )
    return {
        "features": FEATURES,
        "fillModel": fill_model,
        "sizeModel": size_model,
        "markoutQ25Model": markout_model,
        "trainingRows": len(training),
        "trainingFilledRows": len(filled),
    }


def evaluate(name: str, frame: pd.DataFrame, expected_ids: list[int], model: dict[str, Any]) -> dict[str, Any]:
    scored = frame.copy()
    xx = feature_matrix(scored)
    scored["pfill"] = model["fillModel"].predict_proba(xx)[:, 1]
    scored["pred_filled_shares_if_fill"] = np.clip(model["sizeModel"].predict(xx), 0.0, QTY)
    scored["pred_markout_q25_per_share"] = model["markoutQ25Model"].predict(xx)
    scored["score"] = scored.pfill * scored.pred_filled_shares_if_fill * scored.pred_markout_q25_per_share
    expected_keys = hybrid.expected_checkpoint_keys(expected_ids)
    observed: set[tuple[int, int]] = set()
    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in scored.groupby(["market_id", "checkpoint_ms"], sort=True):
        observed.add((int(market_id), int(checkpoint_ms)))
        oracle = group.sort_values(["mtm", "action_offset", "side"], ascending=[False, False, True]).iloc[0]
        oracle_reward = max(0.0, float(oracle.mtm))
        chosen = group.sort_values(["score", "action_offset", "side"], ascending=[False, False, True]).iloc[0]
        if float(chosen.score) > 0.0:
            action = f"{chosen.side}_{int(chosen.action_offset)}"
            reward = float(chosen.mtm)
            shares = float(chosen.filled_shares)
            floor_delta = float(chosen.delta_floor_realized)
        else:
            action = "WAIT"
            reward = 0.0
            shares = 0.0
            floor_delta = 0.0
        choices.append(
            {
                "marketId": int(market_id),
                "checkpointMs": int(checkpoint_ms),
                "action": action,
                "predictedValue": float(chosen.score),
                "pFill": float(chosen.pfill),
                "predictedMarkoutQ25PerShare": float(chosen.pred_markout_q25_per_share),
                "rewardMtm1sUsdt": reward,
                "filledShares5s": shares,
                "realizedFloorDeltaAudit": floor_delta,
                "oracleAction": f"{oracle.side}_{int(oracle.action_offset)}" if oracle_reward > EPS else "WAIT",
                "oracleRewardMtm1sUsdt": oracle_reward,
            }
        )
    for market_id, checkpoint_ms in expected_keys:
        if (market_id, checkpoint_ms) in observed:
            continue
        choices.append(
            {
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "action": "WAIT",
                "predictedValue": 0.0,
                "pFill": 0.0,
                "predictedMarkoutQ25PerShare": 0.0,
                "rewardMtm1sUsdt": 0.0,
                "filledShares5s": 0.0,
                "realizedFloorDeltaAudit": 0.0,
                "oracleAction": "WAIT",
                "oracleRewardMtm1sUsdt": 0.0,
            }
        )
    choices.sort(key=lambda row: (row["marketId"], row["checkpointMs"]))
    try:
        fill_auc = float(roc_auc_score(scored.filled, scored.pfill))
    except Exception:
        fill_auc = None
    filled = scored[scored.filled > 0]
    size_mae = float(mean_absolute_error(filled.filled_shares, filled.pred_filled_shares_if_fill)) if not filled.empty else None
    q25_coverage = float(np.mean(filled.markout_per_share >= filled.pred_markout_q25_per_share)) if not filled.empty else None
    acts = [row for row in choices if row["action"] != "WAIT"]
    reward = float(sum(row["rewardMtm1sUsdt"] for row in choices))
    oracle_reward = float(sum(row["oracleRewardMtm1sUsdt"] for row in choices))
    return {
        "name": name,
        "markets": len(set(expected_ids)),
        "checkpoints": len(choices),
        "actionRows": len(scored),
        "fillAuc": fill_auc,
        "filledSharesMaeConditional": size_mae,
        "markoutQ25EmpiricalCoverage": q25_coverage,
        "feasibleOracleMtm1sUsdt": oracle_reward,
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "policyMtm1sUsdt": reward,
        "policyActs": len(acts),
        "policyActRate": len(acts) / len(choices),
        "waits": len(choices) - len(acts),
        "filledActs": sum(row["filledShares5s"] > EPS for row in acts),
        "positiveActs": sum(row["rewardMtm1sUsdt"] > EPS for row in acts),
        "negativeActs": sum(row["rewardMtm1sUsdt"] < -EPS for row in acts),
        "zeroActs": sum(abs(row["rewardMtm1sUsdt"]) <= EPS for row in acts),
        "filledShares5s": float(sum(row["filledShares5s"] for row in acts)),
        "realizedFloorDeltaAudit": float(sum(row["realizedFloorDeltaAudit"] for row in acts)),
        "oracleCapture": reward / oracle_reward if oracle_reward > EPS else None,
        "rows": choices,
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "rows"}


def main() -> None:
    set_seed()
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    data = np.load(DATASET)
    encoder, pretraining, x_mean, x_std = pretrain_encoder(data)
    hft_embeddings = embed(encoder, np.asarray(data["hft_x"], dtype=np.float32), x_mean, x_std)
    hft = joblib.load(HFT_FRAME).copy()
    if len(hft) != len(hft_embeddings):
        raise RuntimeError("HFT frame and sequence embedding lengths differ")
    for index, name in enumerate(EMBEDDING_FEATURES):
        hft[name] = hft_embeddings[:, index]
    joblib.dump(hft, EMBEDDED_FRAME)
    torch.save(
        {
            "version": "HFT_ORDERFLOW_SEQUENCE_VALUE_V1_ENCODER",
            "stateDict": encoder.state_dict(),
            "inputMean": x_mean,
            "inputStd": x_std,
            "channels": 10,
            "embeddingDim": EMBEDDING_DIM,
            "seed": SEED,
        },
        ENCODER,
    )
    ids, contract = hybrid.split_ids()
    training = hft[hft.market_id.isin(ids["train"])].copy()
    validation = hft[hft.market_id.isin(ids["validation"])].copy()
    model = fit_value_model(training)
    train_metrics = evaluate("train", training, ids["train"], model)
    validation_metrics = evaluate("validation", validation, ids["validation"], model)
    grouped_frame = joblib.load(GROUPED_FRAME)
    grouped_validation_frame = grouped_frame[grouped_frame.market_id.isin(ids["validation"])].copy()
    grouped_artifact = joblib.load(GROUPED_MODEL)
    grouped_validation = grouped.evaluate(
        "validation", grouped_validation_frame, ids["validation"], grouped_artifact["primary"]
    )
    sufficient_oracle = validation_metrics["feasibleOracleMtm1sUsdt"] > EPS
    sufficient_fills = validation_metrics["filledActs"] >= 2
    conditions = {
        "sufficientValidationOracle": sufficient_oracle,
        "sufficientValidationActualFills": sufficient_fills,
        "validationMtmPositive": validation_metrics["policyMtm1sUsdt"] > EPS,
        "validationBeatsGroupedBaseline": (
            validation_metrics["policyMtm1sUsdt"] > grouped_validation["policyMtm1sUsdt"] + EPS
        ),
        "validationMajorityWait": validation_metrics["policyActRate"] < 0.5,
    }
    if not sufficient_oracle or not sufficient_fills:
        promotion = "NEED_MORE_DATA"
    elif all(conditions.values()):
        promotion = "PROMOTE_TO_PILOT"
    else:
        promotion = "REJECT_BEFORE_PILOT"
    artifact = {
        "version": "HFT_ORDERFLOW_SEQUENCE_VALUE_V1_MODEL",
        "researchOnly": True,
        "features": FEATURES,
        "trainMarketIds": ids["train"],
        "validationMarketIds": ids["validation"],
        "pilotMarketIds": ids["holdout"][-5:],
        "valueModel": model,
        "promotionDecision": promotion,
    }
    joblib.dump(artifact, MODEL)
    report = {
        "version": "HFT_ORDERFLOW_SEQUENCE_VALUE_V1_TRAIN_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "execution": contract["execution"],
        "pretraining": pretraining,
        "encoderArtifact": str(ENCODER.resolve()),
        "embeddedFrame": str(EMBEDDED_FRAME.resolve()),
        "representation": {
            "embeddingDimension": EMBEDDING_DIM,
            "frozenDuringHftModelTraining": True,
            "targetPriorUsed": False,
            "pilotIncluded": False,
        },
        "primaryTrain": train_metrics,
        "primaryValidation": validation_metrics,
        "groupedBaselineValidation": grouped_validation,
        "promotionConditions": conditions,
        "promotionDecision": promotion,
        "guards": prereg["guards"],
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(REPORT), "model": str(MODEL), "encoder": str(ENCODER), "promotionDecision": promotion, "conditions": conditions, "pretraining": {key: value for key, value in pretraining.items() if key != "history"}, "primaryTrain": compact(train_metrics), "primaryValidation": compact(validation_metrics), "groupedBaselineValidation": compact(grouped_validation)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
