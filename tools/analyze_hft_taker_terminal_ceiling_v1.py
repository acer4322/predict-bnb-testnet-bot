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
OFFICIAL = ROOT / "data" / "hft_forward_paper_v1" / "markets"
SOURCES = [
    "hft_native_taker_new3b_v0.json",
    "hft_native_taker_new2_hold_v0.json",
    "hft_native_taker_b9a_v0.json",
    "hft_native_taker_b9b_v0.json",
    "hft_native_taker_b9c_v0.json",
]
OUTPUT = BASE / "hft_taker_terminal_ceiling_v1_opened13_report.json"
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


def evaluate(name: str) -> dict[str, Any]:
    dataset = json.loads((BASE / name).read_text(encoding="utf-8"))
    rows = dataset.get("rows") or []
    market_ids = sorted({int(row["marketId"]) for row in rows})
    outcome_map = winners(market_ids)
    choices: list[dict[str, Any]] = []
    for row in rows:
        market_id = int(row["marketId"])
        winner = str(outcome_map[market_id]).upper()
        direction = finite((row.get("features") or {}).get("directionScore"))
        selected_side = "UP" if direction >= 0.0 else "DOWN"
        actions = [action for action in row.get("actions") or [] if str(action.get("side") or "").upper() in {"UP", "DOWN"}]
        selected = [action for action in actions if str(action["side"]).upper() == selected_side]
        if len(selected) != 1:
            raise RuntimeError("direction action is not unique")
        chosen = selected[0]
        chosen_reward = terminal(chosen, winner)
        oracle_reward, oracle_action = max([(terminal(action, winner), action) for action in actions], key=lambda pair: pair[0])
        choices.append(
            {
                "marketId": market_id,
                "checkpointMs": int(row["checkpointMs"]),
                "winner": winner,
                "directionScore": direction,
                "action": selected_side,
                "filledShares": float(chosen.get("filled") or 0.0),
                "terminalRewardNetFeeUsdt": chosen_reward,
                "mtm1sNetFeeAudit": float(chosen.get("reward1sNetFee") or 0.0),
                "oracleAction": str(oracle_action["side"]).upper() if oracle_reward > EPS else "WAIT",
                "oracleTerminalRewardNetFeeUsdt": max(0.0, float(oracle_reward)),
            }
        )
    return {
        "source": name,
        "markets": len(market_ids),
        "marketIds": market_ids,
        "checkpoints": len(choices),
        "acts": len(choices),
        "waits": 0,
        "filledActs": sum(float(row["filledShares"]) > EPS for row in choices),
        "terminalRewardNetFeeUsdt": float(sum(float(row["terminalRewardNetFeeUsdt"]) for row in choices)),
        "mtm1sNetFeeAudit": float(sum(float(row["mtm1sNetFeeAudit"]) for row in choices)),
        "positiveActs": sum(float(row["terminalRewardNetFeeUsdt"]) > EPS for row in choices),
        "negativeActs": sum(float(row["terminalRewardNetFeeUsdt"]) < -EPS for row in choices),
        "zeroActs": sum(abs(float(row["terminalRewardNetFeeUsdt"])) <= EPS for row in choices),
        "oracleTerminalRewardNetFeeUsdt": float(sum(float(row["oracleTerminalRewardNetFeeUsdt"]) for row in choices)),
        "oracleActs": sum(row["oracleAction"] != "WAIT" for row in choices),
        "rows": choices,
    }


def compact(metrics: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in metrics.items() if key != "rows"}


def main() -> None:
    parts = [evaluate(name) for name in SOURCES]
    official_ids: set[int] = set()
    if OFFICIAL.exists():
        for path in OFFICIAL.glob("*_hft_closed_loop_v1.json.xz"):
            try:
                official_ids.add(int(path.name.split("_", 1)[0]))
            except Exception:
                continue
    source_market_ids = {int(value) for part in parts for value in part["marketIds"]}
    contaminated = sorted(source_market_ids & official_ids)
    combined = {
        "markets": sum(part["markets"] for part in parts),
        "checkpoints": sum(part["checkpoints"] for part in parts),
        "acts": sum(part["acts"] for part in parts),
        "waits": 0,
        "filledActs": sum(part["filledActs"] for part in parts),
        "terminalRewardNetFeeUsdt": sum(part["terminalRewardNetFeeUsdt"] for part in parts),
        "mtm1sNetFeeAudit": sum(part["mtm1sNetFeeAudit"] for part in parts),
        "positiveActs": sum(part["positiveActs"] for part in parts),
        "negativeActs": sum(part["negativeActs"] for part in parts),
        "zeroActs": sum(part["zeroActs"] for part in parts),
        "oracleTerminalRewardNetFeeUsdt": sum(part["oracleTerminalRewardNetFeeUsdt"] for part in parts),
        "oracleActs": sum(part["oracleActs"] for part in parts),
    }
    report = {
        "version": "HFT_TAKER_TERMINAL_CEILING_V1_OPENED13",
        "researchOnly": True,
        "exploratoryOpenedCohort": True,
        "performanceClaim": False,
        "invalidForResearchSelection": bool(contaminated),
        "officialHftForwardContaminatedMarketIds": contaminated,
        "sources": SOURCES,
        "execution": "Existing HftBacktest selective-Taker counterfactual actions with measured latency, actual fills and recorded taker fee.",
        "candidate": "directionScore sign, no threshold, WAIT only if action is absent.",
        "reward": "Actual-filled incremental settlement PnL net of recorded Taker fee; winner offline only.",
        "winnerRuntimeInput": False,
        "parts": parts,
        "combined": combined,
        "decisionRule": "Only justify a new frozen/new-data candidate if the no-threshold public direction rule is materially positive; oracle alone is not learnable evidence.",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(OUTPUT), "combined": combined, "parts": [compact(part) for part in parts]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
