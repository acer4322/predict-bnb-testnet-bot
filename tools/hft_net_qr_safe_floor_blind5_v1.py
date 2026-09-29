from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import math
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_archive_v1 import load_archive  # noqa: E402
from tools import hftbacktest_execution_shift_audit_v0 as ex  # noqa: E402
from tools.evaluate_hft_net_qr_world_model_pilot_v1 import (  # noqa: E402
    TransitionSampler,
    apply_update,
    percentile,
    state_key,
)
from tools.hft_safe_floor_contingent_pair_smoke_v1 import (  # noqa: E402
    GRID,
    selected_checkpoint,
    run_offset,
)
from tools.hftbacktest_true_match_calibration_v0 import normalize_match  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
PREREG = BASE / "hft_net_qr_safe_floor_blind5_v1_preregistered.json"
DECISIONS = BASE / "hft_net_qr_safe_floor_blind5_v1_decisions.json"
REPORT = BASE / "hft_net_qr_safe_floor_blind5_v1_report.json"
EPS = 1e-9


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def top_book_state(
    bids: dict[float, float],
    asks: dict[float, float],
    timestamp: int,
    end_ms: int,
    levels: int,
) -> dict[str, Any] | None:
    bid_rows = sorted(((p, q) for p, q in bids.items() if q > EPS), reverse=True)[:levels]
    ask_rows = sorted((p, q) for p, q in asks.items() if q > EPS)[:levels]
    if not bid_rows or not ask_rows:
        return None
    best_bid, bid_qty = bid_rows[0]
    best_ask, ask_qty = ask_rows[0]
    if best_ask <= best_bid or bid_qty + ask_qty <= EPS:
        return None
    return {
        "timestamp": float(timestamp),
        "secondsLeft": max(0.0, (end_ms - timestamp) / 1000.0),
        "bestBid": float(best_bid),
        "bestAsk": float(best_ask),
        "spread": float(max(1, round((best_ask - best_bid) / GRID))),
        "bidQty": float(bid_qty),
        "askQty": float(ask_qty),
        "totalBest": float(bid_qty + ask_qty),
        "imbalance": float((bid_qty - ask_qty) / (bid_qty + ask_qty)),
        "bids": {round(float(p), 2): float(q) for p, q in bid_rows},
        "asks": {round(float(p), 2): float(q) for p, q in ask_rows},
        "intervalTrades": [],
    }


def market_series(market_id: int, levels: int, cutoff_ms: int | None = None) -> dict[str, Any]:
    tape = load_archive(TAPES / f"{market_id}.json.xz")
    updates = sorted(tape.get("updates") or [], key=lambda row: (int(row[1]), int(row[0])))
    if cutoff_ms is not None:
        updates = [row for row in updates if int(row[1]) <= cutoff_ms]
    if not updates:
        raise RuntimeError(f"market {market_id} has no usable updates")
    end_ms = int((tape.get("market") or {}).get("window_end_ms") or max(int(row[1]) for row in updates))
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    snapshots: list[dict[str, Any]] = []
    index = 0
    while index < len(updates):
        timestamp = int(updates[index][1])
        while index < len(updates) and int(updates[index][1]) == timestamp:
            apply_update(bids, asks, updates[index])
            index += 1
        state = top_book_state(bids, asks, timestamp, end_ms, levels)
        if state is not None:
            snapshots.append(state)
    if len(snapshots) < 1:
        raise RuntimeError(f"market {market_id} has no valid book state")

    trades: list[dict[str, Any]] = []
    for raw in tape.get("matches") or []:
        normalized = normalize_match(raw)
        if normalized is None:
            continue
        timestamp = int(normalized["tsMs"]) + 500
        if cutoff_ms is not None and timestamp > cutoff_ms:
            continue
        trades.append(
            {
                "timestamp": timestamp,
                "aggressor": str(normalized["nativeAggressor"]),
                "price": float(normalized["nativeYesPrice"]),
                "qty": float(normalized["qty"]),
            }
        )
    trades.sort(key=lambda row: (int(row["timestamp"]), str(row["aggressor"]), float(row["price"])))
    trade_times = [int(row["timestamp"]) for row in trades]
    for current, nxt in zip(snapshots, snapshots[1:]):
        lo = bisect.bisect_right(trade_times, int(current["timestamp"]))
        hi = bisect.bisect_right(trade_times, int(nxt["timestamp"]))
        nxt["intervalTrades"] = trades[lo:hi]
    return {
        "marketId": market_id,
        "endMs": end_ms,
        "snapshots": snapshots,
        "prefixFilteringApplied": cutoff_ms is not None,
        "maximumUsedUpdateMs": int(snapshots[-1]["timestamp"]),
        "maximumUsedTradeMs": max((int(row["timestamp"]) for row in trades), default=None),
    }


def relative_shape(book: dict[float, float], best: float, side: str, levels: int) -> list[tuple[int, float]]:
    rows = sorted(book.items(), reverse=side == "bids")[:levels]
    if side == "bids":
        return [(int(round((best - price) / GRID)), float(qty)) for price, qty in rows]
    return [(int(round((price - best) / GRID)), float(qty)) for price, qty in rows]


def train_transitions(
    series_by_market: dict[int, dict[str, Any]],
    train_ids: list[int],
    quartiles: tuple[float, float, float],
    levels: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for market_id in train_ids:
        snapshots = series_by_market[market_id]["snapshots"]
        for current, nxt in zip(snapshots, snapshots[1:]):
            gap = int(nxt["timestamp"] - current["timestamp"])
            if gap <= 0:
                continue
            mapped_trades = []
            for trade in nxt["intervalTrades"]:
                if trade["aggressor"] == "BUY":
                    relative_ticks = int(round((float(trade["price"]) - current["bestAsk"]) / GRID))
                else:
                    relative_ticks = int(round((current["bestBid"] - float(trade["price"])) / GRID))
                if -2 <= relative_ticks <= 30:
                    mapped_trades.append(
                        {
                            "aggressor": trade["aggressor"],
                            "relativeTicks": relative_ticks,
                            "qty": float(trade["qty"]),
                        }
                    )
            rows.append(
                {
                    "marketId": market_id,
                    "state": state_key(current, quartiles),
                    "gapMs": gap,
                    "bidMoveTicks": int(round((nxt["bestBid"] - current["bestBid"]) / GRID)),
                    "nextSpread": int(round(nxt["spread"])),
                    "nextBidShape": relative_shape(nxt["bids"], nxt["bestBid"], "bids", levels),
                    "nextAskShape": relative_shape(nxt["asks"], nxt["bestAsk"], "asks", levels),
                    "trades": mapped_trades,
                }
            )
    if not rows:
        raise RuntimeError("no train transitions")
    return rows


def bounded_best(best_bid: float, spread_ticks: int) -> tuple[float, float]:
    spread_ticks = min(20, max(1, int(spread_ticks)))
    spread = spread_ticks * GRID
    best_bid = round(min(0.98 - spread, max(0.01, best_bid)), 2)
    best_ask = round(best_bid + spread, 2)
    if best_ask > 0.99:
        best_ask = 0.99
        best_bid = round(best_ask - spread, 2)
    return best_bid, best_ask


def mapped_book(
    best_bid: float,
    best_ask: float,
    donor: dict[str, Any],
) -> tuple[dict[float, float], dict[float, float]]:
    bids = {
        round(best_bid - int(offset) * GRID, 2): float(qty)
        for offset, qty in donor["nextBidShape"]
        if 0.01 <= round(best_bid - int(offset) * GRID, 2) < best_ask and float(qty) > EPS
    }
    asks = {
        round(best_ask + int(offset) * GRID, 2): float(qty)
        for offset, qty in donor["nextAskShape"]
        if best_bid < round(best_ask + int(offset) * GRID, 2) <= 0.99 and float(qty) > EPS
    }
    if best_bid not in bids:
        bids[best_bid] = max(EPS, float(donor["nextBidShape"][0][1]))
    if best_ask not in asks:
        asks[best_ask] = max(EPS, float(donor["nextAskShape"][0][1]))
    return bids, asks


def current_state(book: dict[str, dict[float, float]], timestamp: int, end_ms: int) -> dict[str, float]:
    best_bid = max(book["bids"])
    best_ask = min(book["asks"])
    bid_qty = float(book["bids"][best_bid])
    ask_qty = float(book["asks"][best_ask])
    total = bid_qty + ask_qty
    return {
        "timestamp": float(timestamp),
        "secondsLeft": max(0.0, (end_ms - timestamp) / 1000.0),
        "spread": float(max(1, round((best_ask - best_bid) / GRID))),
        "bidQty": bid_qty,
        "askQty": ask_qty,
        "totalBest": total,
        "imbalance": (bid_qty - ask_qty) / total,
    }


def add_depth_diff(
    out: list[np.void],
    timestamp: int,
    old: dict[float, float],
    new: dict[float, float],
    flag: int,
) -> None:
    for price in sorted(set(old) | set(new)):
        old_qty = float(old.get(price, 0.0))
        new_qty = float(new.get(price, 0.0))
        if abs(old_qty - new_qty) > EPS:
            out.append(ex.event_row(ex.DEPTH_EVENT | flag, timestamp, price, new_qty))


def generate_feed(
    initial: dict[str, Any],
    checkpoint: dict[str, Any],
    sampler: TransitionSampler,
    quartiles: tuple[float, float, float],
    levels: int,
    rng: np.random.Generator,
    max_steps: int = 2500,
) -> tuple[np.ndarray, dict[str, Any]]:
    checkpoint_ms = int(checkpoint["sampledAtMs"])
    end_ms = int(round(checkpoint_ms + float(checkpoint["secondsLeft"]) * 1000.0))
    up_quote = round(float(checkpoint["predictUpBid"]) - GRID, 2)
    down_quote_native = round(1.0 - (float(checkpoint["predictDownBid"]) - GRID), 2)
    bids = dict(initial["bids"])
    asks = dict(initial["asks"])
    if up_quote in initial["bids"]:
        bids[up_quote] = float(initial["bids"][up_quote])
    if down_quote_native in initial["asks"]:
        asks[down_quote_native] = float(initial["asks"][down_quote_native])
    book = {"bids": bids, "asks": asks}
    out: list[np.void] = []
    for price, qty in bids.items():
        out.append(ex.event_row(ex.DEPTH_SNAPSHOT_EVENT | ex.BUY_EVENT, checkpoint_ms, price, qty))
    for price, qty in asks.items():
        out.append(ex.event_row(ex.DEPTH_SNAPSHOT_EVENT | ex.SELL_EVENT, checkpoint_ms, price, qty))
    timestamp = checkpoint_ms
    backoffs: Counter[str] = Counter()
    generated_trades = 0
    generated_trade_qty = 0.0
    steps = 0
    while timestamp < end_ms and steps < max_steps:
        state = current_state(book, timestamp, end_ms)
        donor, backoff = sampler.sample(state_key(state, quartiles), rng)
        backoffs[backoff] += 1
        next_timestamp = timestamp + max(1, int(donor["gapMs"]))
        if next_timestamp > end_ms:
            break
        current_best_bid = max(book["bids"])
        current_best_ask = min(book["asks"])
        next_best_bid, next_best_ask = bounded_best(
            current_best_bid + int(donor["bidMoveTicks"]) * GRID,
            int(donor["nextSpread"]),
        )
        next_bids, next_asks = mapped_book(next_best_bid, next_best_ask, donor)
        trade_ms = max(timestamp + 1, next_timestamp - 1)
        for trade_index, trade in enumerate(donor["trades"]):
            if trade["aggressor"] == "BUY":
                price = round(current_best_ask + int(trade["relativeTicks"]) * GRID, 2)
                flag = ex.BUY_EVENT
            else:
                price = round(current_best_bid - int(trade["relativeTicks"]) * GRID, 2)
                flag = ex.SELL_EVENT
            if not 0.01 <= price <= 0.99:
                continue
            event = ex.event_row(ex.TRADE_EVENT | flag, trade_ms, price, float(trade["qty"]))
            nanos = min(trade_index, 999) * 1000
            event["exch_ts"] += nanos
            event["local_ts"] += nanos
            out.append(event)
            generated_trades += 1
            generated_trade_qty += float(trade["qty"])
        add_depth_diff(out, next_timestamp, book["bids"], next_bids, ex.BUY_EVENT)
        add_depth_diff(out, next_timestamp, book["asks"], next_asks, ex.SELL_EVENT)
        book = {"bids": next_bids, "asks": next_asks}
        timestamp = next_timestamp
        steps += 1
    events = np.asarray(out, dtype=ex.event_dtype)
    events.sort(order=["local_ts", "exch_ts"])
    samples = sum(backoffs.values())
    meta = {
        "version": "HFT_NET_QR_RELATIVE_L2_FEED_V1",
        "firstReceivedMs": checkpoint_ms,
        "lastReceivedMs": timestamp,
        "events": len(events),
        "generatedSteps": steps,
        "generatedTrades": generated_trades,
        "generatedTradeQty": generated_trade_qty,
        "conditioningUsage": {key: value / samples for key, value in sorted(backoffs.items())} if samples else {},
        "winnerSettlementPnlUsed": False,
    }
    return events, meta


def finite_percentile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q)) if values else 0.0


def decide(prereg_path: Path = PREREG, decisions_path: Path = DECISIONS) -> dict[str, Any]:
    if decisions_path.exists():
        raise RuntimeError(f"locked decision artifact already exists: {decisions_path}")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    train_ids = [int(value) for value in prereg["cohort"]["worldModelTrainMarkets"]]
    blind_ids = [int(value) for value in prereg["cohort"]["blindRealTapeMarkets"]]
    levels = int(prereg["worldModel"]["relativeBookLevelsPerSide"])
    paths = int(prereg["worldModel"]["pathsPerBlindMarket"])
    seed = int(prereg["worldModel"]["randomSeed"])
    train_series = {market_id: market_series(market_id, levels) for market_id in train_ids}
    train_totals = [
        float(row["totalBest"])
        for market_id in train_ids
        for row in train_series[market_id]["snapshots"]
    ]
    quartiles = (
        percentile(train_totals, 0.25),
        percentile(train_totals, 0.50),
        percentile(train_totals, 0.75),
    )
    transitions = train_transitions(train_series, train_ids, quartiles, levels)
    sampler = TransitionSampler(transitions, conditional=True)
    rows: list[dict[str, Any]] = []
    for market_index, market_id in enumerate(blind_ids):
        checkpoint = selected_checkpoint(market_id)
        checkpoint_ms = int(checkpoint["sampledAtMs"])
        prefix = market_series(market_id, levels, cutoff_ms=checkpoint_ms)
        initial = prefix["snapshots"][-1]
        rng = np.random.default_rng(seed + market_index * 10_000)
        floors: list[float] = []
        maker_shares: list[float] = []
        taker_shares: list[float] = []
        violations = 0
        conditioning: Counter[str] = Counter()
        for _path_index in range(paths):
            events, feed = generate_feed(initial, checkpoint, sampler, quartiles, levels, rng)
            result = run_offset(
                market_id,
                1,
                checkpoint_override=checkpoint,
                events_override=events,
                feed_override=feed,
                winner_audit=False,
            )
            actual = result["actualExecution"]
            floors.append(float(actual["worstCaseFloor"]))
            maker_shares.append(float(actual["makerUp"] + actual["makerDown"]))
            taker_shares.append(float(actual["takerUp"] + actual["takerDown"]))
            violations += int(result["cycleInvariantViolationCount"])
            for key, rate in feed["conditioningUsage"].items():
                conditioning[key] += float(rate)
        mean_floor = float(np.mean(floors))
        action = "CONTINGENT_PAIR_OFFSET1" if mean_floor > 0.0 else "WAIT"
        rows.append(
            {
                "marketId": market_id,
                "checkpointMs": checkpoint_ms,
                "secondsLeft": float(checkpoint["secondsLeft"]),
                "prefixAudit": {
                    "maximumUsedUpdateMs": prefix["maximumUsedUpdateMs"],
                    "maximumUsedTradeMs": prefix["maximumUsedTradeMs"],
                    "strictPastUpdate": prefix["maximumUsedUpdateMs"] <= checkpoint_ms,
                    "strictPastTrade": prefix["maximumUsedTradeMs"] is None or prefix["maximumUsedTradeMs"] <= checkpoint_ms,
                },
                "syntheticPaths": paths,
                "syntheticMeanTerminalFloor": mean_floor,
                "syntheticMedianTerminalFloor": float(np.median(floors)),
                "syntheticP10TerminalFloor": finite_percentile(floors, 0.10),
                "syntheticPositiveRate": float(np.mean([value > EPS for value in floors])),
                "syntheticTailRate": float(np.mean([value < -EPS for value in floors])),
                "syntheticNoFillRate": float(np.mean([abs(value) <= EPS for value in floors])),
                "syntheticMeanMakerFilledShares": float(np.mean(maker_shares)),
                "syntheticMeanTakerFilledShares": float(np.mean(taker_shares)),
                "syntheticLifecycleViolations": violations,
                "meanConditioningUsage": {key: value / paths for key, value in sorted(conditioning.items())},
                "lockedAction": action,
            }
        )
        print(
            json.dumps(
                {
                    "phase": "decide",
                    "progress": f"{market_index + 1}/{len(blind_ids)}",
                    "marketId": market_id,
                    "syntheticMeanFloor": mean_floor,
                    "syntheticTailRate": rows[-1]["syntheticTailRate"],
                    "lockedAction": action,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
    artifact = {
        "version": "HFT_NET_QR_SAFE_FLOOR_BLIND5_V1_DECISIONS",
        "researchOnly": True,
        "preregistration": prereg_path.name,
        "preregistrationSha256": digest(prereg_path),
        "blindProtocolPhase": (
            "POST_REVEAL_FROZEN_RULE_DIAGNOSTIC"
            if prereg.get("postRevealDiagnostic")
            else "DECISIONS_LOCKED_BEFORE_REAL_TAPE_ACTION_REVEAL"
        ),
        "fullBlindTapeActionOutcomesLoaded": False,
        "generator": {
            "trainMarkets": train_ids,
            "trainTransitions": len(transitions),
            "trainOnlyTotalBestQuartiles": list(quartiles),
            "relativeBookLevelsPerSide": levels,
            "pathsPerMarket": paths,
            "randomSeed": seed,
        },
        "rows": rows,
        "waitActRate": {
            "wait": sum(row["lockedAction"] == "WAIT" for row in rows) / len(rows),
            "act": sum(row["lockedAction"] != "WAIT" for row in rows) / len(rows),
        },
    }
    decisions_path.write_text(json.dumps(artifact, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"decisions": str(decisions_path), "waitActRate": artifact["waitActRate"]}, ensure_ascii=False, indent=2))
    return artifact


def compact_real_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "marketId": int(result["marketId"]),
        "winnerAuditOnly": result["winnerAuditOnly"],
        "actualExecution": result["actualExecution"],
        "fills": result["fills"],
        "lifecycle": result["lifecycle"],
        "cycleInvariantViolations": result["cycleInvariantViolations"],
        "cycleInvariantViolationCount": int(result["cycleInvariantViolationCount"]),
    }


def reveal(
    prereg_path: Path = PREREG,
    decisions_path: Path = DECISIONS,
    report_path: Path = REPORT,
) -> dict[str, Any]:
    if not decisions_path.exists():
        raise RuntimeError("decision artifact must exist before reveal")
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
    if decisions.get("fullBlindTapeActionOutcomesLoaded") is not False:
        raise RuntimeError("decision artifact does not preserve blind phase marker")
    if decisions.get("preregistrationSha256") != digest(prereg_path):
        raise RuntimeError("preregistration changed after decisions were locked")
    blind_ids = [int(value) for value in prereg["cohort"]["blindRealTapeMarkets"]]
    decision_by_market = {int(row["marketId"]): row for row in decisions["rows"]}
    if sorted(decision_by_market) != sorted(blind_ids):
        raise RuntimeError("decision cohort mismatch")

    real_by_market: dict[int, dict[str, Any]] = {}
    for index, market_id in enumerate(blind_ids):
        real_by_market[market_id] = run_offset(market_id, 1)
        actual = real_by_market[market_id]["actualExecution"]
        print(
            json.dumps(
                {
                    "phase": "reveal",
                    "progress": f"{index + 1}/{len(blind_ids)}",
                    "marketId": market_id,
                    "lockedAction": decision_by_market[market_id]["lockedAction"],
                    "realFloor": actual["worstCaseFloor"],
                    "realPnl": actual["realizedPnl"],
                    "violations": real_by_market[market_id]["cycleInvariantViolationCount"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    rows: list[dict[str, Any]] = []
    for market_id in blind_ids:
        decision = decision_by_market[market_id]
        real = real_by_market[market_id]
        actual = real["actualExecution"]
        act = decision["lockedAction"] != "WAIT"
        rows.append(
            {
                "marketId": market_id,
                "lockedAction": decision["lockedAction"],
                "syntheticMeanTerminalFloor": decision["syntheticMeanTerminalFloor"],
                "syntheticP10TerminalFloor": decision["syntheticP10TerminalFloor"],
                "syntheticTailRate": decision["syntheticTailRate"],
                "policyTerminalFloor": float(actual["worstCaseFloor"]) if act else 0.0,
                "policySettledRealizedPnlAudit": float(actual["realizedPnl"]) if act else 0.0,
                "realActionCounterfactual": compact_real_result(real),
            }
        )
    act_rows = [row for row in rows if row["lockedAction"] != "WAIT"]
    policy_floor = sum(float(row["policyTerminalFloor"]) for row in rows)
    policy_pnl = sum(float(row["policySettledRealizedPnlAudit"]) for row in rows)
    real_violations = sum(
        int(row["realActionCounterfactual"]["cycleInvariantViolationCount"]) for row in act_rows
    )
    always_floor = sum(float(real_by_market[market_id]["actualExecution"]["worstCaseFloor"]) for market_id in blind_ids)
    always_pnl = sum(float(real_by_market[market_id]["actualExecution"]["realizedPnl"]) for market_id in blind_ids)
    positive_floor_markets = [
        market_id
        for market_id in blind_ids
        if float(real_by_market[market_id]["actualExecution"]["worstCaseFloor"]) > EPS
    ]
    oracle_floor = sum(
        max(0.0, float(real_by_market[market_id]["actualExecution"]["worstCaseFloor"]))
        for market_id in blind_ids
    )
    oracle_pnl = sum(
        float(real_by_market[market_id]["actualExecution"]["realizedPnl"])
        for market_id in positive_floor_markets
    )
    gates = {
        "minimumActMarkets": len(act_rows) >= int(prereg["primaryGate"]["minimumActMarkets"]),
        "positiveTerminalWorstCaseFloor": policy_floor
        > float(prereg["primaryGate"]["learnedPolicyTerminalWorstCaseFloorSumMinExclusive"]),
        "positiveSettledRealizedPnlAudit": policy_pnl
        > float(prereg["primaryGate"]["learnedPolicySettledRealizedPnlAuditSumMinExclusive"]),
        "realActLifecycleViolations": real_violations
        <= int(prereg["primaryGate"]["maximumRealActLifecycleViolations"]),
    }
    primary_keep = all(gates.values())
    discrimination_contract = prereg.get("secondaryGateDiscrimination")
    rejected_negative_tail_markets = sum(
        row["lockedAction"] == "WAIT"
        and float(row["realActionCounterfactual"]["actualExecution"]["worstCaseFloor"]) < -EPS
        for row in rows
    )
    delta_vs_always_act = policy_floor - always_floor
    discrimination_gates: dict[str, bool] | None = None
    if discrimination_contract is not None:
        discrimination_gates = {
            "positiveDeltaVsAlwaysAct": delta_vs_always_act
            > float(discrimination_contract["minimumDeltaVsAlwaysActExclusive"]),
            "rejectedNegativeTailMarkets": rejected_negative_tail_markets
            >= int(discrimination_contract["minimumRejectedNegativeTailMarkets"]),
        }
    discrimination_keep = discrimination_gates is None or all(discrimination_gates.values())
    if not primary_keep:
        decision = "REJECT_NET_QR_SAFE_FLOOR_GATE_V1"
    elif discrimination_keep:
        decision = "KEEP_FOR_LARGER_BLIND_REPLICATION"
    else:
        decision = "NEED_MORE_DATA_NET_QR_GATE"
    report = {
        "version": "HFT_NET_QR_SAFE_FLOOR_BLIND5_V1_REPORT",
        "researchOnly": True,
        "preregistration": prereg_path.name,
        "preregistrationSha256": digest(prereg_path),
        "decisionArtifact": decisions_path.name,
        "decisionArtifactSha256": digest(decisions_path),
        "blindProtocol": {
            "decisionsLockedBeforeReveal": True,
            "decisionPhaseUsedOnlyStrictPastBlindPrefixes": all(
                row["prefixAudit"]["strictPastUpdate"] and row["prefixAudit"]["strictPastTrade"]
                for row in decisions["rows"]
            ),
            "thresholdSwept": False,
        },
        "cohort": prereg["cohort"],
        "fixedAction": prereg["fixedAction"],
        "executionSemantics": prereg["executionSemantics"],
        "waitActRate": {
            "actMarkets": len(act_rows),
            "waitMarkets": len(rows) - len(act_rows),
            "actRate": len(act_rows) / len(rows),
            "waitRate": 1.0 - len(act_rows) / len(rows),
        },
        "learnedPolicyRealizedValue": {
            "terminalWorstCaseFloor": policy_floor,
            "settledRealizedPnlAudit": policy_pnl,
            "realActLifecycleViolations": real_violations,
        },
        "alwaysActComparator": {
            "terminalWorstCaseFloor": always_floor,
            "settledRealizedPnlAudit": always_pnl,
            "policyDeltaTerminalFloorVsAlwaysAct": delta_vs_always_act,
        },
        "oracleValueCeiling": {
            "terminalWorstCaseFloor": oracle_floor,
            "settledRealizedPnlAudit": oracle_pnl,
            "actMarkets": len(positive_floor_markets),
            "actRate": len(positive_floor_markets) / len(blind_ids),
            "meaning": "Revealed WAIT plus positive-real-floor ACT; audit-only and not deployable.",
        },
        "primaryGates": gates,
        "secondaryGateDiscrimination": {
            "contract": discrimination_contract,
            "gates": discrimination_gates,
            "rejectedNegativeTailMarkets": rejected_negative_tail_markets,
        },
        "rows": rows,
        "decision": decision,
        "decisionReason": (
            "The locked nonzero ACT subset retained positive terminal floor and settled value with zero real lifecycle violations on the blind real-tape cohort."
            if decision == "KEEP_FOR_LARGER_BLIND_REPLICATION"
            else "The action subset retained positive value, but the gate did not beat always-ACT or did not reject a real negative tail. The action family remains viable while gate discrimination needs more data."
            if decision == "NEED_MORE_DATA_NET_QR_GATE"
            else "The blind real-tape cohort failed at least one locked ACT-rate, terminal-floor, settled-value, or lifecycle gate. Do not tune the generator or zero threshold on these revealed markets."
        ),
        "next": (
            "Freeze the generator and zero expected-floor rule, then run one larger chronological pre-official blind block before any official-forward consideration."
            if decision == "KEEP_FOR_LARGER_BLIND_REPLICATION"
            else "Do not tune this revealed block. Keep the action family separate from the unproven gate and collect a later blind block or pivot the reachability representation."
            if decision == "NEED_MORE_DATA_NET_QR_GATE"
            else "Reject this generated-value gate. Inspect structural mismatch between synthetic and real action outcomes, then pivot model class or data capture without threshold tuning."
        ),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "waitActRate": report["waitActRate"], "learnedPolicyRealizedValue": report["learnedPolicyRealizedValue"], "oracleValueCeiling": report["oracleValueCeiling"], "decision": report["decision"]}, ensure_ascii=False, indent=2))
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", choices=("decide", "reveal"), required=True)
    parser.add_argument("--prereg", type=Path, default=PREREG)
    parser.add_argument("--decisions", type=Path, default=DECISIONS)
    parser.add_argument("--report", type=Path, default=REPORT)
    args = parser.parse_args()
    if args.phase == "decide":
        decide(args.prereg, args.decisions)
    else:
        reveal(args.prereg, args.decisions, args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
