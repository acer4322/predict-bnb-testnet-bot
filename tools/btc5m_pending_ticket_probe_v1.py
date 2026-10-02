"""One offline Passive15 completion backed by current outstanding UP quantity.

This is an explicitly new admission experiment, not restoration of a lost order.
Outstanding UP is conditional exposure, never counted as confirmed inventory.
"""
EPS = 1e-8


def decide(state, operations, price, ask, active_confirmed, crossing):
    pending = dict(state['pending_qty'])
    cash = dict(state['pending_cash'])
    owners = [dict(key=o['key'], side=o['side'], price=o['limit']) for o in state['owners']]
    cancelling = {o['key'] for o in operations if o['kind'] == 'CANCEL'}
    usable_up = sum(o['qty'] for o in state['owners'] if o['side'] == 'UP'
                    and o['state'] == 'SUBMITTED' and o['key'] not in cancelling)
    for op in operations:
        if op['kind'] == 'NEW':
            pending[op['side']] += op['qty']
            cash[op['side']] += op['qty'] * op['price']
            owners.append(dict(key=op['key'], side=op['side'], price=op['price']))
            if op['side'] == 'UP':
                usable_up += op['qty']
    gap = max(0., state['inv']['UP'] - state['inv']['DOWN'] - pending['DOWN'])
    extra = max(0., 15. - gap)
    row = dict(eligible=False, reason='NOT_ELIGIBLE', quantity=15., price=price, ask=ask,
               active_confirmed=active_confirmed, uncovered_filled_gap=gap,
               excess_over_filled_gap=extra, usable_pending_up=usable_up,
               pending_qty=pending, pending_cash=cash, cash_budget_enabled=False,
               conflicts=[], projected_down_if_all_current_pending_fill=
               state['payoff']['DOWN'] + pending['DOWN'] - sum(cash.values()))
    if not active_confirmed:
        row['reason'] = 'WAIT_FOR_EXISTING_ACTIVE_TERMINAL_RECEIPT'
    elif any(o['kind'] == 'NEW' and o['side'] == 'DOWN' for o in operations):
        row['reason'] = 'ORIGINAL_DOWN_NEW_HAS_PRIORITY'
    elif state['payoff']['DOWN'] >= -EPS or not EPS < gap < 15. - EPS:
        row['reason'] = 'NO_NEGATIVE_DOWN_WITH_SUBTICKET_GAP'
    elif price is None or not 0 < price < 1 or 15. * price < 1. - EPS:
        row['reason'] = 'FIXED15_NEW_PRICE_INVALID'
    elif ask is None or price >= ask - EPS:
        row['reason'] = 'NO_PASSIVE_SPREAD'
    elif usable_up + EPS < extra:
        row['reason'] = 'NO_UNCANCELLED_UP_QUANTITY_FOR_EXCESS'
    else:
        row['conflicts'] = crossing('DOWN', price, owners)
        if row['conflicts']:
            row['reason'] = 'PENDING_OR_SAME_PLAN_OWN_CROSS'
        else:
            row.update(eligible=True, reason='FIRST_PENDING_UP_BACKED_FULL15_COMPLETION')
    return row


class PendingTicketProbe:
    def __init__(self, snapshot):
        self.snapshot = snapshot
        self.rows = []
        self.first = None
        self.submissions = []

    def apply(self, frame, producer, operations, validate, crossing):
        if self.first is not None or not frame['start'] <= frame['t'] < frame['end']:
            return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t'] != frame['t']:
            return operations
        ledger = frame['ledger']
        active = producer.opportunity.submissions
        if len(active) != 1:
            confirmed = False
        else:
            owner = ledger.carriers.get(active[0]['key'])
            confirmed = owner is not None and owner.state == 'TERMINAL' and float(owner.filled) > EPS
        state = self.snapshot(frame, ledger)
        price = producer.demand.rows[-1]['eligibility']['price']
        ask = ((frame.get('quotes') or {}).get('DOWN') or {}).get('ask')
        row = decide(state, operations, price, ask, confirmed, crossing)
        row.update(t=int(frame['t']), state=state, original_operations=[dict(o) for o in operations],
                   gateway_state_id=frame['gateway_state_id'])
        if row['eligible']:
            count = len(state['owners']) + sum(o['kind'] == 'NEW' for o in operations)
            if count >= frame['world_profile']['max_live_owners']:
                row.update(eligible=False, reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'], 'PASSIVE', price, 15.,
                         quantity_step=frame['world_profile']['quantity_step'])
                assert abs(price / frame['world_profile']['tick'] - round(price / frame['world_profile']['tick'])) < EPS
        self.rows.append(row)
        if not row['eligible']:
            return operations
        self.first = row
        index = frame['own_view']['n'] + sum(o['kind'] == 'NEW' for o in operations)
        op = dict(kind='NEW', key=f'DOWN_{index}', parent_id=2, side='DOWN', route='PASSIVE',
                  price=price, qty=15., role='PASSIVE_PENDING_TICKET_COMPLETION')
        producer.passive_births += 1
        self.submissions.append(dict(t=int(frame['t']), **op))
        return [*operations, op]


def instrument(source, replace):
    marker = 'self.opportunity=_ActiveOpportunity(_OPPORTUNITY_MODE,_OWN_SNAPSHOT)'
    source = replace(source, marker, marker + ';self.pending_ticket=_PendingTicketProbe(_OWN_SNAPSHOT)')
    marker = '   producer.demand.on_plan(f,ops)'
    source = replace(source, marker,
                     '   ops=producer.pending_ticket.apply(f,producer,ops,validate_size,_goal_crossing)\n' + marker)
    marker = '  result.update(active_opportunity_mode='
    source = replace(source, marker,
                     "  result.update(pending_ticket_first=producer.pending_ticket.first,pending_ticket_submissions=producer.pending_ticket.submissions)\n" + marker)
    return source


def self_test(crossing):
    from copy import deepcopy
    s = dict(inv=dict(UP=100., DOWN=80.), payoff=dict(UP=15., DOWN=-5.),
             pending_qty=dict(UP=20., DOWN=10.), pending_cash=dict(UP=18., DOWN=.7),
             owners=[dict(key='u', side='UP', state='SUBMITTED', qty=20., limit=.90),
                     dict(key='d', side='DOWN', state='CANCEL_PENDING', qty=10., limit=.07)])
    valid = decide(s, [], .07, .09, True, crossing)
    assert valid['eligible'] and valid['uncovered_filled_gap'] == 10 and valid['excess_over_filled_gap'] == 5
    assert not valid['cash_budget_enabled'] and valid['pending_qty']['DOWN'] == 10
    for price, ask, confirmed in [(.06, .09, True), (.09, .09, True), (.07, .09, False)]:
        assert not decide(s, [], price, ask, confirmed, crossing)['eligible']
    changed = deepcopy(s); changed['owners'][0]['state'] = 'CANCEL_PENDING'
    assert not decide(changed, [], .07, .09, True, crossing)['eligible']
    assert not decide(s, [dict(kind='CANCEL', key='u')], .07, .09, True, crossing)['eligible']
    changed = deepcopy(s); changed['owners'][0]['limit'] = .93
    assert decide(changed, [], .07, .09, True, crossing)['conflicts'] == ['u']
    changed = deepcopy(s); changed['owners'][1]['limit'] = .99
    # Same-side cancellation remains reserved; it neither self-crosses nor restores quantity capacity.
    r = decide(changed, [dict(kind='CANCEL', key='d')], .07, .09, True, crossing)
    assert r['eligible'] and r['uncovered_filled_gap'] == 10
    assert not decide(s, [dict(kind='NEW', key='n', side='DOWN', qty=15., price=.07)], .07, .09, True, crossing)['eligible']
    for down in (70., 90.):
        changed = deepcopy(s); changed['inv']['DOWN'] = down
        assert not decide(changed, [], .07, .09, True, crossing)['eligible']
    changed = deepcopy(s); changed['payoff'] = dict(UP=-30., DOWN=-50.)
    assert decide(changed, [], .07, .09, True, crossing)['eligible'], 'No hidden positive-profit cash cap'
    changed = deepcopy(s); changed['payoff']['DOWN'] = 1.
    assert not decide(changed, [], .07, .09, True, crossing)['eligible']
    return dict(status='PASS', cash_cap=False, cancel_pending_reserved=True, same_plan_checked=True)
