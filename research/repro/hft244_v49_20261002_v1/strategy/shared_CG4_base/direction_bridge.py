"""V48 research-only integration. No native import or dispatch in this module."""
from contextlib import contextmanager
from copy import deepcopy
import math

from dual_opening import inventory_direction, opening_plan, passive_prices
from roles_runtime import roles

SIDES = ('UP', 'DOWN')
PARTS = ('demand', 'addition_growth', 'growth_hold', 'opportunity', 'commitment_repair', 'coordination')


def choose_direction(inv, previous, rule, increments, guard):
    candidate = inventory_direction(inv, previous)
    gap = abs(inv['UP'] - inv['DOWN'])
    reason = 'MAJORITY' if candidate != previous else 'RETAIN'
    if rule == 'INVENTORY' or previous is None or candidate == previous:
        return candidate, None, reason
    assert rule == 'REPAIR_GRACE'
    fills = [x for x in increments if x['side'] == candidate and x['qty'] > 1e-8]
    independent = any(x['purpose'] != 'REPAIR' for x in fills)
    only_repair = bool(fills) and not independent
    if gap <= 15. + 1e-8 and not independent and (guard == candidate or only_repair):
        return previous, candidate, 'REPAIR_OVERTAKE_WITHIN_ONE_TICKET'
    return candidate, None, 'INDEPENDENT_FILL' if independent else 'OVERTAKE_EXCEEDS_ONE_TICKET'


class DirectionBridge:
    def __init__(self, rule, snapshot):
        self.rule = rule
        self.snapshot = snapshot
        self.banks = {}
        self.guard = None
        self.seen = {}
        self.births = {}
        self.frames = []
        self.decisions = []
        self.opening_rows = []
        self.cap_rows = []
        self.maintenance_rows = []
        self.work_observations = []
        self.final = None

    def initialize(self, producer):
        if self.banks or self.rule == 'LEGACY':
            return
        initial = {k: getattr(producer, k) for k in PARTS}
        for side in SIDES:
            bank = deepcopy(initial)
            bank['addition_growth'].strong = side
            assert bank['commitment_repair'].coordinator is bank['coordination']
            self.banks[side] = bank

    @contextmanager
    def scope(self, producer, side):
        before_side = roles.side
        before = {k: getattr(producer, k) for k in PARTS}
        try:
            roles.side = side
            for k, value in self.banks[side].items():
                setattr(producer, k, value)
            yield
        finally:
            roles.side = before_side
            for k, value in before.items():
                setattr(producer, k, value)

    def observe(self, frame, producer):
        if self.rule == 'LEGACY':
            roles.observe(frame)
            return
        self.initialize(producer)
        increments = []
        for key, owner in frame['ledger'].carriers.items():
            filled = float(owner.filled)
            delta = filled - self.seen.get(key, 0.)
            assert delta >= -1e-8
            if delta > 1e-8:
                birth = self.births[key]
                increments.append(dict(key=key, side=birth['side'], purpose=birth['purpose'], qty=delta))
            self.seen[key] = filled
        previous = roles.side
        inv = dict(frame['own_view']['inv'])
        selected, self.guard, reason = choose_direction(inv, previous, self.rule, increments, self.guard)
        roles.side = selected
        row = dict(t=int(frame['t']), index=int(frame['index']), side=selected, previous=previous,
                   inv=inv, guard=self.guard, reason=reason, increments=increments)
        self.decisions.append(row)
        roles.rows.append(dict(row))
        if selected is not None and roles.birth is None:
            roles.birth = dict(row)
        state = self.snapshot(frame, frame['ledger'])
        self.frames.append(dict(t=int(frame['t']), index=int(frame['index']), start=frame['start'], end=frame['end'],
            selected=selected, state=state, book=deepcopy(frame['book']), quotes=deepcopy(frame.get('quotes')),
            cancellable=dict(frame['cancellable']), own_n=frame['own_view']['n'], world_profile=deepcopy(frame['world_profile'])))
        for side, bank in self.banks.items():
            with self.scope(producer, side):
                bank['demand'].observe(frame)
                work = bank['demand'].current_work
                self.work_observations.append(dict(t=int(frame['t']), index=int(frame['index']), bank=side,
                    work_id=work['id'] if work else None, progress=bank['demand'].goal.update(state, frame['t'], frame['end']) if work else None))
        for k, value in self.banks[selected or 'UP'].items():
            setattr(producer, k, value)

    def observe_demand(self, frame, producer):
        if self.rule == 'LEGACY':
            producer.demand.observe(frame)

    def opening(self, frame, producer, validate, crossing):
        if self.rule == 'LEGACY' or roles.side is not None:
            return None
        tick = frame['world_profile']['tick']
        state = self.snapshot(frame, frame['ledger'])
        book = frame['book']
        prices = passive_prices(dict(best_bid=max(book['bids']), best_ask=min(book['asks'])), producer.theta[9], tick)
        asks = {s: (frame.get('quotes') or {}).get(s, {}).get('ask') for s in SIDES}
        planned = opening_plan(frame, None, prices, asks, state['owners'], crossing,
            1. + math.log1p(math.exp(producer.theta[11])), tick)
        operations = planned['operations']
        draft = deepcopy(frame['ledger'])
        try:
            if sum(o['kind'] == 'NEW' for o in operations) + len(state['owners']) > frame['world_profile']['max_live_owners']:
                raise ValueError('NEUTRAL_PAIR_NEEDS_TWO_SLOTS')
            for o in operations:
                if o['kind'] == 'NEW':
                    validate(frame['world_profile']['asset'], 'PASSIVE', o['price'], o['qty'], quantity_step=frame['world_profile']['quantity_step'])
                    draft.reserve(o['key'], o['parent_id'], 'PASSIVE', o['qty'], o['price'], 0., now_ms=frame['t'], market_end_ms=frame['end'])
                else:
                    draft.request_cancel(o['key'])
        except ValueError as error:
            operations = []
            planned.update(reason='CANONICAL_DRAFT_DECLINED', error=str(error))
        planned.update(t=int(frame['t']), index=int(frame['index']), operations=deepcopy(operations))
        self.opening_rows.append(planned)
        count = sum(o['kind'] == 'NEW' for o in operations)
        producer.passive_births += count
        if count:
            producer.initial_new_sent = True
            producer.bidirectional_plans += 1
            producer.multi_new_plans += 1
            producer.new_eligible_frames += 1
        return operations

    def all_submissions(self, producer, part):
        if self.rule == 'LEGACY':
            return getattr(producer, part).submissions
        return sorted([dict(x, bank=s) for s, bank in self.banks.items() for x in bank[part].submissions], key=lambda x: (x.get('t', 0), x['key']))

    def counts(self, operations=()):
        active = [x for x in self.births.values() if x['route'] == 'ACTIVE']
        active += [x for x in operations if x['kind'] == 'NEW' and x['route'] == 'ACTIVE']
        return dict(total=len(active), opportunity=sum(x['role'] == 'ACTIVE_OPPORTUNITY_REPAIR' for x in active),
            renewed=sum(x['role'].startswith('ACTIVE_RENEWED_') for x in active),
            coordination=sum(x['role'] == 'ACTIVE_CONFIRMED_REEXPOSURE_REPAIR' for x in active))

    def service(self, part, frame, producer, operations, validate, crossing):
        service = getattr(producer, part)
        if self.rule == 'LEGACY':
            return service.apply(frame, producer, operations, validate, crossing)
        if roles.side is None:
            return operations
        counts = self.counts(operations)
        category = 'opportunity' if part == 'opportunity' else ('renewed' if len(producer.coordination.submissions) >= 2 else 'coordination')
        limit = 1 if category == 'opportunity' else 2
        if part != 'commitment_repair' and (counts['total'] >= 5 or counts[category] >= limit):
            self.cap_rows.append(dict(t=frame['t'], index=frame['index'], bank=roles.side, part=part, counts=counts, category=category, limit=limit))
            return operations
        return service.apply(frame, producer, operations, validate, crossing)

    def on_plan(self, frame, producer, operations):
        if self.rule == 'LEGACY':
            producer.demand.on_plan(frame, operations)
            return
        for o in operations:
            if o['kind'] == 'NEW':
                assert o['key'] not in self.births
                purpose = 'NEUTRAL' if roles.side is None else 'ADD' if o['side'] == roles.side else 'REPAIR'
                self.births[o['key']] = dict(o, t=int(frame['t']), index=int(frame['index']), purpose=purpose, bank=roles.side)
        counts = self.counts()
        assert counts['total'] <= 5 and counts['opportunity'] <= 1 and counts['coordination'] <= 2 and counts['renewed'] <= 2, counts
        producer.demand.on_plan(frame, operations)

    def maintenance(self, frame, producer, key, owner, price, stale, threshold):
        physical = frame['ledger'].grants[owner.parent_id].side
        if self.rule == 'LEGACY':
            return producer.commitment_repair.maintenance(frame, key, owner, price, stale, threshold) if physical == roles.weak else (price, stale)
        birth = self.births[key]
        bank = birth['bank']
        if birth['purpose'] != 'REPAIR':
            return price, stale
        with self.scope(producer, bank):
            assert physical == roles.weak
            output = producer.commitment_repair.maintenance(frame, key, owner, price, stale, threshold)
        self.maintenance_rows.append(dict(t=frame['t'], index=frame['index'], key=key, physical=physical, birth_bank=bank, selected=roles.side, input_price=price, input_stale=stale, threshold=threshold, output_price=output[0], output_stale=output[1]))
        return output

    def demand_maintenance(self, frame, producer, key, side, price, stale, surplus):
        if self.rule == 'LEGACY':
            return producer.demand.maintenance(frame, key, side, price, stale, surplus)
        bank = self.births[key]['bank'] or 'UP'
        with self.scope(producer, bank):
            producer.demand.maintenance(frame, key, side, price, stale, surplus)

    def finish(self, producer, ledger, seen, native_orders):
        if self.rule == 'LEGACY':
            return producer.demand.finish(ledger, seen, native_orders)
        for side, bank in self.banks.items():
            with self.scope(producer, side):
                bank['demand'].finish(ledger, seen, native_orders)
        self.final = deepcopy(producer.demand.final)
        self.final['works'] = [dict(w, bank=s) for s,b in self.banks.items() for w in b['demand'].works]

    def trace_payload(self):
        if self.rule == 'LEGACY':
            return dict(direction_rule=self.rule)
        banks = {}
        mapping = dict(demand_rows=('demand','rows'), demand_events=('demand','events'), demand_owner_rows=('demand','owner_rows'),
            demand_plan_rows=('demand','plan_rows'), demand_maintenance_rows=('demand','maintenance_rows'),
            addition_growth_rows=('addition_growth','rows'), growth_hold_rows=('growth_hold','rows'), growth_hold_events=('growth_hold','events'),
            opportunity_rows=('opportunity','rows'), opportunity_submissions=('opportunity','submissions'),
            commitment_repair_rows=('commitment_repair','rows'), commitment_repair_submissions=('commitment_repair','submissions'),
            commitment_maintenance_rows=('commitment_repair','maintenance_rows'), maintenance_scope_rows=('commitment_repair','scope_rows'),
            coordination_rows=('coordination','rows'), coordination_submissions=('coordination','submissions'))
        for side, bank in self.banks.items():
            out = {key: deepcopy(getattr(bank[part], field)) for key,(part,field) in mapping.items()}
            renewal = bank['coordination'].renewal
            out.update(demand_final=deepcopy(bank['demand'].final), addition_growth_anchor=deepcopy(bank['addition_growth'].anchor),
                growth_hold_arm=deepcopy(bank['growth_hold'].armed), renewal_rows=deepcopy(renewal.rows), renewal_events=deepcopy(renewal.memory.events),
                renewal_work=deepcopy(renewal.memory.work), renewal_submissions=deepcopy(renewal.submissions))
            banks[side] = out
        payload = {key: [dict(row, bank=side) for side, b in banks.items() for row in b[key]] for key in mapping}
        payload.update(direction_rule=self.rule, banks=banks, bridge_frames=self.frames, direction_decisions=self.decisions,
            birth_provenance=self.births, opening_rows=self.opening_rows, global_service_caps=self.cap_rows,
            physical_maintenance_rows=self.maintenance_rows, physical_work_observations=self.work_observations)
        if self.final is not None:
            payload['demand_final'] = self.final
        return payload


def instrument(source, replace):
    marker = 'self.demand = _DemandGate(_DEMAND_SELECTION, _DEMAND_MODE, _OWN_SNAPSHOT)'
    source = replace(source, marker, marker + '\n                self.bridge = _DirectionBridge(_DIRECTION_RULE, _OWN_SNAPSHOT)')
    source = replace(source, 'roles.observe(f)', 'self.bridge.observe(f, self)')
    source = replace(source, 'self.demand.observe(f)', 'self.bridge.observe_demand(f, self)')
    source = replace(source, '                w = self.theta', '                neutral = self.bridge.opening(f, self, validate_size, _goal_crossing)\n                if neutral is not None:\n                    self.prev_terminal_keys = set(term_map)\n                    return envelope(f, self, neutral)\n                w = self.theta')
    for part in ('opportunity', 'commitment_repair', 'coordination'):
        source = replace(source, f'ops = producer.{part}.apply(f, producer, ops, validate_size, _goal_crossing)',
            f'ops = producer.bridge.service({part!r}, f, producer, ops, validate_size, _goal_crossing)')
    source = replace(source, 'producer.demand.on_plan(f, ops)', 'producer.bridge.on_plan(f, producer, ops)')
    source = replace(source, '                    if s == roles.weak:\n                        maintenance_price, stale = self.commitment_repair.maintenance(f, k, c, prices[s], stale, tick * (1.0 + softplus(w[11])))',
        '                    maintenance_price, stale = self.bridge.maintenance(f, self, k, c, prices[s], stale, tick * (1.0 + softplus(w[11])))')
    source = replace(source, 'self.demand.maintenance(f, k, s, maintenance_price, stale, surplus)', 'self.bridge.demand_maintenance(f, self, k, s, maintenance_price, stale, surplus)')
    source = replace(source, 'producer.demand.finish(actual, sim._receipt_ledger.seen, sim.orders)', 'producer.bridge.finish(producer, actual, sim._receipt_ledger.seen, sim.orders)')
    for part in ('opportunity', 'coordination', 'commitment_repair'):
        source = source.replace(f'producer.{part}.submissions', f'producer.bridge.all_submissions(producer, {part!r})')
    return source
