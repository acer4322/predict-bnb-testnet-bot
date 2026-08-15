from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sqlite3
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LIVE_DB = Path(
    os.environ.get("PREDICT_POLY_GAP_LIVE_DB", ROOT / "data" / "poly_gap_live.db")
)
DEFAULT_CROSS_DB = Path(
    os.environ.get("PREDICT_CROSS_ORACLE_DB", ROOT / "data" / "cross_oracle.db")
)
DEFAULT_OUTPUT_PREFIX = ROOT / "data" / "live_loss_attribution_report"

COMPLETED_STATES = {"CLOSED", "SETTLED"}
LOSS_EPSILON = -1e-9
DEFAULT_WINDOWS_HOURS = (1.0, 3.0, 6.0)
POST_SIGNAL_OFFSETS_MS = (1_000, 3_000, 5_000)
SAMPLE_NEAREST_TOLERANCE_MS = 1_000
LEAD_THRESHOLDS = (0.70, 0.80, 0.90)
LEAD_REARM_HYSTERESIS = 0.03
LEAD_MAX_PAIR_LAG_MS = 15_000
LEAD_TIE_MS = 300


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _int(value: Any) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    row = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (str(table),),
    ).fetchone()
    return row is not None


def _connect_readonly(path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    db.row_factory = sqlite3.Row
    return db


def _selected(up_mid: float | None, side: str) -> float | None:
    if up_mid is None:
        return None
    if str(side).upper() == "UP":
        return up_mid
    if str(side).upper() == "DOWN":
        return 1.0 - up_mid
    return None


def _nearest_sample(
    rows: list[dict[str, Any]],
    at_ms: int | None,
    *,
    tolerance_ms: int = SAMPLE_NEAREST_TOLERANCE_MS,
) -> dict[str, Any] | None:
    if at_ms is None or not rows:
        return None
    best = min(
        rows,
        key=lambda row: abs(int(row.get("observed_at_ms") or 0) - int(at_ms)),
    )
    delta = abs(int(best.get("observed_at_ms") or 0) - int(at_ms))
    if delta > tolerance_ms:
        return None
    result = dict(best)
    result["nearestDeltaMs"] = delta
    return result


def _trajectory_snapshot(
    rows: list[dict[str, Any]],
    *,
    at_ms: int | None,
    side: str,
) -> dict[str, Any] | None:
    sample = _nearest_sample(rows, at_ms)
    if sample is None:
        return None
    poly_up = _finite(sample.get("poly_up_mid"))
    binance_up = _finite(sample.get("binance_up_mid"))
    return {
        "observedAtMs": _int(sample.get("observed_at_ms")),
        "nearestDeltaMs": _int(sample.get("nearestDeltaMs")),
        "polyReceivedAtMs": _int(sample.get("poly_received_at_ms")),
        "binanceObservedAtMs": _int(sample.get("binance_observed_at_ms")),
        "polyUpMid": poly_up,
        "binanceUpMid": binance_up,
        "polySelectedMid": _selected(poly_up, side),
        "binanceSelectedMid": _selected(binance_up, side),
        "secondsLeftSkew": _finite(sample.get("seconds_left_skew")),
        "binanceBookAgeMs": _finite(sample.get("binance_book_age_ms")),
        "binanceBookSkewMs": _finite(sample.get("binance_book_skew_ms")),
    }


def _dedupe_series(
    rows: list[dict[str, Any]], *, time_key: str, value_key: str
) -> list[tuple[int, float]]:
    points: dict[int, float] = {}
    for row in rows:
        at = _int(row.get(time_key))
        value = _finite(row.get(value_key))
        if at is None or at <= 0 or value is None or not 0 <= value <= 1:
            continue
        points[at] = value
    return sorted(points.items())


def _crossings(
    series: list[tuple[int, float]], *, side: str, threshold: float
) -> list[int]:
    if not series:
        return []
    first = _selected(series[0][1], side)
    armed = bool(first is not None and first < threshold)
    reset_level = max(0.0, threshold - LEAD_REARM_HYSTERESIS)
    result: list[int] = []
    for at_ms, up_mid in series:
        value = _selected(up_mid, side)
        if value is None:
            continue
        if not armed and value <= reset_level + 1e-12:
            armed = True
        if armed and value + 1e-12 >= threshold:
            result.append(int(at_ms))
            armed = False
    return result


def _pair_crossings(poly_times: list[int], binance_times: list[int]) -> list[dict[str, Any]]:
    i = 0
    j = 0
    events: list[dict[str, Any]] = []
    while i < len(poly_times) and j < len(binance_times):
        poly_at = int(poly_times[i])
        binance_at = int(binance_times[j])
        delta = binance_at - poly_at
        if abs(delta) <= LEAD_MAX_PAIR_LAG_MS:
            if abs(delta) <= LEAD_TIE_MS:
                leader = "TIE"
            elif delta > 0:
                leader = "POLY"
            else:
                leader = "BINANCE"
            events.append(
                {
                    "leader": leader,
                    "polyAtMs": poly_at,
                    "binanceAtMs": binance_at,
                    "signedPolyLeadMs": delta,
                }
            )
            i += 1
            j += 1
        elif poly_at < binance_at:
            i += 1
        else:
            j += 1
    return events


def _market_lead_context(rows: list[dict[str, Any]]) -> dict[str, Any]:
    poly_series = _dedupe_series(rows, time_key="poly_received_at_ms", value_key="poly_up_mid")
    binance_series = _dedupe_series(
        rows, time_key="binance_observed_at_ms", value_key="binance_up_mid"
    )
    events: list[dict[str, Any]] = []
    for side in ("UP", "DOWN"):
        for threshold in LEAD_THRESHOLDS:
            pairs = _pair_crossings(
                _crossings(poly_series, side=side, threshold=threshold),
                _crossings(binance_series, side=side, threshold=threshold),
            )
            for event in pairs:
                events.append({"side": side, "milestone": threshold, **event})

    signed = [int(event["signedPolyLeadMs"]) for event in events]
    poly_first = sum(event["leader"] == "POLY" for event in events)
    binance_first = sum(event["leader"] == "BINANCE" for event in events)
    ties = sum(event["leader"] == "TIE" for event in events)
    if not signed:
        leader = "INSUFFICIENT"
    else:
        median = statistics.median(signed)
        if median > LEAD_TIE_MS:
            leader = "POLY"
        elif median < -LEAD_TIE_MS:
            leader = "BINANCE"
        else:
            leader = "TIE_MIXED"
    return {
        "samples": len(rows),
        "matchedMilestoneEvents": len(events),
        "polyFirstEvents": poly_first,
        "binanceFirstEvents": binance_first,
        "tieEvents": ties,
        "medianSignedPolyLeadMs": statistics.median(signed) if signed else None,
        "meanSignedPolyLeadMs": statistics.fmean(signed) if signed else None,
        "marketLeader": leader,
        "milestones": list(LEAD_THRESHOLDS),
    }


def _load_rounds(db: sqlite3.Connection, cutoff_ms: int) -> list[dict[str, Any]]:
    if not _table_exists(db, "poly_gap_live_rounds"):
        return []
    rows = db.execute(
        """SELECT * FROM poly_gap_live_rounds
             WHERE updated_at_ms >= ?
             ORDER BY updated_at_ms, id""",
        (int(cutoff_ms),),
    ).fetchall()
    return [dict(row) for row in rows]


def _load_attempts(
    db: sqlite3.Connection, round_ids: Iterable[int]
) -> dict[int, list[dict[str, Any]]]:
    ids = [int(value) for value in round_ids]
    if not ids or not _table_exists(db, "poly_gap_live_execution_attempts"):
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"""SELECT * FROM poly_gap_live_execution_attempts
              WHERE round_id IN ({placeholders})
              ORDER BY round_id, action, attempt_no, id""",
        ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["round_id"])].append(dict(row))
    return dict(grouped)


def _load_market_samples(
    db: sqlite3.Connection, market_ids: Iterable[int]
) -> dict[int, list[dict[str, Any]]]:
    ids = sorted(set(int(value) for value in market_ids if int(value) > 0))
    if not ids or not _table_exists(db, "poly_binance_lead_samples"):
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = db.execute(
        f"""SELECT * FROM poly_binance_lead_samples
              WHERE binance_market_id IN ({placeholders})
              ORDER BY binance_market_id, observed_at_ms""",
        ids,
    ).fetchall()
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["binance_market_id"])].append(dict(row))
    return dict(grouped)


def _attempt_summary(attempts: list[dict[str, Any]], action: str) -> dict[str, Any]:
    rows = [row for row in attempts if str(row.get("action") or "") == action]
    no_fills = [row for row in rows if str(row.get("outcome") or "") == "NO_FILL"]
    place_rtts = [
        value
        for row in rows
        if (value := _finite(row.get("place_rtt_ms"))) is not None
    ]
    local_gaps = [
        value
        for row in rows
        if (value := _finite(row.get("quote_response_to_place_start_ms"))) is not None
    ]
    combined = [
        value
        for row in rows
        if (value := _finite(row.get("quote_response_to_order_response_ms"))) is not None
    ]
    coverages = [
        value
        for row in rows
        if (value := _finite(row.get("coverage_ratio"))) is not None
    ]
    return {
        "attempts": len(rows),
        "noFills": len(no_fills),
        "first": dict(rows[0]) if rows else None,
        "last": dict(rows[-1]) if rows else None,
        "placeRttMs": {
            "median": statistics.median(place_rtts) if place_rtts else None,
            "max": max(place_rtts) if place_rtts else None,
        },
        "quoteResponseToPlaceStartMs": {
            "median": statistics.median(local_gaps) if local_gaps else None,
            "max": max(local_gaps) if local_gaps else None,
        },
        "quoteResponseToOrderResponseMs": {
            "median": statistics.median(combined) if combined else None,
            "max": max(combined) if combined else None,
        },
        "depthCoverageRatio": {
            "median": statistics.median(coverages) if coverages else None,
            "min": min(coverages) if coverages else None,
            "max": max(coverages) if coverages else None,
        },
    }


def _signal_trajectory(
    rows: list[dict[str, Any]], round_row: dict[str, Any]
) -> dict[str, Any]:
    side = str(round_row.get("side") or "")
    signal_at = _int(round_row.get("entry_signal_at_ms"))
    entry = _trajectory_snapshot(rows, at_ms=signal_at, side=side)
    post: dict[str, Any] = {}
    for offset in POST_SIGNAL_OFFSETS_MS:
        snapshot = _trajectory_snapshot(rows, at_ms=(signal_at + offset if signal_at else None), side=side)
        key = f"plus{offset // 1000}s"
        if snapshot is None:
            post[key] = None
            continue
        if entry is not None:
            for venue in ("poly", "binance"):
                start_value = _finite(entry.get(f"{venue}SelectedMid"))
                end_value = _finite(snapshot.get(f"{venue}SelectedMid"))
                snapshot[f"{venue}SelectedDeltaFromSignal"] = (
                    end_value - start_value
                    if start_value is not None and end_value is not None
                    else None
                )
        post[key] = snapshot
    return {"atSignal": entry, **post}


def _round_attribution(
    row: dict[str, Any],
    attempts: list[dict[str, Any]],
    market_samples: list[dict[str, Any]],
    lead_context: dict[str, Any],
) -> dict[str, Any]:
    round_id = int(row["id"])
    side = str(row.get("side") or "")
    shares = _finite(row.get("shares")) or 0.0
    pnl = _finite(row.get("pnl_usdt"))
    state = str(row.get("state") or "")
    buy = _attempt_summary(attempts, "BUY")
    sell = _attempt_summary(attempts, "SELL")

    first_buy = buy.get("first") if isinstance(buy.get("first"), dict) else {}
    first_sell = sell.get("first") if isinstance(sell.get("first"), dict) else {}
    last_sell = sell.get("last") if isinstance(sell.get("last"), dict) else {}

    entry_actual = _finite(row.get("entry_quote_average"))
    entry_reference = _finite(first_buy.get("expected_vwap"))
    if entry_reference is None:
        entry_reference = _finite(row.get("entry_binance_ask"))
    entry_price_deterioration = (
        entry_actual - entry_reference
        if entry_actual is not None and entry_reference is not None
        else None
    )
    entry_execution_drag = (
        max(0.0, entry_price_deterioration) * shares
        if entry_price_deterioration is not None and shares > 0
        else 0.0
    )

    first_sell_quote = _finite(first_sell.get("quote_average"))
    final_sell_quote = _finite(last_sell.get("quote_average"))
    if final_sell_quote is None:
        final_sell_quote = _finite(row.get("exit_quote_average"))
    exit_quote_drop = (
        first_sell_quote - final_sell_quote
        if first_sell_quote is not None and final_sell_quote is not None
        else None
    )
    exit_execution_drag = (
        max(0.0, exit_quote_drop) * shares
        if exit_quote_drop is not None and shares > 0
        else 0.0
    )

    first_sell_created = _int(first_sell.get("created_at_ms"))
    last_sell_response = _int(last_sell.get("place_response_at_ms"))
    if last_sell_response is None:
        last_sell_response = _int(last_sell.get("order_response_at_ms"))
    if last_sell_response is None:
        last_sell_response = _int(last_sell.get("created_at_ms"))
    exit_attempt_span_ms = (
        max(0, last_sell_response - first_sell_created)
        if first_sell_created is not None and last_sell_response is not None
        else None
    )
    exit_signal_at = _int(row.get("exit_signal_at_ms"))
    exit_signal_to_first_attempt_ms = (
        max(0, first_sell_created - exit_signal_at)
        if first_sell_created is not None and exit_signal_at is not None
        else None
    )

    entry_signal_at = _int(row.get("entry_signal_at_ms"))
    first_buy_created = _int(first_buy.get("created_at_ms"))
    entry_signal_to_first_attempt_ms = (
        max(0, first_buy_created - entry_signal_at)
        if first_buy_created is not None and entry_signal_at is not None
        else None
    )

    trajectory = _signal_trajectory(market_samples, row)
    plus3 = trajectory.get("plus3s") if isinstance(trajectory.get("plus3s"), dict) else {}
    poly_delta_3s = _finite(plus3.get("polySelectedDeltaFromSignal"))
    binance_delta_3s = _finite(plus3.get("binanceSelectedDeltaFromSignal"))
    signal_reversal_suspected = bool(
        (poly_delta_3s is not None and poly_delta_3s <= -0.08)
        or (binance_delta_3s is not None and binance_delta_3s <= -0.08)
    )

    loss_abs = abs(pnl) if pnl is not None and pnl < 0 else 0.0
    exit_execution_suspected = bool(
        int(sell.get("noFills") or 0) > 0
        or (exit_quote_drop is not None and exit_quote_drop >= 0.05)
        or (exit_attempt_span_ms is not None and exit_attempt_span_ms >= 1_000)
    )
    entry_execution_suspected = bool(
        entry_execution_drag >= max(0.02, loss_abs * 0.20)
        or (_finite((buy.get("placeRttMs") or {}).get("max")) or 0) >= 250
        or (_finite((buy.get("quoteResponseToPlaceStartMs") or {}).get("max")) or 0) >= 50
    )

    reasons: list[str] = []
    evidence: list[str] = []
    if state in COMPLETED_STATES and pnl is not None and pnl < LOSS_EPSILON:
        if exit_execution_suspected:
            reasons.append("EXIT_EXECUTION_DELAY")
            evidence.append(
                f"SELL no-fills={int(sell.get('noFills') or 0)}, "
                f"first-to-final quote drop={exit_quote_drop}, attempt span ms={exit_attempt_span_ms}"
            )
        if signal_reversal_suspected:
            reasons.append("SIGNAL_REVERSAL")
            evidence.append(
                f"3s selected-mid deltas: Poly={poly_delta_3s}, Binance={binance_delta_3s}"
            )
        if entry_execution_suspected:
            reasons.append("ENTRY_EXECUTION_DELAY")
            evidence.append(
                f"entry measured drag≈{entry_execution_drag:.4f} USDT; "
                f"place RTT max={(buy.get('placeRttMs') or {}).get('max')}ms"
            )
        if not reasons:
            reasons.append("UNRESOLVED_MARKET_OR_SIGNAL")
            evidence.append("available timing/trajectory evidence did not cross a diagnostic threshold")

    if len(reasons) > 1:
        primary = "MIXED"
        confidence = "MEDIUM"
    elif reasons:
        primary = reasons[0]
        confidence = "HIGH" if reasons[0] == "EXIT_EXECUTION_DELAY" and int(sell.get("noFills") or 0) > 0 else "MEDIUM"
    else:
        primary = None
        confidence = None

    measured_drag = entry_execution_drag + exit_execution_drag
    estimated_without_drag = pnl + measured_drag if pnl is not None else None

    return {
        "roundId": round_id,
        "marketId": int(row.get("market_id") or 0),
        "roundNo": int(row.get("round_no") or 0),
        "side": side,
        "state": state,
        "exitIntent": row.get("exit_intent"),
        "closeReason": row.get("close_reason"),
        "createdAtMs": _int(row.get("created_at_ms")),
        "updatedAtMs": _int(row.get("updated_at_ms")),
        "entrySignalAtMs": entry_signal_at,
        "exitSignalAtMs": exit_signal_at,
        "stakeUsdt": _finite(row.get("stake_usdt")),
        "shares": shares or None,
        "pnlUsdt": pnl,
        "entry": {
            "triggerAsk": _finite(row.get("entry_binance_ask")),
            "referenceVwap": entry_reference,
            "signedQuoteAverage": entry_actual,
            "priceDeterioration": entry_price_deterioration,
            "estimatedExecutionDragUsdt": entry_execution_drag,
            "signalToFirstAttemptMs": entry_signal_to_first_attempt_ms,
            "execution": buy,
        },
        "exit": {
            "firstSignedQuoteAverage": first_sell_quote,
            "finalSignedQuoteAverage": final_sell_quote,
            "firstToFinalQuoteDrop": exit_quote_drop,
            "estimatedExecutionDragUsdt": exit_execution_drag,
            "signalToFirstAttemptMs": exit_signal_to_first_attempt_ms,
            "attemptSpanMs": exit_attempt_span_ms,
            "execution": sell,
        },
        "trajectory": trajectory,
        "leadLagContext": lead_context,
        "attribution": {
            "primarySuspectedCause": primary,
            "confidence": confidence,
            "suspectedCauses": reasons,
            "evidence": evidence,
            "signalReversalSuspected": signal_reversal_suspected,
            "entryExecutionDelaySuspected": entry_execution_suspected,
            "exitExecutionDelaySuspected": exit_execution_suspected,
            "measuredExecutionDragUsdt": measured_drag,
            "estimatedPnlWithoutMeasuredExecutionDragUsdt": estimated_without_drag,
            "counterfactual": True,
        },
    }


def _execution_failure(row: dict[str, Any], attempts: list[dict[str, Any]]) -> dict[str, Any]:
    buy = _attempt_summary(attempts, "BUY")
    sell = _attempt_summary(attempts, "SELL")
    return {
        "roundId": int(row["id"]),
        "marketId": int(row.get("market_id") or 0),
        "roundNo": int(row.get("round_no") or 0),
        "side": str(row.get("side") or ""),
        "state": str(row.get("state") or ""),
        "createdAtMs": _int(row.get("created_at_ms")),
        "updatedAtMs": _int(row.get("updated_at_ms")),
        "errorKind": row.get("error_kind"),
        "errorMessage": row.get("error_message"),
        "closeReason": row.get("close_reason"),
        "entryQuoteAverage": _finite(row.get("entry_quote_average")),
        "buyExecution": buy,
        "sellExecution": sell,
    }


def _window_summary(
    *,
    hours: float,
    now_ms: int,
    analyses: list[dict[str, Any]],
    failures: list[dict[str, Any]],
) -> dict[str, Any]:
    cutoff = now_ms - int(hours * 3_600_000)
    completed = [
        row
        for row in analyses
        if int(row.get("updatedAtMs") or 0) >= cutoff
        and row.get("state") in COMPLETED_STATES
        and row.get("pnlUsdt") is not None
    ]
    losses = [row for row in completed if float(row["pnlUsdt"]) < LOSS_EPSILON]
    wins = [row for row in completed if float(row["pnlUsdt"]) > 1e-9]
    recent_failures = [row for row in failures if int(row.get("updatedAtMs") or 0) >= cutoff]
    cause_counts: Counter[str] = Counter()
    for row in losses:
        primary = (row.get("attribution") or {}).get("primarySuspectedCause")
        if primary:
            cause_counts[str(primary)] += 1
    pnl = sum(float(row["pnlUsdt"]) for row in completed)
    gross_loss = -sum(float(row["pnlUsdt"]) for row in losses)
    entry_drag = sum(
        float(((row.get("entry") or {}).get("estimatedExecutionDragUsdt") or 0.0))
        for row in losses
    )
    exit_drag = sum(
        float(((row.get("exit") or {}).get("estimatedExecutionDragUsdt") or 0.0))
        for row in losses
    )
    buy_no_fills = sum(
        int(((row.get("entry") or {}).get("execution") or {}).get("noFills") or 0)
        for row in completed
    ) + sum(int((row.get("buyExecution") or {}).get("noFills") or 0) for row in recent_failures)
    sell_no_fills = sum(
        int(((row.get("exit") or {}).get("execution") or {}).get("noFills") or 0)
        for row in completed
    ) + sum(int((row.get("sellExecution") or {}).get("noFills") or 0) for row in recent_failures)
    return {
        "hours": hours,
        "cutoffMs": cutoff,
        "completedRounds": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": len(wins) / len(completed) if completed else None,
        "pnlUsdt": pnl,
        "grossLossUsdt": gross_loss,
        "estimatedEntryExecutionDragOnLossesUsdt": entry_drag,
        "estimatedExitExecutionDragOnLossesUsdt": exit_drag,
        "estimatedMeasuredExecutionDragOnLossesUsdt": entry_drag + exit_drag,
        "measuredExecutionDragShareOfGrossLoss": (
            (entry_drag + exit_drag) / gross_loss if gross_loss > 0 else None
        ),
        "buyNoFills": buy_no_fills,
        "sellNoFills": sell_no_fills,
        "executionFailureRounds": len(recent_failures),
        "primarySuspectedCauseCounts": dict(cause_counts),
    }


def build_report(
    *,
    live_db_path: Path = DEFAULT_LIVE_DB,
    cross_db_path: Path = DEFAULT_CROSS_DB,
    hours: float = 6.0,
    windows_hours: tuple[float, ...] = DEFAULT_WINDOWS_HOURS,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = int(now_ms or time.time() * 1000)
    cutoff_ms = now_ms - int(float(hours) * 3_600_000)
    warnings: list[str] = []

    if not live_db_path.exists():
        raise FileNotFoundError(f"live DB not found: {live_db_path}")

    live_db = _connect_readonly(live_db_path)
    try:
        rounds = _load_rounds(live_db, cutoff_ms)
        attempts = _load_attempts(live_db, (int(row["id"]) for row in rounds))
    finally:
        live_db.close()

    market_ids = [int(row.get("market_id") or 0) for row in rounds]
    samples: dict[int, list[dict[str, Any]]] = {}
    if cross_db_path.exists():
        cross_db = _connect_readonly(cross_db_path)
        try:
            samples = _load_market_samples(cross_db, market_ids)
        finally:
            cross_db.close()
    else:
        warnings.append(f"cross-oracle DB not found: {cross_db_path}; trajectory/lead attribution unavailable")

    lead_by_market = {
        market_id: _market_lead_context(rows)
        for market_id, rows in samples.items()
    }
    analyses: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for row in rounds:
        round_id = int(row["id"])
        market_id = int(row.get("market_id") or 0)
        state = str(row.get("state") or "")
        row_attempts = attempts.get(round_id, [])
        if state in COMPLETED_STATES:
            analyses.append(
                _round_attribution(
                    row,
                    row_attempts,
                    samples.get(market_id, []),
                    lead_by_market.get(
                        market_id,
                        {
                            "samples": 0,
                            "matchedMilestoneEvents": 0,
                            "marketLeader": "INSUFFICIENT",
                        },
                    ),
                )
            )
        elif state in {"REJECTED", "FAILED", "AMBIGUOUS", "HALTED"}:
            failures.append(_execution_failure(row, row_attempts))

    analyses.sort(key=lambda row: int(row.get("updatedAtMs") or 0))
    failures.sort(key=lambda row: int(row.get("updatedAtMs") or 0))
    loss_analyses = [
        row
        for row in analyses
        if row.get("pnlUsdt") is not None and float(row["pnlUsdt"]) < LOSS_EPSILON
    ]

    coverage_markets = sum(bool(samples.get(market_id)) for market_id in set(market_ids))
    return {
        "version": "live_loss_attribution_v1",
        "generatedAtMs": now_ms,
        "analysisHours": float(hours),
        "cutoffMs": cutoff_ms,
        "sources": {
            "liveDb": str(live_db_path),
            "crossOracleDb": str(cross_db_path),
            "roundsLoaded": len(rounds),
            "marketsReferenced": len(set(market_ids)),
            "marketsWithLeadSamples": coverage_markets,
            "leadSamplesLoaded": sum(len(rows) for rows in samples.values()),
        },
        "windows": {
            f"{hours_value:g}h": _window_summary(
                hours=hours_value,
                now_ms=now_ms,
                analyses=analyses,
                failures=failures,
            )
            for hours_value in sorted(set(windows_hours))
            if hours_value <= hours + 1e-12
        },
        "lossRounds": loss_analyses,
        "completedRounds": analyses,
        "executionFailures": failures,
        "warnings": warnings,
        "limitations": [
            "Execution drag is a diagnostic counterfactual, not an accounting identity.",
            "Entry drag compares the signed BUY average with the pre-attempt expected VWAP/trigger Ask.",
            "Exit drag compares the first and final signed SELL quote averages; it does not prove the first quote would have filled.",
            "Poly/Binance trajectory samples are aligned research observations, not a full historical order-book replay.",
            "Older attempts created before V34 may not contain exact place RTT or quote-response-to-place-start timing.",
        ],
    }


def _flatten_round(row: dict[str, Any]) -> dict[str, Any]:
    entry = row.get("entry") or {}
    exit_data = row.get("exit") or {}
    attribution = row.get("attribution") or {}
    lead = row.get("leadLagContext") or {}
    trajectory = row.get("trajectory") or {}
    signal = trajectory.get("atSignal") or {}
    plus3 = trajectory.get("plus3s") or {}
    return {
        "round_id": row.get("roundId"),
        "market_id": row.get("marketId"),
        "round_no": row.get("roundNo"),
        "side": row.get("side"),
        "state": row.get("state"),
        "exit_intent": row.get("exitIntent"),
        "pnl_usdt": row.get("pnlUsdt"),
        "entry_trigger_ask": entry.get("triggerAsk"),
        "entry_reference_vwap": entry.get("referenceVwap"),
        "entry_quote_average": entry.get("signedQuoteAverage"),
        "entry_estimated_drag_usdt": entry.get("estimatedExecutionDragUsdt"),
        "entry_buy_no_fills": (entry.get("execution") or {}).get("noFills"),
        "entry_place_rtt_max_ms": ((entry.get("execution") or {}).get("placeRttMs") or {}).get("max"),
        "entry_quote_to_place_start_max_ms": ((entry.get("execution") or {}).get("quoteResponseToPlaceStartMs") or {}).get("max"),
        "exit_first_quote_average": exit_data.get("firstSignedQuoteAverage"),
        "exit_final_quote_average": exit_data.get("finalSignedQuoteAverage"),
        "exit_quote_drop": exit_data.get("firstToFinalQuoteDrop"),
        "exit_estimated_drag_usdt": exit_data.get("estimatedExecutionDragUsdt"),
        "exit_sell_no_fills": (exit_data.get("execution") or {}).get("noFills"),
        "exit_attempt_span_ms": exit_data.get("attemptSpanMs"),
        "exit_place_rtt_max_ms": ((exit_data.get("execution") or {}).get("placeRttMs") or {}).get("max"),
        "poly_selected_at_signal": signal.get("polySelectedMid"),
        "binance_selected_at_signal": signal.get("binanceSelectedMid"),
        "poly_selected_delta_3s": plus3.get("polySelectedDeltaFromSignal"),
        "binance_selected_delta_3s": plus3.get("binanceSelectedDeltaFromSignal"),
        "market_leader": lead.get("marketLeader"),
        "median_signed_poly_lead_ms": lead.get("medianSignedPolyLeadMs"),
        "primary_suspected_cause": attribution.get("primarySuspectedCause"),
        "suspected_causes": "|".join(attribution.get("suspectedCauses") or []),
        "confidence": attribution.get("confidence"),
        "measured_execution_drag_usdt": attribution.get("measuredExecutionDragUsdt"),
        "estimated_pnl_without_measured_drag_usdt": attribution.get("estimatedPnlWithoutMeasuredExecutionDragUsdt"),
    }


def _flatten_failure(row: dict[str, Any]) -> dict[str, Any]:
    buy = row.get("buyExecution") or {}
    sell = row.get("sellExecution") or {}
    return {
        "round_id": row.get("roundId"),
        "market_id": row.get("marketId"),
        "round_no": row.get("roundNo"),
        "side": row.get("side"),
        "state": row.get("state"),
        "error_kind": row.get("errorKind"),
        "close_reason": row.get("closeReason"),
        "entry_quote_average": row.get("entryQuoteAverage"),
        "buy_attempts": buy.get("attempts"),
        "buy_no_fills": buy.get("noFills"),
        "buy_depth_coverage_min": (buy.get("depthCoverageRatio") or {}).get("min"),
        "buy_place_rtt_max_ms": (buy.get("placeRttMs") or {}).get("max"),
        "sell_attempts": sell.get("attempts"),
        "sell_no_fills": sell.get("noFills"),
        "sell_depth_coverage_min": (sell.get("depthCoverageRatio") or {}).get("min"),
        "sell_place_rtt_max_ms": (sell.get("placeRttMs") or {}).get("max"),
        "message": row.get("errorMessage"),
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def write_report(report: dict[str, Any], output_prefix: Path) -> dict[str, str]:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    rounds_csv = output_prefix.parent / f"{output_prefix.name}_rounds.csv"
    failures_csv = output_prefix.parent / f"{output_prefix.name}_execution_failures.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_csv(rounds_csv, [_flatten_round(row) for row in report.get("completedRounds") or []])
    _write_csv(failures_csv, [_flatten_failure(row) for row in report.get("executionFailures") or []])
    return {
        "json": str(json_path),
        "roundsCsv": str(rounds_csv),
        "executionFailuresCsv": str(failures_csv),
    }


def _parse_windows(raw: str, hours: float) -> tuple[float, ...]:
    values: list[float] = []
    for token in str(raw).split(","):
        try:
            value = float(token.strip())
        except ValueError:
            continue
        if value > 0 and value <= hours + 1e-12:
            values.append(value)
    if hours not in values:
        values.append(hours)
    return tuple(sorted(set(values)))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Join Live rounds/execution attempts with Poly-Binance trajectories and attribute recent PnL losses."
    )
    parser.add_argument("--hours", type=float, default=6.0, help="analysis lookback in hours (default: 6)")
    parser.add_argument("--windows", default="1,3,6", help="summary windows, comma separated hours")
    parser.add_argument("--live-db", type=Path, default=DEFAULT_LIVE_DB)
    parser.add_argument("--cross-db", type=Path, default=DEFAULT_CROSS_DB)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()

    if not math.isfinite(args.hours) or args.hours <= 0:
        parser.error("--hours must be a positive number")
    windows = _parse_windows(args.windows, args.hours)
    report = build_report(
        live_db_path=args.live_db,
        cross_db_path=args.cross_db,
        hours=args.hours,
        windows_hours=windows,
    )
    paths = write_report(report, args.output_prefix)
    print(json.dumps({"windows": report["windows"], "outputs": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
