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
PREREG = BASE / "hft_direction_taker_terminal_pilot_v1_preregistered.json"
SOURCE = BASE / "hft_direction_taker_terminal_pilot_v1_actions.json"
OUTPUT = BASE / "hft_direction_taker_terminal_pilot_v1_report.json"
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except Exception:
        return math.nan


def terminal(action: dict[str, Any], winner: str) -> float:
    shares = float(action.get("filled") or 0.0)
    price = finite(action.get("fillPrice"))
    fee = float(action.get("fee") or 0.0)
    if shares <= EPS or not math.isfinite(price):
        return 0.0
    return shares * (float(str(action["side"]).upper() == winner) - price) - fee


def main() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    dataset = json.loads(SOURCE.read_text(encoding="utf-8"))
    expected = [int(value) for value in prereg["cohort"]["markets"]]
    if [int(value) for value in dataset.get("markets") or []] != expected:
        raise RuntimeError("pilot market order/coverage mismatch")
    outcome_map = winners(expected)
    rows: list[dict[str, Any]] = []
    for checkpoint in dataset.get("rows") or []:
        market_id = int(checkpoint["marketId"])
        winner = str(outcome_map[market_id]).upper()
        direction = finite((checkpoint.get("features") or {}).get("directionScore"))
        chosen_side = "UP" if direction >= 0.0 else "DOWN"
        actions = [action for action in checkpoint.get("actions") or [] if str(action.get("side") or "").upper() in {"UP", "DOWN"}]
        chosen = [action for action in actions if str(action["side"]).upper() == chosen_side]
        if len(chosen) != 1:
            raise RuntimeError("candidate action is not unique")
        action = chosen[0]
        action_reward = terminal(action, winner)
        oracle_reward, oracle_action = max([(terminal(value, winner), value) for value in actions], key=lambda pair: pair[0])
        filled = float(action.get("filled") or 0.0)
        rows.append(
            {
                "marketId": market_id,
                "checkpointMs": int(checkpoint["checkpointMs"]),
                "winner": winner,
                "directionScore": direction,
                "action": chosen_side if filled > EPS else "WAIT_NO_FILL",
                "filledShares": filled,
                "terminalRewardNetFeeUsdt": action_reward,
                "mtm1sNetFeeAudit": float(action.get("reward1sNetFee") or 0.0),
                "oracleAction": str(oracle_action["side"]).upper() if oracle_reward > EPS else "WAIT",
                "oracleTerminalRewardNetFeeUsdt": max(0.0, float(oracle_reward)),
            }
        )
    acts = [row for row in rows if not row["action"].startswith("WAIT")]
    per_market: dict[str, float] = {}
    for row in rows:
        key = str(row["marketId"])
        per_market[key] = per_market.get(key, 0.0) + float(row["terminalRewardNetFeeUsdt"])
    metrics = {
        "markets": len(expected),
        "checkpoints": len(rows),
        "waits": len(rows) - len(acts),
        "acts": len(acts),
        "actRate": len(acts) / max(1, len(rows)),
        "filledActs": len(acts),
        "filledShares": float(sum(float(row["filledShares"]) for row in acts)),
        "terminalRewardNetFeeUsdt": float(sum(float(row["terminalRewardNetFeeUsdt"]) for row in acts)),
        "mtm1sNetFeeAudit": float(sum(float(row["mtm1sNetFeeAudit"]) for row in acts)),
        "positiveActs": sum(float(row["terminalRewardNetFeeUsdt"]) > EPS for row in acts),
        "negativeActs": sum(float(row["terminalRewardNetFeeUsdt"]) < -EPS for row in acts),
        "oracleTerminalRewardNetFeeUsdt": float(sum(float(row["oracleTerminalRewardNetFeeUsdt"]) for row in rows)),
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in rows),
        "positiveMarkets": sum(value > EPS for value in per_market.values()),
        "negativeMarkets": sum(value < -EPS for value in per_market.values()),
        "perMarketTerminalRewardNetFeeUsdt": per_market,
        "rows": rows,
    }
    conditions = {
        "oracleTerminalValuePositive": metrics["oracleTerminalRewardNetFeeUsdt"] > EPS,
        "candidateTerminalValuePositive": metrics["terminalRewardNetFeeUsdt"] > EPS,
        "minimumFilledActs": metrics["filledActs"] >= 6,
    }
    decision = "KEEP_COMPONENT" if all(conditions.values()) else "REJECT"
    report = {
        "version": "HFT_DIRECTION_TAKER_TERMINAL_PILOT_V1",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "source": SOURCE.name,
        "execution": prereg["execution"],
        "winnerRuntimeInput": False,
        "winnerUsedAsOfflineOutcomeOnly": True,
        "metrics": metrics,
        "conditions": conditions,
        "decision": decision,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(OUTPUT), "decision": decision, "conditions": conditions, "metrics": {key: value for key, value in metrics.items() if key != "rows"}}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
