from roles_runtime import roles
'Two independent repair-only corrections, with the V24 demand kept pinned.'
import importlib.util
import math
from pathlib import Path
CONCURRENT = True
LEGAL_QUOTE = True
EPS = 1e-08

def load_base():
    path = Path(__file__).with_name('commitment_base.py')
    if not path.exists():
        path = Path(__file__).with_name('btc5m_commitment_repair_probe_v1.py')
    spec = importlib.util.spec_from_file_location('pinned_commitment_base', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
base = load_base()

def legal_quote(raw, ask, tick=0.01, ticket=15.0, enabled=True):
    minimum = round(math.ceil((1.0 / ticket - EPS) / tick) * tick, 10)
    price = raw
    reason = 'UNCHANGED'
    if enabled and raw is not None and (raw < minimum - EPS):
        if ask is not None and minimum < ask - EPS:
            price = minimum
            reason = 'MINIMUM_LEGAL_PASSIVE_TICKET'
        else:
            reason = 'NO_LEGAL_PASSIVE_PRICE'
    return dict(raw_price=raw, price=price, ask=ask, minimum_legal_price=minimum, changed=price != raw, reason=reason, enabled=enabled)

def decide(state, operations, raw_price, ask, active_confirmed, outstanding, crossing, tick=0.01):
    quote = legal_quote(raw_price, ask, tick, enabled=LEGAL_QUOTE and active_confirmed)
    known = {o['key'] for o in state['owners']}
    missing = [k for k in outstanding if k not in known]
    gate_owners = [] if CONCURRENT else outstanding
    row = base.decide(state, operations, quote['price'], ask, active_confirmed, gate_owners, crossing)
    row.update(outstanding=list(outstanding), missing_reserved_owners=missing, concurrent_repair=CONCURRENT, quote=quote)
    if active_confirmed and missing:
        row.update(eligible=False, reason='WAIT_FOR_OBSERVABLE_OWNER_RESERVATION')
    return row

class CommitmentRepairProbe(base.CommitmentRepairProbe):

    def __init__(self, snapshot):
        super().__init__(snapshot)
        self.maintenance_rows = []

    def maintenance(self, frame, key, owner, raw_price, stale, threshold):
        if not LEGAL_QUOTE or key not in {s['key'] for s in self.submissions}:
            return (raw_price, stale)
        ask = ((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
        quote = legal_quote(raw_price, ask, frame['world_profile']['tick'])
        revised = abs(float(owner.limit) - quote['price']) > threshold + 1e-09
        self.maintenance_rows.append(dict(t=int(frame['t']), key=key, owner_state=owner.state, limit=float(owner.limit), quote=quote, threshold=threshold, old_stale=stale, new_stale=revised))
        return (quote['price'], revised)

    def apply(self, frame, producer, operations, validate, crossing):
        if not frame['start'] <= frame['t'] < frame['end']:
            return operations
        if not producer.demand.rows or producer.demand.rows[-1]['t'] != frame['t']:
            return operations
        ledger = frame['ledger']
        active = producer.opportunity.submissions
        owner = ledger.carriers.get(active[0]['key']) if len(active) == 1 else None
        confirmed = owner is not None and owner.state == 'TERMINAL' and (float(owner.filled) > EPS)
        outstanding = [o['key'] for o in self.submissions if o['key'] not in ledger.carriers or ledger.carriers[o['key']].state != 'TERMINAL']
        state = self.snapshot(frame, ledger)
        raw = producer.demand.rows[-1]['eligibility']['price']
        ask = ((frame.get('quotes') or {}).get(roles.weak) or {}).get('ask')
        row = decide(state, operations, raw, ask, confirmed, outstanding, crossing, frame['world_profile']['tick'])
        row.update(t=int(frame['t']), state=state, original_operations=[dict(o) for o in operations], gateway_state_id=frame['gateway_state_id'])
        price = row['price']
        if row['eligible']:
            count = len(state['owners']) + sum((o['kind'] == 'NEW' for o in operations))
            if count >= frame['world_profile']['max_live_owners']:
                row.update(eligible=False, reason='RESOURCE_OWNER_LIMIT')
            else:
                validate(frame['world_profile']['asset'], 'PASSIVE', price, 15.0, quantity_step=frame['world_profile']['quantity_step'])
                assert abs(price / frame['world_profile']['tick'] - round(price / frame['world_profile']['tick'])) < EPS
        self.rows.append(row)
        if not row['eligible']:
            return operations
        if self.first is None:
            self.first = row
        index = frame['own_view']['n'] + sum((o['kind'] == 'NEW' for o in operations))
        op = dict(kind='NEW', key=f'{roles.weak}_{index}', parent_id=roles.pid(roles.weak), side=roles.weak, route='PASSIVE', price=price, qty=15.0, role='PASSIVE_CURRENT_COMMITMENT_REPAIR')
        producer.passive_births += 1
        self.submissions.append(dict(t=int(frame['t']), **op))
        return [*operations, op]

def instrument(source, replace):
    source = base.instrument(source, replace)
    if LEGAL_QUOTE:
        marker = '     self.demand.maintenance(f,k,s,prices[s],stale,surplus)'
        source = replace(source, marker, "     maintenance_price=prices[s]\n     if s=='DOWN':maintenance_price,stale=self.commitment_repair.maintenance(f,k,c,prices[s],stale,tick*(1.+softplus(w[11])))\n     self.demand.maintenance(f,k,s,maintenance_price,stale,surplus)")
    return source

def self_test(crossing):
    from copy import deepcopy
    global CONCURRENT, LEGAL_QUOTE
    saved = (CONCURRENT, LEGAL_QUOTE)
    try:
        s = dict(inv=dict(**{roles.strong: 100.0}, **{roles.weak: 80.0}), payoff=dict(**{roles.strong: 15.0}, **{roles.weak: -5.0}), pending_qty=dict(**{roles.strong: 30.0}, **{roles.weak: 15.0}), pending_cash=dict(**{roles.strong: 27.0}, **{roles.weak: 1.05}), owners=[dict(key='u', side=roles.strong, state='SUBMITTED', qty=30.0, limit=0.9), dict(key='d', side=roles.weak, state='CANCEL_PENDING', qty=15.0, limit=0.07)])
        CONCURRENT = True
        LEGAL_QUOTE = False
        r = decide(s, [], 0.07, 0.09, True, ['d'], crossing)
        assert r['eligible'] and r['pending_qty'][roles.weak] == 15.0
        assert abs(r['deterioration_debt'] - 13.05) < EPS
        assert not decide(s, [], 0.07, 0.09, True, ['missing'], crossing)['eligible']
        c = deepcopy(s)
        c['pending_qty'][roles.weak] = 30.0
        c['pending_cash'][roles.weak] = 2.1
        c['owners'].append(dict(key='d2', side=roles.weak, state='UNKNOWN', qty=15.0, limit=0.07))
        assert not decide(c, [], 0.07, 0.09, True, ['d', 'd2'], crossing)['eligible'], 'All pending supply already covers this debt'
        assert not decide(c, [dict(kind='CANCEL', key='d2')], 0.07, 0.09, True, ['d', 'd2'], crossing)['eligible']
        assert decide(s, [], 0.07, 0.09, True, ['d'], crossing)['eligible']
        CONCURRENT = False
        assert not decide(s, [], 0.07, 0.09, True, ['d'], crossing)['eligible']
        for raw, ask, price in ((0.06, 0.09, 0.07), (0.05, 0.08, 0.07), (0.06, 0.07, 0.06), (0.08, 0.1, 0.08), (None, 0.09, None), (0.0, 0.09, 0.07), (-0.01, 0.09, 0.07)):
            assert legal_quote(raw, ask)['price'] == price
        assert legal_quote(0.04, 0.06, 0.01, 30.0)['price'] == 0.04, 'Floor comes from quantity and tick, not a hardcoded .07'
        LEGAL_QUOTE = True
        assert decide(s, [], 0.05, 0.09, True, [], crossing)['eligible']
        assert not decide(s, [], 0.05, 0.07, True, [], crossing)['eligible']
        assert decide(s, [], 0.05, 0.09, False, [], crossing)['price'] == 0.05, 'Existing Active selection remains untouched'
        c = deepcopy(s)
        c['owners'][0]['limit'] = 0.93
        assert not decide(c, [dict(kind='CANCEL', key='u')], 0.05, 0.09, True, [], crossing)['eligible']
        assert not decide(s, [dict(kind='NEW', key='new', side=roles.strong, qty=15.0, price=0.93)], 0.05, 0.09, True, [], crossing)['eligible']
        from types import SimpleNamespace
        obj = CommitmentRepairProbe(None)
        obj.submissions = [dict(key='extra')]
        frame = dict(t=1, quotes=dict(**{roles.weak: dict(ask=0.09)}), world_profile=dict(tick=0.01))
        owner = SimpleNamespace(limit=0.07, state='SUBMITTED')
        assert obj.maintenance(frame, 'extra', owner, 0.05, True, 0.011) == (0.07, False)
        assert obj.maintenance(frame, 'original', owner, 0.05, True, 0.011) == (0.05, True)
        frame['quotes'][roles.weak]['ask'] = 0.07
        assert obj.maintenance(frame, 'extra', owner, 0.05, True, 0.011) == (0.05, True)
        return dict(status='PASS', pending_supply_counted=True, missing_owner_blocks=True, release_only_terminal=True, fixed_ticket_legal_quote=True, current_ask_and_cross_checked=True, maintenance_uses_same_quote=True, active_selection_unchanged=True)
    finally:
        CONCURRENT, LEGAL_QUOTE = saved
