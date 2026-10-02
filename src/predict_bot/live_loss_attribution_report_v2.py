from __future__ import annotations

import argparse
import json
import math
import statistics
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from . import live_loss_attribution_report as v1


DEFAULT_OUTPUT_PREFIX = v1.ROOT / "data" / "live_loss_attribution_report_v2"
LATE_OFFSETS_MS = (10_000, 30_000, 60_000, 120_000)


def _late_trajectory(
    market_samples: list[dict[str, Any]], row: dict[str, Any]
) -> dict[str, Any]:
    signal_at = v1._int(row.get("entrySignalAtMs"))
    side = str(row.get("side") or "")
    at_signal = (row.get("trajectory") or {}).get("atSignal") or {}
    result: dict[str, Any] = {}
    for offset in LATE_OFFSETS_MS:
        snap = v1._trajectory_snapshot(
            market_samples,
            at_ms=(signal_at + offset if signal_at else None),
            side=side,
            tolerance_ms=1_500,
        )
        key = f"plus{offset // 1000}s"
        if snap is not None and at_signal:
            for venue in ("poly", "binance"):
                start = v1._finite(at_signal.get(f"{venue}SelectedMid"))
                end = v1._finite(snap.get(f"{venue}SelectedMid"))
                snap[f"{venue}SelectedDeltaFromSignal"] = (
                    end - start if start is not None and end is not None else None
                )
        result[key] = snap
    return result


def _market_churn_context(completed: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in completed:
        grouped[int(row.get("marketId") or 0)].append(row)
    contexts: dict[int, dict[str, Any]] = {}
    for market_id, rows in grouped.items():
        ordered = sorted(rows, key=lambda item: int(item.get("createdAtMs") or 0))
        pnl_values = [v1._finite(row.get("pnlUsdt")) for row in ordered]
        pnl_values = [value for value in pnl_values if value is not None]
        loss_count = sum(value < 0 for value in pnl_values)
        win_count = sum(value > 0 for value in pnl_values)
        reversal_exits = sum(
            str(row.get("exitIntent") or "") == "POLY_DIRECTION_FLIP"
            for row in ordered
        )
        side_switches = sum(
            str(ordered[index - 1].get("side") or "")
            != str(ordered[index].get("side") or "")
            for index in range(1, len(ordered))
        )
        total_pnl = sum(pnl_values)
        churn = bool(
            len(ordered) >= 3
            and loss_count >= 2
            and total_pnl < 0
            and reversal_exits >= 2
        )
        contexts[market_id] = {
            "marketId": market_id,
            "rounds": len(ordered),
            "wins": win_count,
            "losses": loss_count,
            "totalPnlUsdt": total_pnl,
            "reversalExits": reversal_exits,
            "sideSwitches": side_switches,
            "sameMarketChopReentrySuspected": churn,
            "roundIds": [int(row.get("roundId") or 0) for row in ordered],
        }
    return contexts


def _late_failure(row: dict[str, Any]) -> tuple[bool, list[str]]:
    trajectory = row.get("trajectory") or {}
    evidence: list[str] = []
    suspected = False
    for seconds in (10, 30, 60, 120):
        snap = trajectory.get(f"plus{seconds}s")
        if not isinstance(snap, dict):
            continue
        poly_delta = v1._finite(snap.get("polySelectedDeltaFromSignal"))
        binance_delta = v1._finite(snap.get("binanceSelectedDeltaFromSignal"))
        poly_mid = v1._finite(snap.get("polySelectedMid"))
        binance_mid = v1._finite(snap.get("binanceSelectedMid"))
        severe_delta = any(
            value is not None and value <= -0.10
            for value in (poly_delta, binance_delta)
        )
        below_even = any(
            value is not None and value < 0.48
            for value in (poly_mid, binance_mid)
        )
        if severe_delta or below_even:
            suspected = True
            evidence.append(
                f"T+{seconds}s Poly={poly_mid} (Δ={poly_delta}), Binance={binance_mid} (Δ={binance_delta})"
            )
    return suspected, evidence


def _reclassify_round(
    row: dict[str, Any], market_context: dict[str, Any]
) -> None:
    pnl = v1._finite(row.get("pnlUsdt"))
    if pnl is None or pnl >= v1.LOSS_EPSILON:
        row["marketContextV2"] = market_context
        return

    entry = row.get("entry") or {}
    exit_data = row.get("exit") or {}
    buy = entry.get("execution") or {}
    sell = exit_data.get("execution") or {}
    old = row.get("attribution") or {}

    loss_abs = abs(pnl)
    entry_drag = v1._finite(entry.get("estimatedExecutionDragUsdt")) or 0.0
    exit_drag = v1._finite(exit_data.get("estimatedExecutionDragUsdt")) or 0.0
    exit_drop = v1._finite(exit_data.get("firstToFinalQuoteDrop"))
    exit_span = v1._int(exit_data.get("attemptSpanMs"))
    sell_no_fills = int(sell.get("noFills") or 0)

    plus3 = (row.get("trajectory") or {}).get("plus3s") or {}
    poly_3 = v1._finite(plus3.get("polySelectedDeltaFromSignal"))
    binance_3 = v1._finite(plus3.get("binanceSelectedDeltaFromSignal"))
    early_reversal = any(
        value is not None and value <= -0.08 for value in (poly_3, binance_3)
    )
    late_failure, late_evidence = _late_failure(row)

    no_fill_no_damage = bool(
        sell_no_fills > 0
        and exit_drag <= 0.02
        and (exit_drop is None or exit_drop <= 0.01)
    )
    exit_delay = bool(
        (exit_drag >= max(0.03, loss_abs * 0.10))
        or (exit_drop is not None and exit_drop >= 0.03)
        or (
            sell_no_fills > 0
            and exit_span is not None
            and exit_span >= 1_000
            and exit_drop is not None
            and exit_drop > 0.01
        )
    )
    entry_delay = bool(
        entry_drag >= max(0.02, loss_abs * 0.20)
        or (v1._finite((buy.get("placeRttMs") or {}).get("max")) or 0) >= 350
        or (v1._finite((buy.get("quoteResponseToPlaceStartMs") or {}).get("max")) or 0) >= 75
    )
    churn = bool(market_context.get("sameMarketChopReentrySuspected"))
    binance_leading = str((row.get("leadLagContext") or {}).get("marketLeader") or "") == "BINANCE"

    material: list[str] = []
    contexts: list[str] = []
    evidence: list[str] = []
    if exit_delay:
        material.append("EXIT_EXECUTION_DELAY")
        evidence.append(
            f"SELL no-fills={sell_no_fills}, quote drop={exit_drop}, attempt span ms={exit_span}, drag≈{exit_drag:.4f} USDT"
        )
    elif no_fill_no_damage:
        contexts.append("EXECUTION_NO_FILL_NO_MEASURED_DAMAGE")
        evidence.append(
            f"SELL no-fill occurred but final quote did not materially deteriorate; quote drop={exit_drop}"
        )
    if early_reversal:
        material.append("SIGNAL_REVERSAL")
        evidence.append(f"T+3s selected deltas Poly={poly_3}, Binance={binance_3}")
    elif late_failure:
        material.append("LATE_SIGNAL_FAILURE")
        evidence.extend(late_evidence[:2])
    if entry_delay:
        material.append("ENTRY_EXECUTION_DELAY")
        evidence.append(f"entry measured drag≈{entry_drag:.4f} USDT")
    if churn:
        material.append("SAME_MARKET_CHOP_REENTRY")
        evidence.append(
            f"market rounds={market_context.get('rounds')}, losses={market_context.get('losses')}, "
            f"reversal exits={market_context.get('reversalExits')}, market PnL={market_context.get('totalPnlUsdt')}"
        )
    if binance_leading:
        contexts.append("BINANCE_LEADING_CONTEXT")
        lead = row.get("leadLagContext") or {}
        evidence.append(
            f"post-hoc market leader=BINANCE; median signed Poly lead={lead.get('medianSignedPolyLeadMs')}ms"
        )

    if len(material) > 1:
        primary = "MIXED"
        confidence = "MEDIUM"
    elif material:
        primary = material[0]
        confidence = "HIGH" if material[0] == "EXIT_EXECUTION_DELAY" and exit_drag > 0.05 else "MEDIUM"
    elif contexts:
        primary = contexts[0]
        confidence = "LOW" if primary == "BINANCE_LEADING_CONTEXT" else "MEDIUM"
    else:
        primary = "UNRESOLVED_MARKET_OR_SIGNAL"
        confidence = "LOW"
        evidence.append("V2 diagnostics still found no threshold-crossing cause")

    old.update(
        {
            "primarySuspectedCause": primary,
            "confidence": confidence,
            "suspectedCauses": material + contexts,
            "materialSuspectedCauses": material,
            "contextFlags": contexts,
            "evidence": evidence,
            "signalReversalSuspected": early_reversal,
            "lateSignalFailureSuspected": late_failure,
            "entryExecutionDelaySuspected": entry_delay,
            "exitExecutionDelaySuspected": exit_delay,
            "executionNoFillNoMeasuredDamage": no_fill_no_damage,
            "sameMarketChopReentrySuspected": churn,
            "binanceLeadingContext": binance_leading,
            "measuredExecutionDragUsdt": entry_drag + exit_drag,
            "estimatedPnlWithoutMeasuredExecutionDragUsdt": pnl + entry_drag + exit_drag,
            "counterfactual": True,
        }
    )
    row["attribution"] = old
    row["marketContextV2"] = market_context


def _window_summary_v2(hours: float, now_ms: int, rows: list[dict[str, Any]]) -> dict[str, Any]:
    cutoff = now_ms - int(hours * 3_600_000)
    completed = [
        row
        for row in rows
        if int(row.get("updatedAtMs") or 0) >= cutoff
        and row.get("state") in v1.COMPLETED_STATES
        and row.get("pnlUsdt") is not None
    ]
    losses = [row for row in completed if float(row["pnlUsdt"]) < v1.LOSS_EPSILON]
    wins = [row for row in completed if float(row["pnlUsdt"]) > 1e-9]
    primary = Counter(
        str((row.get("attribution") or {}).get("primarySuspectedCause"))
        for row in losses
        if (row.get("attribution") or {}).get("primarySuspectedCause")
    )
    all_flags = Counter()
    for row in losses:
        for flag in (row.get("attribution") or {}).get("suspectedCauses") or []:
            all_flags[str(flag)] += 1
    pnl = sum(float(row["pnlUsdt"]) for row in completed)
    return {
        "hours": hours,
        "cutoffMs": cutoff,
        "completedRounds": len(completed),
        "wins": len(wins),
        "losses": len(losses),
        "winRate": len(wins) / len(completed) if completed else None,
        "pnlUsdt": pnl,
        "primarySuspectedCauseCounts": dict(primary),
        "allSuspectedCauseAndContextCounts": dict(all_flags),
        "unresolvedLossShare": (
            primary.get("UNRESOLVED_MARKET_OR_SIGNAL", 0) / len(losses) if losses else None
        ),
    }


def build_report(
    *,
    live_db_path: Path = v1.DEFAULT_LIVE_DB,
    cross_db_path: Path = v1.DEFAULT_CROSS_DB,
    hours: float = 6.0,
    windows_hours: tuple[float, ...] = v1.DEFAULT_WINDOWS_HOURS,
    now_ms: int | None = None,
) -> dict[str, Any]:
    now_ms = int(now_ms or time.time() * 1000)
    report = v1.build_report(
        live_db_path=live_db_path,
        cross_db_path=cross_db_path,
        hours=hours,
        windows_hours=windows_hours,
        now_ms=now_ms,
    )
    completed = report.get("completedRounds") or []
    market_ids = sorted({int(row.get("marketId") or 0) for row in completed if int(row.get("marketId") or 0) > 0})
    samples: dict[int, list[dict[str, Any]]] = {}
    if cross_db_path.exists() and market_ids:
        db = v1._connect_readonly(cross_db_path)
        try:
            samples = v1._load_market_samples(db, market_ids)
        finally:
            db.close()

    for row in completed:
        market_id = int(row.get("marketId") or 0)
        row.setdefault("trajectory", {}).update(
            _late_trajectory(samples.get(market_id, []), row)
        )

    market_contexts = _market_churn_context(completed)
    for row in completed:
        _reclassify_round(
            row,
            market_contexts.get(
                int(row.get("marketId") or 0),
                {"sameMarketChopReentrySuspected": False},
            ),
        )

    report["version"] = "live_loss_attribution_v2"
    report["marketContextsV2"] = list(market_contexts.values())
    report["lossRounds"] = [
        row for row in completed
        if row.get("pnlUsdt") is not None and float(row["pnlUsdt"]) < v1.LOSS_EPSILON
    ]
    report["windowsV2"] = {
        f"{value:g}h": _window_summary_v2(value, now_ms, completed)
        for value in sorted(set(windows_hours))
        if value <= hours + 1e-12
    }
    report.setdefault("limitations", []).extend(
        [
            "BINANCE_LEADING_CONTEXT is post-hoc research context and must not be used as a live gate without a causal opening-window test.",
            "LATE_SIGNAL_FAILURE uses sparse 10/30/60/120s probability snapshots and is diagnostic, not a counterfactual execution replay.",
            "SAME_MARKET_CHOP_REENTRY is a market-level repeated-entry diagnostic and does not by itself prove each individual entry was wrong.",
        ]
    )
    return report


def _flatten(row: dict[str, Any]) -> dict[str, Any]:
    flat = v1._flatten_round(row)
    attr = row.get("attribution") or {}
    market = row.get("marketContextV2") or {}
    trajectory = row.get("trajectory") or {}
    flat.update(
        {
            "late_signal_failure": attr.get("lateSignalFailureSuspected"),
            "same_market_chop_reentry": attr.get("sameMarketChopReentrySuspected"),
            "binance_leading_context": attr.get("binanceLeadingContext"),
            "execution_no_fill_no_damage": attr.get("executionNoFillNoMeasuredDamage"),
            "market_rounds": market.get("rounds"),
            "market_total_pnl_usdt": market.get("totalPnlUsdt"),
            "market_reversal_exits": market.get("reversalExits"),
        }
    )
    for seconds in (10, 30, 60, 120):
        snap = trajectory.get(f"plus{seconds}s") or {}
        flat[f"poly_selected_delta_{seconds}s"] = snap.get("polySelectedDeltaFromSignal")
        flat[f"binance_selected_delta_{seconds}s"] = snap.get("binanceSelectedDeltaFromSignal")
    return flat


def write_report(report: dict[str, Any], output_prefix: Path) -> dict[str, str]:
    output_prefix.parent.mkdir(parents=True, exist_ok=True)
    json_path = output_prefix.with_suffix(".json")
    rounds_csv = output_prefix.parent / f"{output_prefix.name}_rounds.csv"
    failures_csv = output_prefix.parent / f"{output_prefix.name}_execution_failures.csv"
    markets_csv = output_prefix.parent / f"{output_prefix.name}_markets.csv"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    v1._write_csv(rounds_csv, [_flatten(row) for row in report.get("completedRounds") or []])
    v1._write_csv(failures_csv, [v1._flatten_failure(row) for row in report.get("executionFailures") or []])
    v1._write_csv(markets_csv, list(report.get("marketContextsV2") or []))
    return {
        "json": str(json_path),
        "roundsCsv": str(rounds_csv),
        "executionFailuresCsv": str(failures_csv),
        "marketsCsv": str(markets_csv),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="V2 Live loss attribution with late-signal, churn and leader-regime context.")
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--windows", default="1,3,6")
    parser.add_argument("--live-db", type=Path, default=v1.DEFAULT_LIVE_DB)
    parser.add_argument("--cross-db", type=Path, default=v1.DEFAULT_CROSS_DB)
    parser.add_argument("--output-prefix", type=Path, default=DEFAULT_OUTPUT_PREFIX)
    args = parser.parse_args()
    if not math.isfinite(args.hours) or args.hours <= 0:
        parser.error("--hours must be positive")
    windows = v1._parse_windows(args.windows, args.hours)
    report = build_report(
        live_db_path=args.live_db,
        cross_db_path=args.cross_db,
        hours=args.hours,
        windows_hours=windows,
    )
    paths = write_report(report, args.output_prefix)
    print(json.dumps({"windowsV2": report["windowsV2"], "outputs": paths}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
