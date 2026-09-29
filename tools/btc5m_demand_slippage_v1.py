"""Pure research candidate: finite repair demand chooses a visible depth prefix.

No transport or simulator calls. Expected depth cost and worst limit reservation
are separate; pending shares never become completed repair or new net capacity.
"""
from decimal import Decimal, ROUND_FLOOR

from hft244_pair_route_legality_v1 import crossing_owners

MODES = ('BEST_ASK', 'FIXED_10_PERCENT', 'FIXED_15_PERCENT', 'DEMAND_DEPTH')
EPS = Decimal('0.00000001')


def d(x):
    value = Decimal(str(x))
    if not value.is_finite():
        raise ValueError('nonfinite input')
    return value


def floor_step(value, step):
    return ((value + EPS) / step).to_integral_value(rounding=ROUND_FLOOR) * step


def book_cost(book, quantity):
    remaining, cost, deepest = quantity, d(0), None
    for price, size in book:
        take = min(remaining, size)
        if take > 0:
            cost += take * price
            deepest = price
            remaining -= take
        if remaining <= 0:
            break
    if remaining > EPS:
        raise ValueError('quantity exceeds displayed support')
    return cost, deepest


def solve(asks, need, capacity, mode='DEMAND_DEPTH', step=.01, tick=.01):
    """Maximize displayed repair up to finite need/capacity, cheapest levels first.

    Adaptive price is the deepest consumed level. Fixed percentage modes instead
    retain their price buffer even if displayed quantity fits at a better price.
    Expected repair is capped by need; actual worse fills may leave work open.
    """
    if mode not in MODES:
        raise ValueError('unknown mode')
    step, tick = d(step), d(tick)
    if step <= 0 or tick <= 0:
        raise ValueError('invalid grid')
    need, capacity = max(d(0), d(need)), max(d(0), d(capacity))
    levels = {}
    for price, size in asks:
        price, size = d(price), d(size)
        if not 0 < price < 1 or price % tick != 0 or size < 0:
            raise ValueError('invalid observed book')
        if size > 0:
            levels[price] = levels.get(price, d(0)) + size
    book = sorted(levels.items())
    base = dict(mode=mode, need=float(need), capacity=float(capacity), quantity=0.,
                price_limit=None, executable=False, reason='NO_DEMAND_OR_CAPACITY_OR_BOOK')
    if not book or need <= EPS or capacity <= EPS:
        return base
    ask = book[0][0]
    bps = dict(BEST_ASK=0, FIXED_10_PERCENT=1000, FIXED_15_PERCENT=1500).get(mode)
    ceiling = (floor_step(ask * (1 + d(bps) / 10000), tick)
               if bps is not None else d(1)-tick)
    ceiling = min(ceiling, d(1)-tick)
    allowed = [(p, q) for p, q in book if p <= ceiling]
    quantity, expected_lift = d(0), d(0)
    for price, size in allowed:
        take = max(d(0), min(size, capacity-quantity, (need-expected_lift)/(1-price)))
        quantity += take
        expected_lift += take*(1-price)
        if capacity-quantity <= EPS or need-expected_lift <= EPS:
            break
    quantity = min(floor_step(quantity, step), floor_step(capacity, step))
    if quantity <= 0:
        return base
    expected_cost, deepest = book_cost(allowed, quantity)
    limit = deepest if mode == 'DEMAND_DEPTH' else ceiling
    worst_cost = quantity*limit
    expected_lift = quantity-expected_cost
    assert expected_lift <= need+EPS and quantity <= capacity+EPS
    return dict(base, quantity=float(quantity), price_limit=float(limit),
        executable=worst_cost >= 1-EPS,
        reason='QUOTABLE' if worst_cost >= 1-EPS else 'NEW_BELOW_ONE_DOLLAR',
        best_ask=float(ask), deepest_used=float(deepest), inspected_depth=float(sum(q for _, q in allowed)),
        displayed_depth=float(sum(q for p, q in allowed if p <= limit)),
        expected_cost=float(expected_cost), expected_average_price=float(expected_cost/quantity),
        worst_reserved_cost=float(worst_cost), expected_weak_lift=float(expected_lift),
        worst_weak_lift_if_all_filled=float(quantity-worst_cost),
        remaining_need_on_displayed_fill=float(max(d(0), need-expected_lift)),
        price_increase_bps=float((limit/ask-1)*10000),
        theoretical_same_quantity_at_best_cost=float(quantity*ask))


def context(state, operations, weak, reference, include_strong_pending=False):
    strong = 'DOWN' if weak == 'UP' else 'UP'
    qty = {s:d(state['pending_qty'][s]) for s in ('UP','DOWN')}
    cash = {s:d(state['pending_cash'][s]) for s in ('UP','DOWN')}
    reservations = [dict(key=o['key'], side=o['side'], price=o['limit'])
                    for o in state['owners'] if o.get('state') != 'TERMINAL']
    for op in operations:
        if op['kind'] == 'NEW':
            qty[op['side']] += d(op['qty'])
            cash[op['side']] += d(op['qty'])*d(op['price'])
            reservations.append(dict(key=op['key'], side=op['side'], price=op['price']))
    projected = d(state['payoff'][weak])+qty[weak]-cash[weak]
    if include_strong_pending:
        projected -= cash[strong]
    return dict(weak=weak, strong=strong,
        need=float(max(d(0),d(reference)-projected)),
        capacity=float(max(d(0),d(state['inv'][strong])-d(state['inv'][weak])-qty[weak])),
        strong_pending_cash=float(cash[strong]), weak_pending_qty=float(qty[weak]),
        reservations=reservations)


def coordination(decision, ctx, state, operations, cancellable):
    """Describe conflict resolution without assuming cancellations succeeded.

    Even removing only same-plan NEW requires demand to be recomputed. These are
    proposals for a later controller, not full executable native envelopes.
    """
    if not decision['executable']:
        return dict(status='NO_ACTIVE', conflicts=[], cancel=[], suppress_new=[], active_new_allowed=False)
    hits = crossing_owners(ctx['weak'],decision['price_limit'],ctx['reservations'])
    if not hits:
        return dict(status='READY', conflicts=[], cancel=[], suppress_new=[], active_new_allowed=True)
    new = {o['key'] for o in operations if o['kind']=='NEW'}
    owners = {o['key']:o for o in state['owners']}
    suppress, cancel, waiting = [], [], []
    for key in hits:
        if key in new:
            suppress.append(key)
        else:
            owner = owners[key]
            if owner.get('state') not in ('CANCEL_PENDING','UNKNOWN') and cancellable.get(key,False):
                cancel.append(key)
            waiting.append(key)
    return dict(status='WAIT_TERMINAL_AND_RECOMPUTE' if waiting else 'SUPPRESS_NEW_AND_RECOMPUTE',
        conflicts=hits, cancel=cancel, suppress_new=suppress,
        retained_conflict_reservations=waiting, active_new_allowed=False)


def replan(state, operations, weak, reference, asks, cancellable, legacy):
    """Bounded final-service integration; recompute after suppressing conflicts.

    Preserve the existing trigger, weak NEW priority and Passive-price gate.
    Changed depth capacity/minimum sizing and price come from the new solver.
    Cancel success is never inferred here: pending owners keep blocking Active.
    """
    from copy import deepcopy
    ops=deepcopy(operations)
    preserved=('WAIT_FOR_CONFIRMED_REEXPOSURE_EPISODE','CURRENT_DOWN_NO_LONGER_NEGATIVE',
               'ORIGINAL_DOWN_NEW_HAS_PRIORITY','LEGAL_PASSIVE15_STILL_AVAILABLE')
    if legacy['reason'] in preserved:
        return ops,dict(legacy),dict(status='PRESERVED_GATE',steps=[],suppressed=[])
    steps=[];suppressed=[]
    for _ in range(1+sum(o['kind']=='NEW' for o in ops)):
        ctx=context(state,ops,weak,reference,True)
        decision=solve(asks,ctx['need'],ctx['capacity'])
        control=coordination(decision,ctx,state,ops,cancellable)
        steps.append(dict(context=ctx,quote=decision,coordination=control))
        if not control['suppress_new']:
            break
        suppressed.extend(control['suppress_new'])
        ops=[o for o in ops if o['key'] not in control['suppress_new']]
    else:
        raise AssertionError('conflict suppression failed to converge')
    old_cancel={o['key'] for o in ops if o['kind']=='CANCEL'}
    for key in control['cancel']:
        if key not in old_cancel:
            ops.append(dict(kind='CANCEL',key=key,origin='WHOLE_POLICY',reason='FINITE_REPAIR_PRICE_CONFLICT'))
    result=dict(legacy,eligible=control['active_new_allowed'],quantity=decision['quantity'],
        execution_limit=decision['price_limit'],reason='ADAPTIVE_DEPTH_READY' if control['active_new_allowed'] else control['status'],
        adaptive_expected_cost=decision.get('expected_cost'),adaptive_reserved_cost=decision.get('worst_reserved_cost'))
    return ops,result,dict(status=control['status'],steps=steps,suppressed=suppressed)
