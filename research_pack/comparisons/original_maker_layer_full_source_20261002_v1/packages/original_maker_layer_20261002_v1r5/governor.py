"""Causal post-FLIP quantity ablation and research-only 300 USDT repair reserve."""
import atexit
from copy import deepcopy
import gzip
import json
import math
import os
from pathlib import Path

EPS = 1e-8
SIDES = ('UP', 'DOWN')
INSTANCE = None

def required(state):
    """Worst independent pending-fill cash plus a $1/share future balancing reserve.

    max over every pending fill subset of spent + fill cash + abs(resulting net).
    Prices are interior and fees are zero in this frozen experiment. CANCEL is
    absent from the formula: only canonical remaining pending quantities count.
    """
    d = float(state['inv']['UP']) - float(state['inv']['DOWN'])
    return float(state['cost']) + max(d + state['pending_qty']['UP'] + state['pending_cash']['UP'],
                                    -d + state['pending_qty']['DOWN'] + state['pending_cash']['DOWN'])

def repair_room(state, side):
    other = 'DOWN' if side == 'UP' else 'UP'
    return max(0., state['inv'][other] - state['inv'][side] - state['pending_qty'][side])

def choose(state, side, price, qty, route, *, factor=1., cap=None, step=.01, passive_ticket=15.):
    assert side in SIDES and route in ('ACTIVE', 'PASSIVE')
    assert 0. < price < 1. and qty > 0. and factor in (0., .5, 1.)
    repair = min(qty, repair_room(state, side))
    expansion = max(0., qty - repair)
    wanted = repair + factor * expansion
    capacity = None
    if cap is not None:
        assert required(state) <= cap + 1e-7, ('CAP_STATE_BREACH', required(state), cap)
        other = 'DOWN' if side == 'UP' else 'UP'
        d = state['inv'][side] - state['inv'][other]
        capacity = max(0., (cap - state['cost'] - d - state['pending_qty'][side] - state['pending_cash'][side]) / (1. + price))
        wanted = min(wanted, capacity)
    q = qty if factor == 1. and cap is None else max(0., round(math.floor((wanted + 1e-9) / step) * step, 8))
    reason = 'UNCHANGED' if abs(q - qty) < EPS else 'REDUCED'
    # Keep the frozen research sizing contract. Never round up to bypass a cap.
    if q <= EPS or q * price < 1. - EPS or (route == 'PASSIVE' and abs(q - passive_ticket) > EPS):
        q = 0.; reason = 'BELOW_ORIGINAL_SUBMITTED_MINIMUM'
    return dict(quantity=q, proposed_quantity=qty, repair_component=repair,
                expansion_component=expansion, factor=factor, cap=cap,
                funding_capacity=capacity, funding_before=required(state), reason=reason)

class Governor:
    def __init__(self, ctx, roles):
        from sizing import TICKET
        self.ticket = TICKET
        self.ctx = ctx; self.roles = roles
        self.mode = os.environ.get('V12G_POSTFLIP_ADD_MODE', 'BASE')
        assert self.mode in ('BASE', 'HALF', 'REPAIR_ONLY')
        self.cap = float(os.environ['V12G_MARKET_CAP']) if os.environ.get('V12G_MARKET_CAP') else None
        assert self.cap in (None, 300.)
        self.rows = []; self.plans = []; self.initial = None

    def flipped(self):
        return any(e['kind'] == 'FLIP' for e in getattr(self.roles, 'v12g_events', []))

    def quantity(self, state, side, price, qty, route, origin, frame=None):
        frame = self.ctx.frame if frame is None else frame
        if frame is None or (self.mode == 'BASE' and self.cap is None):
            return float(qty)
        active = self.flipped()
        factor = {'BASE': 1., 'HALF': .5, 'REPAIR_ONLY': 0.}[self.mode] if active else 1.
        row = choose(state, side, float(price), float(qty), route, factor=factor,
                     cap=self.cap, step=float(frame['world_profile']['quantity_step']), passive_ticket=self.ticket)
        self.rows.append(dict(t=int(frame['t']), index=int(frame['index']), side=side,
                             price=float(price), route=route, origin=origin,
                             flipped=active, state=deepcopy(state), **row))
        return row['quantity']

    def pre_reserve(self, frame, ledger, side, route, qty, price):
        return self.quantity(self.ctx.snapshot(frame, ledger), side, price, qty, route,
                             'BASE_PRE_RESERVE', frame)

    def row(self, row, state, side, price, route, origin):
        if not row.get('eligible') or self.ctx.frame is None:
            return row
        q = float(row.get('quantity', self.ticket if route == 'PASSIVE' else 15.))
        actual = self.quantity(state, side, price, q, route, origin)
        if actual <= EPS:
            row.update(eligible=False, reason='V52_QUANTITY_OR_REPAIR_RESERVE', v52_original_quantity=q)
        elif abs(actual - q) > EPS:
            row.update(quantity=actual, quoted_cost=actual * price, v52_original_quantity=q)
        return row

    def verify(self, frame, ops):
        from economics import with_plan
        state = self.ctx.snapshot(frame, frame['ledger'])
        active = self.flipped()
        if active and self.initial is None:
            self.initial = dict(t=frame['t'], index=frame['index'], state=deepcopy(state))
        # The zero-ADD arm also retires resting orders that now expand the more-held
        # physical branch. Partial/UNKNOWN/cancel-pending stays reserved until receipt.
        if active and self.mode == 'REPAIR_ONLY':
            planned = {o['key'] for o in ops if o['kind'] == 'CANCEL'}
            for owner in state['owners']:
                side = owner['side']; other = 'DOWN' if side == 'UP' else 'UP'
                if (state['inv'][side] >= state['inv'][other] - EPS
                    and owner['state'] != 'CANCEL_PENDING'
                    and frame.get('cancellable', {}).get(owner['key'], False)
                    and owner['key'] not in planned):
                    ops.append(dict(kind='CANCEL', key=owner['key'], origin='WHOLE_POLICY', reason='V52_POSTFLIP_EXPANSION_RETIRE'))
                    planned.add(owner['key'])
        current = deepcopy(state)
        for op in ops:
            if op['kind'] != 'NEW':
                continue
            if active and self.mode == 'REPAIR_ONLY':
                assert op['qty'] <= repair_room(current, op['side']) + EPS, ('POSTFLIP_ADD_COVERAGE_GAP', frame['index'], op)
            current = with_plan(current, [op])
            if self.cap is not None:
                assert required(current) <= self.cap + 1e-7, ('CAP_COVERAGE_GAP', frame['index'], op, required(current))
        self.plans.append(dict(t=frame['t'], index=frame['index'], flipped=active,
                               state=state, operations=deepcopy(ops), funding_after=required(current)))
        return ops

    def save(self):
        out = os.environ.get('BTC5M_LAN_RESULT_DIR')
        if out and Path(out).is_dir():
            value = dict(passive_ticket=self.ticket, mode=self.mode, cap=self.cap, initial=self.initial, rows=self.rows, plans=self.plans,
                         fee_assumption=0., live_eligible=False)
            with gzip.GzipFile(filename=str(Path(out) / 'governor_trace.json.gz'), mode='wb', mtime=0) as f:
                f.write(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode())

def install(ctx, roles):
    global INSTANCE
    assert INSTANCE is None
    INSTANCE = Governor(ctx, roles); atexit.register(INSTANCE.save)
    return INSTANCE

def verify_plan(frame, ops):
    return INSTANCE.verify(frame, ops)
