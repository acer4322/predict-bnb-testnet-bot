from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_quality_v1 import assess_archive  # noqa: E402
from tools.hftbacktest_r2_execution_school_v0 import STRATEGY_DB, VERSION  # noqa: E402
from tools.select_hft_native_timegrid_inventory_v3 import ids_from_json  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
OFFICIAL = ROOT / "data" / "hft_forward_paper_v1" / "markets"
AFTER_2026_08_16_MS = 1_786_896_000_000
OFFICIAL_ACTIVATION_AFTER_WINDOW_END_MS = 1_787_391_600_000
TARGET_REGIME_MAX_CHECKPOINT_MS = 1_787_106_225_500


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=int, default=60)
    parser.add_argument("--validation", type=int, default=15)
    parser.add_argument("--holdout", type=int, default=15)
    parser.add_argument("--output", default="hft_native_constrained_rank_v4_preregistered.json")
    args = parser.parse_args()
    requested = int(args.train + args.validation + args.holdout)

    used: set[int] = set()
    for path in BASE.glob("hft_native*.json"):
        used.update(ids_from_json(path))
    sealed: set[int] = set()
    for path in BASE.glob("*.json"):
        if any(token in path.name.lower() for token in ("graduation", "sealed", "exam_registry")):
            sealed.update(ids_from_json(path))
    official: set[int] = set()
    if OFFICIAL.exists():
        for path in OFFICIAL.glob("*_hft_closed_loop_v1.json.xz"):
            try:
                official.add(int(path.name.split("_", 1)[0]))
            except Exception:
                pass

    con = sqlite3.connect(STRATEGY_DB)
    try:
        candidates = con.execute(
            """SELECT market_id,MIN(decision_ms),MAX(decision_ms),COUNT(*)
                 FROM our_decisions
                WHERE strategy_version=?
                GROUP BY market_id
                ORDER BY MIN(decision_ms),market_id""",
            (VERSION,),
        ).fetchall()
    finally:
        con.close()

    selected_reverse: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    scanned = 0

    def reject(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    for market_id, first_ms, last_ms, decisions in reversed(candidates):
        scanned += 1
        market_id = int(market_id)
        if market_id in used:
            reject("ALREADY_IN_HFT_NATIVE_ARTIFACT")
            continue
        if market_id in sealed:
            reject("SEALED_OR_GRADUATION")
            continue
        if market_id in official:
            reject("OFFICIAL_HFT_FORWARD")
            continue
        tape_path = TAPES / f"{market_id}.json.xz"
        if not tape_path.exists():
            reject("NO_EXECUTION_TAPE")
            continue
        quality = assess_archive(tape_path)
        if quality.get("qualityStatus") != "COMPLETE_FORWARD_V1":
            reject("NOT_COMPLETE_FORWARD_V1")
            continue
        window_start = int(quality.get("windowStartMs") or 0)
        window_end = int(quality.get("windowEndMs") or 0)
        if window_start < AFTER_2026_08_16_MS:
            reject("ON_OR_BEFORE_2026_08_16")
            continue
        if window_start <= TARGET_REGIME_MAX_CHECKPOINT_MS:
            reject("NOT_AFTER_TARGET_REGIME_PRETRAINING")
            continue
        if window_end > OFFICIAL_ACTIVATION_AFTER_WINDOW_END_MS:
            reject("AT_OR_AFTER_OFFICIAL_CUTOVER")
            continue
        if int(decisions) < 4:
            reject("INSUFFICIENT_PUBLIC_STATES")
            continue
        selected_reverse.append(
            {
                "marketId": market_id,
                "firstDecisionMs": int(first_ms),
                "lastDecisionMs": int(last_ms),
                "decisionRows": int(decisions),
                "windowStartMs": window_start,
                "windowEndMs": window_end,
                "qualityStatus": quality.get("qualityStatus"),
            }
        )
        if len(selected_reverse) >= requested:
            break
    if len(selected_reverse) < requested:
        raise RuntimeError(f"only {len(selected_reverse)} unused eligible markets, need {requested}")

    selected = list(reversed(selected_reverse))
    train = selected[: args.train]
    validation = selected[args.train : args.train + args.validation]
    holdout = selected[args.train + args.validation :]
    report = {
        "version": "HFT_NATIVE_CONSTRAINED_RANK_V4_PREREGISTERED",
        "researchOnly": True,
        "selectionUsesActionOutcomes": False,
        "hypothesis": "A checkpoint-group pairwise ranker with explicit WAIT and portfolio floor as a feasibility constraint, rather than additive reward, can identify positive realized 1s markout on unseen HftBacktest outcomes. A strict-past Target-public-book regime dictionary may improve cross-regime generalization without using Target actions.",
        "selectionRule": f"Newest {requested} chronological unused COMPLETE_FORWARD_V1 ordinary markets strictly after Target public-regime pretraining data and before official HFT Forward cutover, excluding all existing hft_native, official Forward, and sealed/graduation markets.",
        "targetRegimePretraining": {
            "source": "target_actionpoint_interrogation_v0_*_controls.csv",
            "rows": 16028,
            "markets": 60,
            "maxCheckpointMs": TARGET_REGIME_MAX_CHECKPOINT_MS,
            "usesTargetActionOrPortfolio": False,
            "features": "public book/depth/spread/book-age only",
            "model": "StandardScaler + KMeans(n_clusters=8,n_init=10,random_state=20260823)",
        },
        "execution": {
            "engine": "HftBacktest",
            "marketData": "PREDICT_EXECUTION_TAPE_V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFill": True,
            "fillHorizonMs": 5000,
            "markoutHorizonAfterFillMs": 1000,
        },
        "checkpointRule": "Three deterministic 10%/50%/90% usable-public-snapshot checkpoints per market; no R2/Target placement selection.",
        "actionSpace": ["WAIT", "UP_0", "UP_1", "UP_2", "DOWN_0", "DOWN_1", "DOWN_2"],
        "feasibilityConstraint": "Keep WAIT plus only actions whose deterministic 18-share full-fill delta worst-case floor is >= 0. Floor delta is not part of reward.",
        "reward": "Realized 1s post-fill MTM USDT only; no portfolio-floor addition, winner, settlement, or PnL.",
        "rankingLock": {
            "objective": "checkpoint-group pairwise action ranking; WAIT wins exact zero-reward ties",
            "model": "SimpleImputer(median) + StandardScaler + LogisticRegression(C=0.25,max_iter=2000,fit_intercept=False,random_state=20260823)",
            "pairConstruction": "All unequal action-action rewards plus every action-vs-WAIT pair; symmetric difference augmentation",
            "primary": "with Target public-book KMeans regime-action one-hots",
            "ablation": "same ranker without Target regime one-hots",
            "thresholdSweep": False,
            "hyperparameterSweep": False,
        },
        "selectedMarkets": len(selected),
        "recentCandidateMarketsScanned": scanned,
        "excludedCounts": reasons,
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "decisionRule": {
            "KEEP": "primary unseen holdout realized MTM > 0 with constrained oracle > 0",
            "REJECT": "primary unseen holdout realized MTM <= 0 when constrained oracle > 0",
            "NEED_MORE_DATA": "holdout constrained oracle is zero",
        },
        "guards": {
            "winnerSettlementPnlRuntimeInput": False,
            "targetFutureActionRuntimeInput": False,
            "targetActionTeacherUsed": False,
            "floorAddedToReward": False,
            "officialHftForwardTuning": False,
            "special20260816Used": False,
            "supervisorFinal75To99Used": False,
        },
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "ok": True,
                "output": str(output),
                "selectedMarkets": len(selected),
                "trainIds": [row["marketId"] for row in train],
                "validationIds": [row["marketId"] for row in validation],
                "holdoutIds": [row["marketId"] for row in holdout],
                "spans": {
                    split: {
                        "minWindowStartMs": min(row["windowStartMs"] for row in rows),
                        "maxWindowEndMs": max(row["windowEndMs"] for row in rows),
                    }
                    for split, rows in (("train", train), ("validation", validation), ("holdout", holdout))
                },
                "excludedCounts": reasons,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
