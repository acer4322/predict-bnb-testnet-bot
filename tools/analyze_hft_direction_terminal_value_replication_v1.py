from __future__ import annotations

import argparse
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
PREREG = BASE / "hft_direction_terminal_value_replication_v1_preregistered.json"
PILOT_REPORT = BASE / "hft_direction_terminal_value_replication_v1_pilot_report.json"
EXPANSION_REPORT = BASE / "hft_direction_terminal_value_replication_v1_expansion_report.json"
EPS = 1e-9


def finite(value: Any) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else math.nan
    except Exception:
        return math.nan


def reward(action: dict[str, Any], winner: str) -> float:
    shares = float(action.get("filledShares5s") or 0.0)
    if shares <= EPS:
        return 0.0
    fill_price = finite(action.get("fillPrice"))
    price = fill_price if math.isfinite(fill_price) else float(action["price"])
    return shares * (float(str(action["side"]).upper() == winner) - price)


def evaluate_dataset(dataset_name: str) -> dict[str, Any]:
    dataset = json.loads((BASE / dataset_name).read_text(encoding="utf-8"))
    market_ids = sorted({int(row["marketId"]) for row in dataset.get("rows") or []})
    outcome_map = winners(market_ids)
    if set(outcome_map) != set(market_ids):
        raise RuntimeError(f"winner coverage mismatch in {dataset_name}")
    choices: list[dict[str, Any]] = []
    for checkpoint in dataset.get("rows") or []:
        market_id = int(checkpoint["marketId"])
        checkpoint_ms = int(checkpoint["checkpointMs"])
        winner = str(outcome_map[market_id]).upper()
        features = dict(checkpoint.get("features") or {})
        direction = finite(features.get("directionScore"))
        side = "UP" if direction >= 0.0 else "DOWN"
        actions = [
            action
            for action in checkpoint.get("actions") or []
            if not action.get("invalid") and str(action.get("side") or "").upper() in {"UP", "DOWN"}
        ]
        chosen_rows = [
            action
            for action in actions
            if str(action["side"]).upper() == side and int(action["offset"]) == 2
        ]
        if len(chosen_rows) > 1:
            raise RuntimeError("candidate action is not unique")
        all_rewards = [(reward(action, winner), action) for action in actions]
        oracle_reward, oracle_action = max(all_rewards, key=lambda pair: pair[0]) if all_rewards else (0.0, None)
        if oracle_reward <= EPS:
            oracle_reward = 0.0
            oracle_name = "WAIT"
        else:
            oracle_name = f"{oracle_action['side']}_{int(oracle_action['offset'])}"
        if not chosen_rows:
            choices.append(
                {
                    "marketId": market_id,
                    "checkpointMs": checkpoint_ms,
                    "winner": winner,
                    "directionScore": direction,
                    "action": "WAIT_INVALID_ACTION",
                    "filledShares5s": 0.0,
                    "terminalRewardUsdt": 0.0,
                    "mtm1sUsdtAudit": 0.0,
                    "oracleAction": oracle_name,
                    "oracleTerminalRewardUsdt": oracle_reward,
                }
            )
            continue
        action = chosen_rows[0]
        choices.append(
            {
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "winner": winner,
                "directionScore": direction,
                "action": f"{side}_2",
                "filledShares5s": float(action.get("filledShares5s") or 0.0),
                "terminalRewardUsdt": reward(action, winner),
                "mtm1sUsdtAudit": float(action.get("mtm1sUsdt") or 0.0),
                "oracleAction": oracle_name,
                "oracleTerminalRewardUsdt": oracle_reward,
            }
        )
    acts = [row for row in choices if not row["action"].startswith("WAIT")]
    per_market: dict[str, float] = {}
    for row in choices:
        key = str(row["marketId"])
        per_market[key] = per_market.get(key, 0.0) + float(row["terminalRewardUsdt"])
    return {
        "dataset": dataset_name,
        "markets": len(market_ids),
        "marketIds": market_ids,
        "checkpoints": len(choices),
        "waits": len(choices) - len(acts),
        "acts": len(acts),
        "actRate": len(acts) / max(1, len(choices)),
        "filledActs": sum(float(row["filledShares5s"]) > EPS for row in acts),
        "filledShares5s": float(sum(float(row["filledShares5s"]) for row in acts)),
        "positiveActs": sum(float(row["terminalRewardUsdt"]) > EPS for row in acts),
        "negativeActs": sum(float(row["terminalRewardUsdt"]) < -EPS for row in acts),
        "zeroActs": sum(abs(float(row["terminalRewardUsdt"])) <= EPS for row in acts),
        "terminalRewardUsdt": float(sum(float(row["terminalRewardUsdt"]) for row in acts)),
        "mtm1sUsdtAudit": float(sum(float(row["mtm1sUsdtAudit"]) for row in acts)),
        "oracleTerminalRewardUsdt": float(sum(float(row["oracleTerminalRewardUsdt"]) for row in choices)),
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "perMarketTerminalRewardUsdt": per_market,
        "rows": choices,
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "rows"}


def pilot() -> None:
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    metrics = evaluate_dataset(str(prereg["pilot"]["dataset"]))
    if metrics["marketIds"] != [int(value) for value in prereg["pilot"]["markets"]]:
        raise RuntimeError("pilot market coverage differs from preregistration")
    conditions = {
        "oraclePositive": metrics["oracleTerminalRewardUsdt"] > EPS,
        "candidatePositive": metrics["terminalRewardUsdt"] > EPS,
        "atLeastTwoFilledActs": metrics["filledActs"] >= 2,
    }
    decision = "PROMOTE_TO_EXPANSION" if all(conditions.values()) else "REJECT_BEFORE_EXPANSION"
    report = {
        "version": "HFT_DIRECTION_TERMINAL_VALUE_REPLICATION_V1_PILOT",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "winnerRuntimeInput": False,
        "winnerUsedAsOfflineOutcomeOnly": True,
        "metrics": metrics,
        "conditions": conditions,
        "decision": decision,
    }
    PILOT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(PILOT_REPORT), "decision": decision, "conditions": conditions, "metrics": compact(metrics)}, ensure_ascii=False, indent=2))


def expansion() -> None:
    pilot_report = json.loads(PILOT_REPORT.read_text(encoding="utf-8"))
    if pilot_report.get("decision") != "PROMOTE_TO_EXPANSION":
        raise RuntimeError("pilot did not unlock expansion")
    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    datasets = [str(value) for value in prereg["expansion"]["datasetsInChronologicalOrder"]]
    parts = [evaluate_dataset(name) for name in datasets]
    total = {
        "markets": sum(part["markets"] for part in parts),
        "checkpoints": sum(part["checkpoints"] for part in parts),
        "waits": sum(part["waits"] for part in parts),
        "acts": sum(part["acts"] for part in parts),
        "filledActs": sum(part["filledActs"] for part in parts),
        "filledShares5s": sum(part["filledShares5s"] for part in parts),
        "positiveActs": sum(part["positiveActs"] for part in parts),
        "negativeActs": sum(part["negativeActs"] for part in parts),
        "zeroActs": sum(part["zeroActs"] for part in parts),
        "terminalRewardUsdt": sum(part["terminalRewardUsdt"] for part in parts),
        "mtm1sUsdtAudit": sum(part["mtm1sUsdtAudit"] for part in parts),
        "oracleTerminalRewardUsdt": sum(part["oracleTerminalRewardUsdt"] for part in parts),
        "oracleActs": sum(part["oracleActs"] for part in parts),
    }
    total["actRate"] = total["acts"] / max(1, total["checkpoints"])
    conditions = {
        "terminalRewardPositive": total["terminalRewardUsdt"] > EPS,
        "atLeastFiveFilledActs": total["filledActs"] >= 5,
    }
    decision = "KEEP_COMPONENT" if all(conditions.values()) else "REJECT"
    report = {
        "version": "HFT_DIRECTION_TERMINAL_VALUE_REPLICATION_V1_EXPANSION",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "pilotReport": PILOT_REPORT.name,
        "winnerRuntimeInput": False,
        "winnerUsedAsOfflineOutcomeOnly": True,
        "parts": parts,
        "combined": total,
        "conditions": conditions,
        "decision": decision,
    }
    EXPANSION_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(EXPANSION_REPORT), "decision": decision, "conditions": conditions, "combined": total, "parts": [compact(part) for part in parts]}, ensure_ascii=False, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=["pilot", "expansion"], required=True)
    args = parser.parse_args()
    if args.phase == "pilot":
        pilot()
    else:
        expansion()


if __name__ == "__main__":
    main()
