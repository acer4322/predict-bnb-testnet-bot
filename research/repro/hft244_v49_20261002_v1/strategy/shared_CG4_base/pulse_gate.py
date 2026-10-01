from roles_runtime import roles
'One frozen, separately declared upstream repair request, not minimum rounding.\n\nThe one-frame pulse intentionally leaves subsequent maintenance unchanged so\nits lifecycle can falsify proposal-only fixes. It never ignores pending orders.\n'
import math
MODES = ('CONTROL', 'PULSE30')

def request(state, original, price, quantity):
    gap = max(0.0, state['inv'][roles.strong] - state['inv'][roles.weak] - state['pending_qty'][roles.weak])
    potential = state['payoff'][roles.weak] + state['pending_qty'][roles.weak] - state['pending_cash'][roles.weak] - state['pending_cash'][roles.strong]
    money = max(0.0, -potential / (1.0 - price))
    assert state['payoff'][roles.weak] < 0 and quantity <= min(gap, money) + 1e-09
    owned = state['inv'][roles.weak] + state['pending_qty'][roles.weak]
    original_deficit = max(0.0, original[roles.weak] - owned)
    assert 0 < original_deficit < 18
    effective = dict(original, **{roles.weak: owned + quantity})
    return (effective, dict(original_deficit=original_deficit, quantity_capacity=gap, money_capacity=money, authorized_once=quantity, original_desired=dict(original), effective_desired=effective, original_deficit_exceeded_by=max(0.0, quantity - original_deficit), true_uncovered_gap_exceeded=False))

class SingleRepairDemand:

    def __init__(self, selection, mode, snapshot):
        assert mode in MODES
        self.selection = selection
        self.mode = mode
        self.snapshot = snapshot
        self.events = []
        self.visited = False

    def apply(self, frame, desired):
        if int(frame['t']) != self.selection['t']:
            return desired
        assert not self.visited, 'one visit only'
        self.visited = True
        state = self.snapshot(frame, frame['ledger'])
        expected = self.selection['state']
        for section in ('inv', 'pending_qty', 'pending_cash', 'payoff'):
            for side in (roles.strong, roles.weak):
                assert math.isclose(state[section][side], expected[section][side], rel_tol=0.0, abs_tol=1e-07), (section, side)
        assert state['owners'] == expected['owners'], 'common-prefix owners changed'
        effective, event = request(state, desired, self.selection['price'], self.selection['proposed'])
        event.update(t=int(frame['t']), mode=self.mode, applied=self.mode == 'PULSE30', state=state, maintenance_override=False, persistent_goal=False)
        self.events.append(event)
        return effective if self.mode == 'PULSE30' else desired

def instrument(source, replace):
    marker = 'self.money_gate=_MoneyGate(_MONEY_SELECTION,_MONEY_MODE)'
    source = replace(source, marker, marker + ';self.demand=_DemandGate(_DEMAND_SELECTION,_DEMAND_MODE,_OWN_SNAPSHOT)')
    marker = '    self.intent.capture(f,self,ledger,legacy_exposure,exposure,desired)\n'
    return replace(source, marker, '    desired=self.demand.apply(f,desired)\n' + marker)

def self_test():
    base = dict(inv={roles.strong: 100.0, roles.weak: 40.0}, payoff={roles.strong: 20.0, roles.weak: -40.0}, pending_qty={roles.strong: 0.0, roles.weak: 10.0}, pending_cash={roles.strong: 0.0, roles.weak: 3.0})
    original = dict(**{roles.strong: 130.0}, **{roles.weak: 50.53})
    effective, event = request(base, original, 0.3, 30.0)
    assert effective == dict(**{roles.strong: 130.0}, **{roles.weak: 80.0}) and original[roles.weak] == 50.53
    assert event['quantity_capacity'] == 50.0 and abs(event['original_deficit'] - 0.53) < 1e-08
    blocked = dict(base, pending_qty={roles.strong: 0.0, roles.weak: 50.0}, pending_cash={roles.strong: 0.0, roles.weak: 15.0})
    try:
        request(blocked, dict(**{roles.strong: 130.0}, **{roles.weak: 90.53}), 0.3, 30.0)
    except AssertionError:
        pass
    else:
        raise AssertionError('pending must still occupy repair capacity')
    paid = dict(base, payoff={roles.strong: 60.0, roles.weak: 0.0})
    try:
        request(paid, original, 0.3, 30.0)
    except AssertionError:
        pass
    else:
        raise AssertionError('no diagnostic repair without negative payoff')
if __name__ == '__main__':
    self_test()
    print('PASS')
