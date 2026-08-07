from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence


LIFECYCLE_VERSION = "STRATEGY_LIFECYCLE_GUARD_V1"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _family_for_order(order_strategy: Any, supported: Sequence[str]) -> str | None:
    raw = str(order_strategy or "").strip().upper()
    supported_set = set(supported)
    if raw in supported_set:
        return raw
    if ":CONFIRM_ADD_" in raw:
        parent = raw.split(":CONFIRM_ADD_", 1)[0]
        return parent if parent in supported_set else None
    if raw.endswith(":UP") or raw.endswith(":DOWN"):
        parent = raw.rsplit(":", 1)[0]
        if parent.startswith("PAIR_ARB_") and parent in supported_set:
            return parent
    return None


def _window(events: Sequence[dict[str, Any]], size: int) -> dict[str, Any]:
    rows = list(events[-size:])
    pnl = sum(float(row["pnlUsdt"]) for row in rows)
    cost = sum(float(row["costUsdt"]) for row in rows)
    wins = sum(float(row["pnlUsdt"]) > 0 for row in rows)
    losses = sum(float(row["pnlUsdt"]) < 0 for row in rows)
    flat = len(rows) - wins - losses
    return {
        "window": size,
        "samples": len(rows),
        "wins": wins,
        "losses": losses,
        "flat": flat,
        "winRatePct": (wins / (wins + losses) * 100.0) if wins + losses else None,
        "pnlUsdt": pnl,
        "costUsdt": cost,
        "roiPct": (pnl / cost * 100.0) if cost > 0 else None,
        "expectancyUsdt": (pnl / len(rows)) if rows else None,
    }


def _drawdown(events: Sequence[dict[str, Any]]) -> dict[str, Any]:
    equity = 0.0
    peak = 0.0
    current_peak_index = -1
    max_drawdown = 0.0
    max_drawdown_at: str | None = None
    curve: list[float] = []

    for index, row in enumerate(events):
        equity += float(row["pnlUsdt"])
        curve.append(equity)
        if equity >= peak:
            peak = equity
            current_peak_index = index
        drawdown = equity - peak
        if drawdown < max_drawdown:
            max_drawdown = drawdown
            max_drawdown_at = str(row.get("settledAt") or "") or None

    current_drawdown = equity - peak
    if not events:
        episode_depth = 0.0
        rebound = 0.0
        recovery_pct = None
    elif current_drawdown >= -1e-12:
        episode_depth = 0.0
        rebound = 0.0
        recovery_pct = 100.0
    else:
        episode_start = current_peak_index + 1
        episode_curve = curve[episode_start:] if episode_start < len(curve) else [equity]
        trough = min(episode_curve) if episode_curve else equity
        episode_depth = trough - peak
        rebound = max(0.0, equity - trough)
        recovery_pct = (
            min(100.0, rebound / abs(episode_depth) * 100.0)
            if episode_depth < 0
            else 100.0
        )

    return {
        "lifetimePnlUsdt": equity,
        "peakPnlUsdt": peak,
        "currentDrawdownUsdt": current_drawdown,
        "maxDrawdownUsdt": max_drawdown,
        "maxDrawdownAt": max_drawdown_at,
        "episodeDepthUsdt": episode_depth,
        "reboundFromTroughUsdt": rebound,
        "recoveryPct": recovery_pct,
    }


def _status(samples: int, last20: dict[str, Any], last50: dict[str, Any]) -> str:
    if samples == 0:
        return "NO_DATA"
    if samples < 20:
        return "BUILDING"
    if samples < 50:
        return "WATCH" if float(last20["pnlUsdt"]) < 0 else "ACTIVE"
    short_negative = float(last20["pnlUsdt"]) < 0
    medium_negative = float(last50["pnlUsdt"]) < 0
    if short_negative and medium_negative:
        return "DEGRADED"
    if not short_negative and medium_negative:
        return "RECOVERY"
    if short_negative and not medium_negative:
        return "WATCH"
    return "ACTIVE"


def build_strategy_lifecycle_summary(
    rows: Iterable[dict[str, Any]],
    supported_strategies: Sequence[str],
    active_strategies: Sequence[str] = (),
) -> dict[str, Any]:
    """Build advisory lifecycle evidence from settled real-money orders only.

    Confirmation-add children and PAIR_ARB UP/DOWN legs are aggregated into one
    parent-strategy market result before Last20/Last50/DD are calculated.
    """
    supported = tuple(
        dict.fromkeys(
            str(value).strip().upper()
            for value in supported_strategies
            if str(value).strip()
        )
    )
    active = {str(value).strip().upper() for value in active_strategies}
    grouped: dict[tuple[str, int], dict[str, Any]] = {}

    for raw in rows:
        family = _family_for_order(raw.get("strategy"), supported)
        if family is None:
            continue
        try:
            market_id = int(raw.get("market_id"))
            cost = float(raw.get("cost_usdt") or 0.0)
            pnl = float(raw.get("pnl_usdt") or 0.0)
        except (TypeError, ValueError):
            continue
        settled_at = str(raw.get("settled_at") or "")
        item = grouped.setdefault(
            (family, market_id),
            {
                "strategy": family,
                "marketId": market_id,
                "costUsdt": 0.0,
                "pnlUsdt": 0.0,
                "settledAt": settled_at,
                "ledgerRows": 0,
            },
        )
        item["costUsdt"] += max(0.0, cost)
        item["pnlUsdt"] += pnl
        item["ledgerRows"] += 1
        if settled_at > str(item.get("settledAt") or ""):
            item["settledAt"] = settled_at

    by_strategy: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in grouped.values():
        by_strategy[str(item["strategy"])].append(item)
    for events in by_strategy.values():
        events.sort(key=lambda row: (str(row.get("settledAt") or ""), int(row["marketId"])))

    result_rows: list[dict[str, Any]] = []
    for strategy in supported:
        events = by_strategy.get(strategy, [])
        last10 = _window(events, 10)
        last20 = _window(events, 20)
        last50 = _window(events, 50)
        drawdown = _drawdown(events)
        exp20 = last20.get("expectancyUsdt")
        exp50 = last50.get("expectancyUsdt")
        expectancy_delta = (
            float(exp20) - float(exp50)
            if exp20 is not None and exp50 is not None and last50["samples"] >= 20
            else None
        )
        evidence = {
            "last10Positive": bool(last10["samples"] and float(last10["pnlUsdt"]) > 0),
            "last20Positive": bool(last20["samples"] and float(last20["pnlUsdt"]) > 0),
            "expectancyImproving": bool(expectancy_delta is not None and expectancy_delta > 0),
            "drawdownRecovering": bool(
                drawdown["currentDrawdownUsdt"] < 0
                and drawdown["recoveryPct"] is not None
                and float(drawdown["recoveryPct"]) >= 25.0
            ),
            "last10PnlUsdt": float(last10["pnlUsdt"]),
            "expectancyDelta20Vs50Usdt": expectancy_delta,
            "recoveryPct": drawdown["recoveryPct"],
            "reboundFromTroughUsdt": drawdown["reboundFromTroughUsdt"],
        }
        evidence["positiveSignals"] = sum(
            bool(evidence[key])
            for key in (
                "last10Positive",
                "last20Positive",
                "expectancyImproving",
                "drawdownRecovering",
            )
        )
        result_rows.append(
            {
                "strategy": strategy,
                "active": strategy in active,
                "status": _status(len(events), last20, last50),
                "settledMarkets": len(events),
                "lastSettledAt": events[-1]["settledAt"] if events else None,
                "last20": last20,
                "last50": last50,
                "drawdown": drawdown,
                "recoveryEvidence": evidence,
            }
        )

    counts = {
        status: sum(row["status"] == status for row in result_rows)
        for status in ("ACTIVE", "WATCH", "DEGRADED", "RECOVERY", "BUILDING", "NO_DATA")
    }
    return {
        "version": LIFECYCLE_VERSION,
        "status": "READY",
        "advisoryOnly": True,
        "automaticBlocking": False,
        "automaticStakeChanges": False,
        "sampleBasis": "one settled real-money market per parent strategy; confirmation-add children and pair legs are aggregated",
        "supportedStrategies": len(result_rows),
        "activeStrategies": [row["strategy"] for row in result_rows if row["active"]],
        "counts": counts,
        "strategies": result_rows,
        "updatedAt": _utc_iso(),
    }
