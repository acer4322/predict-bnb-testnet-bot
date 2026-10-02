from roles_runtime import roles
'Finite, receipt-updated DOWN inventory floor for one offline repair exercise.\n\nAn absolute goal includes the existing committed service, plus one new30 request.\nIt never ratchets with new inventory or pending orders and never treats cancel\nintent as a release. Original price maintenance and all gateway checks remain.\n'
import importlib.util
from pathlib import Path
_path = Path(__file__).with_name('pulse_gate.py')
if not _path.exists():
    _path = Path(__file__).with_name('btc5m_single_repair_demand_v1.py')
_spec = importlib.util.spec_from_file_location('frozen_single_repair_reference', _path)
legacy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(legacy)
EPS = 1e-08

class FiniteGoal:

    def __init__(self, initial, extra):
        self.initial_down = initial['inv'][roles.weak]
        self.initial_pending = initial['pending_qty'][roles.weak]
        self.extra = extra
        self.target = self.initial_down + self.initial_pending + extra
        self.status = 'ACTIVE'
        self.stopped_t = None

    def update(self, state, t, end):
        remaining = max(0.0, self.target - state['inv'][roles.weak])
        if self.status == 'ACTIVE':
            if remaining <= EPS:
                self.status = 'CONFIRMED_TARGET_REACHED'
            elif t >= end:
                self.status = 'WITHDRAWN_MARKET_END'
            elif state['inv'][roles.strong] <= state['inv'][roles.weak] + EPS:
                self.status = 'WITHDRAWN_ORIGINAL_NET_DIRECTION_GONE'
            elif state['payoff'][roles.weak] >= 0:
                self.status = 'WITHDRAWN_DOWN_ALREADY_NONNEGATIVE'
            if self.status != 'ACTIVE':
                self.stopped_t = t
        pending = state['pending_qty'][roles.weak]
        return dict(status=self.status, stopped_t=self.stopped_t, target=self.target, initial_down=self.initial_down, initial_committed_down=self.initial_pending, new_request=self.extra, acquired_since_birth=state['inv'][roles.weak] - self.initial_down, remaining_confirmed=remaining, pending_down=pending, pending_cash_down=state['pending_cash'][roles.weak], pending_cash_up=state['pending_cash'][roles.strong], unreserved_need=max(0.0, remaining - pending), pending_excess_over_goal=max(0.0, pending - remaining), down_payoff=state['payoff'][roles.weak])

    def desired(self, original):
        if self.status != 'ACTIVE':
            return original
        return dict(original, **{roles.weak: max(original[roles.weak], self.target)})

class SingleRepairDemand:

    def __init__(self, selection, mode, snapshot):
        assert mode in ('PULSE_CONTROL', 'PERSIST30')
        self.mode = mode
        self.selection = selection
        self.snapshot = snapshot
        self.pulse = legacy.SingleRepairDemand(selection, 'PULSE30', snapshot)
        self.events = self.pulse.events
        self.goal = None
        self.rows = []
        self.owner_rows = []
        self.plan_rows = []
        self.maintenance_rows = []
        self.tracked = {r['key'] for r in selection['state']['owners']}
        self.initial_keys = sorted(self.tracked)
        self.last_carrier = {}
        self.final = None

    @property
    def visited(self):
        return self.pulse.visited

    def observe(self, frame):
        if int(frame['t']) < self.selection['t']:
            return
        state = self.snapshot(frame, frame['ledger'])
        if self.goal is not None:
            self.goal.update(state, int(frame['t']), int(frame['end']))
        for key in sorted(self.tracked):
            c = frame['ledger'].carriers.get(key)
            if c is None:
                continue
            row = dict(key=key, state=c.state, qty=float(c.qty), filled=float(c.filled), payment=float(c.payment), fees=float(c.fees), limit=float(c.limit), remaining=max(0.0, float(c.qty) - float(c.filled)))
            if row != self.last_carrier.get(key):
                self.owner_rows.append(dict(t=int(frame['t']), **row))
                self.last_carrier[key] = row

    def apply(self, frame, desired):
        effective = self.pulse.apply(frame, desired)
        if not self.visited:
            return effective
        state = self.snapshot(frame, frame['ledger'])
        if self.goal is None:
            self.goal = FiniteGoal(state, self.selection['proposed'])
        progress = self.goal.update(state, int(frame['t']), int(frame['end']))
        if self.mode == 'PERSIST30':
            effective = self.goal.desired(desired)
        self.rows.append(dict(t=int(frame['t']), state=state, original_desired=dict(desired), effective_desired=dict(effective), mode=self.mode, progress=progress, floor_increment=max(0.0, effective[roles.weak] - desired[roles.weak]), cancel_pending_down=sum((r['qty'] for r in state['owners'] if r['side'] == roles.weak and r['state'] == 'CANCEL_PENDING'))))
        return effective

    def on_plan(self, frame, operations):
        if int(frame['t']) < self.selection['t']:
            return
        cohort_open = self.goal is not None and self.goal.status == 'ACTIVE'
        for op in operations:
            if op['kind'] == 'NEW' and op['side'] == roles.weak and cohort_open:
                self.tracked.add(op['key'])
        interesting = [dict(op) for op in operations if op.get('key') in self.tracked]
        if interesting:
            self.plan_rows.append(dict(t=int(frame['t']), operations=interesting))

    def maintenance(self, frame, key, side, price, stale, surplus):
        if key in self.tracked and self.visited:
            self.maintenance_rows.append(dict(t=int(frame['t']), key=key, side=side, current_passive_price=price, stale=stale, surplus=surplus, cancellable=bool(frame['cancellable'].get(key, False)), owner_state=frame['ledger'].carriers[key].state))

    def finish(self, ledger, receipts):
        owners = []
        for key in sorted(self.tracked):
            c = ledger.carriers.get(key)
            if c is not None:
                owners.append(dict(key=key, state=c.state, qty=float(c.qty), filled=float(c.filled), payment=float(c.payment), fees=float(c.fees), limit=float(c.limit)))
        self.final = dict(tracked_owners=owners, tracked_keys=sorted(self.tracked), canonical_receipts=[dict(r) for r in receipts if r.get('key') in self.tracked], final_accounts={s: dict(ledger.account(pid)) for pid, s in ((roles.pid(roles.strong), roles.strong), (roles.pid(roles.weak), roles.weak))}, work_status=self.goal.status if self.goal else 'NOT_STARTED', work_stopped_t=self.goal.stopped_t if self.goal else None, note='Tracked cohort includes preexisting pending and DOWN orders while the finite goal is open; not every fill or order is attributable solely to the extra30 request.')

def instrument(source, replace):
    source = legacy.instrument(source, replace)
    marker = '    self.money_gate.on_frame(f,self)\n'
    source = replace(source, marker, marker + '    self.demand.observe(f)\n')
    marker = '  from tools.minimal_student_training_world_v2 import envelope,passive_ask,training_frame_id'
    source = replace(source, marker, '  from tools.minimal_student_training_world_v2 import envelope as _original_envelope,passive_ask,training_frame_id\n  def envelope(f,producer,ops):\n   producer.demand.on_plan(f,ops)\n   return _original_envelope(f,producer,ops)')
    marker = "     if (stale or surplus) and c.state!='CANCEL_PENDING'"
    source = replace(source, marker, '     self.demand.maintenance(f,k,s,prices[s],stale,surplus)\n' + marker)
    marker = "  result['clock_smoke']=_save_clock_trace(out,tr,producer,len(sim.payload['updates']))"
    return replace(source, marker, '  producer.demand.finish(actual,sim._receipt_delta_rows)\n' + marker)

def self_test():
    legacy.self_test()

    def state(down, pending, cost=90.0, up=100.0):
        return dict(inv=dict(**{roles.strong: up}, **{roles.weak: down}), payoff=dict(**{roles.strong: up - cost}, **{roles.weak: down - cost}), pending_qty=dict(**{roles.strong: 0.0}, **{roles.weak: pending}), pending_cash=dict(**{roles.strong: 0.0}, **{roles.weak: pending * 0.3}))
    initial = state(40.0, 10.0)
    g = FiniteGoal(initial, 30.0)
    assert g.target == 80.0 and g.update(initial, 1, 100)['unreserved_need'] == 30.0
    assert g.update(state(40.0, 40.0), 2, 100)['unreserved_need'] == 0.0
    partial = g.update(state(45.0, 35.0), 3, 100)
    assert partial['remaining_confirmed'] == 35.0 and partial['unreserved_need'] == 0.0
    assert g.update(state(45.0, 35.0), 4, 100)['unreserved_need'] == 0.0
    assert g.update(state(45.0, 5.0), 5, 100)['unreserved_need'] == 30.0
    assert g.desired(dict(**{roles.strong: 120.0}, **{roles.weak: 50.0})) == dict(**{roles.strong: 120.0}, **{roles.weak: 80.0})
    assert g.desired(dict(**{roles.strong: 120.0}, **{roles.weak: 100.0})) == dict(**{roles.strong: 120.0}, **{roles.weak: 100.0})
    assert g.update(state(73.0, 0.0), 6, 100)['unreserved_need'] == 7.0
    assert g.target == 80.0
    assert g.update(state(80.0, 0.0), 7, 100)['status'] == 'CONFIRMED_TARGET_REACHED'
    assert g.desired(dict(**{roles.strong: 120.0}, **{roles.weak: 60.0}))[roles.weak] == 60.0
    assert g.update(state(81.0, 0.0, cost=120.0), 8, 100)['status'] == 'CONFIRMED_TARGET_REACHED'
    for s, t, end, reason in ((state(50.0, 0.0, cost=40.0), 2, 100, 'WITHDRAWN_DOWN_ALREADY_NONNEGATIVE'), (state(50.0, 0.0, up=45.0), 2, 100, 'WITHDRAWN_ORIGINAL_NET_DIRECTION_GONE'), (initial, 100, 100, 'WITHDRAWN_MARKET_END')):
        h = FiniteGoal(initial, 30.0)
        assert h.update(s, t, end)['status'] == reason
        assert h.desired(dict(**{roles.strong: 120.0}, **{roles.weak: 60.0}))[roles.weak] == 60.0
if __name__ == '__main__':
    self_test()
    print('PASS: finite goal, partial/cancel reservations, release, dust and withdrawal')
