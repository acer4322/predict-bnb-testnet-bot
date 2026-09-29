"""Research-only same-intent Passive geometry. No execution or directional authority.

The V1 cross-side score is rejected. Pending quantity is reservation, not service.
No fill probability or queue position is inferred from public level membership.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

EPS = 1e-9
REPAIR_ROLES = frozenset({"ECONOMIC_CORE", "SATELLITE_REPAIR"})


@dataclass(frozen=True)
class PassiveIntent:
    side: str
    role: str
    baseline_price: float
    baseline_qty: float
    bid: float
    ask: float
    opposite_lots: tuple[tuple[float, float], ...]
    used_prices: tuple[float, ...]
    tick_size: float
    # Match the existing _pair_ok tolerance, NOT a fitted economic parameter.
    pair_sum_limit: float = 1.0000001


def fifo_terms(price, qty, lots):
    left, credit = float(qty), 0.0
    for lot_qty, lot_price in lots:
        paid = min(left, float(lot_qty))
        credit += paid * (1.0 - float(lot_price) - float(price))
        left -= paid
        if left <= EPS:
            break
    return {"matchedQty": qty - left, "overflowQty": left,
            "finiteFifoCredit": credit}


def bounded_frontier(intent: PassiveIntent) -> dict:
    """Choose a closer unused grid price ONLY for an already-existing legal intent.

    Keep 1/p sizing (one unit notional), <=12 shares and the original Pair ceiling.
    No new candidate is born when the caller's original candidate is absent.
    """
    c = intent
    result = {"intent": asdict(c), "price": c.baseline_price, "qty": c.baseline_qty,
              "changed": False, "reason": "BASELINE_UNCHANGED",
              "rankingAuthority": "SAME_INTENT_PASSIVE_DISTANCE_ONLY"}
    if c.role not in REPAIR_ROLES:
        result["reason"] = "ROLE_OUTSIDE_INTERVENTION"
        return result
    values = [c.baseline_price, c.baseline_qty, c.bid, c.ask, c.tick_size,
              c.pair_sum_limit, *c.used_prices,
              *(v for lot in c.opposite_lots for v in lot)]
    if (not all(math.isfinite(v) for v in values) or c.tick_size <= 0
            or not 0 < c.bid < c.ask < 1 or c.baseline_price <= 0
            or c.baseline_qty <= 0 or any(q < 0 for q, _ in c.opposite_lots)):
        result["reason"] = "INVALID_GEOMETRY_BASELINE_FALLBACK"
        return result
    gap = sum(q for q, _ in c.opposite_lots)
    avg = sum(q * p for q, p in c.opposite_lots) / gap if gap > EPS else None
    ceiling = c.pair_sum_limit - avg if avg is not None else 1.0
    upper = min(c.bid, ceiling)
    price_tick = math.floor((upper + 1e-12) / c.tick_size)
    occupied = {round(p, 10) for p in c.used_prices}
    # At most the number of reserved prices can collide; no data-dependent scan.
    for _ in range(len(occupied) + 1):
        price = round(price_tick * c.tick_size, 10)
        if price not in occupied:
            break
        price_tick -= 1
    result.update({"pairCeiling": ceiling, "frontierPrice": price,
                   "unavoidableBidCeilingGap": max(0.0, c.bid - ceiling),
                   "baselineFifo": fifo_terms(c.baseline_price, c.baseline_qty, c.opposite_lots)})
    if price <= c.baseline_price + EPS:
        result["reason"] = "NO_CLOSER_UNUSED_PAIR_LEGAL_TICK"
        return result
    if (not EPS < price < 1 - EPS or price >= c.ask - EPS
            or price > ceiling + 1e-12 or 1.0 / price > 12.0 + EPS):
        result["reason"] = "VENUE_OR_PAIR_GEOMETRY_BASELINE_FALLBACK"
        return result
    result.update({"price": price, "qty": 1.0 / price, "changed": True,
                   "reason": "CLOSER_UNUSED_PAIR_LEGAL_PASSIVE_TICK",
                   "selectedFifo": fifo_terms(price, 1.0 / price, c.opposite_lots),
                   "priceDistanceTicks": max(0.0, c.bid - price) / c.tick_size})
    return result


def pending_reachability(carriers: list[dict], opposite_gap: float) -> dict:
    """Strict-current observable classes, not a fitted fill/queue-service model.

    Unknown/in-flight/cancel-pending carriers stay physically reserved. No state
    class is used to subtract debt, block a role, or discount a new Repair choice.
    """
    totals = {}
    reserved = 0.0
    rows = []
    for source in carriers:
        c = dict(source)
        qty = max(0.0, float(c["remainingQty"]))
        reserved += qty
        status = str(c.get("status") or "UNKNOWN").upper()
        if c.get("cancelRequested") or c.get("ttlCancelAttemptAt") is not None:
            group = "CANCEL_UNCERTAIN"
        elif status == "NONE":
            group = "SUBMIT_IN_FLIGHT"
        elif status not in {"NEW", "PARTIALLY_FILLED"}:
            group = "STATUS_UNKNOWN_OR_TERMINAL_AWAITING_RECONCILIATION"
        elif c["price"] >= c["bid"] - EPS:
            group = "RESTING_AT_OR_BETTER_THAN_BID"
        else:
            group = "RESTING_BEHIND_BID"
        totals[group] = totals.get(group, 0.0) + qty
        c.update({"reachabilityClass": group, "fillProbability": None,
                  "queueAhead": None, "publicLevelPresenceIsQueueProof": False,
                  "bidDistance": max(0.0, c["bid"] - c["price"])})
        rows.append(c)
    return {"carriers": rows, "reservedQty": reserved, "qtyByClass": totals,
            "futureServiceQtyBounds": [0.0, reserved],
            "confirmedPaymentFromPending": 0.0,
            "effectiveRedundancyDiscount": 1.0,
            "rejectedV1RawQuantityDiscount": opposite_gap / (opposite_gap + reserved)
            if opposite_gap > EPS else 1.0,
            "use": "DIAGNOSTIC_ONLY_NO_DEBT_RELEASE_OR_ADMISSION"}


def synthetic_keep_decision(*, synthetic, pair_ok, price, frontier_price,
                            age_ms, ttl_ms, cancel_pending) -> dict:
    """Retain an existing frontier carrier when its public level disappears.

    No extension of TTL; no claim that disappearance proves queue depletion.
    A closer replacement, Pair invalidation, or pending cancel keeps the inherited
    cancellation path. Never creates a replacement or reserves a future slot.
    """
    keep = (synthetic and pair_ok and frontier_price is not None
            and price >= frontier_price - EPS and age_ms < ttl_ms
            and not cancel_pending)
    return {"suppressPublicMembershipCancel": bool(keep),
            "selectedRoute": "KEEP" if keep else "INHERITED_REANCHOR",
            "reason": "OWN_FRONTIER_QUEUE_NOT_PUBLIC_DEPTH_MEMBERSHIP" if keep
            else "INHERITED_CANCELLATION_UNCHANGED", "queueValue": "UNKNOWN"}
