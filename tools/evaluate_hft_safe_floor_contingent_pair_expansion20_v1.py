from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_contingent_pair_expansion20_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_contingent_pair_expansion20_v1_report.json"
MARKETS = (
    1575819, 1576119, 1576324, 1576518, 1576765,
    1576991, 1577181, 1577392, 1577751, 1577937,
    1578351, 1578546, 1578732, 1578921, 1579116,
    1579313, 1579674, 1579874, 1580153, 1580346,
)
OFFSET = 1
EPS = 1e-9


def max_drawdown(values: list[float]) -> float:
    peak = cumulative = drawdown = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    return drawdown


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    rows = []
    for index, market_id in enumerate(MARKETS, 1):
        row = run_offset(market_id, OFFSET)
        rows.append(row)
        actual = row["actualExecution"]
        print(
            json.dumps(
                {
                    "progress": f"{index}/{len(MARKETS)}",
                    "marketId": market_id,
                    "floor": actual["worstCaseFloor"],
                    "pnl": actual["realizedPnl"],
                    "state": actual["terminalState"],
                    "makerShares": actual["makerUp"] + actual["makerDown"],
                    "takerShares": actual["takerUp"] + actual["takerDown"],
                    "violations": row["cycleInvariantViolationCount"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    floors = [float(row["actualExecution"]["worstCaseFloor"]) for row in rows]
    pnls = [float(row["actualExecution"]["realizedPnl"]) for row in rows]
    states = [str(row["actualExecution"]["terminalState"]) for row in rows]
    negative_markets = sum(value < -EPS for value in pnls)
    violations = sum(int(row["cycleInvariantViolationCount"]) for row in rows)
    aggregate = {
        "markets": len(rows),
        "actRate": 1.0,
        "formalWaitValue": 0.0,
        "terminalWorstCaseFloor": sum(floors),
        "settledRealizedPnlAudit": sum(pnls),
        "meanPnl": sum(pnls) / len(pnls),
        "positiveMarkets": sum(value > EPS for value in pnls),
        "zeroMarkets": sum(abs(value) <= EPS for value in pnls),
        "negativeMarkets": negative_markets,
        "maxCumulativeDrawdown": max_drawdown(pnls),
        "safePositiveFloorMarkets": states.count("SAFE_POSITIVE_FLOOR"),
        "zeroFillWaitEquivalentMarkets": states.count("NO_FILL_WAIT_EQUIVALENT"),
        "directionalOptionalityMarkets": states.count("DIRECTIONAL_OPTIONALITY"),
        "dominatedFailureMarkets": states.count("DOMINATED_NEGATIVE_BEST"),
        "makerFilledShares": sum(float(row["actualExecution"]["makerUp"] + row["actualExecution"]["makerDown"]) for row in rows),
        "takerFilledShares": sum(float(row["actualExecution"]["takerUp"] + row["actualExecution"]["takerDown"]) for row in rows),
        "takerFeesUsdt": sum(float(row["actualExecution"]["takerFeesUsdt"]) for row in rows),
        "cycleInvariantViolations": violations,
    }
    keep = bool(
        aggregate["terminalWorstCaseFloor"] > EPS
        and aggregate["settledRealizedPnlAudit"] > EPS
        and violations == 0
        and negative_markets <= 4
    )
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_CONTINGENT_PAIR_EXPANSION20_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": {
            "markets": list(MARKETS),
            "type": "chronological pre-official expansion after family-unseen holdout5",
            "officialHftForwardCutoverMarket": 1606593,
            "officialHftForwardUsed": False,
            "sealed20260816Used": False,
        },
        "fixedPolicy": "CONTINGENT_PAIR_OFFSET1",
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
        "oracleValueCeiling": "Not swept; WAIT and one frozen policy only",
        "learnedPolicyRealizedValue": aggregate["settledRealizedPnlAudit"],
        "decision": "KEEP_BUILD_CHRONOLOGICAL_SAFE_FLOOR_DATASET" if keep else "REJECT_FIXED_CONTINGENT_PAIR_OFFSET1_EXPANSION",
        "decisionReason": (
            "The frozen policy retained positive aggregate execution value with acceptable terminal-tail count and zero lifecycle violations."
            if keep
            else "The frozen policy failed the preregistered aggregate value, tail-count, or lifecycle gate."
        ),
        "next": (
            "Build a chronology-rich state/trajectory dataset for fill/no-fill/cancel/Taker timing and evaluate one untouched pre-official block before any official-forward canary. Formal WAIT remains available; do not optimize offsets on these 20 markets."
            if keep
            else "Stop this exact family and do not tune revealed expansion markets."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("aggregate", "decision", "decisionReason", "next")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
