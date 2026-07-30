from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from predict_bot.professionalization import (  # noqa: E402
    FROZEN_COMPOSITES,
    ProfessionalizationLedger,
    advantage_fingerprints,
    blocked_counterfactual_records,
    calibration_report,
    conservative_calibrated_value_shadow,
    data_coverage_audit,
    edge_realization_waterfall,
    execution_stress_records,
    platform_risk_registry,
    quote_edge_stress,
    recorded_horizon_markouts,
    selected_composite_trades,
    select_development_exit_candidate,
    sha256_file,
    stable_hash,
    strategy_local_sizing_research,
    summarize_execution_stress,
    summarize_markouts,
    trade_probability_records,
    utc_iso,
)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def calibration_by_composite(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for definition in FROZEN_COMPOSITES:
        if definition.strategy != "R_CALIBRATED_VALUE":
            unavailable = calibration_report([])
            unavailable["reason"] = (
                "strategy emits a directional score, not a recoverable selected-side probability"
            )
            result[definition.composite_id] = {
                "overall": unavailable,
                "splits": {
                    split: unavailable.copy()
                    for split in ("development", "validation", "holdout")
                },
            }
            continue
        values = [
            record for record in records if record["strategy"] == definition.strategy
        ]
        result[definition.composite_id] = {
            "overall": calibration_report(values),
            "splits": {
                split: calibration_report(
                    [record for record in values if record["split"] == split]
                )
                for split in ("development", "validation", "holdout")
            },
        }
    return result


def selected_trade_summary(trades: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for definition in FROZEN_COMPOSITES:
        values = [trade for trade in trades if trade["strategy"] == definition.strategy]
        stake = sum(float(trade["stake"]) for trade in values)
        result[definition.composite_id] = {
            "trades": len(values),
            "wins": sum(float(trade["pnl"]) > 0 for trade in values),
            "losses": sum(float(trade["pnl"]) < 0 for trade in values),
            "realizedPnl": sum(float(trade["pnl"]) for trade in values),
            "filledCost": stake + sum(float(trade["fee"]) for trade in values),
            "roiOnStake": (
                sum(float(trade["pnl"]) for trade in values) / stake if stake else None
            ),
            "splits": dict(Counter(str(trade["split"]) for trade in values)),
        }
    return result


def summarize_shadow(shadow: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in shadow.items() if key != "decisions"}


def opportunity_leakage(
    selected: list[dict[str, Any]],
    counterfactual_summary: dict[str, Any],
    execution_summary: dict[str, Any],
) -> dict[str, Any]:
    selected_summary = selected_trade_summary(selected)
    result: dict[str, Any] = {}
    for definition in FROZEN_COMPOSITES:
        composite_id = definition.composite_id
        stress = execution_summary.get(composite_id, {})
        result[composite_id] = {
            "selectedOpportunity": selected_summary[composite_id],
            "marginalBlockedCounterfactualBySplit": counterfactual_summary.get(
                composite_id, {}
            ),
            "executionRetentionBySplit": {
                split: {
                    scenario: {
                        "executionRate": values["executionRate"],
                        "averageFillRatio": values["averageFillRatio"],
                        "realizedPnl": values["realizedPnl"],
                    }
                    for scenario, values in scenarios.items()
                }
                for split, scenarios in stress.items()
            },
            "observerVersionFrozen": definition.observer_version,
            "status": "DESCRIPTIVE_AUDIT_ONLY_NOT_A_NEW_GATE",
        }
    return result


def format_number(value: Any, digits: int = 4) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        return f"{value:,.{digits}f}"
    return str(value)


def render_markdown(report: dict[str, Any]) -> str:
    coverage = report["part1"]["dataCoverage"]
    selected = report["part1"]["selectedCompositePerformance"]
    calibration = report["part1"]["probabilityCalibration"]
    part2 = report["part2"]
    lines = [
        "# Professionalization V1 execution report",
        "",
        f"- Experiment: `{report['experimentId']}`",
        f"- Deterministic core hash: `{report['reportCoreSha256']}`",
        f"- Promotion status: `{report['governance']['promotionStatus']}`",
        "- Live activation: **not performed**",
        "- Observer versions: frozen; no ranking, migration, or replacement performed",
        "",
        "## Part 1 - evidence foundation",
        "",
        f"Historical coverage is **{format_number(coverage['observedDays'], 2)} days** "
        f"across **{format_number(coverage['distinctMarkets'])} markets**. Status: "
        f"`{coverage['status']}` (30-day minimum, 90-day preferred).",
        "",
        "| Frozen composite | Trades | Wins | Losses | PnL | ROI on stake |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for composite_id, values in selected.items():
        lines.append(
            f"| `{composite_id}` | {values['trades']} | {values['wins']} | "
            f"{values['losses']} | {format_number(values['realizedPnl'])} | "
            f"{format_number(values['roiOnStake'])} |"
        )
    lines.extend([
        "",
        "Calibration availability:",
        "",
    ])
    for composite_id, values in calibration.items():
        overall = values["overall"]
        if overall["status"] == "READY":
            lines.append(
                f"- `{composite_id}`: {overall['samples']} forecasts, "
                f"Brier {format_number(overall['brierScore'])}, log loss "
                f"{format_number(overall['logLoss'])}, ECE "
                f"{format_number(overall['expectedCalibrationError'])}."
            )
        else:
            lines.append(
                f"- `{composite_id}`: `UNAVAILABLE` — {overall['reason']}."
            )
    lines.extend([
        "",
        "The research ledger stores immutable experiment hashes, forecasts, execution "
        "stress rows, fixed-horizon executable markouts, blocked-signal counterfactuals, "
        "and platform-risk entries. Source market data was opened read-only.",
        "",
        "Capital sizing comparisons are strategy-local research only; no account-wide "
        "daily/weekly loss engine was added and no sizing rule was selected for live use.",
        "",
        "## Part 2 - defect remediation research",
        "",
        f"- Calibrated Value conservative gate: `{part2['calibratedValueConservativeGate']['status']}`.",
        f"- Futures Lead exit candidate: `{part2['futuresLeadExitCandidate']['status']}`; "
        f"selected horizon = {format_number(part2['futuresLeadExitCandidate']['selectedHorizonSeconds'])} seconds.",
        "- Quote-edge stress is reported at 0/50/100/200/500 additional bps and remains a shadow counterfactual.",
        "- Microprice Observer V6, DD20, and causal two-loss/skip-one semantics were preserved.",
        "- Exit candidates and probability gates were not forwarded to live execution.",
        "",
        "## Part 3 - amplify advantages",
        "",
        "Development-frozen advantage fingerprints, the edge-realization waterfall, "
        "and composite-specific opportunity/leakage reports were generated. They are "
        "descriptive and never collapse the three Observer versions into one global score.",
        "",
        "Blocked attribution includes Observer, drawdown, and cooldown where the replay "
        "supports causal matching. Price/execution rejection and overlapping blockers are "
        "explicitly marked unavailable until an all-candidate forward ledger records them.",
        "",
        "## Acceptance decision",
        "",
        f"Overall status: `{report['acceptance']['status']}`.",
        "",
    ])
    for reason in report["acceptance"]["reasons"]:
        lines.append(f"- {reason}")
    lines.extend([
        "",
        "This report is suitable for branch review. It is not approval to merge or activate "
        "live behavior; each remains a separate user decision.",
        "",
    ])
    return "\n".join(lines)


def build_report(replay_report: Path, simulation_db: Path, output_dir: Path) -> Path:
    source_report = load_json(replay_report)
    fixed_end = str(source_report["window"]["endUtc"])
    selected = selected_composite_trades(source_report)
    probability_records = trade_probability_records(selected)
    execution_records = execution_stress_records(selected)
    markout_records = recorded_horizon_markouts(
        simulation_db, selected, end_at=fixed_end
    )
    counterfactual_records, counterfactual_summary = blocked_counterfactual_records(
        source_report
    )
    platform_risks = platform_risk_registry()

    replay_hash = sha256_file(replay_report)
    coverage = data_coverage_audit(simulation_db, end_at=fixed_end)
    simulation_semantic_hash = stable_hash({
        "fixedEndInclusive": fixed_end,
        "coverage": coverage,
        "horizonMarkouts": markout_records,
    })
    dataset_hash = stable_hash({
        "replayReportSha256": replay_hash,
        "simulationSemanticSha256": simulation_semantic_hash,
    })
    config = {
        "schemaVersion": "professionalization-v1",
        "implementationHashes": {
            "professionalizationModuleSha256": sha256_file(
                ROOT / "src" / "predict_bot" / "professionalization.py"
            ),
            "orchestratorSha256": sha256_file(Path(__file__).resolve()),
        },
        "sourceReplaySchemaVersion": source_report.get("schemaVersion"),
        "horizonsSeconds": [3, 10, 30, 60, 120],
        "stressSlippageBps": [50, 100, 200],
        "stressDepthHaircuts": [1.0, 0.5, 0.25],
        "selectionPolicy": "development-only; later splits audit-only",
        "priorHoldoutReused": True,
        "fixedEndInclusive": fixed_end,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = output_dir / "professionalization-v1-ledger.sqlite3"
    with ProfessionalizationLedger(ledger_path) as ledger:
        experiment_id = ledger.register_experiment(
            dataset_sha256=dataset_hash,
            config=config,
            prior_holdout_reused=True,
        )
        ledger.insert_forecasts(experiment_id, probability_records)
        ledger.insert_execution_audits(experiment_id, execution_records)
        ledger.insert_markouts(experiment_id, markout_records)
        ledger.insert_counterfactuals(experiment_id, counterfactual_records)
        ledger.insert_platform_risks(experiment_id, platform_risks)
        ledger_counts = ledger.counts(experiment_id)

    calibration = calibration_by_composite(probability_records)
    execution_summary = summarize_execution_stress(execution_records)
    markout_summary = summarize_markouts(markout_records, selected)
    conservative_shadow = conservative_calibrated_value_shadow(probability_records)
    fingerprints = advantage_fingerprints(selected)
    source_selected_without_cooldown = sum(
        1
        for trade in source_report.get("trades", [])
        if any(
            trade.get("strategy") == definition.strategy
            and trade.get("observer_version") == definition.replay_version
            for definition in FROZEN_COMPOSITES
        )
    )
    cooldown_blocked = [
        record
        for record in counterfactual_records
        if record["blocker"] == "TWO_LOSS_COOLDOWN"
    ]
    exit_candidates = {
        definition.composite_id: select_development_exit_candidate(
            markout_summary, definition.composite_id
        )
        for definition in FROZEN_COMPOSITES
    }
    core = {
        "schemaVersion": "professionalization-v1",
        "experimentId": experiment_id,
        "sourceHashes": {
            "replayReportSha256": replay_hash,
            "simulationSemanticSha256": simulation_semantic_hash,
            "datasetSha256": dataset_hash,
            "activeSourceFileHashExcluded": (
                "simulation.db is append-only and active; identity uses a fixed-end "
                "semantic hash of all consumed aggregates and markouts"
            ),
        },
        "fixedComposites": [asdict(value) for value in FROZEN_COMPOSITES],
        "governance": {
            "selectionSplit": "development",
            "validationAndHoldoutUse": "AUDIT_ONLY_REUSED",
            "promotionStatus": "RESEARCH_ONLY_NEEDS_NEW_FORWARD",
            "observerVersionsFrozen": True,
            "globalPortfolioRiskEngineAdded": False,
            "liveActivationPerformed": False,
        },
        "part1": {
            "dataCoverage": coverage,
            "selectedCompositePerformance": selected_trade_summary(selected),
            "probabilityCalibration": calibration,
            "executionStress": execution_summary,
            "horizonMarkouts": markout_summary,
            "strategyLocalSizingResearch": strategy_local_sizing_research(selected),
            "platformRiskRegistry": platform_risks,
            "ledgerCounts": ledger_counts,
            "cohortAccounting": {
                "selectedBeforeMicropriceCooldown": source_selected_without_cooldown,
                "selectedAfterMicropriceCooldown": len(selected),
                "micropriceCooldownBlocked": len(cooldown_blocked),
            },
        },
        "part2": {
            "calibratedValueConservativeGate": summarize_shadow(conservative_shadow),
            "futuresLeadExitCandidate": exit_candidates["R_FUTURES_LEAD+V2"],
            "allCompositeExitCandidates": exit_candidates,
            "quoteEdgeStress": quote_edge_stress(probability_records),
            "micropriceCooldownCounterfactual": {
                "blocked": len(cooldown_blocked),
                "counterfactualWins": sum(
                    record["counterfactual_result"] == "WIN"
                    for record in cooldown_blocked
                ),
                "counterfactualLosses": sum(
                    record["counterfactual_result"] == "LOSS"
                    for record in cooldown_blocked
                ),
                "counterfactualPnl": sum(
                    record["counterfactual_pnl"] for record in cooldown_blocked
                ),
                "semantics": "causal two losses then skip one Microprice candidate",
            },
            "liveForwarding": "NONE",
        },
        "part3": {
            "advantageFingerprints": fingerprints,
            "edgeRealizationWaterfall": edge_realization_waterfall(selected),
            "blockedSignalCounterfactuals": counterfactual_summary,
            "opportunityAndLeakage": opportunity_leakage(
                selected, counterfactual_summary, execution_summary
            ),
            "dataFlywheel": {
                "ledgerPathRole": "separate appendable research ledger",
                "experimentIdStableForSameDatasetAndConfig": True,
                "newForwardDataRequirement": (
                    "append with a new dataset hash; never relabel reused holdout as fresh"
                ),
            },
        },
    }
    acceptance_reasons = []
    if coverage["status"] != "READY_LONG_HORIZON":
        acceptance_reasons.append(
            "Only %.2f recorded days are available; 30-90 day robustness proof is not met."
            % coverage["observedDays"]
        )
    acceptance_reasons.extend([
        "Validation and holdout were previously inspected and are correctly labeled reused audit data.",
        "All proposed gates, exits, and sizing comparisons remain research-only.",
        "A new untouched forward cohort is required before any promotion decision.",
    ])
    core["acceptance"] = {
        "status": "BRANCH_IMPLEMENTATION_COMPLETE_LIVE_PROMOTION_NOT_APPROVED",
        "reasons": acceptance_reasons,
    }
    report = {
        **core,
        "reportCoreSha256": stable_hash(core),
        "generatedAt": utc_iso(),
        "sourcePaths": {
            "replayReport": str(replay_report.resolve()),
            "simulationDb": str(simulation_db.resolve()),
        },
        "artifactPaths": {
            "ledger": str(ledger_path.resolve()),
        },
    }
    json_path = output_dir / f"professionalization-v1-{experiment_id}.json"
    markdown_path = output_dir / f"professionalization-v1-{experiment_id}.md"
    json_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    markdown_path.write_text(render_markdown(report), encoding="utf-8")
    print(json.dumps({
        "experimentId": experiment_id,
        "reportCoreSha256": report["reportCoreSha256"],
        "jsonReport": str(json_path.resolve()),
        "markdownReport": str(markdown_path.resolve()),
        "ledger": str(ledger_path.resolve()),
        "selectedTrades": len(selected),
        "status": report["acceptance"]["status"],
    }, ensure_ascii=False, indent=2))
    return json_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the isolated Professionalization V1 research plan."
    )
    parser.add_argument("--replay-report", required=True, type=Path)
    parser.add_argument("--simulation-db", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "professionalization-v1",
    )
    return parser.parse_args()


if __name__ == "__main__":
    arguments = parse_args()
    build_report(
        arguments.replay_report,
        arguments.simulation_db,
        arguments.output_dir,
    )
