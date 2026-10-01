"""Research-only objective -> quantity planner; not an economic grant generator.

A price is an execution input, not permission to acquire a position. The supplied
Grant is authoritative for quantity/cash. Plans share the existing ledger and are
invalidated by any owner/receipt change. No policy/HFT import, dispatch or fills.

Call commit from a serialized controller event loop. Snapshot checking is NOT a
thread/process lock. Quote references bind declared inputs, not data authenticity.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_FLOOR
import hashlib
import json
import math


@dataclass(frozen=True)
class ExecutionLimits:
    tick: float
    quantity_step: float
    min_quantity: float
    min_notional: float
    max_child_quantity: float


@dataclass(frozen=True)
class QuantityPlan:
    parent_id: int
    generation: str
    authority_reference: str
    side: str
    route: str
    key: str
    quote_reference: str
    now_ms: int
    market_end_ms: int
    limit_price: float
    quantity: float
    fee_cap: float
    authorized_free_quantity: float
    free_cash_before_child_fee: float
    reserved_child_cost: float
    ledger_snapshot: str
    limits: ExecutionLimits


@dataclass(frozen=True)
class PlanningResult:
    reason: str
    plan: QuantityPlan | None = None


def _finite(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def snapshot(ledger):
    """Includes all sibling grants/owners, not only the chosen parent."""
    ledger.invariants()
    state = dict(capital=ledger.capital,
                 grants=[asdict(ledger.grants[k]) for k in sorted(ledger.grants)],
                 carriers=[asdict(ledger.carriers[k]) for k in sorted(ledger.carriers)],
                 accounts=[(k, ledger.account(k)) for k in sorted(ledger.grants)])
    return hashlib.sha256(json.dumps(state, sort_keys=True, allow_nan=False,
                                    separators=(',', ':')).encode()).hexdigest()


def prepare(ledger, parent_id, route, limit_price, *, key, quote_reference,
            now_ms, market_end_ms, limits, fee_cap=0.):
    """Return a conditional order plan; never infer debt or mutate the ledger.

    Remaining authority controls quantity; cash is a ceiling, not a spend target.
    Fill probability, economic value, and actual execution are NOT estimated.
    """
    if type(parent_id) is not int or parent_id not in ledger.grants:
        return PlanningResult('NO_EXPLICIT_GRANT')
    if (route not in ('PASSIVE', 'ACTIVE') or not isinstance(key, str) or not key
            or not isinstance(quote_reference, str) or not quote_reference
            or type(now_ms) is not int or type(market_end_ms) is not int
            or now_ms < 0 or market_end_ms <= 0):
        return PlanningResult('INVALID_IDENTITY_ROUTE_OR_CLOCK')
    if now_ms >= market_end_ms:
        return PlanningResult('MARKET_ENDED')
    if (not isinstance(limits, ExecutionLimits)
            or not all(_finite(x, positive=True) for x in
                       (limits.tick, limits.quantity_step, limits.min_quantity,
                        limits.max_child_quantity))
            or not _finite(limits.min_notional) or not _finite(fee_cap)
            or not _finite(limit_price, positive=True) or limit_price >= 1):
        return PlanningResult('INVALID_PRICE_OR_DECLARED_LIMITS')
    price = Decimal(str(limit_price))
    tick = Decimal(str(limits.tick))
    if price % tick != 0:
        return PlanningResult('PRICE_OFF_DECLARED_TICK')
    before = snapshot(ledger)
    grant = ledger.grants[parent_id]
    account = ledger.account(parent_id)
    decimal = lambda x: Decimal(str(x))
    free_qty = (decimal(account['repair_remaining']) + decimal(account['add_remaining'])
                - decimal(account['reserved_qty']))
    free_cash = (decimal(grant.cash_limit) - decimal(account['spent'])
                 - decimal(account['reserved_cash']))
    spendable = free_cash - decimal(fee_cap)
    if free_qty <= 0:
        return PlanningResult('NO_UNRESERVED_QUANTITY_AUTHORITY')
    if spendable <= 0:
        return PlanningResult('NO_UNRESERVED_CASH_AUTHORITY')
    raw_qty = min(free_qty, decimal(limits.max_child_quantity), spendable / price)
    step = decimal(limits.quantity_step)
    qty = (raw_qty / step).to_integral_value(rounding=ROUND_FLOOR) * step
    if qty <= 0 or qty < decimal(limits.min_quantity):
        return PlanningResult('BELOW_DECLARED_MIN_QUANTITY')
    if qty * price < decimal(limits.min_notional):
        return PlanningResult('BELOW_DECLARED_MIN_NOTIONAL')
    # Reuse authoritative pool/cash/quantity/cross checks rather than writing a
    # second competing legality model. The dry run allocates no physical fills.
    trial = deepcopy(ledger)
    try:
        trial.reserve(key, parent_id, route, float(qty), limit_price, fee_cap,
                      now_ms=now_ms, market_end_ms=market_end_ms)
        trial.invariants()
    except ValueError as exc:
        return PlanningResult('RESERVATION_REJECTED: ' + str(exc))
    if snapshot(ledger) != before:
        raise RuntimeError('ledger changed during planning; serialize event handling')
    return PlanningResult('PLANNED_NOT_SUBMITTED', QuantityPlan(
        parent_id, grant.generation, grant.authority_reference, grant.side, route,
        key, quote_reference, now_ms, market_end_ms, float(price), float(qty),
        float(fee_cap), float(free_qty), float(free_cash), float(qty * price + decimal(fee_cap)),
        before, limits))


def commit_reservation(ledger, plan, *, quote_reference, now_ms):
    """Commit only a still-current claim; native submit/ack is a separate adapter.

    Does not submit an order, assume acceptance, simulate a fill or issue a Grant.
    A submit failure needs an explicit upstream terminal/release protocol.
    """
    if not isinstance(plan, QuantityPlan):
        raise ValueError('an explicit immutable plan is required')
    if quote_reference != plan.quote_reference or now_ms != plan.now_ms:
        raise ValueError('quote/decision snapshot changed; replan')
    if snapshot(ledger) != plan.ledger_snapshot:
        raise ValueError('ledger snapshot changed; replan')
    refreshed = prepare(ledger, plan.parent_id, plan.route, plan.limit_price,
        key=plan.key, quote_reference=quote_reference, now_ms=now_ms,
        market_end_ms=plan.market_end_ms, limits=plan.limits, fee_cap=plan.fee_cap)
    if refreshed.plan != plan:
        raise ValueError('plan is not the current authorized quantity plan')
    ledger.reserve(plan.key, plan.parent_id, plan.route, plan.quantity,
                   plan.limit_price, plan.fee_cap, now_ms=now_ms,
                   market_end_ms=plan.market_end_ms)
    ledger.invariants()
