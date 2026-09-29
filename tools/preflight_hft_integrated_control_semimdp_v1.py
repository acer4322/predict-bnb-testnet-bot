from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_quality_v1 import assess_archive  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
OFFICIAL = ROOT / "data" / "hft_forward_paper_v1" / "markets"
CONTRACT = BASE / "hft_integrated_control_semimdp_v1_contract.json"
REFERENCE = ROOT / "tools" / "hftbacktest_r2_online_target_ledger_pair_completion_v10_objective_token_adapter.py"
OUTPUT = BASE / "hft_integrated_control_semimdp_v1_preflight.json"


REQUIRED_LIFECYCLE_TOKENS = [
    "ACKED_OPEN",
    "PARTIAL",
    "CANCEL_REQUESTED",
    "TERMINAL_ZERO_FILL",
    "RETURN_TO_CONTROLLER_UNRESOLVED",
    "PARTIAL_CHILD_REMAINDER",
    "actual.apply",
    "taker_fee",
]


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    market_ids = [int(value) for value in contract["smallPilot"]["markets"]]
    official_ids: set[int] = set()
    if OFFICIAL.exists():
        for path in OFFICIAL.glob("*_hft_closed_loop_v1.json.xz"):
            try:
                official_ids.add(int(path.name.split("_", 1)[0]))
            except Exception:
                continue
    source = REFERENCE.read_text(encoding="utf-8")
    lifecycle = {token: token in source for token in REQUIRED_LIFECYCLE_TOKENS}
    markets = []
    for market_id in market_ids:
        tape = TAPES / f"{market_id}.json.xz"
        quality = assess_archive(tape) if tape.exists() else {"qualityStatus": "MISSING"}
        markets.append(
            {
                "marketId": market_id,
                "tapeExists": tape.exists(),
                "qualityStatus": quality.get("qualityStatus"),
                "windowStartMs": quality.get("windowStartMs"),
                "windowEndMs": quality.get("windowEndMs"),
                "officialHftForward": market_id in official_ids,
            }
        )
    checks = {
        "allTapesCompleteForwardV1": all(row["qualityStatus"] == "COMPLETE_FORWARD_V1" for row in markets),
        "noOfficialHftForwardMarkets": all(not row["officialHftForward"] for row in markets),
        "referenceLifecycleComplete": all(lifecycle.values()),
        "formalWaitPresent": any(str(value).startswith("WAIT_KEEP_OBJECTIVE") for value in contract["formalActions"]),
        "actualFillStateMutationLocked": "Only confirmed HftBacktest fills" in contract["environment"]["stateMutation"],
        "logicLayerAuthorityPreserved": contract["architecture"]["logicLayer"].startswith("Frozen R2 owns"),
        "desiredPortfolioExecutionSeparated": "remain distinct" in contract["architecture"]["separationRule"],
        "actualFillFeedbackToFrozenR2": "Frozen R2 is evaluated again" in contract["architecture"]["feedbackRule"],
        "optionActionsConstrained": all(
            token in " ".join(contract["formalActions"])
            for token in ["current R2", "R2-authorized", "current Frozen R2 objective", "return actual state"]
        ),
        "exactPaperOrderReplayExcluded": "individual paper orders" in contract["architecture"]["orderReplayBoundary"],
        "cycleInvariantsComplete": len(contract["cycleInvariants"]) >= 9,
    }
    decision = "READY_FOR_ONE_MARKET_SYSTEM_EPISODE_SMOKE" if all(checks.values()) else "BLOCKED"
    report = {
        "version": "HFT_INTEGRATED_CONTROL_SEMIMDP_V1_R2_CYCLE_PREFLIGHT",
        "researchOnly": True,
        "contract": CONTRACT.name,
        "referenceLifecycle": str(REFERENCE.relative_to(ROOT)),
        "referenceLifecycleTokens": lifecycle,
        "markets": markets,
        "checks": checks,
        "decision": decision,
        "winnerSettlementPnlLoaded": False,
        "actionOutcomesLoaded": False,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(OUTPUT), "decision": decision, "checks": checks, "markets": markets}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
