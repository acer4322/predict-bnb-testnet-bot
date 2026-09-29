"""Research-only atomic reservation preview with explicit payoff-side bounds.

No economic authority generator, native submission, actual fill simulation, price
forecast, budget calibration or controller mode. Caller bounds are test inputs.
Reuses existing grant/allocation/sizing modules without modifying them.
"""
from dataclasses import dataclass, asdict
from copy import deepcopy
import hashlib
import json
import math
from tools.pair_core_objective_quantity_planner_v1 import snapshot as ledger_snapshot
from tools.pair_core_asset_route_sizing_v2 import reserve_authorized

EPS = 1e-8


def finite(*xs):
    return all(isinstance(x, (int, float)) and not isinstance(x, bool)
               and math.isfinite(x) for x in xs)


@dataclass(frozen=True)
class InitialPortfolio:
    up: float
    down: float
    cost: float


@dataclass(frozen=True)
class EndpointAuthority:
    min_up_payoff: float
    min_down_payoff: float
    reference: str


@dataclass(frozen=True)
class ProposedOrder:
    key: str
    parent_id: int
    route: str
    quantity: float
    limit_price: float
    fee_cap: float = 0.


def validate_context(initial, bounds):
    if not isinstance(initial, InitialPortfolio) or not finite(initial.up, initial.down, initial.cost):
        raise ValueError('explicit finite initial portfolio required')
    if min(initial.up, initial.down, initial.cost) < 0:
        raise ValueError('this buy-only fixture requires nonnegative initial amounts')
    if not isinstance(bounds, EndpointAuthority) or not finite(bounds.min_up_payoff, bounds.min_down_payoff) or not bounds.reference:
        raise ValueError('explicit endpoint authority required; no inferred risk budget')


def fingerprint(ledger, initial, bounds, asset, quote_reference, now_ms, end_ms, orders):
    value = dict(ledger=ledger_snapshot(ledger), initial=asdict(initial), bounds=asdict(bounds),
                 asset=asset, quote=quote_reference, now=now_ms, end=end_ms,
                 orders=[asdict(x) for x in orders])
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False,
                                     separators=(',', ':')).encode()).hexdigest()


def payoff_projection(ledger, initial):
    """Exact coordinate minima for independent fills with linear buy-limit costs.

    Every order may fill 0..remaining. Beneficial pending fills give NO protection
    in the coordinate minimum. Unpaid fee caps may be charged and are conservatively
    deducted from both coordinates even at0fill; hence exact for that conservative
    fee uncertainty set, not a venue fee prediction.
    """
    up, down, cost = initial.up, initial.down, initial.cost
    for c in ledger.carriers.values():
        side = ledger.grants[c.parent_id].side
        if side == 'UP': up += c.filled
        else: down += c.filled
        cost += c.payment + c.fees
    confirmed = dict(UP=up-cost, DOWN=down-cost)
    lower = dict(confirmed)
    full = dict(confirmed)
    pending = []
    for c in ledger.carriers.values():
        if c.state == 'TERMINAL': continue
        q = c.reserved_qty
        fee = max(0., c.fee_cap-c.fees)
        side = ledger.grants[c.parent_id].side
        contribution = {s:(q if s == side else 0.)-q*c.limit for s in ('UP','DOWN')}
        for s in lower:
            lower[s] += min(0., contribution[s])-fee
            full[s] += contribution[s]-fee
        pending.append(dict(key=c.key, parent_id=c.parent_id, side=side, route=c.route,
                            state=c.state, remaining=q, limit=c.limit, unpaid_fee_cap=fee,
                            full_increment=contribution))
    return dict(confirmed=confirmed, coordinate_worst=lower,
                conditional_all_filled_at_limits=full, unresolved=pending,
                uncertainty='INDEPENDENT_ZERO_PARTIAL_FULL_RECEIPTS_NO_PROBABILITY',
                simultaneous_minima_required=False)


def reserve_trial(ledger, asset, orders, *, now_ms, market_end_ms, quantity_step):
    trial = deepcopy(ledger)
    for o in orders:
        if not isinstance(o, ProposedOrder): raise ValueError('explicit immutable order required')
        reserve_authorized(trial, asset, o.key, o.parent_id, o.route, o.quantity,
                           o.limit_price, o.fee_cap, now_ms=now_ms,
                           market_end_ms=market_end_ms, quantity_step=quantity_step)
    trial.invariants()
    return trial


def preview(ledger, initial, bounds, asset, orders, *, quote_reference,
            now_ms, market_end_ms, quantity_step=.01):
    """No mutation. Admission is only to caller constraints and legacy API.

    A rejection reason must not be converted into an observed Target HOLD label.
    Execution pool4/1 and finite per-grant cash claims belong to the reused test
    component, not the separate nonbinding-funding student/world.
    """
    validate_context(initial, bounds)
    if not quote_reference or type(now_ms) is not int or type(market_end_ms) is not int:
        raise ValueError('explicit quote reference and integer clocks required')
    orders = tuple(orders)
    if not all(isinstance(o, ProposedOrder) for o in orders):
        raise ValueError('explicit immutable orders required')
    before = fingerprint(ledger, initial, bounds, asset, quote_reference, now_ms, market_end_ms, orders)
    result = dict(accepted=False, status=None, detail=None, snapshot=before,
                  proposed=[asdict(o) for o in orders], projection=None,
                  native_submitted=False, newly_created_grants=0)
    try:
        trial = reserve_trial(ledger, asset, orders, now_ms=now_ms,
                              market_end_ms=market_end_ms, quantity_step=quantity_step)
    except ValueError as exc:
        text = str(exc)
        status = ('EXECUTION_CAPACITY_UNSUPPORTED' if 'execution pool full' in text else
                  'OWN_CROSS_CONFLICT' if 'own cross' in text else
                  'NO_EXPLICIT_GRANT' if 'no economic grant' in text else
                  'SIZE_SPECIFICATION_REJECTED' if 'passive minimum' in text or 'grid' in text else
                  'MARKET_ENDED' if 'market ended' in text else
                  'RESERVATION_AUTHORITY_REJECTED')
        result.update(status=status, detail=text)
    else:
        p = payoff_projection(trial, initial)
        limits = dict(UP=bounds.min_up_payoff, DOWN=bounds.min_down_payoff)
        bad = {s:dict(worst=p['coordinate_worst'][s], required=limits[s])
               for s in limits if p['coordinate_worst'][s] < limits[s]-EPS}
        result.update(projection=p, full_fill_within_bounds=all(
            p['conditional_all_filled_at_limits'][s] >= limits[s]-EPS for s in limits),
            adverse_fill_violations=bad)
        result.update(accepted=not bool(bad), status=('ADMISSIBLE_RESERVATION_NOT_EXECUTION'
                      if not bad else 'DECLARED_ENDPOINT_AUTHORITY_EXCEEDED'))
    after = fingerprint(ledger, initial, bounds, asset, quote_reference, now_ms, market_end_ms, orders)
    if before != after: raise RuntimeError('input changed during preview; serialize event loop')
    return result


def commit_reservations(ledger, initial, bounds, asset, orders, *, inspected,
                        quote_reference, now_ms, market_end_ms, quantity_step=.01):
    """Atomic ledger-only commit in a serialized event loop, NOT a thread lock.

    Original references to individual carrier objects are invalidated on commit;
    consumers must resolve carriers by key. Native send/ack requires another adapter.
    """
    orders = tuple(orders)  # Reuse a one-shot iterable for preview and commit.
    current = preview(ledger, initial, bounds, asset, orders, quote_reference=quote_reference,
                      now_ms=now_ms, market_end_ms=market_end_ms, quantity_step=quantity_step)
    if current != inspected:
        raise ValueError('stale or changed objective/receipt/quote snapshot; replan')
    if not current['accepted']:
        raise ValueError('joint reservation rejected: '+current['status'])
    trial = reserve_trial(ledger, asset, tuple(orders), now_ms=now_ms,
                          market_end_ms=market_end_ms, quantity_step=quantity_step)
    # Three assignments require the explicitly serialized caller, no asynchronous sends.
    ledger.grants = trial.grants
    ledger.carriers = trial.carriers
    ledger.allocation = trial.allocation
    ledger.invariants()
    return current
