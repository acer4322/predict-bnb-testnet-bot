"""Recurrent finite Passive15 authority for current commitment deterioration.

The reference is the current confirmed payoff floor, never a zero target.
Uncancelled SUBMITTED UP plus same-plan UP NEW define a conditional scenario,
not predicted or confirmed fills. All DOWN reservations remain counted.
"""
EPS = 1e-8


def decide(state, operations, price, ask, active_confirmed, outstanding, crossing):
    cancelling = {o['key'] for o in operations if o['kind'] == 'CANCEL'}
    pending = dict(state['pending_qty']); cash = dict(state['pending_cash'])
    owners = [dict(key=o['key'], side=o['side'], price=o['limit']) for o in state['owners']]
    usable = [o for o in state['owners'] if o['side'] == 'UP' and
              o['state'] == 'SUBMITTED' and o['key'] not in cancelling]
    up_qty = sum(o['qty'] for o in usable)
    up_cash = sum(o['qty'] * o['limit'] for o in usable)
    for op in operations:
        if op['kind'] == 'NEW':
            pending[op['side']] += op['qty']; cash[op['side']] += op['qty'] * op['price']
            owners.append(dict(key=op['key'], side=op['side'], price=op['price']))
            if op['side'] == 'UP':
                up_qty += op['qty']; up_cash += op['qty'] * op['price']
    payoff = state['payoff']; floor = min(payoff.values())
    scenario = dict(UP=payoff['UP'] + up_qty - up_cash - cash['DOWN'],
                    DOWN=payoff['DOWN'] + pending['DOWN'] - cash['DOWN'] - up_cash)
    debt = max(0., floor - scenario['DOWN'])
    valid = price is not None and 0 < price < 1
    after = {s: v + (15. if s == 'DOWN' else 0.) - 15.*price for s,v in scenario.items()} if valid else None
    isolated = {s: v + (15. if s == 'DOWN' else 0.) - 15.*price for s,v in payoff.items()} if valid else None
    row = dict(eligible=False, reason='NOT_ELIGIBLE', quantity=15., price=price, ask=ask,
        active_confirmed=active_confirmed, outstanding=list(outstanding), cash_budget_enabled=False,
        pending_qty=pending, pending_cash=cash, usable_pending_up=up_qty, usable_pending_up_cash=up_cash,
        confirmed_payoff_floor=floor, conditional_payoff=scenario, conditional_after_ticket=after,
        confirmed_if_only_ticket_fills=isolated, deterioration_debt=debt,
        money_quantity_need=debt/(1-price) if valid else None,
        old_filled_quantity_capacity=max(0., state['inv']['UP']-state['inv']['DOWN']-pending['DOWN']),
        conflicts=[])
    if not active_confirmed:
        row['reason'] = 'WAIT_FOR_EXISTING_ACTIVE_TERMINAL_RECEIPT'
    elif outstanding:
        row['reason'] = 'WAIT_FOR_EXTRA_OWNER_TERMINAL'
    elif any(o['kind'] == 'NEW' and o['side'] == 'DOWN' for o in operations):
        row['reason'] = 'ORIGINAL_DOWN_NEW_HAS_PRIORITY'
    elif not valid or 15.*price < 1.-EPS:
        row['reason'] = 'FIXED15_NEW_PRICE_INVALID'
    elif ask is None or price >= ask-EPS:
        row['reason'] = 'NO_PASSIVE_SPREAD'
    elif debt <= EPS or scenario['DOWN'] >= scenario['UP']-EPS:
        row['reason'] = 'NO_DOWN_COMMITMENT_DETERIORATION'
    elif min(after.values()) <= min(scenario.values())+EPS:
        row['reason'] = 'TICKET_DOES_NOT_IMPROVE_CONDITIONAL_FLOOR'
    elif min(isolated.values()) <= floor+EPS:
        row['reason'] = 'TICKET_ALONE_DOES_NOT_IMPROVE_CONFIRMED_FLOOR'
    else:
        row['conflicts'] = crossing('DOWN',price,owners)
        row.update(eligible=not row['conflicts'], reason='PENDING_OR_SAME_PLAN_OWN_CROSS'
                   if row['conflicts'] else 'CURRENT_COMMITMENT_FLOOR_REPAIR')
    return row


class CommitmentRepairProbe:
    def __init__(self, snapshot):
        self.snapshot = snapshot; self.rows = []; self.first = None; self.submissions = []

    def apply(self, frame, producer, operations, validate, crossing):
        if not frame['start'] <= frame['t'] < frame['end']:
            return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t'] != frame['t']:
            return operations
        ledger = frame['ledger']; active = producer.opportunity.submissions
        owner = ledger.carriers.get(active[0]['key']) if len(active) == 1 else None
        confirmed = owner is not None and owner.state == 'TERMINAL' and float(owner.filled) > EPS
        outstanding = [o['key'] for o in self.submissions if o['key'] not in ledger.carriers
                       or ledger.carriers[o['key']].state != 'TERMINAL']
        state = self.snapshot(frame, ledger)
        price = producer.demand.rows[-1]['eligibility']['price']
        ask = ((frame.get('quotes') or {}).get('DOWN') or {}).get('ask')
        row = decide(state, operations, price, ask, confirmed, outstanding, crossing)
        row.update(t=int(frame['t']),state=state,original_operations=[dict(o) for o in operations],
                   gateway_state_id=frame['gateway_state_id'])
        if row['eligible']:
            count = len(state['owners']) + sum(o['kind']=='NEW' for o in operations)
            if count >= frame['world_profile']['max_live_owners']:
                row.update(eligible=False,reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'],'PASSIVE',price,15.,
                         quantity_step=frame['world_profile']['quantity_step'])
                assert abs(price/frame['world_profile']['tick']-round(price/frame['world_profile']['tick'])) < EPS
        self.rows.append(row)
        if not row['eligible']:
            return operations
        if self.first is None:
            self.first = row
        index = frame['own_view']['n'] + sum(o['kind']=='NEW' for o in operations)
        op = dict(kind='NEW',key=f'DOWN_{index}',parent_id=2,side='DOWN',route='PASSIVE',
                  price=price,qty=15.,role='PASSIVE_CURRENT_COMMITMENT_REPAIR')
        producer.passive_births += 1
        self.submissions.append(dict(t=int(frame['t']),**op))
        return [*operations,op]


def instrument(source, replace):
    marker='self.opportunity=_ActiveOpportunity(_OPPORTUNITY_MODE,_OWN_SNAPSHOT)'
    source=replace(source,marker,marker+';self.commitment_repair=_CommitmentRepairProbe(_OWN_SNAPSHOT)')
    marker='   producer.demand.on_plan(f,ops)'
    source=replace(source,marker,'   ops=producer.commitment_repair.apply(f,producer,ops,validate_size,_goal_crossing)\n'+marker)
    marker='  result.update(active_opportunity_mode='
    return replace(source,marker,'  result.update(commitment_repair_first=producer.commitment_repair.first,commitment_repair_submissions=producer.commitment_repair.submissions)\n'+marker)


def self_test(crossing):
    from copy import deepcopy
    s=dict(inv=dict(UP=100.,DOWN=80.),payoff=dict(UP=15.,DOWN=-5.),
           pending_qty=dict(UP=30.,DOWN=15.),pending_cash=dict(UP=27.,DOWN=1.05),
           owners=[dict(key='u',side='UP',state='SUBMITTED',qty=30.,limit=.90),
                   dict(key='d',side='DOWN',state='CANCEL_PENDING',qty=15.,limit=.07)])
    def check(st=s,ops=(),p=.07,ask=.09,active=True,live=()):
        return decide(st,list(ops),p,ask,active,list(live),crossing)
    r=check(); assert r['eligible'] and r['old_filled_quantity_capacity']==5.
    assert abs(r['deterioration_debt']-13.05)<EPS and not r['cash_budget_enabled']
    for p,ask,active,live in [(.06,.09,True,()),(.09,.09,True,()),(.07,.09,False,()),(.07,.09,True,('unknown',))]:
        assert not check(p=p,ask=ask,active=active,live=live)['eligible']
    for status in ('CANCEL_PENDING','UNKNOWN','TERMINAL'):
        changed=deepcopy(s);changed['owners'][0]['state']=status
        assert not check(st=changed)['eligible']
    assert not check(ops=[dict(kind='CANCEL',key='u')])['eligible']
    assert check(ops=[dict(kind='CANCEL',key='d')])['pending_qty']['DOWN']==15.
    changed=deepcopy(s);changed['owners'][0]['limit']=.93
    assert check(st=changed)['conflicts']==['u']
    assert not check(ops=[dict(kind='NEW',key='new',side='UP',qty=15.,price=.94)])['eligible']
    assert not check(ops=[dict(kind='NEW',key='new',side='DOWN',qty=15.,price=.07)])['eligible']
    # Equal payoff translations leave the dynamic reference unchanged: no zero target or profit cash cap.
    for delta in (-100.,100.):
        changed=deepcopy(s);changed['payoff']={k:v+delta for k,v in s['payoff'].items()}
        translated=check(st=changed)
        assert translated['eligible'] and abs(translated['deterioration_debt']-r['deterioration_debt'])<EPS
    changed=deepcopy(s);changed['payoff']['UP']=-4.5
    assert not check(st=changed)['eligible'], 'Ticket alone must improve actual filled floor'
    return dict(status='PASS',zero_target=False,cash_cap=False,unknown_reserved=True,
                current_original_plan_priority=True,conditional_not_promised_fill=True)
