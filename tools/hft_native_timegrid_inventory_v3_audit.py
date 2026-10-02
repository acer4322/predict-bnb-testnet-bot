from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import hft_native_queue_regime_value_v1 as v1  # noqa: E402
from tools import hft_native_timegrid_inventory_value_v3 as v3  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
EPS = v1.EPS


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fixed_inventory_repair(frame: pd.DataFrame, offset: int) -> dict[str, Any]:
    choices: list[dict[str, Any]] = []
    for (market_id, checkpoint_ms), group in frame.groupby(["market_id", "checkpoint_ms"], sort=True):
        net = float(group.iloc[0].combined_net)
        side = "DOWN" if net > EPS else "UP" if net < -EPS else None
        selected = group[(group.side == side) & (group.action_offset == float(offset))] if side else group.iloc[0:0]
        if len(selected) == 1:
            row = selected.iloc[0]
            choices.append(
                {
                    "marketId": int(market_id),
                    "checkpointMs": int(checkpoint_ms),
                    "action": f"{side}_{offset}",
                    "filledShares5s": float(row.filled_shares),
                    "mtm1sUsdt": float(row.mtm),
                    "portfolioFloorDelta": float(row.delta_floor_realized),
                    "portfolioReward": float(row.portfolio_reward),
                }
            )
        else:
            choices.append(
                {
                    "marketId": int(market_id),
                    "checkpointMs": int(checkpoint_ms),
                    "action": "WAIT",
                    "filledShares5s": 0.0,
                    "mtm1sUsdt": 0.0,
                    "portfolioFloorDelta": 0.0,
                    "portfolioReward": 0.0,
                }
            )
    acts = [row for row in choices if row["action"] != "WAIT"]
    return {
        "checkpoints": len(choices),
        "policyReward": float(sum(row["portfolioReward"] for row in acts)),
        "policyActs": len(acts),
        "policyActRate": len(acts) / max(1, len(choices)),
        "waits": len(choices) - len(acts),
        "filledActs": sum(row["filledShares5s"] > EPS for row in acts),
        "positiveActs": sum(row["portfolioReward"] > EPS for row in acts),
        "negativeActs": sum(row["portfolioReward"] < -EPS for row in acts),
        "zeroActs": sum(abs(row["portfolioReward"]) <= EPS for row in acts),
        "mtm1sUsdt": float(sum(row["mtm1sUsdt"] for row in acts)),
        "portfolioFloorDelta": float(sum(row["portfolioFloorDelta"] for row in acts)),
        "rows": choices,
    }


def learned_audit(primary_metrics: dict[str, Any], frame: pd.DataFrame) -> dict[str, Any]:
    checkpoints: dict[int, list[int]] = defaultdict(list)
    for row in primary_metrics["rows"]:
        checkpoints[int(row["marketId"])].append(int(row["checkpointMs"]))
    for values in checkpoints.values():
        values.sort()
    action_positions: Counter[int] = Counter()
    positive_positions: Counter[int] = Counter()
    oracle_positions: Counter[int] = Counter()
    reduces_imbalance = 0
    acts = 0
    for choice in primary_metrics["rows"]:
        market_id = int(choice["marketId"])
        checkpoint_ms = int(choice["checkpointMs"])
        position = checkpoints[market_id].index(checkpoint_ms) + 1
        group = frame[(frame.market_id == market_id) & (frame.checkpoint_ms == checkpoint_ms)]
        if str(choice["oracleAction"]) != "WAIT":
            oracle_positions[position] += 1
        if str(choice["action"]) == "WAIT":
            continue
        acts += 1
        action_positions[position] += 1
        if float(choice["reward"]) > EPS:
            positive_positions[position] += 1
        side = str(choice["action"]).split("_")[0]
        net = float(group.iloc[0].combined_net)
        reduces_imbalance += int((net > EPS and side == "DOWN") or (net < -EPS and side == "UP"))
    return {
        "acts": acts,
        "reducesInventoryImbalanceActs": reduces_imbalance,
        "actionCheckpointPositions": dict(sorted(action_positions.items())),
        "positiveCheckpointPositions": dict(sorted(positive_positions.items())),
        "oracleCheckpointPositions": dict(sorted(oracle_positions.items())),
    }


def compact_primary(metrics: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "checkpoints",
        "oracleReward",
        "oracleActs",
        "oracleActRate",
        "policyReward",
        "policyActs",
        "policyActRate",
        "waits",
        "positiveActs",
        "negativeActs",
        "zeroActs",
    )
    return {key: metrics.get(key) for key in keys}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--contract", default="hft_native_timegrid_inventory_chronology_v3_preregistered.json")
    parser.add_argument("--dataset", default="hft_native_timegrid_inventory_unused80_v3.json")
    parser.add_argument("--primary", default="hft_native_timegrid_inventory_value_v3_report.json")
    parser.add_argument("--output", default="hft_native_timegrid_inventory_v3_audit.json")
    args = parser.parse_args()

    contract_path = BASE / args.contract
    dataset_path = BASE / args.dataset
    primary_path = BASE / args.primary
    policy_path = ROOT / "tools" / "hft_native_timegrid_inventory_value_v3.py"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    primary = json.loads(primary_path.read_text(encoding="utf-8"))
    ids = {
        split: {int(row["marketId"]) for row in contract[split]}
        for split in ("train", "validation", "holdout")
    }
    all_rows = v3.load_rows(args.dataset, set().union(*ids.values()))
    frames = {
        split: all_rows[all_rows.market_id.isin(market_ids)].copy()
        for split, market_ids in ids.items()
    }
    fixed = {
        f"offset{offset}": {
            split: fixed_inventory_repair(frame, offset)
            for split, frame in frames.items()
        }
        for offset in (0, 1, 2)
    }
    learned = {
        split: learned_audit(primary["evaluations"][split], frames[split])
        for split in frames
    }
    report = {
        "version": "HFT_NATIVE_TIMEGRID_INVENTORY_V3_AUDIT",
        "researchOnly": True,
        "auditOnlyNotUsedForTuning": True,
        "winnerSettlementPnlRuntimeInput": False,
        "targetFutureActionRuntimeInput": False,
        "lockedArtifactHashes": {
            "contractSha256": sha256(contract_path),
            "datasetSha256": sha256(dataset_path),
            "policyToolSha256": sha256(policy_path),
            "primaryReportSha256": sha256(primary_path),
        },
        "learnedPolicy": {
            split: {
                **compact_primary(primary["evaluations"][split]),
                **learned[split],
            }
            for split in frames
        },
        "fixedInventoryRepairBaselines": {
            name: {
                split: {key: value for key, value in metrics.items() if key != "rows"}
                for split, metrics in evaluations.items()
            }
            for name, evaluations in fixed.items()
        },
        "interpretationRule": "If a fixed inventory-reducing offset matches or exceeds the learned policy OOS, retain the economic inventory-repair direction but reject the current learned gate as necessary evidence.",
    }
    output = BASE / args.output
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=True), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(output), **report}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
