"""One frozen, separately declared upstream repair request, not minimum rounding.

The one-frame pulse intentionally leaves subsequent maintenance unchanged so
its lifecycle can falsify proposal-only fixes. It never ignores pending orders.
"""
import math

MODES = ('CONTROL', 'PULSE30')


def request(state, original, price, quantity):
    gap = max(0., state['inv']['UP'] - state['inv']['DOWN'] - state['pending_qty']['DOWN'])
    potential = (state['payoff']['DOWN'] + state['pending_qty']['DOWN']
                 - state['pending_cash']['DOWN'] - state['pending_cash']['UP'])
    money = max(0., -potential / (1. - price))
    assert state['payoff']['DOWN'] < 0 and quantity <= min(gap, money) + 1e-9
    owned = state['inv']['DOWN'] + state['pending_qty']['DOWN']
    original_deficit = max(0., original['DOWN'] - owned)
    assert 0 < original_deficit < 18
    effective = dict(original, DOWN=owned + quantity)
    return effective, dict(original_deficit=original_deficit, quantity_capacity=gap,
                          money_capacity=money, authorized_once=quantity,
                          original_desired=dict(original), effective_desired=effective,
                          original_deficit_exceeded_by=max(0., quantity - original_deficit),
                          true_uncovered_gap_exceeded=False)


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
            for side in ('UP', 'DOWN'):
                assert math.isclose(state[section][side], expected[section][side], rel_tol=0., abs_tol=1e-7), (section, side)
        assert state['owners'] == expected['owners'], 'common-prefix owners changed'
        effective, event = request(state, desired, self.selection['price'], self.selection['proposed'])
        event.update(t=int(frame['t']), mode=self.mode, applied=self.mode == 'PULSE30',
                     state=state, maintenance_override=False, persistent_goal=False)
        self.events.append(event)
        return effective if self.mode == 'PULSE30' else desired


def instrument(source, replace):
    marker = 'self.money_gate=_MoneyGate(_MONEY_SELECTION,_MONEY_MODE)'
    source = replace(source, marker, marker + ';self.demand=_DemandGate(_DEMAND_SELECTION,_DEMAND_MODE,_OWN_SNAPSHOT)')
    marker = '    self.intent.capture(f,self,ledger,legacy_exposure,exposure,desired)\n'
    return replace(source, marker, '    desired=self.demand.apply(f,desired)\n' + marker)


def self_test():
    base = dict(inv={'UP':100., 'DOWN':40.}, payoff={'UP':20., 'DOWN':-40.},
                pending_qty={'UP':0., 'DOWN':10.}, pending_cash={'UP':0., 'DOWN':3.})
    original = dict(UP=130., DOWN=50.53)
    effective, event = request(base, original, .3, 30.)
    assert effective == dict(UP=130., DOWN=80.) and original['DOWN'] == 50.53
    assert event['quantity_capacity'] == 50. and abs(event['original_deficit'] - .53) < 1e-8
    blocked = dict(base, pending_qty={'UP':0., 'DOWN':50.}, pending_cash={'UP':0., 'DOWN':15.})
    try:
        request(blocked, dict(UP=130., DOWN=90.53), .3, 30.)
    except AssertionError:
        pass
    else:
        raise AssertionError('pending must still occupy repair capacity')
    paid = dict(base, payoff={'UP':60., 'DOWN':0.})
    try:
        request(paid, original, .3, 30.)
    except AssertionError:
        pass
    else:
        raise AssertionError('no diagnostic repair without negative payoff')


if __name__ == '__main__':
    self_test()
    print('PASS')
