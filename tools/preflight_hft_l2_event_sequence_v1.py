from __future__ import annotations

import json
import math
import sys
from bisect import bisect_left, bisect_right
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
CONTRACT = BASE / "hft_native_constrained_rank_v4_preregistered.json"
DATASET = BASE / "hft_native_constrained_rank_unused90_v4.json"
OUTPUT = BASE / "hft_l2_event_sequence_v1_preflight.json"
WINDOW_MS = 5000
GRID = 0.01


from src.predict_bot.execution_tape_archive_v1 import load_archive  # noqa: E402
from tools.hftbacktest_true_match_calibration_v0 import normalize_match  # noqa: E402


def quantiles(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"min": None, "p10": None, "median": None, "p90": None, "max": None, "mean": None}
    array = np.asarray(values, dtype=float)
    return {
        "min": float(np.min(array)),
        "p10": float(np.quantile(array, 0.10)),
        "median": float(np.quantile(array, 0.50)),
        "p90": float(np.quantile(array, 0.90)),
        "max": float(np.max(array)),
        "mean": float(np.mean(array)),
    }


def native_order(side: str, action_price: float) -> tuple[str, float]:
    return ("BUY", float(action_price)) if side == "UP" else ("SELL", 1.0 - float(action_price))


def market_events(market_id: int) -> dict[str, Any]:
    tape = load_archive(TAPES / f"{market_id}.json.xz")
    depth_events: list[dict[str, Any]] = []
    for row in tape.get("updates") or []:
        timestamp = int(row[1])
        changes = row[6] or {}
        for book_side in ("bids", "asks"):
            for item in changes.get(book_side, []) or []:
                price, before, after, delta = map(float, item)
                depth_events.append(
                    {
                        "timestamp": timestamp,
                        "bookSide": book_side,
                        "price": price,
                        "before": before,
                        "after": after,
                        "delta": delta,
                    }
                )
    depth_events.sort(key=lambda row: row["timestamp"])
    trades: list[dict[str, Any]] = []
    for raw in tape.get("matches") or []:
        normalized = normalize_match(raw)
        if normalized is None:
            continue
        trades.append(
            {
                "timestamp": int(normalized["tsMs"]) + 500,
                "aggressor": str(normalized["nativeAggressor"]),
                "price": float(normalized["nativeYesPrice"]),
                "qty": float(normalized["qty"]),
            }
        )
    trades.sort(key=lambda row: row["timestamp"])
    return {
        "depth": depth_events,
        "depthTimes": [int(row["timestamp"]) for row in depth_events],
        "trades": trades,
        "tradeTimes": [int(row["timestamp"]) for row in trades],
        "updates": len(tape.get("updates") or []),
        "matches": len(tape.get("matches") or []),
    }


def window(items: list[dict[str, Any]], times: list[int], checkpoint_ms: int) -> list[dict[str, Any]]:
    left = bisect_left(times, checkpoint_ms - WINDOW_MS)
    right = bisect_right(times, checkpoint_ms)
    return items[left:right]


def action_window(depth: list[dict[str, Any]], trades: list[dict[str, Any]], side: str, price: float) -> dict[str, float]:
    native_side, native_price = native_order(side, price)
    same_book = "bids" if native_side == "BUY" else "asks"
    opposite_book = "asks" if native_side == "BUY" else "bids"
    contra_aggressor = "SELL" if native_side == "BUY" else "BUY"
    exact = [row for row in depth if row["bookSide"] == same_book and abs(row["price"] - native_price) < 0.005]
    if native_side == "BUY":
        better = [row for row in depth if row["bookSide"] == same_book and row["price"] > native_price + 0.005]
        opposite_near = [
            row for row in depth
            if row["bookSide"] == opposite_book and row["price"] <= native_price + 3 * GRID + 0.005
        ]
    else:
        better = [row for row in depth if row["bookSide"] == same_book and row["price"] < native_price - 0.005]
        opposite_near = [
            row for row in depth
            if row["bookSide"] == opposite_book and row["price"] >= native_price - 3 * GRID - 0.005
        ]
    contra = [row for row in trades if row["aggressor"] == contra_aggressor]
    with_side = [row for row in trades if row["aggressor"] != contra_aggressor]
    return {
        "exactDepthEvents5s": float(len(exact)),
        "exactAddQty5s": sum(max(0.0, row["delta"]) for row in exact),
        "exactRemoveQty5s": sum(max(0.0, -row["delta"]) for row in exact),
        "betterDepthEvents5s": float(len(better)),
        "betterRemoveQty5s": sum(max(0.0, -row["delta"]) for row in better),
        "oppositeNearDepthEvents5s": float(len(opposite_near)),
        "oppositeNearRemoveQty5s": sum(max(0.0, -row["delta"]) for row in opposite_near),
        "contraTrades5s": float(len(contra)),
        "contraTradeQty5s": sum(row["qty"] for row in contra),
        "withTrades5s": float(len(with_side)),
        "withTradeQty5s": sum(row["qty"] for row in with_side),
    }


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    window_rows = list({(int(row["marketId"]), int(row["checkpointMs"])) for row in rows})
    metrics = [
        "depthEvents5s",
        "tradeEvents5s",
        "exactDepthEvents5s",
        "exactAddQty5s",
        "exactRemoveQty5s",
        "betterDepthEvents5s",
        "betterRemoveQty5s",
        "oppositeNearDepthEvents5s",
        "oppositeNearRemoveQty5s",
        "contraTrades5s",
        "contraTradeQty5s",
    ]
    return {
        "markets": len({int(row["marketId"]) for row in rows}),
        "checkpoints": len(window_rows),
        "actionSequences": len(rows),
        "checkpointZeroDepthEventRate": (
            len({(int(row["marketId"]), int(row["checkpointMs"])) for row in rows if row["depthEvents5s"] == 0})
            / max(1, len(window_rows))
        ),
        "actionZeroExactLevelEventRate": sum(row["exactDepthEvents5s"] == 0 for row in rows) / max(1, len(rows)),
        "actionZeroContraTradeRate": sum(row["contraTrades5s"] == 0 for row in rows) / max(1, len(rows)),
        "metrics": {name: quantiles([float(row[name]) for row in rows]) for name in metrics},
    }


def main() -> None:
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    split_by_market: dict[int, str] = {}
    for split in ("train", "validation", "holdout"):
        for row in contract[split]:
            split_by_market[int(row["marketId"])] = split
    dataset = json.loads(DATASET.read_text(encoding="utf-8"))
    rows: list[dict[str, Any]] = []
    cache: dict[int, dict[str, Any]] = {}
    for raw in dataset.get("rows") or []:
        market_id = int(raw["marketId"])
        checkpoint_ms = int(raw["checkpointMs"])
        if market_id not in cache:
            cache[market_id] = market_events(market_id)
        events = cache[market_id]
        depth = window(events["depth"], events["depthTimes"], checkpoint_ms)
        trades = window(events["trades"], events["tradeTimes"], checkpoint_ms)
        for action in raw.get("actions") or []:
            if action.get("invalid"):
                continue
            side = str(action.get("side") or "").upper()
            if side not in {"UP", "DOWN"}:
                continue
            row = {
                "split": split_by_market[market_id],
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "side": side,
                "offset": int(action["offset"]),
                "depthEvents5s": float(len(depth)),
                "tradeEvents5s": float(len(trades)),
                **action_window(depth, trades, side, float(action["price"])),
            }
            rows.append(row)
    summaries = {
        split: summarize([row for row in rows if row["split"] == split])
        for split in ("train", "validation", "holdout")
    }
    train = summaries["train"]
    feasibility = {
        "checkpointDepthCoverageAtLeast95Pct": train["checkpointZeroDepthEventRate"] <= 0.05,
        "medianDepthEvents5sAtLeast20": train["metrics"]["depthEvents5s"]["median"] >= 20,
        "exactLevelCoverageAtLeast40Pct": train["actionZeroExactLevelEventRate"] <= 0.60,
        "contraTradeCoverageAtLeast10Pct": train["actionZeroContraTradeRate"] <= 0.90,
    }
    decision = "SEQUENCE_REPRESENTATION_FEASIBLE" if all(feasibility.values()) else "RAW_SEQUENCE_TOO_SPARSE"
    report = {
        "version": "HFT_L2_EVENT_SEQUENCE_V1_PREFLIGHT",
        "researchOnly": True,
        "outcomesUsed": False,
        "windowMs": WINDOW_MS,
        "timestampBoundary": "Only tape events with received/executed time <= checkpoint are counted.",
        "source": DATASET.name,
        "markets": len(cache),
        "summaries": summaries,
        "feasibilityConditions": feasibility,
        "decision": decision,
        "nextIfFeasible": "Build stationary action-oriented 100ms order-flow tensors, pretrain a small causal CNN on pre-official Tape V1 next-flow targets, then fine-tune only on HftBacktest fill/value labels.",
        "rows": rows,
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "output": str(OUTPUT), "decision": decision, "conditions": feasibility, "summaries": summaries}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
