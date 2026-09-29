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
    parser.add_argument("--train", type=int, default=80)
    parser.add_argument("--validation", type=int, default=20)
    parser.add_argument("--holdout", type=int, default=20)
    parser.add_argument("--output", default="hft_native_unused_chronology_v2_preregistered.json")
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
        rows = con.execute(
            """SELECT market_id,MIN(decision_ms),MAX(decision_ms),COUNT(*)
                 FROM our_decisions
                WHERE strategy_version=?
                GROUP BY market_id
                ORDER BY MIN(decision_ms),market_id""",
            (VERSION,),
        ).fetchall()
    finally:
        con.close()

    eligible_reverse: list[dict[str, Any]] = []
    reasons: dict[str, int] = {}
    scanned = 0

    def reject(reason: str) -> None:
        reasons[reason] = reasons.get(reason, 0) + 1

    # We need the newest requested qualifying chronology, not a quality census of
    # every historical archive. Walk backward and stop once the preregistered
    # cohort is full; this avoids decompressing hundreds of irrelevant tapes.
    for market_id, first_ms, last_ms, decisions in reversed(rows):
        scanned += 1
        market_id = int(market_id)
        first_ms = int(first_ms)
        last_ms = int(last_ms)
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
        eligible_reverse.append(
            {
                "marketId": market_id,
                "firstDecisionMs": first_ms,
                "lastDecisionMs": last_ms,
                "decisionRows": int(decisions),
                "windowStartMs": window_start,
                "windowEndMs": window_end,
                "qualityStatus": quality.get("qualityStatus"),
            }
        )
        if len(eligible_reverse) >= requested:
            break

    if len(eligible_reverse) < requested:
        raise RuntimeError(f"only {len(eligible_reverse)} unused eligible markets, need {requested}")
    selected = list(reversed(eligible_reverse))
    train = selected[: args.train]
    validation = selected[args.train : args.train + args.validation]
    holdout = selected[args.train + args.validation :]
    report = {
        "version": "HFT_NATIVE_UNUSED_CHRONOLOGY_V2_PREREGISTERED",
        "researchOnly": True,
        "selectionUsesActionOutcomes": False,
        "selectionRule": "Last 120 chronological unused COMPLETE_FORWARD_V1 ordinary markets after 2026-08-16 and no later than the official HFT Forward activation boundary, excluding every market already present in hft_native artifacts, official HFT Forward reports, and sealed/graduation artifacts.",
        "execution": {
            "engine": "HftBacktest",
            "marketData": "PREDICT_EXECUTION_TAPE_V1",
            "queueModel": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "partialFill": True,
        },
        "modelLock": "Exact HFT_NATIVE_QUEUE_REGIME_VALUE_V1 features, hurdle components, hyperparameters, score, and WAIT>0 decision rule. No threshold or reward-weight sweep after generation.",
        "checkpointRule": "Two deterministic spread-across-market HFT actual-own-state placement checkpoints per market; both sides offsets 0/1/2 plus WAIT.",
        "eligibleUnusedMarketsSelected": len(selected),
        "recentCandidateMarketsScanned": scanned,
        "excludedCounts": reasons,
        "train": train,
        "validation": validation,
        "holdout": holdout,
        "decisionRule": {
            "KEEP": "chronological unseen holdout realized execution value > 0",
            "REJECT": "chronological unseen holdout realized execution value <= 0 when holdout oracle value > 0",
            "NEED_MORE_DATA": "holdout oracle value is zero or too sparse to test the locked policy",
        },
        "guards": {
            "winnerSettlementPnlRuntimeInput": False,
            "targetFutureActionRuntimeInput": False,
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
                "eligibleUnusedMarketsSelected": len(selected),
                "recentCandidateMarketsScanned": scanned,
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
