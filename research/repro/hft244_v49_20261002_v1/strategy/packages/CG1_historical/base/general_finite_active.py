"""Bounded ordinary Active service for an already-open finite repair work.

This smoke component does not use Target actions, a fixed waiting time, or a
price-only opportunity gate.  It reacts only after canonical state has
actually released weak-side reservation without increasing confirmed repair
progress.  Existing plan reservations are counted first; Active can use only
the still-unreserved portion of the same finite work.

Passive NEW remains unchanged and may coexist with this Active child.  CANCEL
intent never releases reservation because the input state continues to count
CANCEL_PENDING/UNKNOWN owners until canonical terminal state removes them.
"""
from copy import deepcopy
import math
from roles_runtime import roles

EPS = 1e-8
GLOBAL_ACTIVE_CAP = 5


def _floor_step(x, step):
    return max(0.0, round(math.floor((max(0.0, x) + 1e-10) / step) * step, 8))


def decide(state, progress, operations, ask, depth, previous, crossing, step=0.01):
    pending_qty = dict(state['pending_qty'])
    pending_cash = dict(state['pending_cash'])
    owners = [dict(key=o['key'], side=o['side'], price=o['limit']) for o in state['owners']]
    active_in_plan = False
    weak_new_qty = 0.0
    for op in operations:
        if op['kind'] != 'NEW':
            continue
        side = op['side']
        qty = float(op['qty']); price = float(op['price'])
        pending_qty[side] += qty
        pending_cash[side] += qty * price
        owners.append(dict(key=op['key'], side=side, price=price))
        if op.get('route') == 'ACTIVE':
            active_in_plan = True
        if side == roles.weak:
            weak_new_qty += qty

    work_id = progress.get('work_id')
    acquired = float(progress.get('acquired_since_birth', 0.0) or 0.0)
    remaining = float(progress.get('remaining_confirmed', 0.0) or 0.0)
    current_pending_before_plan = float(state['pending_qty'][roles.weak])
    released = 0.0
    same_work = previous is not None and previous.get('work_id') == work_id
    no_confirmed_progress = False
    if same_work:
        released = max(0.0, float(previous['pending_weak']) - current_pending_before_plan)
        no_confirmed_progress = acquired <= float(previous['acquired']) + EPS

    unreserved = max(0.0, remaining - pending_qty[roles.weak])
    net_capacity = max(0.0, state['inv'][roles.strong] - state['inv'][roles.weak] - pending_qty[roles.weak])
    valid_market = ask is not None and 0 < float(ask) < 1 and float(depth) > 0
    projected_weak = state['payoff'][roles.weak] + pending_qty[roles.weak] - pending_cash[roles.weak] - pending_cash[roles.strong]
    money_capacity = max(0.0, -projected_weak / (1.0 - float(ask))) if valid_market and projected_weak < 0 else 0.0
    raw = min(unreserved, net_capacity, float(depth), money_capacity) if valid_market else 0.0
    qty = _floor_step(raw, step)
    conflicts = crossing(roles.weak, float(ask), owners) if valid_market and qty > EPS else []

    row = dict(
        eligible=False,
        reason='NOT_ELIGIBLE',
        work_id=work_id,
        same_work=same_work,
        released_reservation=released,
        no_confirmed_progress=no_confirmed_progress,
        acquired_since_birth=acquired,
        remaining_confirmed=remaining,
        pending_qty_after_plan=pending_qty,
        pending_cash_after_plan=pending_cash,
        weak_new_qty_in_original_plan=weak_new_qty,
        unreserved_after_plan=unreserved,
        net_quantity_capacity=net_capacity,
        projected_weak_payoff_after_plan=projected_weak,
        money_capacity=money_capacity,
        active_ask=ask,
        visible_depth=depth,
        quantity=qty,
        quoted_cost=qty * float(ask) if valid_market else 0.0,
        conflicts=conflicts,
        existing_active_in_plan=active_in_plan,
    )
    if progress.get('status') != 'ACTIVE' or work_id is None:
        row['reason'] = 'NO_ACTIVE_FINITE_WORK'
    elif not same_work:
        row['reason'] = 'WAIT_FOR_SAME_WORK_HISTORY'
    elif released <= EPS:
        row['reason'] = 'NO_CANONICAL_RESERVATION_RELEASE'
    elif not no_confirmed_progress:
        row['reason'] = 'CONFIRMED_REPAIR_PROGRESS_OBSERVED'
    elif active_in_plan:
        row['reason'] = 'EXISTING_ACTIVE_PLAN_HAS_PRIORITY'
    elif unreserved <= EPS:
        row['reason'] = 'FINITE_WORK_FULLY_RESERVED'
    elif not valid_market:
        row['reason'] = 'NO_CURRENT_ACTIVE_LIQUIDITY'
    elif state['payoff'][roles.weak] >= -EPS or money_capacity <= EPS:
        row['reason'] = 'NO_NEGATIVE_WEAK_ECONOMIC_DEBT'
    elif net_capacity <= EPS:
        row['reason'] = 'NO_CONFIRMED_NET_CAPACITY_AFTER_RESERVATION'
    elif conflicts:
        row['reason'] = 'PENDING_OR_SAME_PLAN_OWN_CROSS'
    elif qty <= EPS or qty * float(ask) < 1.0 - EPS:
        row['reason'] = 'ACTIVE_NEW_BELOW_ONE_DOLLAR_OR_STEP'
    else:
        row.update(eligible=True, reason='FINITE_WORK_RELEASED_CAPACITY_ACTIVE_SERVICE')
    return row


class GeneralFiniteActive:
    def __init__(self):
        self.previous = None
        self.served_work_ids = set()
        self.rows = []
        self.submissions = []

    def _remember(self, demand_row):
        progress = demand_row.get('progress') or {}
        self.previous = dict(
            work_id=demand_row.get('work_id'),
            pending_weak=float(demand_row['state']['pending_qty'][roles.weak]),
            acquired=float(progress.get('acquired_since_birth', 0.0) or 0.0),
        )

    def apply(self, frame, producer, operations, validate, crossing):
        if not frame['start'] <= frame['t'] < frame['end']:
            return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t'] != frame['t']:
            return operations
        demand_row = producer.demand.rows[-1]
        progress = dict(demand_row.get('progress') or {})
        progress['work_id'] = demand_row.get('work_id')
        state = demand_row['state']
        ask = ((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
        bids = roles.weak_bid_book(frame)
        best = max(bids) if bids else None
        depth = float(bids[best]) if best is not None else 0.0
        if ask is not None and best is not None:
            assert abs(float(ask) - round(1 - float(best), 10)) < EPS
        row = decide(state, progress, operations, ask, depth, self.previous, crossing,
                     frame['world_profile']['quantity_step'])
        row.update(t=int(frame['t']), gateway_state_id=frame['gateway_state_id'],
                   state=deepcopy(state), original_operations=deepcopy(operations),
                   already_served_work=(demand_row.get('work_id') in self.served_work_ids))
        work_id = demand_row.get('work_id')
        if row['eligible'] and work_id in self.served_work_ids:
            row.update(eligible=False, reason='SMOKE_ONE_GENERAL_ACTIVE_PER_WORK')
        existing_active = (len(producer.opportunity.submissions) +
                           len(producer.coordination.submissions) +
                           len(self.submissions))
        if row['eligible'] and existing_active >= GLOBAL_ACTIVE_CAP:
            row.update(eligible=False, reason='SMOKE_GLOBAL_ACTIVE_CAP')
        if row['eligible']:
            count = len(state['owners']) + sum(o['kind'] == 'NEW' for o in operations)
            if count >= frame['world_profile']['max_live_owners']:
                row.update(eligible=False, reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'], 'ACTIVE', ask, row['quantity'],
                         quantity_step=frame['world_profile']['quantity_step'])
                assert abs(float(ask) / frame['world_profile']['tick'] -
                           round(float(ask) / frame['world_profile']['tick'])) < EPS
        self.rows.append(row)
        self._remember(demand_row)
        if not row['eligible']:
            return operations
        index = frame['own_view']['n'] + sum(o['kind'] == 'NEW' for o in operations)
        op = dict(kind='NEW', key=f'{roles.weak}_{index}', parent_id=roles.pid(roles.weak),
                  side=roles.weak, route='ACTIVE', price=float(ask), qty=row['quantity'],
                  role='ACTIVE_GENERAL_FINITE_WORK_SERVICE')
        self.served_work_ids.add(work_id)
        self.submissions.append(dict(t=int(frame['t']), work_id=work_id,
                                     released_reservation=row['released_reservation'],
                                     unreserved_after_plan=row['unreserved_after_plan'], **op))
        return [*operations, op]


def instrument(source, replace):
    marker = 'self.commitment_repair=_CommitmentRepairProbe(_OWN_SNAPSHOT)'
    source = replace(source, marker, marker + ';self.general_finite_active=_GeneralFiniteActive()')
    marker = '   producer.demand.on_plan(f,ops)'
    source = replace(source, marker,
                     '   ops=producer.general_finite_active.apply(f,producer,ops,validate_size,_goal_crossing)\n' + marker)
    old = "active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)+len(producer.coordination.submissions)<=5 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=4 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))"
    new = "active_matches_opportunity=(len(active)==len(producer.opportunity.submissions)+len(producer.coordination.submissions)+len(producer.general_finite_active.submissions)<=5 and len(producer.opportunity.submissions)<=1 and len(producer.coordination.submissions)<=4 and len(producer.general_finite_active.submissions)<=5 and (not active or producer.opportunity.mode=='ONE_ACTIVE'))"
    source = replace(source, old, new)
    source = replace(source,
                     'active_births=producer.active_births+producer.opportunity.submissions+producer.coordination.submissions,',
                     'active_births=producer.active_births+producer.opportunity.submissions+producer.coordination.submissions+producer.general_finite_active.submissions,')
    marker = '  result.update(coordination_episode=producer.coordination.episode,coordination_submissions=producer.coordination.submissions)'
    source = replace(source, marker,
                     marker + "\n  result.update(general_finite_active_rows=producer.general_finite_active.rows,general_finite_active_submissions=producer.general_finite_active.submissions)")
    return source


def self_test(crossing):
    strong, weak = roles.strong, roles.weak
    state = dict(
        inv={strong: 100.0, weak: 40.0},
        payoff={strong: 20.0, weak: -30.0},
        pending_qty={strong: 0.0, weak: 10.0},
        pending_cash={strong: 0.0, weak: 4.0},
        owners=[dict(key='old', side=weak, state='SUBMITTED', qty=10.0, limit=0.4)],
    )
    progress = dict(work_id=7, status='ACTIVE', acquired_since_birth=0.0,
                    remaining_confirmed=40.0)
    previous = dict(work_id=7, pending_weak=25.0, acquired=0.0)
    ops = [dict(kind='NEW', key='p', side=weak, route='PASSIVE', price=0.4, qty=15.0)]
    r = decide(state, progress, ops, 0.5, 8.0, previous, crossing, 0.01)
    assert r['eligible'] and r['released_reservation'] == 15.0
    assert abs(r['unreserved_after_plan'] - 15.0) < EPS and r['quantity'] == 8.0
    full = deepcopy(state); full['pending_qty'][weak] = 25.0; full['pending_cash'][weak] = 10.0
    assert not decide(full, progress, ops, 0.5, 8.0, previous, crossing, 0.01)['eligible']
    progressed = dict(progress, acquired_since_birth=5.0)
    assert not decide(state, progressed, ops, 0.5, 8.0, previous, crossing, 0.01)['eligible']
    cancel_only = [dict(kind='CANCEL', key='old')]
    c = decide(state, progress, cancel_only, 0.5, 8.0, previous, crossing, 0.01)
    assert c['pending_qty_after_plan'][weak] == 10.0, 'CANCEL intent must not release reservation'
    return dict(status='PASS', shared_finite_quota=True, canonical_release_only=True,
                confirmed_progress_blocks_escalation=True, cancel_pending_reserved=True,
                passive_and_active_can_coexist=True)
