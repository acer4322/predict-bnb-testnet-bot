"""Bounded physical demand for an admitted continuation carrier; no ledger edits."""
from copy import deepcopy

EPS = 1e-8


def update(order, state, carrier, weak, t, end):
    side = order['side']; other = 'DOWN' if side == 'UP' else 'UP'
    lower, target = order['independent_inventory_interval']
    assert abs(lower-order['old_target']) < EPS
    assert abs(target-lower-order['independent_quantity']) < EPS
    remaining = max(0., target-state['inv'][side])
    status = order.get('intent_status', 'ACTIVE')
    reason = status
    # Excluding its own reservation is only a marginal demand calculation.
    # Canonical state, ownership, and all admission/risk checks remain untouched.
    own = [o for o in state['owners'] if o['key'] == order['key']]
    own_qty = sum(o['qty'] for o in own)
    own_cash = sum(o['qty']*o['limit'] for o in own)
    other_qty = max(0., state['pending_qty'][side]-own_qty)
    other_cash = max(0., state['pending_cash'][side]-own_cash)
    quantity = max(0., state['inv'][other]-state['inv'][side]-other_qty)
    potential = state['payoff'][side]+other_qty-other_cash-state['pending_cash'][other]
    money = max(0., -potential/(1.-order['price']))
    if status == 'ACTIVE':
        if remaining <= EPS: reason = 'PHYSICAL_TARGET_REACHED'
        elif weak != side: reason = 'WITHDRAWN_DIRECTION_CHANGED'
        elif t >= end: reason = 'WITHDRAWN_MARKET_END'
        elif carrier is not None and carrier.state == 'TERMINAL': reason = 'WITHDRAWN_CARRIER_TERMINAL'
        elif state['payoff'][side] >= 0 or remaining > min(quantity, money)+EPS:
            reason = 'WITHDRAWN_CURRENT_NEED_GONE'
        elif carrier is None: reason = 'WITHDRAWN_MISSING_CANONICAL_CARRIER'
        if reason != 'ACTIVE': order['intent_stopped_t'] = t
    order['intent_status'] = reason
    return dict(key=order['key'], side=side, status=reason, t=t,
                immutable_target=target, independent_interval=[lower,target],
                remaining_confirmed=remaining, quantity_capacity=quantity,
                money_capacity=money, owner_state=carrier.state if carrier else None,
                own_pending_qty=own_qty, own_pending_cash=own_cash,
                eligible=reason == 'ACTIVE')


def desired(frame, original, orders, snapshot, weak):
    state = snapshot(frame, frame['ledger']); before = deepcopy(state)
    effective = dict(original); rows = []
    for order in orders:
        row = update(order,state,frame['ledger'].carriers.get(order['key']),weak,int(frame['t']),int(frame['end']))
        if row['eligible']:
            effective[row['side']] = max(effective[row['side']],row['immutable_target'])
        rows.append(row)
    assert state == before
    return effective, rows
