from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import run_offset  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = BASE / "hft_safe_floor_late_regime_blind25_v1_preregistered.json"
REPORT = BASE / "hft_safe_floor_late_regime_blind25_v1_report.json"
EPS = 1e-9


def max_drawdown(values: list[float]) -> float:
    peak = cumulative = maximum = 0.0
    for value in values:
        cumulative += value
        peak = max(peak, cumulative)
        maximum = max(maximum, peak - cumulative)
    return maximum


def compact(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "marketId": int(row["marketId"]),
        "checkpointMs": int(row["checkpointMs"]),
        "secondsLeft": float(row["secondsLeft"]),
        "initialQuotes": row["initialQuotes"],
        "winnerAuditOnly": row["winnerAuditOnly"],
        "actualExecution": row["actualExecution"],
        "fills": row["fills"],
        "lifecycle": row["lifecycle"],
        "cycleInvariantViolations": row["cycleInvariantViolations"],
        "cycleInvariantViolationCount": int(row["cycleInvariantViolationCount"]),
    }


def main() -> int:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    markets = [int(value) for value in prereg["cohort"]["markets"]]
    rows: list[dict[str, Any]] = []
    for index, market_id in enumerate(markets):
        result = run_offset(market_id, 1)
        rows.append(compact(result))
        actual = result["actualExecution"]
        print(
            json.dumps(
                {
                    "progress": f"{index + 1}/{len(markets)}",
                    "marketId": market_id,
                    "floor": actual["worstCaseFloor"],
                    "pnl": actual["realizedPnl"],
                    "terminalState": actual["terminalState"],
                    "violations": result["cycleInvariantViolationCount"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    floors = [float(row["actualExecution"]["worstCaseFloor"]) for row in rows]
    pnls = [float(row["actualExecution"]["realizedPnl"]) for row in rows]
    violations = sum(int(row["cycleInvariantViolationCount"]) for row in rows)
    negative_markets = sum(value < -EPS for value in floors)
    oracle_markets = [row for row in rows if float(row["actualExecution"]["worstCaseFloor"]) > EPS]
    aggregate = {
        "markets": len(rows),
        "actMarkets": len(rows),
        "waitMarkets": 0,
        "actRate": 1.0,
        "waitRate": 0.0,
        "terminalWorstCaseFloor": sum(floors),
        "settledRealizedPnlAudit": sum(pnls),
        "meanTerminalFloor": sum(floors) / len(floors),
        "medianTerminalFloor": sorted(floors)[len(floors) // 2],
        "positiveMarkets": sum(value > EPS for value in floors),
        "zeroMarkets": sum(abs(value) <= EPS for value in floors),
        "negativeMarkets": negative_markets,
        "maxCumulativeDrawdown": max_drawdown(pnls),
        "cycleInvariantViolations": violations,
        "makerFilledShares": sum(
            float(row["actualExecution"]["makerUp"] + row["actualExecution"]["makerDown"])
            for row in rows
        ),
        "takerFilledShares": sum(
            float(row["actualExecution"]["takerUp"] + row["actualExecution"]["takerDown"])
            for row in rows
        ),
        "takerFeesUsdt": sum(float(row["actualExecution"]["takerFeesUsdt"]) for row in rows),
    }
    contract = prereg["primaryGate"]
    gates = {
        "positiveAggregateTerminalFloor": aggregate["terminalWorstCaseFloor"]
        > float(contract["aggregateTerminalWorstCaseFloorMinExclusive"]),
        "positiveAggregateSettledPnl": aggregate["settledRealizedPnlAudit"]
        > float(contract["aggregateSettledRealizedPnlAuditMinExclusive"]),
        "negativeMarketLimit": negative_markets <= int(contract["maximumNegativeMarkets"]),
        "cycleInvariantViolationLimit": violations <= int(contract["maximumCycleInvariantViolations"]),
    }
    keep = all(gates.values())
    report = {
        "version": "HFT_SAFE_FLOOR_LATE_REGIME_BLIND25_V1_REPORT",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "hypothesis": prereg["hypothesis"],
        "priorEvidence": prereg["evidenceBeforePreregistration"],
        "cohort": prereg["cohort"],
        "fixedPolicy": prereg["fixedPolicy"],
        "executionSemantics": prereg["executionSemantics"],
        "waitActRate": {"actRate": 1.0, "waitRate": 0.0},
        "aggregate": aggregate,
        "learnedPolicyRealizedValue": {
            "meaning": "Frozen fixed-policy realized execution value; no learned gate was used.",
            "terminalWorstCaseFloor": aggregate["terminalWorstCaseFloor"],
            "settledRealizedPnlAudit": aggregate["settledRealizedPnlAudit"],
        },
        "oracleValueCeiling": {
            "terminalWorstCaseFloor": sum(
                float(row["actualExecution"]["worstCaseFloor"]) for row in oracle_markets
            ),
            "settledRealizedPnlAudit": sum(
                float(row["actualExecution"]["realizedPnl"]) for row in oracle_markets
            ),
            "actMarkets": len(oracle_markets),
            "actRate": len(oracle_markets) / len(rows),
            "meaning": "Revealed WAIT plus positive-real-floor ACT; audit-only.",
        },
        "primaryGates": gates,
        "rows": rows,
        "decision": "KEEP_LATE_REGIME_SAFE_FLOOR_PROGRAM" if keep else "REJECT_LATE_REGIME_ALWAYS_ACT",
        "decisionReason": (
            "The unchanged full-cycle action retained positive aggregate worst-case floor and settled value with acceptable tail count and zero lifecycle violations on the final pre-official block."
            if keep
            else "The unchanged action failed aggregate value, tail-count, or lifecycle gates on the final pre-official block. Do not tune this revealed cohort."
        ),
        "next": (
            "Audit the causal execution-regime change and completion timing. Keep the failed net-QR gate separate; do not promote to official-forward or live without an authorized unseen-forward protocol."
            if keep
            else "Reject the late-regime always-ACT hypothesis and do not rescue it with a chronological cutoff or parameter tuning."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(REPORT),
                "aggregate": aggregate,
                "oracleValueCeiling": report["oracleValueCeiling"],
                "primaryGates": gates,
                "decision": report["decision"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
