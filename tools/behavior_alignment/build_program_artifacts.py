from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .capability_matrix import build_capability_matrix
from .diff_engine import compare_market
from .our_trace import load_our_trace
from .schema import trace_json_schema, validate_trace_document
from .target_trace import build_target_trace

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/research/r4_v0/behavior_alignment_v1"
TRACES = OUT / "traces"
EVIDENCE_ROOT = ROOT / "data/research/r4_v0/p0_provenance_v1"
TARGET_DB = EVIDENCE_ROOT / "target_eth_fill_legs_v3_snapshot_20260904.db"

OUR_SOURCES = {
    1916869: EVIDENCE_ROOT / "ETH_V83_SAME_PARENT_PARALLEL_REPAIR_HFT_SMOKE_1916869_RESULT_20260904.json",
    1917324: ROOT
    / "data/research/lan_worker_returns/eth-guarded-cycle-funnel-1917324-20260904-v1/result.json",
    1912961: ROOT
    / "data/research/lan_worker_returns/eth-v90d-minlegal-incremental-1912961-20260904-v1/result.json",
}


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rendered = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == rendered:
            return
        raise FileExistsError(
            f"refusing to overwrite concurrent/non-identical artifact: {path}"
        )
    path.write_text(rendered, encoding="utf-8")


def write_text(path: Path, rendered: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        current = path.read_text(encoding="utf-8")
        if current == rendered:
            return
        raise FileExistsError(
            f"refusing to overwrite concurrent/non-identical artifact: {path}"
        )
    path.write_text(rendered, encoding="utf-8")


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def capability_rows() -> list[dict[str, Any]]:
    regularities = "data/research/r4_v0/p0_provenance_v1/TARGET_SYSTEM_REGULARITIES_RESEARCH_HANDOFF_V1_20260903.md"
    contract = "data/research/r4_v0/p0_provenance_v1/MODULAR_CONTROLLER_RESEARCH_CONTRACT_V2_20260903.md"
    return [
        {
            "capability": "Persistent Responsibility",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#22.3", f"{regularities}#24.9"],
            "ourEvidence": [
                "ETH_V83_EXISTING_PARENT_RESPONSIBILITY_GENERATION_EPOCH_HFT_1916869_RESULT_20260904.json",
                "ETH_V83_SAME_PARENT_PARALLEL_REPAIR_HFT_SMOKE_1916869_RESULT_20260904.json",
            ],
            "ourImplementation": [
                "tools/eth_repair_modular/responsibility_generation_epoch.py",
                "tools/eth_repair_modular/responsibility_transition.py",
            ],
            "primaryModule": "Completion",
            "blocker": "One-market evidence preserves an existing parent, but persistence is not yet a uniform end-to-end contract across all terminal order states.",
            "nextMinimalTest": "Trace one order-terminal parent through a later confirmed payment without rebirth or debt loss.",
        },
        {
            "capability": "Partial Repair Payment",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#4.1", f"{regularities}#24.10"],
            "ourEvidence": [
                "data/research/lan_worker_returns/eth-v90d-minlegal-incremental-1912961-20260904-v1/result.json"
            ],
            "ourImplementation": ["V90D research runner only; no frozen reusable CarrierBudget policy yet"],
            "primaryModule": "Carrier Budget",
            "blocker": "V90D paid one legal tranche and preserved 0.52704 debt, but the smoke intentionally forbade a second payment.",
            "nextMinimalTest": "Move venue-min incremental tranche selection into CarrierBudget and keep Completion/Router frozen.",
        },
        {
            "capability": "Multi-payment Responsibility",
            "status": "BLOCKED_BY_REPAIR_EXECUTION_ROUTER",
            "targetEvidence": [
                "TARGET_ETH_RESPONSIBILITY_LEDGER_V3_ATOMIC_FIFO_20260904.json",
                f"{regularities}#22.3",
            ],
            "ourEvidence": [
                "V90D leaves explicit residual debt but deliberately prevents a second same-parent payment",
                "AllocationLedger V2 can allocate repeated cumulative fills without double-spend",
            ],
            "ourImplementation": [
                "tools/allocation_ledger_v2.py",
                "tools/eth_repair_modular/parallel_repair_execution_budget.py",
            ],
            "primaryModule": "RepairExecutionRouter",
            "blocker": "Accounting can carry residual debt, but no reusable continuation policy decides later partial payments for the same responsibility.",
            "nextMinimalTest": "Two-payment micro-world using one parent/shared ledger, then one-market V90D residual continuation shadow.",
        },
        {
            "capability": "Re-expand Before Debt Zero",
            "status": "BLOCKED_BY_EXPAND_ADMISSION",
            "targetEvidence": [f"{regularities}#24.2", f"{regularities}#24.6"],
            "ourEvidence": [
                "V70G generation rule unlocks the next generation only after prior debt discharge",
                "TARGET_ETH_REEXPAND_BEFORE_REPAIR_PROGRESS_SYNTHESIS_V1_20260904.json",
            ],
            "ourImplementation": ["legacy V70G generation ownership plus V83 Expand admission"],
            "primaryModule": "Expand Admission",
            "blocker": "Current admission/ownership semantics serialize re-expand behind debt clearance rather than prospective recoverability.",
            "nextMinimalTest": "Behavior-inert shadow of bounded re-expand with old debt retained and strict-past prospective Repair capacity.",
        },
        {
            "capability": "Recoverable Temporary Risk Spend",
            "status": "BLOCKED_BY_EXPAND_ADMISSION",
            "targetEvidence": [f"{regularities}#24.1", f"{regularities}#24.3"],
            "ourEvidence": [
                "Allocation-aware direct recoverability positive/negative controls discriminate physical-carrier floor geometry",
                "No integrated risk-capacity state exists",
            ],
            "ourImplementation": ["V83 whole-portfolio recoverability checks"],
            "primaryModule": "Expand Admission",
            "blocker": "Candidate actions are evaluated mostly as immediate floor geometry, not as a bounded risk-spend plus executable future Repair package.",
            "nextMinimalTest": "Offline strict-past prospective Repair-capacity feature audit without action authority.",
        },
        {
            "capability": "Composite Physical Carrier",
            "status": "IMPLEMENTED_CORRECT",
            "targetEvidence": [f"{regularities}#22.1", f"{regularities}#22.4"],
            "ourEvidence": [
                "V89E fresh4: 4/4 floor non-worse, safety/accounting zero",
                "AllocationLedger V2 corrected 1912961: physical conservation and overflow relay PASS",
            ],
            "ourImplementation": ["tools/allocation_ledger_v2.py", "tools/repair_execution_router_v2.py"],
            "primaryModule": "Carrier Budget",
            "blocker": None,
            "nextMinimalTest": "Regression-only in unified trace; do not re-research Target or tune carrier size.",
        },
        {
            "capability": "Repair-first / Overflow-second Allocation",
            "status": "IMPLEMENTED_CORRECT",
            "targetEvidence": [f"{regularities}#22.1", f"{regularities}#22.2"],
            "ourEvidence": [
                "V89A 13/13 micro-world PASS",
                "AllocationLedger V2 fresh evidence: all conservation and double-spend gates zero",
            ],
            "ourImplementation": ["tools/allocation_ledger_v2.py"],
            "primaryModule": "Allocation Ledger",
            "blocker": None,
            "nextMinimalTest": "Keep frozen as accounting authority in every new behavior test.",
        },
        {
            "capability": "Passive + Active Shared Responsibility Budget",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#6.1", f"{regularities}#7.2"],
            "ourEvidence": [
                "ETH_V83_SAME_PARENT_PARALLEL_REPAIR_HFT_SMOKE_1916869_RESULT_20260904.json: fills 3->5, round 0->1, clean accounting"
            ],
            "ourImplementation": [
                "tools/eth_repair_modular/parallel_repair_execution_budget.py",
                "tools/allocation_ledger_v2.py",
            ],
            "primaryModule": "RepairExecutionRouter",
            "blocker": "Positive functional support is one development market; shared budget is not yet a general RepairExecutionRouter policy.",
            "nextMinimalTest": "One fixed replication with the same frozen policy and actual Active fill requirement.",
        },
        {
            "capability": "Maker Continuation After Active",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#7.2"],
            "ourEvidence": [
                "1916869 candidate leaves passive live until confirmed reconciliation and allows one shared-budget Active child"
            ],
            "ourImplementation": ["tools/eth_repair_modular/parallel_repair_execution_budget.py"],
            "primaryModule": "Maker/Taker Execution Adapter",
            "blocker": "The one-market policy preserves passive occupancy, but generic post-Active continuation/reprice semantics are not centralized.",
            "nextMinimalTest": "Micro-world interleaving Active fill, stale passive sibling fill, and post-Active passive continuation.",
        },
        {
            "capability": "Event / Progress-driven Lifecycle Clock",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#9.1", f"{regularities}#10.1"],
            "ourEvidence": [
                "1917324 continuous scheduler: 1108 checks, 0 scheduler-origin admissions, behavior unchanged",
                "1916869 generation epoch rebases progress/churn evidence",
            ],
            "ourImplementation": [
                "tools/eth_repair_modular/scheduler.py",
                "tools/eth_repair_modular/scheduler_adapter.py",
                "tools/eth_repair_modular/responsibility_generation_epoch.py",
            ],
            "primaryModule": "ResponsibilityScheduler",
            "blocker": "Reevaluation density exists, but downstream admission remains event-token adapted and creates no additional valid physical action in 1917324.",
            "nextMinimalTest": "Use unified trace to localize dominant downstream blocker; do not relax threshold merely to create actions.",
        },
        {
            "capability": "Risk-capacity Replenishment",
            "status": "ABSENT",
            "targetEvidence": [f"{regularities}#24.3", f"{regularities}#24.4"],
            "ourEvidence": ["No authoritative riskCapacity/prospectiveRepairCapacity state shared by Repair and Expand"],
            "ourImplementation": [],
            "primaryModule": "Risk-Repair-Expand Coordinator",
            "blocker": "Repair progress updates debt/floor but is not converted into replenished bounded Expand capacity.",
            "nextMinimalTest": "Define a read-only coordinator state projection before any admission mutation.",
        },
        {
            "capability": "Probe -> Confirm -> Commit -> Expand",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#12.2"],
            "ourEvidence": ["Legacy safe-base/formation modules exist, but staged exposure is not integrated with current responsibility ledger"],
            "ourImplementation": ["legacy R3/R4 formation controls"],
            "primaryModule": "Expand Admission",
            "blocker": "Exposure staging and current Repair/Generation ownership use separate lifecycle representations.",
            "nextMinimalTest": "Trace-only mapping of existing probe/expand fills to responsibility generations.",
        },
        {
            "capability": "Late Existing-debt Repair Payment",
            "status": "BLOCKED_BY_REPAIR_EXECUTION_ROUTER",
            "targetEvidence": [
                "Target 1917324 post-market trace contains debt-paying Repair clocks inside <=180s",
                f"{regularities}#7.2",
            ],
            "ourEvidence": [
                "tools/repair_execution_router_v2.py unconditionally returns LATE_NO_NEW_ACTIVE_EXPOSURE for Repair contexts",
                "tools/eth_repair_modular/repair_execution.py applies the same unconditional late block in both legacy and recursive policies",
                "tools/eth_repair_modular/parallel_repair_execution_budget.py has the same unconditional late block",
            ],
            "ourImplementation": [
                "tools/repair_execution_router_v2.py",
                "tools/eth_repair_modular/repair_execution.py",
                "tools/eth_repair_modular/parallel_repair_execution_budget.py",
            ],
            "primaryModule": "RepairExecutionRouter",
            "blocker": "Router context cannot distinguish pure payment of existing debt (qty<=debt, overflow=0) from new speculative exposure.",
            "nextMinimalTest": "Shadow/micro-world semantic carve-out that keeps the <=180 speculative exposure fence unchanged.",
        },
        {
            "capability": "Active Expand Execution",
            "status": "ABSENT",
            "targetEvidence": [
                "TARGET_CROSS_TIMEFRAME_SYSTEM_ARCHITECTURE_SYNTHESIS_V21_20260903.json"
            ],
            "ourEvidence": ["Current architecture synthesis records old OUR Active Expand as structural zero"],
            "ourImplementation": [],
            "primaryModule": "Maker/Taker Execution Adapter",
            "blocker": "No bounded Active lane materializes an already-authorized Expand responsibility under a shared budget.",
            "nextMinimalTest": "After Repair lifecycle correctness, create behavior-inert Active-Expand reachability trace only.",
        },
        {
            "capability": "Route-specific Carrier Geometry",
            "status": "PARTIAL",
            "targetEvidence": [
                "TARGET_CROSS_TIMEFRAME_SYSTEM_ARCHITECTURE_SYNTHESIS_V21_20260903.json"
            ],
            "ourEvidence": [
                "V90D derives passive venue-min qty from bid",
                "Parallel Repair derives Active venue-min qty from ask",
            ],
            "ourImplementation": [
                "tools/repair_execution_router_v2.py",
                "tools/eth_repair_modular/parallel_repair_execution_budget.py",
            ],
            "primaryModule": "Maker/Taker Execution Adapter",
            "blocker": "Route geometry exists in isolated candidates, not one adapter contract separated from Manager debt.",
            "nextMinimalTest": "Common adapter micro-world for bid/ask venue minimums and shared parent allocation.",
        },
        {
            "capability": "Multi-round Risk -> Repair -> Re-expand Lifecycle",
            "status": "PARTIAL",
            "targetEvidence": [f"{regularities}#5.1", f"{regularities}#24.3"],
            "ourEvidence": [
                "1917324 reports 2 Repair-Expand-Repair rounds but 1108 checks produced no scheduler-origin admission",
                "1916869 same-parent repair increased semantic round 0->1",
                "V90D stops after one incremental payment by design",
            ],
            "ourImplementation": ["Multiple isolated modules; no integrated coordinator"],
            "primaryModule": "Risk-Repair-Expand Coordinator",
            "blocker": "The controller lacks one explicit state machine for debt, repair capacity, risk spend, and residual-debt re-expand.",
            "nextMinimalTest": "First complete development trace: Expand -> Repair -> residual debt -> bounded re-Expand -> later Repair.",
        },
    ]


def target_regularities_index() -> dict[str, Any]:
    return {
        "version": "KNOWN_TARGET_REGULARITIES_INDEX_V1",
        "date": "2026-09-04",
        "researchOnly": True,
        "runtimeAuthority": False,
        "rule": "These are existing post-market architecture references. Re-demonstration is replication, not discovery.",
        "regularities": [
            {"id": "T01", "capability": "partial Repair", "evidence": "Regularities 4.1 / 22.3 / V3 ledger"},
            {"id": "T02", "capability": "multi-payment responsibility", "evidence": "V3 ledger multi-payment completion"},
            {"id": "T03", "capability": "composite carrier Repair-first then overflow", "evidence": "Regularities 22.1-22.4"},
            {"id": "T04", "capability": "Passive and Active shared responsibility", "evidence": "Regularities 6.1 / 7.2"},
            {"id": "T05", "capability": "continuation after crossing Repair", "evidence": "Regularities 22.3: about 94% later parent"},
            {"id": "T06", "capability": "temporary floor spend", "evidence": "Regularities 24.1"},
            {"id": "T07", "capability": "re-expand before debt zero", "evidence": "Regularities 24.2 / 24.6"},
            {"id": "T08", "capability": "completed risk/recovery cycles", "evidence": "Regularities 24.3"},
            {"id": "T09", "capability": "event/progress lifecycle clock", "evidence": "Regularities 9.1 / 10.1"},
            {"id": "T10", "capability": "Active Expand", "evidence": "Cross-timeframe Synthesis V21"},
        ],
        "forbiddenRuntimeInputs": [
            "Target future action",
            "Target exact quantity",
            "Target exact delay",
            "Target fitted threshold",
            "winner or settlement",
        ],
    }


def rejected_hypotheses() -> dict[str, Any]:
    return {
        "version": "KNOWN_REJECTED_HYPOTHESES_V1",
        "date": "2026-09-04",
        "researchOnly": True,
        "hypotheses": [
            {"id": "R01", "hypothesis": "pure share balancing", "decision": "REJECT", "reason": "share balance is not responsibility completion"},
            {"id": "R02", "hypothesis": "full-debt immediate Repair", "decision": "REJECT", "reason": "V90C damaged favorable payoff; V90D partial tranche preserved upside"},
            {"id": "R03", "hypothesis": "full-payoff required for incremental Repair", "decision": "REJECT", "reason": "V90C proved the gate over-conservative"},
            {"id": "R04", "hypothesis": "Maker and Taker as separate strategy brains", "decision": "REJECT", "reason": "routes are actuators for one responsibility"},
            {"id": "R05", "hypothesis": "Active only after Passive terminal", "decision": "REJECT", "reason": "Target and 1916869 support shared-budget parallel execution"},
            {"id": "R06", "hypothesis": "fixed wall-clock as lifecycle authority", "decision": "REJECT", "reason": "event/progress semantics transfer better"},
            {"id": "R07", "hypothesis": "venue-min physical qty capped by share gap/debt", "decision": "REJECT", "reason": "Manager debt caps allocation, not carrier size"},
            {"id": "R08", "hypothesis": "round qty upward to make sub-min debt legal", "decision": "REJECT", "reason": "1916869 legal-min composite shadow had zero admissible rows"},
            {"id": "R09", "hypothesis": "fast passive repricing solves 1916869", "decision": "REJECT", "reason": "legal price arrived but observed maker fill evidence was much later"},
            {"id": "R10", "hypothesis": "increase scheduler checks to create HFT", "decision": "REJECT", "reason": "1917324: 1108 checks, zero scheduler-origin admissions, unchanged actions"},
            {"id": "R11", "hypothesis": "prior Repair paid fraction as universal re-expand gate", "decision": "REJECT", "reason": "Target 24.6 recovery varied only modestly by prior-progress bucket"},
            {"id": "R12", "hypothesis": "Vnext special-case inheritance", "decision": "FORBIDDEN", "reason": "Modular Controller Research Contract V2"},
        ],
    }


def imported_experiment_registry() -> dict[str, Any]:
    return {
        "version": "ALIGNMENT_EXPERIMENT_REGISTRY_V1",
        "date": "2026-09-04",
        "researchOnly": True,
        "entries": [
            {
                "id": "IMPORT-1916869-SHARED-PARENT",
                "capability": "Passive + Active Shared Responsibility Budget",
                "beforeStatus": "BLOCKED_BY_REPAIR_EXECUTION_ROUTER",
                "afterStatus": "PARTIAL",
                "primaryModule": "RepairExecutionRouter",
                "frozenModules": ["AllocationLedger V2", "ResponsibilityTransition", "V83 admission", "<=180 fence"],
                "markets": [1916869],
                "physicalEffect": "fills 3->5; confirmed Active Repair; round 0->1",
                "accountingResult": "all fixed safety/accounting zero; conservation PASS",
                "economicResult": "floor non-worse; PnL diagnostic only",
                "decision": "KEEP_FUNCTIONAL_FOR_ONE_REPLICATION",
                "nextSmallestFalsification": "fixed one-market replication",
            },
            {
                "id": "IMPORT-1912961-V90D",
                "capability": "Partial Repair Payment",
                "beforeStatus": "BLOCKED_BY_CARRIER_BUDGET",
                "afterStatus": "PARTIAL",
                "primaryModule": "Carrier Budget",
                "frozenModules": ["V90A relay", "AllocationLedger", "Expand admission", "<=180 fence"],
                "markets": [1912961],
                "physicalEffect": "1.61290 actual Repair fill; 0.52704 residual debt preserved",
                "accountingResult": "all fixed safety/accounting zero",
                "economicResult": "terminal floor -0.982->-0.369; PnL +0.158 vs V90C -0.169",
                "decision": "KEEP_V90D_MIN_LEGAL_INCREMENTAL_FOR_FRESH_REPLICATION",
                "nextSmallestFalsification": "same-responsibility second payment without debt erasure",
            },
            {
                "id": "IMPORT-1917324-CONTINUOUS-SCHEDULER",
                "capability": "Event / Progress-driven Lifecycle Clock",
                "beforeStatus": "BLOCKED_BY_RESPONSIBILITY_SCHEDULER",
                "afterStatus": "PARTIAL",
                "primaryModule": "ResponsibilityScheduler",
                "frozenModules": ["V83 admission", "Generation", "quantity", "price", "execution"],
                "markets": [1917324],
                "physicalEffect": "none; fills/submits/rounds unchanged",
                "accountingResult": "all fixed safety/accounting zero",
                "economicResult": "unchanged; not a promotion claim",
                "decision": "KEEP_CLOCK_MODULE_BUT_ACTION_SUPPORT_INCONCLUSIVE",
                "nextSmallestFalsification": "localize downstream blocker; no threshold relaxation",
            },
        ],
    }


def build() -> dict[str, Any]:
    OUT.mkdir(parents=True, exist_ok=True)
    TRACES.mkdir(parents=True, exist_ok=True)
    matrix = build_capability_matrix(capability_rows(), evidence_cutoff="2026-09-04")
    write_json(OUT / "BEHAVIORAL_ALIGNMENT_CAPABILITY_MATRIX_V1.json", matrix)
    write_json(OUT / "OUR_TARGET_SEMANTIC_TRACE_SCHEMA_V1.json", trace_json_schema())
    write_json(OUT / "KNOWN_TARGET_REGULARITIES_INDEX_V1.json", target_regularities_index())
    write_json(OUT / "KNOWN_REJECTED_HYPOTHESES_V1.json", rejected_hypotheses())
    write_json(OUT / "ALIGNMENT_EXPERIMENT_REGISTRY_V1.json", imported_experiment_registry())

    trace_index: list[dict[str, Any]] = []
    diffs: dict[int, dict[str, Any]] = {}
    for market_id, our_source in OUR_SOURCES.items():
        target = build_target_trace(TARGET_DB, market_id)
        ours = load_our_trace(our_source)
        for source_name, document in (("TARGET", target), ("OUR", ours)):
            errors = validate_trace_document(document)
            if errors:
                raise ValueError(f"{source_name} {market_id} trace invalid: {errors[:10]}")
            path = TRACES / f"{source_name}_{market_id}_SEMANTIC_TRACE_V1.json"
            write_json(path, document)
            trace_index.append(
                {
                    "marketId": market_id,
                    "source": source_name,
                    "path": rel(path),
                    "events": len(document["events"]),
                    "coverage": document["coverage"],
                }
            )
        diff = compare_market(ours, target)
        diffs[market_id] = diff
        write_json(TRACES / f"OUR_TARGET_{market_id}_BEHAVIORAL_DIFF_V1.json", diff)

    gap_rows = [row for row in matrix["rows"] if row["status"] != "IMPLEMENTED_CORRECT"]
    selected = next(row for row in gap_rows if row["capability"] == "Late Existing-debt Repair Payment")
    late_target_rows = [
        event
        for event in build_target_trace(TARGET_DB, 1917324)["events"]
        if event["eventType"] == "REPAIR_PAYMENT" and float(event.get("secondsLeft") or 999) <= 180.0
    ]
    gap_map = {
        "version": "CURRENT_OUR_MODULE_GAP_MAP_V1",
        "date": "2026-09-04",
        "researchOnly": True,
        "runtimeAuthority": False,
        "moduleGaps": gap_rows,
        "selectedFirstGap": {
            "capability": selected["capability"],
            "status": selected["status"],
            "primaryModule": selected["primaryModule"],
            "whyFirst": [
                "single primary module and static code-level blocker",
                "high leverage for late responsibility materialization",
                "can be falsified in micro-world before HFT",
                "does not require threshold, qty, price, or delay tuning",
            ],
            "offlineEvidence": {
                "target1917324LateRepairPaymentEvents": len(late_target_rows),
                "target1917324LateRepairQty": sum(
                    float(event["allocation"]["repairAllocation"]) for event in late_target_rows
                ),
                "ourStaticBlockers": [
                    "tools/repair_execution_router_v2.py: seconds_left<=180 -> LATE_NO_NEW_ACTIVE_EXPOSURE",
                    "tools/eth_repair_modular/repair_execution.py: both Repair policies apply the same unconditional late block",
                    "tools/eth_repair_modular/parallel_repair_execution_budget.py: same unconditional late block",
                ],
                "missingRouterState": [
                    "candidateRepairAllocation",
                    "candidateOverflow",
                    "existingDebtPaymentOnly",
                    "economicRepairPriceLegal",
                    "projectedDownsideReduction",
                ],
            },
            "dedupBoundary": "This does not rediscover Target late Repair. It tests whether OUR conflates existing-debt payment with new exposure at the Router seam.",
            "nextMinimalTest": selected["nextMinimalTest"],
        },
        "traceIndex": trace_index,
    }
    write_json(OUT / "CURRENT_OUR_MODULE_GAP_MAP_V1.json", gap_map)

    handoff = f"""# OUR x Target Behavioral Alignment — Current Handoff V1

Date: 2026-09-04

## Outcome

Phase 0 capability registry and Phase 1 unified semantic trace foundation are implemented. No strategy, live runtime, threshold, quantity, price, delay, scheduler, service, or 8781 behavior changed.

## Generated evidence

- Capability matrix: `BEHAVIORAL_ALIGNMENT_CAPABILITY_MATRIX_V1.json`
- Trace schema: `OUR_TARGET_SEMANTIC_TRACE_SCHEMA_V1.json`
- Known Target regularities: `KNOWN_TARGET_REGULARITIES_INDEX_V1.json`
- Rejected/deduplicated hypotheses: `KNOWN_REJECTED_HYPOTHESES_V1.json`
- Current module gaps: `CURRENT_OUR_MODULE_GAP_MAP_V1.json`
- Imported experiment registry: `ALIGNMENT_EXPERIMENT_REGISTRY_V1.json`
- Development traces and semantic diffs: `traces/`

## Development-case coverage

- `1916869`: OUR authoritative Router/allocation subset + compact summary; Target fill-complete post-market FIFO reconstruction.
- `1917324`: OUR 1,108 authoritative admission clocks + summary physical metrics; Target fill-complete post-market FIFO reconstruction.
- `1912961`: OUR V90D admission/submit authoritative clocks + confirmed-fill compact summary; Target fill-complete post-market FIFO reconstruction.

Coverage gaps are explicit. A missing OUR fill clock is not represented as a factual zero.

## First structural gap selected

`Late Existing-debt Repair Payment` -> `BLOCKED_BY_REPAIR_EXECUTION_ROUTER`.

The current Repair routers apply `seconds_left <= 180` as an unconditional `LATE_NO_NEW_ACTIVE_EXPOSURE` block even though their context is an existing Repair parent. They cannot represent the frozen-contract distinction:

```text
existing debt payment, qty <= debt, overflow = 0
!=
new speculative exposure
```

Existing Target trace for `1917324` contains {len(late_target_rows)} reconstructed debt-paying Repair events inside the final 180 seconds, totaling {sum(float(event['allocation']['repairAllocation']) for event in late_target_rows):.8f} shares. This is post-market reference only, never runtime authority.

## Dedup boundary

Do not re-research whether Target repairs late. The next test is solely whether OUR's Router context and late decision semantics incorrectly conflate pure existing-debt payment with speculative overflow.

## Only next behavior-research step

Create a pure `RepairExecutionRouter` micro-world with the late speculative fence frozen:

1. allow candidate classification only when authoritative existing debt is positive;
2. candidate Repair allocation is `<=` that debt;
3. confirmed/predicted overflow is exactly zero;
4. route-specific physical quantity is venue legal;
5. Repair price is inside the inherited economic envelope;
6. action reduces existing downside and births no responsibility;
7. reject the same carrier if any overflow/new exposure would occur;
8. test stale sibling fill and shared-budget double-spend cases.

Do not run realistic-HFT until the micro-world passes. Then preregister one fixed development market shadow before any behavior mutation.
"""
    write_text(OUT / "ALIGNMENT_CURRENT_HANDOFF_V1.md", handoff)
    return {
        "outputRoot": rel(OUT),
        "matrixSummary": matrix["summary"],
        "traces": trace_index,
        "selectedFirstGap": gap_map["selectedFirstGap"],
        "diffGapCounts": {market_id: len(diff["gaps"]) for market_id, diff in diffs.items()},
    }


if __name__ == "__main__":
    print(json.dumps(build(), indent=2, ensure_ascii=False))
