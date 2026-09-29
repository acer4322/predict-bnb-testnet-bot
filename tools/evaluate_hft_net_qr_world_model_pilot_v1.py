from __future__ import annotations

import argparse
import bisect
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.predict_bot.execution_tape_archive_v1 import load_archive  # noqa: E402
from tools.hftbacktest_true_match_calibration_v0 import normalize_match  # noqa: E402


BASE = ROOT / "data" / "research" / "execution_aware_fill_lifecycle_v0"
TAPES = ROOT / "data" / "execution_tape_v1" / "markets"
PREREG = BASE / "hft_net_qr_world_model_pilot_v1_preregistered.json"
DEFAULT_OUTPUT = BASE / "hft_net_qr_world_model_pilot_v1_report.json"
EPS = 1e-9


def apply_update(bids: dict[float, float], asks: dict[float, float], row: list[Any]) -> None:
    if int(row[3]) == 1 and row[4] is not None and row[5] is not None:
        bids.clear()
        asks.clear()
        bids.update({float(p): float(q) for p, q in (row[4] or {}).items() if float(q) > EPS})
        asks.update({float(p): float(q) for p, q in (row[5] or {}).items() if float(q) > EPS})
        return
    changes = row[6] or {}
    for side, book in (("bids", bids), ("asks", asks)):
        for item in changes.get(side, []) or []:
            price, _before, after, _delta = map(float, item)
            if after > EPS:
                book[price] = after
            else:
                book.pop(price, None)


def best_snapshot(bids: dict[float, float], asks: dict[float, float], timestamp: int, end_ms: int) -> dict[str, float] | None:
    live_bids = [(p, q) for p, q in bids.items() if q > EPS]
    live_asks = [(p, q) for p, q in asks.items() if q > EPS]
    if not live_bids or not live_asks:
        return None
    bid, bid_qty = max(live_bids, key=lambda row: row[0])
    ask, ask_qty = min(live_asks, key=lambda row: row[0])
    if ask <= bid or bid_qty + ask_qty <= EPS:
        return None
    return {
        "timestamp": float(timestamp),
        "secondsLeft": max(0.0, (end_ms - timestamp) / 1000.0),
        "spread": float(max(1, round((ask - bid) / 0.01))),
        "bidQty": float(bid_qty),
        "askQty": float(ask_qty),
        "totalBest": float(bid_qty + ask_qty),
        "imbalance": float((bid_qty - ask_qty) / (bid_qty + ask_qty)),
        "tradeCount": 0.0,
        "tradeQty": 0.0,
    }


def load_series(market_id: int) -> dict[str, Any]:
    tape = load_archive(TAPES / f"{market_id}.json.xz")
    updates = sorted(tape.get("updates") or [], key=lambda row: (int(row[1]), int(row[0])))
    if not updates:
        raise RuntimeError(f"market {market_id} has no updates")
    end_ms = int((tape.get("market") or {}).get("window_end_ms") or max(int(row[1]) for row in updates))
    bids: dict[float, float] = {}
    asks: dict[float, float] = {}
    snapshots: list[dict[str, float]] = []
    index = 0
    while index < len(updates):
        timestamp = int(updates[index][1])
        while index < len(updates) and int(updates[index][1]) == timestamp:
            apply_update(bids, asks, updates[index])
            index += 1
        snapshot = best_snapshot(bids, asks, timestamp, end_ms)
        if snapshot is not None:
            snapshots.append(snapshot)
    if len(snapshots) < 2:
        raise RuntimeError(f"market {market_id} has fewer than two valid snapshots")

    trades: list[dict[str, float]] = []
    for raw in tape.get("matches") or []:
        normalized = normalize_match(raw)
        if normalized is None:
            continue
        trades.append({"timestamp": float(int(normalized["tsMs"]) + 500), "qty": float(normalized["qty"])})
    trades.sort(key=lambda row: row["timestamp"])
    trade_times = [row["timestamp"] for row in trades]
    prefix_qty = [0.0]
    for row in trades:
        prefix_qty.append(prefix_qty[-1] + float(row["qty"]))
    for left, right in zip(snapshots, snapshots[1:]):
        lo = bisect.bisect_right(trade_times, left["timestamp"])
        hi = bisect.bisect_right(trade_times, right["timestamp"])
        right["tradeCount"] = float(hi - lo)
        right["tradeQty"] = float(prefix_qty[hi] - prefix_qty[lo])
    return {"marketId": market_id, "endMs": end_ms, "snapshots": snapshots}


def percentile(values: list[float], q: float) -> float:
    return float(np.quantile(np.asarray(values, dtype=float), q))


def time_bin(seconds_left: float) -> int:
    return min(5, max(0, int(seconds_left // 50.0)))


def spread_bin(spread: float) -> int:
    return min(6, max(1, int(round(spread))))


def imbalance_bin(imbalance: float) -> int:
    return bisect.bisect_right((-0.6, -0.2, 0.2, 0.6), float(imbalance))


def depth_bin(total: float, quartiles: tuple[float, float, float]) -> int:
    return bisect.bisect_right(quartiles, float(total))


def state_key(snapshot: dict[str, float], quartiles: tuple[float, float, float]) -> tuple[int, int, int, int]:
    return (
        time_bin(snapshot["secondsLeft"]),
        spread_bin(snapshot["spread"]),
        imbalance_bin(snapshot["imbalance"]),
        depth_bin(snapshot["totalBest"], quartiles),
    )


def transition_rows(series: dict[str, Any], quartiles: tuple[float, float, float]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    snapshots = series["snapshots"]
    for current, nxt in zip(snapshots, snapshots[1:]):
        gap = float(nxt["timestamp"] - current["timestamp"])
        if gap <= 0:
            continue
        rows.append(
            {
                "marketId": int(series["marketId"]),
                "state": state_key(current, quartiles),
                "gapMs": gap,
                "bidLogRatio": float(math.log(max(nxt["bidQty"], EPS) / max(current["bidQty"], EPS))),
                "askLogRatio": float(math.log(max(nxt["askQty"], EPS) / max(current["askQty"], EPS))),
                "nextSpread": float(nxt["spread"]),
                "tradeCount": float(nxt["tradeCount"]),
                "tradeQty": float(nxt["tradeQty"]),
            }
        )
    return rows


class TransitionSampler:
    def __init__(self, transitions: list[dict[str, Any]], conditional: bool):
        self.conditional = conditional
        self.exact: dict[tuple[int, int, int, int], list[dict[str, Any]]] = defaultdict(list)
        self.tsi: dict[tuple[int, int, int], list[dict[str, Any]]] = defaultdict(list)
        self.ti: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        self.ts: dict[tuple[int, int], list[dict[str, Any]]] = defaultdict(list)
        self.time: dict[int, list[dict[str, Any]]] = defaultdict(list)
        self.all_rows = transitions
        for row in transitions:
            t, s, i, d = row["state"]
            self.exact[(t, s, i, d)].append(row)
            self.tsi[(t, s, i)].append(row)
            self.ti[(t, i)].append(row)
            self.ts[(t, s)].append(row)
            self.time[t].append(row)

    def sample(self, key: tuple[int, int, int, int], rng: np.random.Generator) -> tuple[dict[str, Any], str]:
        t, s, i, d = key
        candidates: list[tuple[str, list[dict[str, Any]], int]]
        if self.conditional:
            candidates = [
                ("exact", self.exact.get((t, s, i, d), []), 3),
                ("timeSpreadImbalance", self.tsi.get((t, s, i), []), 3),
                ("timeImbalance", self.ti.get((t, i), []), 5),
                ("timeSpread", self.ts.get((t, s), []), 5),
                ("time", self.time.get(t, []), 1),
            ]
        else:
            candidates = [("time", self.time.get(t, []), 1)]
        for name, rows, minimum in candidates:
            if len(rows) >= minimum:
                return rows[int(rng.integers(0, len(rows)))], name
        return self.all_rows[int(rng.integers(0, len(self.all_rows)))], "all"


def generate_path(
    initial: dict[str, float],
    end_ms: int,
    quartiles: tuple[float, float, float],
    total_bounds: tuple[float, float],
    sampler: TransitionSampler,
    rng: np.random.Generator,
    max_steps: int,
) -> tuple[list[dict[str, float]], Counter[str]]:
    current = dict(initial)
    current["tradeCount"] = 0.0
    current["tradeQty"] = 0.0
    path = [dict(current)]
    backoffs: Counter[str] = Counter()
    for _step in range(max_steps):
        if current["timestamp"] >= end_ms:
            break
        donor, level = sampler.sample(state_key(current, quartiles), rng)
        backoffs[level] += 1
        gap = max(1.0, float(donor["gapMs"]))
        next_timestamp = current["timestamp"] + gap
        if next_timestamp > end_ms:
            break
        bid_qty = max(EPS, current["bidQty"] * math.exp(float(np.clip(donor["bidLogRatio"], -4.0, 4.0))))
        ask_qty = max(EPS, current["askQty"] * math.exp(float(np.clip(donor["askLogRatio"], -4.0, 4.0))))
        total = bid_qty + ask_qty
        clipped_total = min(total_bounds[1], max(total_bounds[0], total))
        if total > EPS and clipped_total != total:
            scale = clipped_total / total
            bid_qty *= scale
            ask_qty *= scale
        total = bid_qty + ask_qty
        current = {
            "timestamp": float(next_timestamp),
            "secondsLeft": max(0.0, (end_ms - next_timestamp) / 1000.0),
            "spread": float(min(20, max(1, round(float(donor["nextSpread"]))))),
            "bidQty": float(bid_qty),
            "askQty": float(ask_qty),
            "totalBest": float(total),
            "imbalance": float((bid_qty - ask_qty) / total),
            "tradeCount": float(donor["tradeCount"]),
            "tradeQty": float(donor["tradeQty"]),
        }
        path.append(current)
    return path, backoffs


def categorical(values: list[int], size: int) -> np.ndarray:
    counts = np.bincount(np.asarray(values, dtype=int), minlength=size).astype(float)
    return counts / counts.sum() if counts.sum() > 0 else np.zeros(size, dtype=float)


def tvd(left: np.ndarray, right: np.ndarray) -> float:
    return float(0.5 * np.abs(left - right).sum())


def correlation(left: list[float], right: list[float]) -> float:
    if len(left) < 2 or len(right) < 2 or np.std(left) <= EPS or np.std(right) <= EPS:
        return 0.0
    return float(np.corrcoef(np.asarray(left), np.asarray(right))[0, 1])


def sign_code(value: float, tolerance: float = 1e-8) -> int:
    if value < -tolerance:
        return 0
    if value > tolerance:
        return 2
    return 1


def horizon_distribution(path: list[dict[str, float]], seconds: int) -> np.ndarray:
    times = [row["timestamp"] for row in path]
    categories: list[int] = []
    horizon_ms = seconds * 1000
    for index, row in enumerate(path):
        target = row["timestamp"] + horizon_ms
        if target > times[-1]:
            break
        nxt_index = bisect.bisect_left(times, target, lo=index + 1)
        if nxt_index >= len(path):
            continue
        nxt = path[nxt_index]
        spread_direction = sign_code(nxt["spread"] - row["spread"])
        imbalance_direction = sign_code(imbalance_bin(nxt["imbalance"]) - imbalance_bin(row["imbalance"]))
        depth_direction = sign_code(math.log(max(nxt["totalBest"], EPS) / max(row["totalBest"], EPS)), 0.01)
        categories.append(spread_direction * 9 + imbalance_direction * 3 + depth_direction)
    return categorical(categories, 27)


def summarize_path(path: list[dict[str, float]], quartiles: tuple[float, float, float], horizons: list[int]) -> dict[str, Any]:
    spread_dist = categorical([spread_bin(row["spread"]) - 1 for row in path], 6)
    imbalance_dist = categorical([imbalance_bin(row["imbalance"]) for row in path], 5)
    depth_dist = categorical([depth_bin(row["totalBest"], quartiles) for row in path], 4)
    gaps: list[float] = []
    bid_logs: list[float] = []
    ask_logs: list[float] = []
    active: list[float] = []
    bucket_counts: dict[int, float] = defaultdict(float)
    start = path[0]["timestamp"]
    for left, right in zip(path, path[1:]):
        gaps.append(float(right["timestamp"] - left["timestamp"]))
        bid_logs.append(float(math.log(max(right["bidQty"], EPS) / max(left["bidQty"], EPS))))
        ask_logs.append(float(math.log(max(right["askQty"], EPS) / max(left["askQty"], EPS))))
        is_active = float(right["tradeCount"] > 0)
        active.append(is_active)
        bucket = int((right["timestamp"] - start) // 5000.0)
        bucket_counts[bucket] += float(right["tradeCount"])
    max_bucket = max(bucket_counts, default=0)
    counts5s = [bucket_counts.get(index, 0.0) for index in range(max_bucket + 1)]
    fano = float(np.var(counts5s) / np.mean(counts5s)) if counts5s and np.mean(counts5s) > EPS else 0.0
    return {
        "spreadOccupancy": spread_dist,
        "imbalanceOccupancy": imbalance_dist,
        "totalBestOccupancy": depth_dist,
        "bidQueueDirection": categorical([sign_code(value) for value in bid_logs], 3),
        "askQueueDirection": categorical([sign_code(value) for value in ask_logs], 3),
        "gapP50Ms": float(np.median(gaps)) if gaps else 0.0,
        "gapP90Ms": percentile(gaps, 0.90) if gaps else 0.0,
        "tradeActiveRate": float(np.mean(active)) if active else 0.0,
        "tradeFano5s": fano,
        "tradeActiveLag1": correlation(active[:-1], active[1:]) if len(active) > 2 else 0.0,
        "crossSideDepthCorrelation": correlation(bid_logs, ask_logs),
        "horizonOccupancy": {str(horizon): horizon_distribution(path, horizon) for horizon in horizons},
        "steps": len(path) - 1,
        "durationSeconds": float((path[-1]["timestamp"] - path[0]["timestamp"]) / 1000.0),
    }


def metric_errors(actual: dict[str, Any], generated: dict[str, Any], horizons: list[int]) -> dict[str, float]:
    gap_errors = [
        abs(math.log((generated["gapP50Ms"] + 1.0) / (actual["gapP50Ms"] + 1.0))),
        abs(math.log((generated["gapP90Ms"] + 1.0) / (actual["gapP90Ms"] + 1.0))),
    ]
    horizon_tvds = [
        tvd(actual["horizonOccupancy"][str(horizon)], generated["horizonOccupancy"][str(horizon)])
        for horizon in horizons
    ]
    return {
        "spreadOccupancyTvd": tvd(actual["spreadOccupancy"], generated["spreadOccupancy"]),
        "imbalanceOccupancyTvd": tvd(actual["imbalanceOccupancy"], generated["imbalanceOccupancy"]),
        "totalBestOccupancyTvd": tvd(actual["totalBestOccupancy"], generated["totalBestOccupancy"]),
        "queueDirectionTvdMean": statistics.mean(
            [
                tvd(actual["bidQueueDirection"], generated["bidQueueDirection"]),
                tvd(actual["askQueueDirection"], generated["askQueueDirection"]),
            ]
        ),
        "interUpdateGapLogErrorMean": statistics.mean(gap_errors),
        "tradeActiveRateAbsError": abs(generated["tradeActiveRate"] - actual["tradeActiveRate"]),
        "tradeFano5sLogError": abs(math.log((generated["tradeFano5s"] + 1.0) / (actual["tradeFano5s"] + 1.0))),
        "tradeActiveLag1AbsError": abs(generated["tradeActiveLag1"] - actual["tradeActiveLag1"]),
        "crossSideDepthCorrelationAbsError": abs(
            generated["crossSideDepthCorrelation"] - actual["crossSideDepthCorrelation"]
        ),
        "transitionHorizonTvdMean": statistics.mean(horizon_tvds),
    }


def jsonable_summary(summary: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in summary.items():
        if isinstance(value, np.ndarray):
            result[key] = [float(item) for item in value]
        elif isinstance(value, dict):
            result[key] = {
                subkey: [float(item) for item in subvalue] if isinstance(subvalue, np.ndarray) else subvalue
                for subkey, subvalue in value.items()
            }
        else:
            result[key] = value
    return result


def support_audit(
    snapshots: list[dict[str, float]],
    exact_counts: Counter[tuple[int, int, int, int]],
    quartiles: tuple[float, float, float],
) -> dict[str, float]:
    supported = [exact_counts[state_key(row, quartiles)] >= 3 for row in snapshots]
    max_run = 0.0
    run_start: float | None = None
    previous_timestamp = snapshots[0]["timestamp"]
    for row, is_supported in zip(snapshots, supported):
        if not is_supported and run_start is None:
            run_start = row["timestamp"]
        if is_supported and run_start is not None:
            max_run = max(max_run, (previous_timestamp - run_start) / 1000.0)
            run_start = None
        previous_timestamp = row["timestamp"]
    if run_start is not None:
        max_run = max(max_run, (snapshots[-1]["timestamp"] - run_start) / 1000.0)
    return {
        "exactStateCoverage": float(np.mean(supported)),
        "maximumUnsupportedRunSeconds": float(max_run),
    }


def ensemble_audit(
    market: dict[str, Any],
    quartiles: tuple[float, float, float],
    total_bounds: tuple[float, float],
    sampler: TransitionSampler,
    paths: int,
    seed: int,
    max_steps: int,
    facts: dict[str, Any],
    horizons: list[int],
) -> dict[str, Any]:
    actual = summarize_path(market["snapshots"], quartiles, horizons)
    rng = np.random.default_rng(seed)
    errors: dict[str, list[float]] = {name: [] for name in facts}
    backoffs: Counter[str] = Counter()
    steps: list[int] = []
    durations: list[float] = []
    for _path_index in range(paths):
        path, path_backoffs = generate_path(
            market["snapshots"][0],
            int(market["endMs"]),
            quartiles,
            total_bounds,
            sampler,
            rng,
            max_steps,
        )
        generated = summarize_path(path, quartiles, horizons)
        path_errors = metric_errors(actual, generated, horizons)
        for name, value in path_errors.items():
            errors[name].append(float(value))
        backoffs.update(path_backoffs)
        steps.append(int(generated["steps"]))
        durations.append(float(generated["durationSeconds"]))
    error_summary = {
        name: {
            "median": float(np.median(values)),
            "p10": percentile(values, 0.10),
            "p90": percentile(values, 0.90),
            "tolerance": float(facts[name]["tolerance"]),
            "critical": bool(facts[name].get("critical")),
            "pass": float(np.median(values)) <= float(facts[name]["tolerance"]),
        }
        for name, values in errors.items()
    }
    total_samples = sum(backoffs.values())
    return {
        "actual": jsonable_summary(actual),
        "errors": error_summary,
        "meanNormalizedError": statistics.mean(
            row["median"] / row["tolerance"] for row in error_summary.values()
        ),
        "passedFacts": sum(bool(row["pass"]) for row in error_summary.values()),
        "criticalFactsPass": all(bool(row["pass"]) for row in error_summary.values() if row["critical"]),
        "generatedStepsMedian": float(np.median(steps)),
        "generatedDurationSecondsMedian": float(np.median(durations)),
        "conditioningUsage": {
            name: count / total_samples if total_samples else 0.0 for name, count in sorted(backoffs.items())
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    prereg = json.loads(PREREG.read_text(encoding="utf-8"))
    train_ids = [int(value) for value in prereg["cohort"]["trainMarkets"]]
    validation_ids = [int(value) for value in prereg["cohort"]["chronologicalValidationMarkets"]]
    markets = {market_id: load_series(market_id) for market_id in (*train_ids, *validation_ids)}
    train_totals = [
        row["totalBest"] for market_id in train_ids for row in markets[market_id]["snapshots"]
    ]
    quartiles = (
        percentile(train_totals, 0.25),
        percentile(train_totals, 0.50),
        percentile(train_totals, 0.75),
    )
    total_bounds = (percentile(train_totals, 0.005), percentile(train_totals, 0.995))
    train_transitions = [
        row
        for market_id in train_ids
        for row in transition_rows(markets[market_id], quartiles)
    ]
    exact_counts: Counter[tuple[int, int, int, int]] = Counter(row["state"] for row in train_transitions)
    conditional_sampler = TransitionSampler(train_transitions, conditional=True)
    baseline_sampler = TransitionSampler(train_transitions, conditional=False)
    generator_contract = prereg["lockedGenerator"]
    paths = int(generator_contract["pathsPerValidationMarket"])
    seed = int(generator_contract["randomSeed"])
    max_steps = int(generator_contract["maximumGeneratedSteps"])
    facts = prereg["lockedStylizedFacts"]
    horizons = [int(value) for value in facts["transitionHorizonTvdMean"]["horizonsSeconds"]]
    support_contract = prereg["lockedSupportGate"]

    validation_rows: list[dict[str, Any]] = []
    for offset, market_id in enumerate(validation_ids):
        market = markets[market_id]
        support = support_audit(market["snapshots"], exact_counts, quartiles)
        qr = ensemble_audit(
            market,
            quartiles,
            total_bounds,
            conditional_sampler,
            paths,
            seed + offset * 10_000,
            max_steps,
            facts,
            horizons,
        )
        baseline = ensemble_audit(
            market,
            quartiles,
            total_bounds,
            baseline_sampler,
            paths,
            seed + 100_000 + offset * 10_000,
            max_steps,
            facts,
            horizons,
        )
        support_pass = (
            support["exactStateCoverage"] >= float(support_contract["validationExactStateCoverageMin"])
            and support["maximumUnsupportedRunSeconds"] <= float(support_contract["maximumUnsupportedRunSecondsMax"])
        )
        baseline_improvement = 1.0 - qr["meanNormalizedError"] / baseline["meanNormalizedError"]
        baseline_pass = baseline_improvement >= 0.10
        absolute_pass = bool(qr["criticalFactsPass"] and qr["passedFacts"] >= 8)
        market_pass = bool(absolute_pass and support_pass and baseline_pass)
        validation_rows.append(
            {
                "marketId": market_id,
                "realSnapshots": len(market["snapshots"]),
                "realTransitions": len(market["snapshots"]) - 1,
                "support": {**support, "pass": support_pass},
                "conditionalGenerator": qr,
                "expiryOnlyBaseline": baseline,
                "normalizedErrorImprovementVsBaseline": baseline_improvement,
                "baselineImprovementPass": baseline_pass,
                "absoluteStylizedFactsPass": absolute_pass,
                "marketGatePass": market_pass,
            }
        )

    if all(row["marketGatePass"] for row in validation_rows):
        decision = "KEEP_NET_QR_WORLD_MODEL"
    elif all(row["absoluteStylizedFactsPass"] for row in validation_rows):
        decision = "NEED_MORE_QR_TRAIN_MARKETS"
    else:
        decision = "REJECT_NET_QR_WORLD_MODEL_V1"
    report = {
        "version": "HFT_NET_QR_WORLD_MODEL_PILOT_V1_REPORT",
        "researchOnly": True,
        "preregistration": PREREG.name,
        "hypothesis": prereg["hypothesis"],
        "noveltyBoundary": prereg["noveltyBoundary"],
        "cohort": prereg["cohort"],
        "inputSemantics": prereg["inputSemantics"],
        "generator": {
            **generator_contract,
            "trainTransitions": len(train_transitions),
            "trainDistinctExactStates": len(exact_counts),
            "trainOnlyTotalBestQuartiles": list(quartiles),
            "trainOnlyTotalBestClipBounds": list(total_bounds),
        },
        "baseline": prereg["lockedBaseline"],
        "validationMarkets": validation_rows,
        "summary": {
            "marketsPassing": sum(row["marketGatePass"] for row in validation_rows),
            "marketsWithAbsoluteStylizedFactsPassing": sum(
                row["absoluteStylizedFactsPass"] for row in validation_rows
            ),
            "conditionalMeanNormalizedError": statistics.mean(
                row["conditionalGenerator"]["meanNormalizedError"] for row in validation_rows
            ),
            "baselineMeanNormalizedError": statistics.mean(
                row["expiryOnlyBaseline"]["meanNormalizedError"] for row in validation_rows
            ),
            "meanImprovementVsBaseline": statistics.mean(
                row["normalizedErrorImprovementVsBaseline"] for row in validation_rows
            ),
            "decision": decision,
        },
        "executionSemantics": {
            "generatorOnly": True,
            "hftbacktestRun": False,
            "formalWaitIncludedInLaterPolicy": True,
            "waitActRate": None,
            "oracleValueCeiling": None,
            "learnedPolicyRealizedValue": None,
            "performanceBoundary": prereg["performanceBoundary"],
        },
        "decision": decision,
        "decisionReason": (
            "Both chronological validation markets passed absolute, support, and stronger-than-expiry-only gates. The generator may be used only for the next training-only reachability data pilot."
            if decision == "KEEP_NET_QR_WORLD_MODEL"
            else "Absolute queue dynamics were adequate, but current three-market training support or contextual improvement was insufficient. Add genuinely new pre-official training tapes before any policy fit."
            if decision == "NEED_MORE_QR_TRAIN_MARKETS"
            else "At least one chronological validation market failed a critical multi-step queue stylized fact or fewer than eight facts passed. Do not use this generator for policy training."
        ),
        "next": (
            "Generate a small action-conditioned reachability dataset for first-leg fill to opposite completion, then evaluate WAIT versus protected cycle on later untouched real-tape HftBacktest."
            if decision == "KEEP_NET_QR_WORLD_MODEL"
            else "Do not fit a WAIT/ACT policy from this generator. Inspect the failed critical facts and pivot the model class or data capture without threshold tuning."
        ),
    }
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(args.output), "summary": report["summary"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
