from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.evaluate_r2_pending_management_closed_loop_v0 import winners  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
SOURCES = [f"hft_native_joint_pair_sweep_p0{index}_train_v0.json" for index in range(1, 5)]
OUTPUT = BASE / "hft_pair_terminal_ceiling_v1_train40_report.json"
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except Exception:
        return math.nan


def action_terminal(action: dict[str, Any], winner: str) -> float:
    up_qty = float(action.get("upFilled") or 0.0)
    down_qty = float(action.get("downFilled") or 0.0)
    up_price = finite(action.get("upExecPrice"))
    down_price = finite(action.get("downExecPrice"))
    value = 0.0
    if up_qty > EPS and math.isfinite(up_price):
        value += up_qty * (float(winner == "UP") - up_price)
    if down_qty > EPS and math.isfinite(down_price):
        value += down_qty * (float(winner == "DOWN") - down_price)
    return value


def main() -> None:
    datasets = [json.loads((BASE / name).read_text(encoding="utf-8")) for name in SOURCES]
    rows = [row for dataset in datasets for row in dataset.get("rows") or []]
    market_ids = sorted({int(row["marketId"]) for row in rows})
    outcome_map = winners(market_ids)
    if set(outcome_map) != set(market_ids):
        raise RuntimeError("winner coverage mismatch")

    by_offset: dict[str, dict[str, Any]] = {}
    for offset in (0, 1, 2):
        actions = []
        for row in rows:
            match = [
                action
                for action in row.get("actions") or []
                if not action.get("invalid") and int(action["offset"]) == offset
            ]
            if len(match) != 1:
                continue
            action = match[0]
            terminal = action_terminal(action, str(outcome_map[int(row["marketId"])]).upper())
            actions.append((action, terminal))
        by_offset[str(offset)] = {
            "actions": len(actions),
            "terminalRewardUsdt": float(sum(value for _, value in actions)),
            "mtm5sUsdtAudit": float(sum(float(action.get("portfolioMtm5s") or 0.0) for action, _ in actions)),
            "bothFilled": sum(bool(action.get("bothFilled")) for action, _ in actions),
            "oneSidedFill": sum(bool(action.get("oneSidedFill")) for action, _ in actions),
            "anyFilled": sum(float(action.get("upFilled") or 0.0) > EPS or float(action.get("downFilled") or 0.0) > EPS for action, _ in actions),
            "positive": sum(value > EPS for _, value in actions),
            "negative": sum(value < -EPS for _, value in actions),
            "zero": sum(abs(value) <= EPS for _, value in actions),
            "lockedPairEdgeUsdt": float(sum(float(action.get("lockedPairEdgeUsdt") or 0.0) for action, _ in actions)),
        }

    oracle_reward = 0.0
    oracle_acts = 0
    oracle_both = 0
    oracle_one_sided = 0
    choices: list[dict[str, Any]] = []
    for row in rows:
        winner = str(outcome_map[int(row["marketId"])]).upper()
        candidates = [
            (action_terminal(action, winner), action)
            for action in row.get("actions") or []
            if not action.get("invalid")
        ]
        best_value, best_action = max(candidates, key=lambda pair: pair[0]) if candidates else (0.0, None)
        if best_value <= EPS:
            choices.append({"marketId": int(row["marketId"]), "checkpointMs": int(row["checkpointMs"]), "action": "WAIT", "terminalRewardUsdt": 0.0})
            continue
        oracle_reward += float(best_value)
        oracle_acts += 1
        oracle_both += int(bool(best_action.get("bothFilled")))
        oracle_one_sided += int(bool(best_action.get("oneSidedFill")))
        choices.append({"marketId": int(row["marketId"]), "checkpointMs": int(row["checkpointMs"]), "action": f"PAIR_{int(best_action['offset'])}_{int(best_action['offset'])}", "terminalRewardUsdt": float(best_value), "bothFilled": bool(best_action.get("bothFilled")), "oneSidedFill": bool(best_action.get("oneSidedFill"))})

    report = {
        "version": "HFT_PAIR_TERMINAL_CEILING_V1_TRAIN40",
        "researchOnly": True,
        "exploratoryOpenedTrainingCohort": True,
        "performanceClaim": False,
        "sources": SOURCES,
        "markets": len(market_ids),
        "checkpoints": len(rows),
        "execution": "Existing HftBacktest joint-pair actions: risk queue, 1092ms entry / 273ms response latency, partial fills, 5s horizon.",
        "reward": "Actual-filled incremental settlement PnL; winner is offline outcome only.",
        "winnerRuntimeInput": False,
        "byFixedOffset": by_offset,
        "waitPlusBestPairOracle": {
            "terminalRewardUsdt": oracle_reward,
            "acts": oracle_acts,
            "actRate": oracle_acts / max(1, len(rows)),
            "bothFilledActs": oracle_both,
            "oneSidedFilledActs": oracle_one_sided,
        },
        "oracleRows": choices,
        "nextGate": "Only freeze a pair candidate and generate a small later HftBacktest cohort if the terminal oracle is material and the value is not explained solely by winner-selected one-sided fills.",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(OUTPUT), "byFixedOffset": by_offset, "oracle": report["waitPlusBestPairOracle"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
