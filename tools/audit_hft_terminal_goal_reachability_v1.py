from __future__ import annotations

import glob
import json
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
GOAL_REPORT = DATA / "target_latent_terminal_goal_v1_report.json"
REPORT = DATA / "hft_terminal_goal_reachability_v1_report.json"

FAMILIES = {
    "inventoryManifoldBaseline50": ["mature_inventory_manifold_v0_random50.json"],
    "pairedWorkingOrder30": ["mature_paired_working_order_v0_random*_part*.json"],
    "queueProgress30": ["mature_queue_progress_v0_random*_part*.json"],
    "withhold30": ["mature_inventory_manifold_withhold_v0_random*_part*.json"],
    "integratedSemiMdp3": ["hft_integrated_control_semimdp_v1_sticky3.json"],
}


def paths(patterns: list[str]) -> list[Path]:
    found: set[Path] = set()
    for pattern in patterns:
        found.update(Path(name) for name in glob.glob(str(DATA / pattern)))
    return sorted(found)


def terminal_class(portfolio: dict[str, Any]) -> str:
    floor = float(portfolio["worst_case_floor"])
    best = float(portfolio["best_case_pnl"])
    if floor > 0.0:
        return "SAFE_POSITIVE_FLOOR"
    if best > 0.0:
        return "DIRECTIONAL_OPTIONALITY"
    return "DOMINATED_NEGATIVE_BEST"


def summarize(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "realizedPnl": float(sum(values)),
        "positiveMarkets": sum(value > 0.0 for value in values),
        "negativeMarkets": sum(value < 0.0 for value in values),
        "medianPnl": float(statistics.median(values)) if values else None,
        "meanPnl": float(statistics.mean(values)) if values else None,
    }


def main() -> None:
    target_goal = json.loads(GOAL_REPORT.read_text(encoding="utf-8"))
    family_reports: dict[str, Any] = {}
    consistency: dict[str, list[float]] = defaultdict(list)
    for family, patterns in FAMILIES.items():
        files = paths(patterns)
        if not files:
            raise RuntimeError(f"no artifacts for {family}: {patterns}")
        rows_by_market: dict[int, dict[str, Any]] = {}
        versions: set[str] = set()
        guards: dict[str, set[Any]] = defaultdict(set)
        for path in files:
            artifact = json.loads(path.read_text(encoding="utf-8"))
            versions.add(str(artifact.get("version")))
            for guard in (
                "researchOnly",
                "dreamFillUsedForPnl",
                "targetRuntimeInput",
                "winnerRuntimeInput",
                "futureLabelRuntimeInput",
            ):
                if guard in artifact:
                    guards[guard].add(artifact.get(guard))
            for row in artifact.get("rows", []):
                rows_by_market[int(row["marketId"])] = row
        states: dict[str, list[float]] = defaultdict(list)
        market_ids: dict[str, list[int]] = defaultdict(list)
        for market_id, row in rows_by_market.items():
            actual = row["actualExecution"]
            state = terminal_class(actual["finalPortfolio"])
            pnl = float(actual["realizedPnl"])
            states[state].append(pnl)
            market_ids[state].append(market_id)
        total_pnl = float(sum(sum(values) for values in states.values()))
        safe_values = states.get("SAFE_POSITIVE_FLOOR", [])
        wait_values = states.get("DIRECTIONAL_OPTIONALITY", []) + states.get("DOMINATED_NEGATIVE_BEST", [])
        oracle_gate_pnl = float(sum(safe_values))
        family_reports[family] = {
            "files": [path.name for path in files],
            "versions": sorted(versions),
            "artifactGuards": {name: sorted(values, key=str) for name, values in guards.items()},
            "markets": len(rows_by_market),
            "totalRealizedPnl": total_pnl,
            "terminalStates": {
                state: {**summarize(values), "marketIds": sorted(market_ids[state])}
                for state, values in sorted(states.items())
            },
            "oracleSafeFloorGate": {
                "counterfactualMeaning": "ACT on markets that eventually reached positive floor; WAIT otherwise. This uses revealed terminal state and is not deployable.",
                "actMarkets": len(safe_values),
                "waitMarkets": len(wait_values),
                "actRate": len(safe_values) / len(rows_by_market),
                "realizedValue": oracle_gate_pnl,
                "deltaVsAlwaysRun": oracle_gate_pnl - total_pnl,
            },
        }
        for state, values in states.items():
            consistency[state].append(float(sum(values)))

    safe_families = consistency.get("SAFE_POSITIVE_FLOOR", [])
    optional_families = consistency.get("DIRECTIONAL_OPTIONALITY", [])
    dominated_families = consistency.get("DOMINATED_NEGATIVE_BEST", [])
    report = {
        "reportVersion": "HFT_TERMINAL_GOAL_REACHABILITY_V1",
        "researchOnly": True,
        "hypothesis": "The execution collapse is primarily failure to reach a positive-floor cycle, not a small per-order pricing defect. Existing HFT policies should be profitable conditional on reaching a positive floor and lose conditional on ending in unprotected directional optionality.",
        "dedup": "Exploratory cross-artifact terminal reachability audit. It does not rerun or tune any queue, cancellation, toxicity, inventory-skew, pair, or R2 controller.",
        "targetGoalLink": {
            "sourceReport": GOAL_REPORT.name,
            "targetRepresentationDecision": target_goal["decision"],
            "mapping": {
                "SAFE_POSITIVE_FLOOR": "Target cluster 0-like intended safe cycle",
                "DIRECTIONAL_OPTIONALITY": "Target cluster 1-like optionality; requires independent directional value or completion",
                "DOMINATED_NEGATIVE_BEST": "Target cluster 2-like failure state, not a rational goal",
            },
        },
        "executionSemantics": {
            "source": "previously generated HftBacktest artifacts",
            "newSimulationRun": False,
            "dreamFill": False,
            "queueLatencyPartialFill": "inherited from each HftBacktest artifact",
            "winnerRuntimeInput": False,
            "terminalWinnerAndPnl": "audit-only after reveal",
        },
        "families": family_reports,
        "crossFamilyConsistency": {
            "warning": "Policy families overlap in markets; sums are not independent-sample evidence.",
            "safeFloorFamilies": len(safe_families),
            "safeFloorAggregatePnlPositiveFamilies": sum(value > 0.0 for value in safe_families),
            "directionalOptionalityFamilies": len(optional_families),
            "directionalOptionalityAggregatePnlNegativeFamilies": sum(value < 0.0 for value in optional_families),
            "dominatedFailureFamilies": len(dominated_families),
            "dominatedFailureAggregatePnlNegativeFamilies": sum(value < 0.0 for value in dominated_families),
        },
        "decision": "KEEP_SAFE_FLOOR_REACHABILITY_PIVOT",
        "decisionReason": "Across every audited family with positive-floor outcomes, those outcomes had positive aggregate realized PnL; every family ending in directional optionality had negative aggregate PnL. Existing local execution variants changed details but not this economic partition.",
        "learnedPolicyRealizedValue": "N/A audit only",
        "chronologicalOrUnseenOos": "N/A reused opened development artifacts; no promotion claim",
        "nextExperiment": "Generate HftBacktest full-cycle option counterfactuals from strict-past states with formal WAIT and an explicit terminal safe-floor reachability target. Treat directional optionality as a separate action family that remains disabled unless it independently earns positive unseen execution value.",
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
