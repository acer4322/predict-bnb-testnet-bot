from __future__ import annotations

import collections
import sqlite3
from pathlib import Path
from typing import Any, Iterable

from .schema import canonical_event, trace_document

EPS = 1e-9
SIDES = ("UP", "DOWN")


def opposite(side: str) -> str:
    return "DOWN" if side == "UP" else "UP"


def _phase(time_ms: int, market_window_ms: int) -> tuple[float, float]:
    elapsed = int(time_ms) % int(market_window_ms)
    normalized = min(1.0, max(0.0, elapsed / float(market_window_ms)))
    return normalized, max(0.0, (market_window_ms - elapsed) / 1000.0)


def _route(maker_qty: float, taker_qty: float) -> str:
    if maker_qty > EPS and taker_qty > EPS:
        return "MIXED"
    if maker_qty > EPS:
        return "PASSIVE"
    if taker_qty > EPS:
        return "ACTIVE"
    return "UNKNOWN"


def _portfolio(up: float, down: float, up_cost: float, down_cost: float) -> dict[str, float]:
    total_cost = up_cost + down_cost
    up_payoff = up - total_cost
    down_payoff = down - total_cost
    return {
        "upShares": up,
        "downShares": down,
        "upCost": up_cost,
        "downCost": down_cost,
        "totalCost": total_cost,
        "floor": min(up_payoff, down_payoff),
        "bestPayoff": max(up_payoff, down_payoff),
    }


def load_fill_legs_read_only(db_path: str | Path, market_id: int) -> list[dict[str, Any]]:
    """Load one market without creating SQLite journals or mutating the source."""
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        tables = {
            str(row[0])
            for row in connection.execute("select name from sqlite_master where type='table'")
        }
        if "eth_events" in tables:
            table = "eth_events"
            where = "market_id = ?"
            params: tuple[Any, ...] = (int(market_id),)
        elif "wallet_shadow_target_events" in tables:
            table = "wallet_shadow_target_events"
            where = "asset = 'ETH' and market_id = ?"
            params = (int(market_id),)
        else:
            raise ValueError("no supported Target fill-leg table found")
        sql = f"""
            select id, leg_id, market_id, role, side, order_hash,
                   transaction_hash, settlement_id, event_ms, observed_at_ms,
                   price, shares
            from {table}
            where {where}
            order by event_ms, id
        """
        return [dict(row) for row in connection.execute(sql, params)]
    finally:
        connection.close()


def build_target_trace_from_rows(
    rows: Iterable[dict[str, Any]],
    *,
    market_id: int,
    provenance: dict[str, Any],
    market_window_ms: int = 300_000,
) -> dict[str, Any]:
    """Reconstruct responsibility semantics using atomic-clock FIFO allocation.

    This is the already-established V3 research reconstruction: an opposite-side
    fill pays only responsibility that existed before its clock.  Same-clock
    residual quantity can birth a new responsibility but cannot retroactively be
    paid at that same clock.
    """
    clocks: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for raw in rows:
        if int(raw["market_id"]) == int(market_id):
            clocks[int(raw["event_ms"])].append(dict(raw))
    queues: dict[str, collections.deque[dict[str, Any]]] = {
        side: collections.deque() for side in SIDES
    }
    events: list[dict[str, Any]] = []
    sequence = 0
    responsibility_id = 0
    up = down = up_cost = down_cost = 0.0
    completed = 0
    multi_payment_completed = 0
    repair_payment_clocks: set[int] = set()
    composite_clocks = 0

    for time_ms, legs in sorted(clocks.items()):
        phase, seconds_left = _phase(time_ms, market_window_ms)
        aggregates = {
            side: {"qty": 0.0, "notional": 0.0, "maker": 0.0, "taker": 0.0, "ids": []}
            for side in SIDES
        }
        for leg in legs:
            side = str(leg.get("side") or "").upper()
            qty = float(leg.get("shares") or 0.0)
            price = float(leg.get("price") or 0.0)
            route = str(leg.get("role") or "").upper()
            if side not in SIDES or qty <= EPS:
                continue
            aggregates[side]["qty"] += qty
            aggregates[side]["notional"] += qty * price
            aggregates[side]["ids"].append(str(leg.get("leg_id") or leg.get("id")))
            if route == "MAKER":
                aggregates[side]["maker"] += qty
            elif route == "TAKER":
                aggregates[side]["taker"] += qty
        for side in SIDES:
            qty = aggregates[side]["qty"]
            aggregates[side]["price"] = aggregates[side]["notional"] / qty if qty > EPS else None

        input_qty = {side: float(aggregates[side]["qty"]) for side in SIDES}
        remaining = dict(input_qty)
        repair_at_clock = 0.0
        birth_at_clock = 0.0
        pending_events: list[dict[str, Any]] = []

        # Pay debt that existed before this atomic clock, oldest first.
        for pay_side in SIDES:
            debt_side = opposite(pay_side)
            queue = queues[debt_side]
            available = remaining[pay_side]
            if available <= EPS or not queue:
                continue
            carrier_id = f"TARGET:{market_id}:{time_ms}:{pay_side}"
            route = _route(aggregates[pay_side]["maker"], aggregates[pay_side]["taker"])
            while available > EPS and queue:
                responsibility = queue[0]
                debt_before = float(responsibility["remaining"])
                paid = min(available, debt_before)
                responsibility["remaining"] = max(0.0, debt_before - paid)
                responsibility["paid"] += paid
                responsibility["paymentClocks"].add(time_ms)
                available -= paid
                repair_at_clock += paid
                repair_payment_clocks.add(time_ms)
                pending_events.append(
                    canonical_event(
                        source="TARGET",
                        evidence_class="POST_MARKET_RECONSTRUCTION",
                        market_id=market_id,
                        sequence=0,
                        event_type="REPAIR_PAYMENT",
                        time_ms=time_ms,
                        seconds_left=seconds_left,
                        normalized_phase=phase,
                        responsibility={
                            "responsibilityId": responsibility["id"],
                            "objectiveId": f"TARGET_FIFO:{responsibility['id']}",
                            "generationId": responsibility["id"],
                            "role": "REPAIR",
                            "side": pay_side,
                            "parentId": responsibility["id"],
                            "stateBefore": "OPEN",
                            "stateAfter": "PAID" if responsibility["remaining"] <= EPS else "OPEN",
                        },
                        manager_debt={"before": debt_before, "after": responsibility["remaining"]},
                        execution={
                            "carrierId": carrier_id,
                            "route": route,
                            "price": aggregates[pay_side]["price"],
                            "quantity": input_qty[pay_side],
                        },
                        allocation={
                            "physicalFillQty": paid,
                            "confirmedPaidQty": paid,
                            "repairAllocation": paid,
                            "overflowAllocation": 0.0,
                        },
                        metadata={
                            "responsibilityBornAt": responsibility["bornAt"],
                            "responsibilityAgeMs": time_ms - int(responsibility["bornAt"]),
                            "physicalLegIds": aggregates[pay_side]["ids"],
                            "postMarketOnly": True,
                        },
                    )
                )
                if responsibility["remaining"] <= EPS:
                    queue.popleft()
                    completed += 1
                    if len(responsibility["paymentClocks"]) >= 2:
                        multi_payment_completed += 1
                    pending_events.append(
                        canonical_event(
                            source="TARGET",
                            evidence_class="POST_MARKET_RECONSTRUCTION",
                            market_id=market_id,
                            sequence=0,
                            event_type="RESPONSIBILITY_COMPLETED",
                            time_ms=time_ms,
                            seconds_left=seconds_left,
                            normalized_phase=phase,
                            responsibility={
                                "responsibilityId": responsibility["id"],
                                "objectiveId": f"TARGET_FIFO:{responsibility['id']}",
                                "generationId": responsibility["id"],
                                "role": "REPAIR",
                                "side": pay_side,
                                "parentId": responsibility["id"],
                                "stateBefore": "OPEN",
                                "stateAfter": "COMPLETED",
                            },
                            manager_debt={"before": 0.0, "after": 0.0},
                            metadata={
                                "paymentClockCount": len(responsibility["paymentClocks"]),
                                "postMarketOnly": True,
                            },
                        )
                    )
            remaining[pay_side] = max(0.0, available)

        # Simultaneous residual on both sides is a direct pair build.
        direct_pair_qty = min(remaining["UP"], remaining["DOWN"])
        if direct_pair_qty > EPS:
            pending_events.append(
                canonical_event(
                    source="TARGET",
                    evidence_class="POST_MARKET_RECONSTRUCTION",
                    market_id=market_id,
                    sequence=0,
                    event_type="DIRECT_PAIR_FILL",
                    time_ms=time_ms,
                    seconds_left=seconds_left,
                    normalized_phase=phase,
                    responsibility={"role": "DIRECT_PAIR"},
                    execution={"route": "MIXED", "quantity": direct_pair_qty * 2.0},
                    metadata={
                        "upQty": direct_pair_qty,
                        "downQty": direct_pair_qty,
                        "upPrice": aggregates["UP"]["price"],
                        "downPrice": aggregates["DOWN"]["price"],
                        "postMarketOnly": True,
                    },
                )
            )
            remaining["UP"] -= direct_pair_qty
            remaining["DOWN"] -= direct_pair_qty

        # One-sided residual births an explicit new responsibility.
        for side in SIDES:
            qty = max(0.0, remaining[side])
            if qty <= EPS:
                continue
            responsibility_id += 1
            responsibility = {
                "id": responsibility_id,
                "side": side,
                "bornAt": time_ms,
                "initial": qty,
                "remaining": qty,
                "paid": 0.0,
                "paymentClocks": set(),
            }
            queues[side].append(responsibility)
            birth_at_clock += qty
            pending_events.append(
                canonical_event(
                    source="TARGET",
                    evidence_class="POST_MARKET_RECONSTRUCTION",
                    market_id=market_id,
                    sequence=0,
                    event_type="RESPONSIBILITY_BIRTH",
                    time_ms=time_ms,
                    seconds_left=seconds_left,
                    normalized_phase=phase,
                    responsibility={
                        "responsibilityId": responsibility_id,
                        "objectiveId": f"TARGET_FIFO:{responsibility_id}",
                        "generationId": responsibility_id,
                        "role": "EXPAND",
                        "side": side,
                        "parentId": responsibility_id,
                        "stateBefore": None,
                        "stateAfter": "OPEN",
                    },
                    manager_debt={"before": 0.0, "after": qty},
                    execution={
                        "carrierId": f"TARGET:{market_id}:{time_ms}:{side}",
                        "route": _route(aggregates[side]["maker"], aggregates[side]["taker"]),
                        "price": aggregates[side]["price"],
                        "quantity": input_qty[side],
                    },
                    allocation={
                        "physicalFillQty": qty,
                        "confirmedPaidQty": 0.0,
                        "repairAllocation": 0.0,
                        "overflowAllocation": qty,
                        "newResponsibilityId": responsibility_id,
                    },
                    metadata={
                        "physicalLegIds": aggregates[side]["ids"],
                        "postMarketOnly": True,
                    },
                )
            )

        # Portfolio state is factual, but role/allocation labels remain reconstructed.
        up += input_qty["UP"]
        down += input_qty["DOWN"]
        up_cost += float(aggregates["UP"]["notional"])
        down_cost += float(aggregates["DOWN"]["notional"])
        portfolio_after = _portfolio(up, down, up_cost, down_cost)
        for event in pending_events:
            event["portfolio"].update(portfolio_after)
            event["sequence"] = sequence
            sequence += 1
            events.append(event)
        if repair_at_clock > EPS and birth_at_clock > EPS:
            composite_clocks += 1
            events.append(
                canonical_event(
                    source="TARGET",
                    evidence_class="POST_MARKET_RECONSTRUCTION",
                    market_id=market_id,
                    sequence=sequence,
                    event_type="COMPOSITE_CARRIER",
                    time_ms=time_ms,
                    seconds_left=seconds_left,
                    normalized_phase=phase,
                    portfolio=portfolio_after,
                    responsibility={"role": "REPAIR"},
                    metadata={
                        "repairQty": repair_at_clock,
                        "overflowBirthQty": birth_at_clock,
                        "postMarketOnly": True,
                    },
                )
            )
            sequence += 1

    terminal_outstanding = sum(
        float(responsibility["remaining"])
        for side in SIDES
        for responsibility in queues[side]
    )
    summary = {
        "atomicClocks": len(clocks),
        "physicalFillLegs": sum(len(legs) for legs in clocks.values()),
        "responsibilitiesBorn": responsibility_id,
        "responsibilitiesCompleted": completed,
        "multiPaymentCompletedResponsibilities": multi_payment_completed,
        "repairPaymentClocks": len(repair_payment_clocks),
        "compositeRepairThenExpandClocks": composite_clocks,
        "terminalOutstandingDebt": terminal_outstanding,
        "terminalPortfolio": _portfolio(up, down, up_cost, down_cost),
    }
    return trace_document(
        source="TARGET",
        market_id=market_id,
        events=events,
        provenance=provenance,
        coverage={
            "physicalFillLegs": "COMPLETE_FOR_SNAPSHOT_MARKET",
            "responsibilitySemantics": "POST_MARKET_ATOMIC_FIFO_RECONSTRUCTION",
            "managerDecisions": "UNOBSERVED",
            "runtimeUseAllowed": False,
        },
        summary=summary,
    )


def build_target_trace(
    db_path: str | Path,
    market_id: int,
    *,
    market_window_ms: int = 300_000,
) -> dict[str, Any]:
    rows = load_fill_legs_read_only(db_path, market_id)
    return build_target_trace_from_rows(
        rows,
        market_id=market_id,
        market_window_ms=market_window_ms,
        provenance={
            "sourceArtifact": str(Path(db_path).as_posix()),
            "adapter": "target_atomic_fifo_responsibility_trace_v1",
            "sourceRows": len(rows),
            "strictPastRuntimeInput": False,
            "winnerUsed": False,
            "targetFutureActionUsed": False,
        },
    )
