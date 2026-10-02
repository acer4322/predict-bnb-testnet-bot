from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_contingent_pair_holdout5_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_contingent_pair_holdout5_v1_report.json"
MARKETS = (1574737, 1574932, 1575211, 1575399, 1575633)
OFFSET = 1
EPS = 1e-9


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    rows = []
    for market_id in MARKETS:
        row = run_offset(market_id, OFFSET)
        rows.append(row)
        actual = row["actualExecution"]
        print(
            json.dumps(
                {
                    "marketId": market_id,
                    "floor": actual["worstCaseFloor"],
                    "pnl": actual["realizedPnl"],
                    "state": actual["terminalState"],
                    "makerUp": actual["makerUp"],
                    "makerDown": actual["makerDown"],
                    "takerUp": actual["takerUp"],
                    "takerDown": actual["takerDown"],
                    "violations": row["cycleInvariantViolationCount"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    floors = [float(row["actualExecution"]["worstCaseFloor"]) for row in rows]
    pnls = [float(row["actualExecution"]["realizedPnl"]) for row in rows]
    violations = sum(int(row["cycleInvariantViolationCount"]) for row in rows)
    aggregate = {
        "markets": len(rows),
        "actRate": 1.0,
        "waitRate": 0.0,
        "terminalWorstCaseFloor": sum(floors),
        "settledRealizedPnlAudit": sum(pnls),
        "positiveFloorMarkets": sum(value > EPS for value in floors),
        "nonnegativeFloorMarkets": sum(value >= -EPS for value in floors),
        "positivePnlMarkets": sum(value > EPS for value in pnls),
        "zeroFillWaitEquivalentMarkets": sum(row["actualExecution"]["terminalState"] == "NO_FILL_WAIT_EQUIVALENT" for row in rows),
        "directionalOptionalityMarkets": sum(row["actualExecution"]["terminalState"] == "DIRECTIONAL_OPTIONALITY" for row in rows),
        "dominatedFailureMarkets": sum(row["actualExecution"]["terminalState"] == "DOMINATED_NEGATIVE_BEST" for row in rows),
        "cycleInvariantViolations": violations,
    }
    keep = bool(
        aggregate["terminalWorstCaseFloor"] > EPS
        and aggregate["settledRealizedPnlAudit"] > EPS
        and violations == 0
        and aggregate["nonnegativeFloorMarkets"] >= 3
    )
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_CONTINGENT_PAIR_HOLDOUT5_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": {
            "markets": list(MARKETS),
            "type": "family-unseen chronological pre-official holdout",
            "officialHftForwardCutoverMarket": 1606593,
            "officialHftForwardUsed": False,
            "sealed20260816Used": False,
        },
        "fixedPolicy": "CONTINGENT_PAIR_OFFSET1",
        "formalWaitValue": 0.0,
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "partialFills": True,
            "actualFillInventory": True,
            "cancelAckBeforeTaker": True,
            "dreamFill": False,
            "runtimeR2TargetPaperWinnerPnlInput": False,
        },
        "aggregate": aggregate,
        "rows": rows,
        "oracleValueCeiling": "Not swept; WAIT and the single frozen policy only",
        "learnedPolicyRealizedValue": aggregate["settledRealizedPnlAudit"],
        "decision": "KEEP_EXPAND_CHRONOLOGICAL_DATASET" if keep else "REJECT_FIXED_CONTINGENT_PAIR_OFFSET1",
        "decisionReason": (
            "The single frozen policy cleared aggregate floor/PnL, lifecycle, and market-breadth gates on all family-unseen holdout markets."
            if keep and aggregate["nonnegativeFloorMarkets"] == len(MARKETS)
            else "The single frozen policy cleared the preregistered aggregate gate."
            if keep
            else "The single frozen policy failed at least one preregistered aggregate or lifecycle gate."
        ),
        "next": (
            "Expand only to a chronological dataset large enough to estimate no-fill and one-sided tail rates; preserve formal WAIT and do not tune on official HFT Forward."
            if keep
            else "Do not change offset, checkpoint, margin, or timeout on revealed holdout markets."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("aggregate", "decision", "decisionReason", "next")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
