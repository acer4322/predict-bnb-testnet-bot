"""Offline fixed-UP parallel capacity diagnostic; no Target price/timing inputs."""
import importlib.util
import math
from pathlib import Path

_path = Path(__file__).with_name('isolated_gate.py')
if not _path.exists():
    _path = Path(__file__).with_name('btc5m_payoff_repair_gate_v1.py')
_spec = importlib.util.spec_from_file_location('isolated_payoff_reference', _path)
old = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(old)
MODES = ('INERT', 'PARALLEL_QUANTITY', 'PARALLEL_PAYOFF_ZERO')
instrument = old.instrument
snapshot = old.snapshot


def capacity(state, side, price, requested, mode):
    assert mode in MODES
    if mode == 'INERT':
        return old.capacity(state, side, price, requested, mode)
    if side == 'UP':
        return requested, dict(reason='PRESERVE_FROZEN_NEW_UP_CANDIDATE')
    translated = 'ISOLATED_QUANTITY' if mode == 'PARALLEL_QUANTITY' else 'ISOLATED_PAYOFF_ZERO'
    return old.capacity(state, side, price, requested, translated)


class PayoffRepairGate(old.PayoffRepairGate):
    def __init__(self, selection, mode):
        assert mode in MODES
        self.selection = selection
        self.mode = mode
        self.started = False
        self.events = []
        self.rows = []

    def cap(self, frame, side, price, qty, draft):
        if not self.started:
            return qty
        state = snapshot(frame, draft)
        limit, why = capacity(state, side, price, qty, self.mode)
        step = float(frame['world_profile']['quantity_step'])
        admitted = qty if self.mode == 'INERT' or side == 'UP' else min(qty, round(math.floor((limit + 1e-10) / step) * step, 8))
        self.rows.append(dict(t=frame['t'], side=side, price=price, requested=qty, capped=admitted, state=state, **why))
        return admitted


def self_test():
    old.self_test()
    base = dict(inv={'UP':100., 'DOWN':60.}, payoff={'UP':35., 'DOWN':-5.},
                pending_qty={'UP':0., 'DOWN':0.}, pending_cash={'UP':0., 'DOWN':0.})
    assert capacity(base, 'UP', .5, 30., 'PARALLEL_PAYOFF_ZERO')[0] == 30.
    assert capacity(base, 'DOWN', .5, 40., 'PARALLEL_PAYOFF_ZERO')[0] == 10.
    assert capacity(base, 'DOWN', .5, 40., 'PARALLEL_QUANTITY')[0] == 40.
    # An UP order can consume future cash before it fills; do not count it as shares acquired.
    up_pending = dict(base, pending_qty={'UP':20., 'DOWN':0.}, pending_cash={'UP':10., 'DOWN':0.})
    assert capacity(up_pending, 'DOWN', .5, 40., 'PARALLEL_PAYOFF_ZERO')[0] == 30.
    # DOWN capacity reservations remain until terminal, including cancellation pending.
    down_pending = dict(base, pending_qty={'UP':0., 'DOWN':10.}, pending_cash={'UP':0., 'DOWN':5.})
    assert capacity(down_pending, 'DOWN', .5, 40., 'PARALLEL_PAYOFF_ZERO')[0] == 0.
    assert capacity(base, 'DOWN', .5, 40., 'PARALLEL_PAYOFF_ZERO')[0] == 10.


if __name__ == '__main__':
    self_test()
    print('PASS')
