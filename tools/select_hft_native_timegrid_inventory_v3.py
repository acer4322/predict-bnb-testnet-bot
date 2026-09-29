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


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
OFFICIAL = ROOT / "data" / "hft_forward_paper_v1" / "markets"
AFTER_2026_08_16_MS = 1_786_896_000_000
OFFICIAL_ACTIVATION_AFTER_WINDOW_END_MS = 1_787_391_600_000


def market_ids(value: Any) -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"marketid", "market_id"}:
                try:
                    found.add(int(item))
                except Exception:
                    pass
            elif lower in {"markets", "marketids", "market_ids", "exammarketids", "formalmarketids"} and isinstance(item, list):
                for candidate in item:
                    try:
                        found.add(int(candidate))
                    except Exception:
                        found.update(market_ids(candidate))
            else:
                found.update(market_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(market_ids(item))
    return found


def ids_from_json(path: Path) -> set[int]:
    try:
        return market_ids(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return set()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--train", type=int, default=50)
    parser.add_argument("--validation", type=int, default=10)
    parser.add_argument("--holdout", type=int, default=20)
    parser.add_argument("--output", default="hft_native_timegrid_inventory_chronology_v3_preregistered.json")
    args = parser.parse_args()
    requested = int(args.train + args.validation + args.holdout)

    used: set[int] = set()
    for path in BASE.glob("hft_native*.json"):
        used.update(ids_from_json(path))
    sealed: set[int] = set()
    for path in BASE.glob("*.json"):
        lower = path.name.lower()
        if any(token in lower for token in ("graduation", "sealed", "exam_registry")):
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
        "version": "HFT_NATIVE_TIMEGRID_INVENTORY_CHRONOLOGY_V3_PREREGISTERED",
        "researchOnly": True,
        "selectionUsesActionOutcomes": False,
        "selectionRule": f"Newest {requested} chronological unused COMPLETE_FORWARD_V1 ordinary markets after 2026-08-16 and before official HFT Forward cutover, excluding all markets in existing hft_native artifacts, official Forward reports, and sealed/graduation artifacts.",
        "hypothesis": "The positive placement-checkpoint result is a sparse passive inventory-repair opportunity. It should retain positive realized execution value on target-placement-independent public time-grid checkpoints and a completely unused chronological market holdout.",
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
        "checkpointRule": "Three deterministic checkpoints per market at the 10%, 50%, and 90% indices of usable public snapshots. No R2/Target placement or future action is used to select a checkpoint.",
        "ownStateRule": "Replay the Frozen base trajectory only to reconstruct actual fills and portfolio state. At each checkpoint use only fills with eventMs <= checkpointMs; future R2/Target actions are excluded.",
        "actionSpace": ["WAIT", "UP_0", "UP_1", "UP_2", "DOWN_0", "DOWN_1", "DOWN_2"],
        "modelLock": "Exact HFT_NATIVE_QUEUE_REGIME_VALUE_V1 static portfolio/public/queue features, three hurdle components, hyperparameters, score, and WAIT>0 rule. Queue-reactive raw add/remove features are excluded after their same-cohort ablation showed no robust validation increment. No threshold or reward-weight sweep.",
        "score": "P(fill) * (E(shares|fill) * E(markout/share|fill) + deterministic floor delta at E(shares|fill))",
        "selectedMarkets": len(selected),
        "recentCandidateMarketsScanned": scanned,
        "excludedCounts": reasons,
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "decisionRule": {
            "KEEP": "chronological unseen holdout realized execution value > 0 with holdout oracle value > 0",
            "REJECT": "chronological unseen holdout realized execution value <= 0 when holdout oracle value > 0",
            "NEED_MORE_DATA": "holdout oracle value is zero",
        },
        "guards": {
            "winnerSettlementPnlRuntimeInput": False,
            "targetFutureActionRuntimeInput": False,
            "targetPlacementCheckpointSelection": False,
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
                "excludedCounts": reasons,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
