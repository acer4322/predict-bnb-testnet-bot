from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
CONTRACT = BASE / "hft_net_qr_safe_floor_final25_postreveal_diagnostic_v1_contract.json"
DECISIONS = BASE / "hft_net_qr_safe_floor_final25_postreveal_diagnostic_v1_decisions.json"
ACTUAL = BASE / "hft_safe_floor_late_regime_blind25_v1_report.json"
REPORT = BASE / "hft_net_qr_safe_floor_final25_postreveal_diagnostic_v1_report.json"
EPS = 1e-9


def main() -> int:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    decisions = json.loads(DECISIONS.read_text(encoding="utf-8"))
    actual = json.loads(ACTUAL.read_text(encoding="utf-8"))
    if decisions.get("blindProtocolPhase") != "POST_REVEAL_FROZEN_RULE_DIAGNOSTIC":
        raise RuntimeError("diagnostic artifact is missing post-reveal marker")
    decision_by_market = {int(row["marketId"]): row for row in decisions["rows"]}
    actual_by_market = {int(row["marketId"]): row for row in actual["rows"]}
    expected = [int(value) for value in contract["cohort"]["blindRealTapeMarkets"]]
    if sorted(decision_by_market) != sorted(expected) or sorted(actual_by_market) != sorted(expected):
        raise RuntimeError("cohort mismatch")
    rows = []
    for market_id in expected:
        decision = decision_by_market[market_id]
        execution = actual_by_market[market_id]["actualExecution"]
        floor = float(execution["worstCaseFloor"])
        act = decision["lockedAction"] != "WAIT"
        rows.append(
            {
                "marketId": market_id,
                "frozenDiagnosticAction": decision["lockedAction"],
                "syntheticMeanTerminalFloor": float(decision["syntheticMeanTerminalFloor"]),
                "syntheticTailRate": float(decision["syntheticTailRate"]),
                "realTerminalFloor": floor,
                "realTail": floor < -EPS,
                "diagnosticPolicyValue": floor if act else 0.0,
            }
        )
    tails = [row for row in rows if row["realTail"]]
    positives = [row for row in rows if row["realTerminalFloor"] > EPS]
    acts = [row for row in rows if row["frozenDiagnosticAction"] != "WAIT"]
    waits = [row for row in rows if row["frozenDiagnosticAction"] == "WAIT"]
    caught_tails = [row for row in tails if row["frozenDiagnosticAction"] == "WAIT"]
    missed_tails = [row for row in tails if row["frozenDiagnosticAction"] != "WAIT"]
    positive_acts = [row for row in acts if row["realTerminalFloor"] > EPS]
    value = sum(float(row["diagnosticPolicyValue"]) for row in rows)
    always = sum(float(row["realTerminalFloor"]) for row in rows)
    oracle = sum(max(0.0, float(row["realTerminalFloor"])) for row in rows)
    summary = {
        "markets": len(rows),
        "actMarkets": len(acts),
        "waitMarkets": len(waits),
        "actRate": len(acts) / len(rows),
        "realTailMarkets": len(tails),
        "caughtTailMarkets": len(caught_tails),
        "missedTailMarkets": len(missed_tails),
        "tailRecall": len(caught_tails) / len(tails) if tails else None,
        "positiveActionPrecision": len(positive_acts) / len(acts) if acts else None,
        "frozenRuleTerminalFloor": value,
        "alwaysActTerminalFloor": always,
        "deltaVsAlwaysAct": value - always,
        "revealedOracleTerminalFloor": oracle,
        "oracleValueCapture": value / oracle if oracle > EPS else None,
    }
    report = {
        "version": "HFT_NET_QR_SAFE_FLOOR_FINAL25_POSTREVEAL_DIAGNOSTIC_V1_REPORT",
        "researchOnly": True,
        "postRevealDiagnostic": True,
        "performanceClaimAllowed": False,
        "contract": CONTRACT.name,
        "decisionArtifact": DECISIONS.name,
        "actualOutcomeArtifact": ACTUAL.name,
        "frozenRuleAudit": {
            "worldModelChangedAfterBlind5OrBlind20": False,
            "decisionThresholdSwept": False,
            "realOutcomeUsedByDecisionGeneration": False,
            "realOutcomeKnownBeforeThisComparison": True,
        },
        "summary": summary,
        "missedTailMarkets": [int(row["marketId"]) for row in missed_tails],
        "caughtTailMarkets": [int(row["marketId"]) for row in caught_tails],
        "rows": rows,
        "decision": "REJECT_CURRENT_NET_QR_GATE_MECHANISM",
        "decisionReason": "The frozen gate missed three of five real one-sided tails and its post-reveal diagnostic realized floor remained negative. The general state-conditioned L2 generator does not identify first-fill-conditioned opposite completion feasibility.",
        "next": "Build a phase-conditioned semi-Markov target that separately models first-leg fill side/time, opposite ask and queue state at confirmed fill, passive completion, cancel-to-ACK, bounded-Taker fill, and terminal floor. Do not tune the zero threshold or general QR generator on final25.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"report": str(REPORT), "summary": summary, "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
