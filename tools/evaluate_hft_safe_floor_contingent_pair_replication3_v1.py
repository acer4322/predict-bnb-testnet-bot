from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.hft_safe_floor_contingent_pair_smoke_v1 import run_offset


OUT_DIR = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
PREREG = OUT_DIR / "hft_safe_floor_contingent_pair_replication3_v1_preregistered.json"
REPORT = OUT_DIR / "hft_safe_floor_contingent_pair_replication3_v1_report.json"
MARKETS = (1574038, 1574352, 1574538)
OFFSETS = (0, 1)
EPS = 1e-9


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError(f"missing preregistration: {PREREG}")
    rows = []
    for market_id in MARKETS:
        for offset in OFFSETS:
            row = run_offset(market_id, offset)
            rows.append(row)
            actual = row["actualExecution"]
            print(
                json.dumps(
                    {
                        "marketId": market_id,
                        "offset": offset,
                        "floor": actual["worstCaseFloor"],
                        "pnl": actual["realizedPnl"],
                        "state": actual["terminalState"],
                        "violations": row["cycleInvariantViolationCount"],
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )

    fixed: dict[str, dict] = {}
    for offset in OFFSETS:
        selected = [row for row in rows if int(row["offset"]) == offset]
        floors = [float(row["actualExecution"]["worstCaseFloor"]) for row in selected]
        pnls = [float(row["actualExecution"]["realizedPnl"]) for row in selected]
        violations = sum(int(row["cycleInvariantViolationCount"]) for row in selected)
        fixed[str(offset)] = {
            "markets": len(selected),
            "terminalWorstCaseFloor": sum(floors),
            "settledRealizedPnlAudit": sum(pnls),
            "positiveFloorMarkets": sum(value > EPS for value in floors),
            "nonnegativeFloorMarkets": sum(value >= -EPS for value in floors),
            "positivePnlMarkets": sum(value > EPS for value in pnls),
            "cycleInvariantViolations": violations,
            "actRate": 1.0,
        }

    oracle_rows = []
    for market_id in MARKETS:
        candidates = [row for row in rows if int(row["marketId"]) == market_id and row["cycleInvariantViolationCount"] == 0]
        best = max(candidates, key=lambda row: float(row["actualExecution"]["worstCaseFloor"]))
        floor = float(best["actualExecution"]["worstCaseFloor"])
        if floor > EPS:
            oracle_rows.append(
                {
                    "marketId": market_id,
                    "action": f"CONTINGENT_PAIR_OFFSET{int(best['offset'])}",
                    "floor": floor,
                    "pnlAudit": float(best["actualExecution"]["realizedPnl"]),
                }
            )
        else:
            oracle_rows.append({"marketId": market_id, "action": "WAIT", "floor": 0.0, "pnlAudit": 0.0})

    keep_offsets = [
        offset
        for offset, aggregate in fixed.items()
        if float(aggregate["terminalWorstCaseFloor"]) > EPS
        and float(aggregate["settledRealizedPnlAudit"]) > EPS
        and int(aggregate["cycleInvariantViolations"]) == 0
        and int(aggregate["nonnegativeFloorMarkets"]) >= 2
    ]
    report = {
        "reportVersion": "HFT_SAFE_FLOOR_CONTINGENT_PAIR_REPLICATION3_V1",
        "researchOnly": True,
        "preregisteredContract": PREREG.name,
        "cohort": {
            "markets": list(MARKETS),
            "type": "chronological opened development replication; not graduation OOS",
            "officialHftForwardUsed": False,
            "sealedUsed": False,
        },
        "executionSemantics": {
            "engine": "HftBacktest + Predict Execution Tape V1",
            "queue": "risk",
            "entryLatencyMs": 1092,
            "responseLatencyMs": 273,
            "ownStatePollMs": 250,
            "partialFills": True,
            "actualFillInventory": True,
            "dreamFill": False,
            "runtimeR2TargetPaperWinnerPnlInput": False,
        },
        "fixedPolicyResults": fixed,
        "oracle": {
            "rows": oracle_rows,
            "terminalWorstCaseFloor": sum(float(row["floor"]) for row in oracle_rows),
            "settledRealizedPnlAudit": sum(float(row["pnlAudit"]) for row in oracle_rows),
            "actMarkets": sum(row["action"] != "WAIT" for row in oracle_rows),
            "waitMarkets": sum(row["action"] == "WAIT" for row in oracle_rows),
            "actRate": sum(row["action"] != "WAIT" for row in oracle_rows) / len(oracle_rows),
        },
        "rows": rows,
        "learnedPolicyRealizedValue": "N/A fixed-program replication",
        "decision": "KEEP_FOR_CONTEXTUAL_DATASET_PILOT" if keep_offsets else "REJECT_EXACT_CONTINGENT_PAIR_PROGRAM",
        "decisionReason": (
            f"Frozen fixed offsets cleared the preregistered replication gate: {keep_offsets}."
            if keep_offsets
            else "Neither frozen fixed offset had positive aggregate floor and PnL with zero violations and at least two nonnegative markets."
        ),
        "next": (
            "Generate a small chronological strict-past dataset with formal WAIT and the same full-cycle program; learn safe-floor reachability/ranking, not offset imitation."
            if keep_offsets
            else "Do not tune this program on revealed markets; return to the net Queue-Reactive world-model generator or a different economic action family."
        ),
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("fixedPolicyResults", "oracle", "decision", "decisionReason", "next")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
