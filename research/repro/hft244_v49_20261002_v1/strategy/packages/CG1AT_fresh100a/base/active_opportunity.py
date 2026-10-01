from roles_runtime import roles
'One causal Active opportunity beside the unchanged Passive15 controller.\n\nDiagnostic only: first eligible own-state event, no Target clock/quantity inputs.\nActive quantity uses existing cash/quantity authority and current top-level depth,\nnever the Passive ticket. Cancel requests do not release draft reservations.\n'
import math
MODES = ('CONTROL', 'ONE_ACTIVE')
EPS = 1e-08

def decide(state, paid, passive_price, ask, depth, operations, step, retention, crossing):
    pending_qty = dict(state['pending_qty'])
    pending_cash = dict(state['pending_cash'])
    owners = [dict(key=o['key'], side=o['side'], price=o['limit']) for o in state['owners']]
    for op in operations:
        if op['kind'] == 'NEW':
            side = op['side']
            pending_qty[side] += op['qty']
            pending_cash[side] += op['qty'] * op['price']
            owners.append(dict(key=op['key'], side=side, price=op['price']))
    gross = state['inv'][roles.strong] - paid[roles.strong]
    assert retention == 0.0, 'This frozen fork has cash budgeting explicitly OFF'
    room = None
    quantity_cap = max(0.0, state['inv'][roles.strong] - state['inv'][roles.weak] - pending_qty[roles.weak])
    report = dict(eligible=False, reason='NOT_ELIGIBLE', passive_price=passive_price, active_ask=ask, visible_depth=depth, paid=dict(paid), confirmed_up_gross=gross, available_cash=room, cash_budget_enabled=False, pending_qty=pending_qty, pending_cash=pending_cash, quantity_cap=quantity_cap, quantity=0.0, conflicts=[])
    if state['payoff'][roles.weak] >= -EPS or quantity_cap <= EPS:
        report['reason'] = 'NO_UNCOVERED_DOWN_REPAIR'
    elif passive_price > 0 and 15.0 * passive_price >= 1 - EPS:
        report['reason'] = 'FIXED15_PASSIVE_PRICE_STILL_AVAILABLE'
    elif ask is None or not 0 < ask < 1 or depth <= 0:
        report['reason'] = 'NO_OBSERVED_ACTIVE_LIQUIDITY'
    else:
        potential = state['payoff'][roles.weak] + pending_qty[roles.weak] - pending_cash[roles.weak] - pending_cash[roles.strong]
        payoff_cap = max(0.0, -potential / (1 - ask))
        raw = min(quantity_cap, payoff_cap, depth)
        quantity = max(0.0, round(math.floor((raw + 1e-10) / step) * step, 8))
        conflicts = crossing(roles.weak, ask, owners)
        report.update(payoff_cap=payoff_cap, quantity=quantity, quoted_cost=quantity * ask, conflicts=conflicts, projected_weak_payoff=potential)
        if conflicts:
            report['reason'] = 'PENDING_OR_SAME_PLAN_OWN_CROSS'
        elif quantity * ask < 1 - EPS:
            report['reason'] = 'ACTIVE_NEW_BELOW_ONE_DOLLAR_CONDITION'
        else:
            report.update(eligible=True, reason='FIRST_LEGAL_ACTIVE_OPPORTUNITY_NO_CASH_GATE')
    return report

class ActiveOpportunity:

    def __init__(self, mode, snapshot):
        assert mode in MODES
        self.mode = mode
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
        state = self.snapshot(frame, ledger)
        paid = {side: math.fsum((float(c.payment) + float(c.fees) for c in ledger.carriers.values() if ledger.grants[c.parent_id].side == side)) for side in (roles.strong, roles.weak)}
        ask = ((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
        bids = roles.weak_bid_book(frame)
        best = max(bids) if bids else None
        depth = float(bids[best]) if best is not None else 0.0
        if ask is not None and best is not None:
            assert abs(float(ask) - round(1 - float(best), 10)) < EPS
        passive_price = producer.demand.rows[-1]['eligibility']['price']
        row = decide(state, paid, passive_price, ask, depth, operations, frame['world_profile']['quantity_step'], producer.money_gate.retention, crossing)
        row.update(t=int(frame['t']), state=state, original_operations=[dict(o) for o in operations], gateway_state_id=frame['gateway_state_id'])
        if row['eligible']:
            count = len(state['owners']) + sum((o['kind'] == 'NEW' for o in operations))
            if count >= frame['world_profile']['max_live_owners']:
                row.update(eligible=False, reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'], 'ACTIVE', ask, row['quantity'], quantity_step=frame['world_profile']['quantity_step'])
                assert abs(ask / frame['world_profile']['tick'] - round(ask / frame['world_profile']['tick'])) < EPS
        self.rows.append(row)
        if not row['eligible']:
            return operations
        self.first = row
        if self.mode == 'CONTROL':
            return operations
        index = frame['own_view']['n'] + sum((o['kind'] == 'NEW' for o in operations))
        op = dict(kind='NEW', key=f'{roles.weak}_{index}', parent_id=roles.pid(roles.weak), side=roles.weak, route='ACTIVE', price=ask, qty=row['quantity'], role='ACTIVE_OPPORTUNITY_REPAIR')
        self.submissions.append(dict(t=int(frame['t']), **op, quantity_cap=row['quantity_cap'], payoff_cap=row['payoff_cap'], available_cash=row['available_cash']))
        return [*operations, op]

def instrument(source, replace):
    marker = 'self.intent=_ExposureIntent(_MODE)'
    source = replace(source, marker, marker + ';self.opportunity=_ActiveOpportunity(_OPPORTUNITY_MODE,_OWN_SNAPSHOT)')
    marker = '   producer.demand.on_plan(f,ops)'
    source = replace(source, marker, '   ops=producer.opportunity.apply(f,producer,ops,validate_size,_goal_crossing)\n' + marker)
    marker = "passive_only_has_no_active=(a.route_mode!='PASSIVE_ONLY' or len(active)==0 or a.exact_frontier_route=='ACTIVE')"
    source = replace(source, marker, "active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)<=1 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))")
    marker = '  if producer.exact_frontier_key is not None:\n'
    source = replace(source, marker, '  result.update(active_opportunity_mode=producer.opportunity.mode,active_opportunity_first=producer.opportunity.first,active_opportunity_submissions=producer.opportunity.submissions,active_birth_count=len(active),active_births=producer.active_births+producer.opportunity.submissions,legacy_active_policy_disabled=True)\n' + marker)
    return source

def self_test(crossing):
    state = dict(inv=dict(**{roles.strong: 100.0}, **{roles.weak: 40.0}), payoff=dict(**{roles.strong: -10.0}, **{roles.weak: -70.0}), pending_qty=dict(**{roles.strong: 0.0}, **{roles.weak: 0.0}), pending_cash=dict(**{roles.strong: 0.0}, **{roles.weak: 0.0}), owners=[])
    paid = dict(**{roles.strong: 80.0}, **{roles.weak: 30.0})
    r = decide(state, paid, 0.05, 0.07, 20.0, [], 0.01, 0.0, crossing)
    assert r['eligible'] and r['quantity'] == 20.0 and (r['available_cash'] is None)
    assert not r['cash_budget_enabled'] and r['quoted_cost'] > 0
    pending = dict(state, owners=[dict(key='old', side=roles.strong, limit=0.94)], pending_qty=dict(**{roles.strong: 15.0}, **{roles.weak: 0.0}), pending_cash=dict(**{roles.strong: 14.1}, **{roles.weak: 0.0}))
    blocked = decide(pending, paid, 0.05, 0.07, 20.0, [dict(kind='CANCEL', key='old')], 0.01, 0.0, crossing)
    assert not blocked['eligible'] and blocked['conflicts'] == ['old']
    new = [dict(kind='NEW', key='new', side=roles.strong, price=0.94, qty=15.0)]
    assert not decide(state, paid, 0.05, 0.07, 20.0, new, 0.01, 0.0, crossing)['eligible']
    assert not decide(state, paid, 0.07, 0.08, 20.0, [], 0.01, 0.0, crossing)['eligible']
    assert not decide(state, paid, 0.05, 0.07, 10.0, [], 0.01, 0.0, crossing)['eligible']
