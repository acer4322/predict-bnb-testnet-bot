from __future__ import annotations

import argparse
import json
import math
from collections import Counter, defaultdict
from pathlib import Path

import joblib
import numpy as np
from interpret.glassbox import ExplainableBoostingRegressor

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
ACTIONS = ["WAIT_PRESERVE", "CANCEL_ACK_BOUNDED_TAKER", "CANCEL_ACK_RETIRE_REBASE"]
WAIT = "WAIT_PRESERVE"
EPS = 1e-9
VERSION = "R2_LOCAL_RECOVERY_Q_V1"

# Fixed before validation reveal. All are strict-past checkpoint fields emitted by
# hft_r2_recovery_option_transition_pilot_v1.py. No winner, settlement, Target
# future action, or terminal value enters runtime features.
FEATURES = [
    "workingRecoveryExists", "workingRecoveryAgeMs", "workingRecoveryOffsetTicks",
    "workingRecoveryRemainingQty", "absTrackingError", "trackingError",
    "actualMakerNet", "actualCombinedGross", "secondsLeft", "recoveryBid",
    "recoveryAsk", "recoverySpreadTicks", "pairAskSum", "pairBidSum",
    "marginalSurplusChunkAvgCost", "lockedPairEdgePerShare", "lastMakerFillAgeMs",
    "lastMakerFillSideIsRecovery", "directionTowardRecovery",
    "spotReturn1sTowardRecovery", "spotReturn3sTowardRecovery",
    "spotQueueTowardRecovery", "spotTaker1sTowardRecovery",
    "futuresReturn1sTowardRecovery", "futuresReturn3sTowardRecovery",
    "futuresQueueTowardRecovery", "futuresTaker1sTowardRecovery",
    "asymmetryAgeMs", "observationDelayMs", "hasPriorObservation",
    "elapsedSincePriorMs", "recoveryStatusNew", "recoveryStatusPartial",
]


def finite_or_nan(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return np.nan
    return x if math.isfinite(x) else np.nan


def load_contexts(path: Path):
    report = json.loads(path.read_text(encoding="utf-8"))
    groups = defaultdict(list)
    for row in report.get("rows", []):
        groups[(int(row["marketId"]), str(row["faultId"]))].append(row)
    contexts = []
    incomplete = []
    for (market_id, fault_id), rows in sorted(groups.items()):
        by = {str(r["actionId"]): r for r in rows if r.get("actionId") in ACTIONS}
        if set(by) != set(ACTIONS):
            incomplete.append({"marketId": market_id, "faultId": fault_id, "actions": sorted(by)})
            continue
        hashes = {str(by[a]["pointStateHash"]) for a in ACTIONS}
        if len(hashes) != 1:
            incomplete.append({"marketId": market_id, "faultId": fault_id, "reason": "START_STATE_MISMATCH"})
            continue
        wait_local = float(by[WAIT]["optionTransition"]["localReward"]["deltaWorstCaseFloor"])
        wait_terminal = float(by[WAIT]["terminalWorstCaseFloor"])
        advantages = {
            a: float(by[a]["optionTransition"]["localReward"]["deltaWorstCaseFloor"]) - wait_local
            for a in ACTIONS
        }
        terminal_adv = {a: float(by[a]["terminalWorstCaseFloor"]) - wait_terminal for a in ACTIONS}
        f = by[WAIT]["optionTransition"]["checkpointFeatures"]
        contexts.append({
            "marketId": market_id,
            "faultId": fault_id,
            "stateHash": next(iter(hashes)),
            "features": {k: finite_or_nan(f.get(k)) for k in FEATURES},
            "localAdvantages": advantages,
            "terminalAdvantages": terminal_adv,
        })
    return report, contexts, incomplete


def matrix(rows, medians=None):
    X = np.asarray([[r["features"][k] for k in FEATURES] for r in rows], dtype=float)
    if medians is None:
        medians = np.nanmedian(X, axis=0)
        medians = np.where(np.isfinite(medians), medians, 0.0)
    inds = np.where(~np.isfinite(X))
    X[inds] = medians[inds[1]]
    return X, medians


def fit_ebm(X, y):
    # Conservative low-complexity EBM. No pairwise interactions in V1: the point
    # is to test whether local semi-MDP value is identifiable before adding capacity.
    model = ExplainableBoostingRegressor(
        interactions=0,
        outer_bags=8,
        max_rounds=5000,
        learning_rate=0.03,
        min_samples_leaf=3,
        random_state=20260823,
        n_jobs=-1,
    )
    model.fit(X, y)
    return model


def evaluate(rows, X, models):
    decisions = []
    total_local = 0.0
    total_terminal = 0.0
    oracle_local = 0.0
    oracle_terminal = 0.0
    for i, row in enumerate(rows):
        pred = {WAIT: 0.0}
        for action, model in models.items():
            pred[action] = float(model.predict(X[i:i+1])[0])
        selected = max(ACTIONS, key=lambda a: (pred[a], a == WAIT))
        if pred[selected] <= 0.0:
            selected = WAIT
        realized_local = float(row["localAdvantages"][selected])
        realized_terminal = float(row["terminalAdvantages"][selected])
        local_oracle = max(ACTIONS, key=lambda a: (row["localAdvantages"][a], a == WAIT))
        terminal_oracle = max(ACTIONS, key=lambda a: (row["terminalAdvantages"][a], a == WAIT))
        total_local += realized_local
        total_terminal += realized_terminal
        oracle_local += float(row["localAdvantages"][local_oracle])
        oracle_terminal += float(row["terminalAdvantages"][terminal_oracle])
        decisions.append({
            "marketId": row["marketId"], "faultId": row["faultId"],
            "selectedAction": selected, "predictedAdvantages": pred,
            "realizedLocalAdvantageVsWait": realized_local,
            "realizedTerminalAdvantageVsWait": realized_terminal,
            "localOracleAction": local_oracle,
            "terminalOracleAction": terminal_oracle,
        })
    return {
        "contexts": len(rows),
        "actionCounts": dict(Counter(d["selectedAction"] for d in decisions)),
        "policyLocalAdvantageVsWait": total_local,
        "policyTerminalAuditAdvantageVsWait": total_terminal,
        "localOracleAdvantageVsWait": oracle_local,
        "terminalOracleAdvantageVsWait": oracle_terminal,
        "positiveLocalDecisions": sum(d["realizedLocalAdvantageVsWait"] > EPS for d in decisions),
        "negativeLocalDecisions": sum(d["realizedLocalAdvantageVsWait"] < -EPS for d in decisions),
        "waitDecisions": sum(d["selectedAction"] == WAIT for d in decisions),
        "decisions": decisions,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="hft_r2_recovery_semimdp_train26_v1_report.json")
    ap.add_argument("--output", default="r2_local_recovery_q_v1_report.json")
    ap.add_argument("--model", default="r2_local_recovery_q_v1.joblib")
    ap.add_argument("--train-fraction", type=float, default=0.70)
    args = ap.parse_args()

    source = BASE / args.source
    raw, contexts, incomplete = load_contexts(source)
    markets = sorted({r["marketId"] for r in contexts})
    if len(markets) < 6:
        raise SystemExit("need at least 6 complete chronological markets")
    cut = max(1, min(len(markets)-1, int(len(markets) * args.train_fraction)))
    train_markets = set(markets[:cut])
    valid_markets = set(markets[cut:])
    train = [r for r in contexts if r["marketId"] in train_markets]
    valid = [r for r in contexts if r["marketId"] in valid_markets]
    X_train, medians = matrix(train)
    X_valid, _ = matrix(valid, medians)

    models = {}
    for action in ACTIONS:
        if action == WAIT:
            continue
        y = np.asarray([r["localAdvantages"][action] for r in train], dtype=float)
        models[action] = fit_ebm(X_train, y)

    train_eval = evaluate(train, X_train, models)
    valid_eval = evaluate(valid, X_valid, models)
    payload = {
        "version": VERSION,
        "researchOnly": True,
        "source": str(source.relative_to(ROOT)),
        "sourceCompleteAtRead": bool(raw.get("complete")),
        "sourceRowsAtRead": len(raw.get("rows", [])),
        "teacher": "matched HftBacktest local semi-MDP deltaWorstCaseFloor advantage versus WAIT at identical strict-past recovery state",
        "runtimeBoundary": "strict-past own/public/lifecycle state only; no Target future action, winner, settlement, or terminal PnL as model input",
        "featureNames": FEATURES,
        "learner": {
            "family": "per-action additive ExplainableBoostingRegressor local-Q",
            "interactions": 0,
            "decisionRule": "argmax predicted local advantage; ACT only if predicted advantage > 0 else WAIT",
            "thresholdSweep": False,
            "seed": 20260823,
        },
        "chronology": {
            "allCompleteMarkets": markets,
            "trainMarkets": sorted(train_markets),
            "validationMarkets": sorted(valid_markets),
            "incompleteGroupsAtRead": incomplete,
        },
        "train": train_eval,
        "validation": valid_eval,
    }
    # Gate fixed ex ante: unseen chronological local value must beat WAIT and must
    # not be produced by more losing ACT decisions than winning ones. Terminal is
    # an audit only and is deliberately not used to select the policy.
    gate = (
        valid_eval["policyLocalAdvantageVsWait"] > 0
        and valid_eval["positiveLocalDecisions"] >= valid_eval["negativeLocalDecisions"]
    )
    payload["validationGatePass"] = bool(gate)
    payload["decision"] = "KEEP_LOCAL_Q_FOR_LARGER_OOS" if gate else "REJECT_LOCAL_Q_V1_BEFORE_HOLDOUT"
    payload["next"] = (
        "If KEEP, freeze this exact feature/action/decision rule and evaluate on a separately locked later cohort before any R2 integration. "
        "If REJECT, do not tune the zero threshold or add EBM interactions on this validation set."
    )
    joblib.dump({"version": VERSION, "features": FEATURES, "medians": medians, "models": models}, BASE / args.model)
    (BASE / args.output).write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({
        "ok": True, "output": str(BASE / args.output), "model": str(BASE / args.model),
        "sourceCompleteAtRead": payload["sourceCompleteAtRead"],
        "trainContexts": train_eval["contexts"], "validationContexts": valid_eval["contexts"],
        "validationLocalAdvantage": valid_eval["policyLocalAdvantageVsWait"],
        "validationTerminalAuditAdvantage": valid_eval["policyTerminalAuditAdvantageVsWait"],
        "validationActions": valid_eval["actionCounts"],
        "validationPositive": valid_eval["positiveLocalDecisions"],
        "validationNegative": valid_eval["negativeLocalDecisions"],
        "decision": payload["decision"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
