from __future__ import annotations

import argparse
import json
import math
import sys
from bisect import bisect_left, bisect_right
from pathlib import Path
from typing import Any

import joblib
import numpy as np


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_archive_v1 import load_archive  # noqa: E402
from tools.hftbacktest_true_match_calibration_v0 import normalize_match  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
PREREG = BASE / "hft_orderflow_sequence_value_v1_preregistered.json"
AMENDMENT = BASE / "hft_orderflow_sequence_value_v1_pretrain100_amendment.json"
HFT_FRAME = BASE / "hft_target_side_prior_value_rank_v1_frame.joblib"
V4_CONTRACT = BASE / "hft_native_constrained_rank_v4_preregistered.json"
OUTPUT_NPZ = BASE / "hft_orderflow_sequence_value_v1_dataset.npz"
OUTPUT_FRAME = BASE / "hft_orderflow_sequence_value_v1_hft_frame.joblib"
OUTPUT_REPORT = BASE / "hft_orderflow_sequence_value_v1_dataset_report.json"
WINDOW_MS = 5000
BIN_MS = 100
BINS = WINDOW_MS // BIN_MS
CHANNELS = [
    "same_exact_add_qty",
    "same_exact_remove_qty",
    "same_better_add_qty",
    "same_better_remove_qty",
    "opposite_near_add_qty",
    "opposite_near_remove_qty",
    "contra_trade_qty",
    "with_trade_qty",
    "depth_event_count",
    "trade_event_count",
]
GRID = 0.01


def collect_market_ids(value: Any) -> set[int]:
    found: set[int] = set()
    if isinstance(value, dict):
        for key, item in value.items():
            lower = str(key).lower()
            if lower in {"marketid", "market_id"}:
                try:
                    found.add(int(item))
                except Exception:
                    pass
            else:
                found.update(collect_market_ids(item))
    elif isinstance(value, list):
        for item in value:
            found.update(collect_market_ids(item))
    return found


def sealed_ids() -> set[int]:
    result: set[int] = set()
    for path in BASE.glob("*.json"):
        lower = path.name.lower()
        if not any(token in lower for token in ("sealed", "graduation", "exam_registry")):
            continue
        try:
            result.update(collect_market_ids(json.loads(path.read_text(encoding="utf-8"))))
        except Exception:
            pass
    return result


def tape_events(tape: dict[str, Any]) -> tuple[list[dict[str, Any]], list[int], list[dict[str, Any]], list[int]]:
    depth: list[dict[str, Any]] = []
    for row in tape.get("updates") or []:
        timestamp = int(row[1])
        for book_side in ("bids", "asks"):
            for item in (row[6] or {}).get(book_side, []) or []:
                price, before, after, delta = map(float, item)
                depth.append({"timestamp": timestamp, "bookSide": book_side, "price": price, "delta": delta})
    depth.sort(key=lambda row: row["timestamp"])
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
    return depth, [row["timestamp"] for row in depth], trades, [row["timestamp"] for row in trades]


def native_order(side: str, action_price: float) -> tuple[str, float]:
    return ("BUY", action_price) if side == "UP" else ("SELL", 1.0 - action_price)


def add_depth_channel(vector: np.ndarray, event: dict[str, Any], native_side: str, native_price: float) -> None:
    same_book = "bids" if native_side == "BUY" else "asks"
    opposite_book = "asks" if native_side == "BUY" else "bids"
    price = float(event["price"])
    delta = float(event["delta"])
    amount = abs(delta)
    vector[8] += 1.0
    if event["bookSide"] == same_book and abs(price - native_price) < 0.005:
        vector[0 if delta >= 0 else 1] += amount
    elif event["bookSide"] == same_book:
        better = price > native_price + 0.005 if native_side == "BUY" else price < native_price - 0.005
        if better:
            vector[2 if delta >= 0 else 3] += amount
    elif event["bookSide"] == opposite_book:
        near = price <= native_price + 3 * GRID + 0.005 if native_side == "BUY" else price >= native_price - 3 * GRID - 0.005
        if near:
            vector[4 if delta >= 0 else 5] += amount


def add_trade_channel(vector: np.ndarray, event: dict[str, Any], native_side: str) -> None:
    contra = "SELL" if native_side == "BUY" else "BUY"
    vector[6 if event["aggressor"] == contra else 7] += float(event["qty"])
    vector[9] += 1.0


def sequence(
    depth: list[dict[str, Any]],
    depth_times: list[int],
    trades: list[dict[str, Any]],
    trade_times: list[int],
    checkpoint_ms: int,
    native_side: str,
    native_price: float,
    *,
    future: bool,
) -> np.ndarray:
    output = np.zeros((len(CHANNELS), BINS), dtype=np.float32)
    if future:
        start = checkpoint_ms
        end = checkpoint_ms + WINDOW_MS
        left_depth = bisect_right(depth_times, start)
        right_depth = bisect_right(depth_times, end)
        left_trade = bisect_right(trade_times, start)
        right_trade = bisect_right(trade_times, end)
    else:
        start = checkpoint_ms - WINDOW_MS
        end = checkpoint_ms
        left_depth = bisect_left(depth_times, start)
        right_depth = bisect_right(depth_times, end)
        left_trade = bisect_left(trade_times, start)
        right_trade = bisect_right(trade_times, end)
    for event in depth[left_depth:right_depth]:
        index = min(BINS - 1, max(0, int((event["timestamp"] - start) // BIN_MS)))
        add_depth_channel(output[:, index], event, native_side, native_price)
    for event in trades[left_trade:right_trade]:
        index = min(BINS - 1, max(0, int((event["timestamp"] - start) // BIN_MS)))
        add_trade_channel(output[:, index], event, native_side)
    return np.log1p(output)


def state_prices(tape: dict[str, Any], checkpoints: list[int]) -> dict[int, tuple[float, float] | None]:
    updates = sorted(tape.get("updates") or [], key=lambda row: (int(row[1]), int(row[0])))
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    output: dict[int, tuple[float, float] | None] = {}
    index = 0
    for checkpoint in sorted(checkpoints):
        while index < len(updates) and int(updates[index][1]) <= checkpoint:
            row = updates[index]
            if int(row[3]) == 1 and row[4] is not None and row[5] is not None:
                bids = {float(price): float(qty) for price, qty in (row[4] or {}).items() if float(qty) > 0}
                asks = {float(price): float(qty) for price, qty in (row[5] or {}).items() if float(qty) > 0}
            else:
                for book_side, book in (("bids", bids), ("asks", asks)):
                    for item in (row[6] or {}).get(book_side, []) or []:
                        price, _before, after, _delta = map(float, item)
                        if after > 0:
                            book[price] = after
                        else:
                            book.pop(price, None)
            index += 1
        output[checkpoint] = (max(bids), min(asks)) if bids and asks else None
    return output


def usable_pretrain_market(tape: dict[str, Any], cutoff_ms: int) -> tuple[int, int] | None:
    market = tape.get("market") or {}
    end = int(market.get("window_end_ms") or 0)
    start = end - 300_000 if end else 0
    if not start or end >= cutoff_ms or len(tape.get("updates") or []) < 50 or not tape.get("matches"):
        return None
    return start, end


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pretrain-markets", type=int, default=200)
    args = parser.parse_args()
    contract = json.loads(V4_CONTRACT.read_text(encoding="utf-8"))
    train_ids = [int(row["marketId"]) for row in contract["train"]]
    validation_ids = [int(row["marketId"]) for row in contract["validation"]]
    first_train_market = train_ids[0]
    first_train_checkpoint = int(contract["train"][0]["windowStartMs"])
    sealed = sealed_ids()

    pretrain_x: list[np.ndarray] = []
    pretrain_y: list[np.ndarray] = []
    pretrain_market: list[int] = []
    selected: list[int] = []
    candidates = sorted(
        (int(path.name.split(".", 1)[0]) for path in TAPES.glob("*.json.xz") if int(path.name.split(".", 1)[0]) < first_train_market and int(path.name.split(".", 1)[0]) not in sealed),
        reverse=True,
    )
    for market_id in candidates:
        tape = load_archive(TAPES / f"{market_id}.json.xz")
        bounds = usable_pretrain_market(tape, first_train_checkpoint)
        if bounds is None:
            continue
        start, end = bounds
        checkpoints = [int(round(value)) for value in np.linspace(start + 30_000, end - 30_000, 6)]
        prices = state_prices(tape, checkpoints)
        depth, depth_times, trades, trade_times = tape_events(tape)
        market_samples = 0
        for checkpoint in checkpoints:
            best = prices.get(checkpoint)
            if best is None:
                continue
            best_bid, best_ask = best
            for native_side, base_price in (("BUY", best_bid), ("SELL", best_ask)):
                for offset in (0, 1, 2):
                    native_price = base_price - offset * GRID if native_side == "BUY" else base_price + offset * GRID
                    if not (0.01 <= native_price <= 0.99):
                        continue
                    past = sequence(depth, depth_times, trades, trade_times, checkpoint, native_side, native_price, future=False)
                    future = sequence(depth, depth_times, trades, trade_times, checkpoint, native_side, native_price, future=True)
                    pretrain_x.append(past)
                    pretrain_y.append(np.log1p(np.expm1(future).sum(axis=1)))
                    pretrain_market.append(market_id)
                    market_samples += 1
        if market_samples >= 30:
            selected.append(market_id)
        else:
            if market_samples:
                del pretrain_x[-market_samples:]
                del pretrain_y[-market_samples:]
                del pretrain_market[-market_samples:]
        if len(selected) >= args.pretrain_markets:
            break
    if len(selected) < args.pretrain_markets:
        raise RuntimeError(f"only {len(selected)} strict-earlier pretraining markets found")

    frame = joblib.load(HFT_FRAME)
    hft = frame[frame.market_id.isin(train_ids + validation_ids)].copy().reset_index(drop=True)
    hft_x: list[np.ndarray] = []
    tape_cache: dict[int, tuple[list[dict[str, Any]], list[int], list[dict[str, Any]], list[int]]] = {}
    for row in hft.itertuples(index=False):
        market_id = int(row.market_id)
        if market_id not in tape_cache:
            tape_cache[market_id] = tape_events(load_archive(TAPES / f"{market_id}.json.xz"))
        depth, depth_times, trades, trade_times = tape_cache[market_id]
        native_side, native_price = native_order(str(row.side), float(row.action_price))
        hft_x.append(
            sequence(depth, depth_times, trades, trade_times, int(row.checkpoint_ms), native_side, native_price, future=False)
        )
    hft["sequence_index"] = np.arange(len(hft), dtype=int)
    joblib.dump(hft, OUTPUT_FRAME)
    np.savez_compressed(
        OUTPUT_NPZ,
        pretrain_x=np.asarray(pretrain_x, dtype=np.float32),
        pretrain_y=np.asarray(pretrain_y, dtype=np.float32),
        pretrain_market=np.asarray(pretrain_market, dtype=np.int64),
        hft_x=np.asarray(hft_x, dtype=np.float32),
    )
    report = {
        "version": "HFT_ORDERFLOW_SEQUENCE_VALUE_V1_DATASET_REPORT",
        "researchOnly": True,
        "preregistration": str(PREREG.resolve()),
        "availabilityAmendment": str(AMENDMENT.resolve()),
        "channels": CHANNELS,
        "windowMs": WINDOW_MS,
        "binMs": BIN_MS,
        "bins": BINS,
        "pretraining": {
            "markets": len(selected),
            "marketIds": sorted(selected),
            "samples": len(pretrain_x),
            "minMarketId": min(selected),
            "maxMarketId": max(selected),
            "strictlyBeforeHftTrain": True,
            "sealedIdsExcluded": len(sealed),
        },
        "hft": {
            "markets": int(hft.market_id.nunique()),
            "trainMarkets": len(train_ids),
            "validationMarkets": len(validation_ids),
            "actionSequences": len(hft),
            "filledActionRows": int(hft.filled.sum()),
            "pilotIncluded": False,
        },
        "tensorShapes": {
            "pretrainX": list(np.asarray(pretrain_x).shape),
            "pretrainY": list(np.asarray(pretrain_y).shape),
            "hftX": list(np.asarray(hft_x).shape),
        },
        "outcomeBoundary": "Pretraining uses only public future-flow proxy targets on markets strictly before HFT train. HFT validation outcomes are labels/evaluation only; pilot markets are absent.",
    }
    OUTPUT_REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "npz": str(OUTPUT_NPZ), "frame": str(OUTPUT_FRAME), "report": str(OUTPUT_REPORT), "pretraining": report["pretraining"], "hft": report["hft"], "tensorShapes": report["tensorShapes"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
