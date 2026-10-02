"""V59 Bounded Partial Repair (BPR): a finite, separately accounted partial repair used only when the
original proposal rejects full restoration as unaffordable.

Component only (stage 1). It never reads or writes the legacy Context (ctx.first, ctx.first_active,
ctx.prepare, ctx.events) and never loads Target, winner, settlement or future data. It adds no new
constant: K and RETAIN come from the frozen economics module, the cutoff is the user STOP290 rule and
the latency is the frozen 250 ms send delay.
"""
import math
from copy import deepcopy

import atexit
import gzip
import json
import os
from pathlib import Path

from economics import admission, geometry, proposal, with_plan

EPS = 1e-8
CUTOFF_MS = 290_000
LATENCY_MS = 250
COUNT_CAP = 5            # original combined active child cap; BPR shares it and never widens it
TRIGGER = 'FULL_RESTORATION_NOT_AFFORDABLE'


def floor_step(x, step):
    return max(0., round(math.floor((max(0., x) + 1e-9) / step) * step, 8))


def debt(state, strong):
    return max(0., -geometry(state, strong)['worst_margin'])


class BPR:
    def __init__(self, mode='OFF', cutoff_ms=CUTOFF_MS, latency_ms=LATENCY_MS, count_cap=COUNT_CAP):
        assert mode in ('OFF', 'ON')
        self.mode = mode
        self.cutoff_ms = cutoff_ms
        self.latency_ms = latency_ms
        self.count_cap = count_cap
        self.works = []          # all works, finite list; at most one ACTIVE
        self.receipts = {}       # receipt_id -> (work_id, key, qty): each fill is credited once
        self.orders = []
        self.rows = []

    # ------------------------------------------------------------------ bookkeeping
    @property
    def active(self):
        w = self.works[-1] if self.works else None
        return w if w is not None and w['status'] == 'ACTIVE' else None

    def _work_of_key(self, key):
        for w in self.works:
            if key in w['keys']:
                return w
        return None

    def on_receipt(self, receipt_id, key, qty):
        """Confirmed fill receipt. Only receipts of BPR-owned keys count; duplicates are ignored."""
        w = self._work_of_key(key)
        if w is None or receipt_id in self.receipts:
            return False
        q = float(qty)
        assert q >= -EPS
        self.receipts[receipt_id] = (w['work_id'], key, q)
        w['filled'] += q
        return True

    def owns(self, key):
        return self._work_of_key(key) is not None

    def sync_fills(self, filled_by_key):
        """Runtime credit from the ledger: cumulative carrier fills of BPR keys (set, never added twice)."""
        for w in self.works:
            total = sum(float(filled_by_key.get(k, 0.)) for k in w['keys'])
            assert total >= w['filled'] - EPS, ('FILL_REGRESSION', w['work_id'], total, w['filled'])
            w['filled'] = total

    def reserved(self, w, state):
        """Own live owners (incl. same-plan, cancel-pending, unknown) stay reserved; nothing is refunded
        on cancel intent. Only owners that left the state (confirmed terminal) release their remainder."""
        total = 0.
        for o in state['owners']:
            if o['key'] in w['keys']:      # any state present in the snapshot is not confirmed-terminal
                total += float(o.get('remaining', o['qty']))   # filled part is credited only via receipts
        return total

    # ------------------------------------------------------------------ lifecycle
    def observe(self, frame, state, strong, weak, flips):
        w = self.active
        if w is None:
            return None
        side, other = w['side'], w['other']
        status = None
        if w['filled'] >= w['target'] - EPS:
            status = 'CONFIRMED_TARGET_REACHED'
        elif flips != w['birth_flips'] or weak != side:
            status = 'WITHDRAWN_DIRECTION_CHANGED'
        elif frame['t'] - frame['start'] >= self.cutoff_ms:
            status = 'WITHDRAWN_STOP290'
        elif state['payoff'][side] >= -EPS:
            status = 'WITHDRAWN_WEAK_NONNEGATIVE'
        elif state['payoff'][other] <= EPS:
            status = 'WITHDRAWN_FAVORABLE_NONPOSITIVE'
        elif state['inv'][other] <= state['inv'][side] + EPS:
            status = 'WITHDRAWN_NET_DIRECTION_GONE'
        if status:
            # Stopping only ends NEW; in-flight owners stay tracked by the ledger until their real terminal.
            w.update(status=status, stopped_t=frame['t'], stopped_index=frame['index'],
                     stop_debt=debt(state, strong) if strong == other else None,
                     stop_strong_inv=state['inv'][other])
        return w

    def _birth_cap(self, state, strong, row, flips):
        """Quantity allowed for a new work. None means not allowed (with reason)."""
        base = min(row['needed_qty'], row['total_cap'], row['visible_depth'])
        prev = self.works[-1] if self.works else None
        if prev is None or prev['birth_flips'] != flips or prev['other'] != strong or prev['stop_debt'] is None:
            # First work, or first work in a new direction episode (bounded by the finite strategy flips).
            return base, 'FIRST_IN_DIRECTION', None
        # New responsibility only from confirmed expansion after the previous work stopped.
        grown = state['inv'][strong] - prev['stop_strong_inv']
        if grown <= EPS:
            return None, 'NO_NEW_CONFIRMED_EXPANSION', dict(grown=grown)
        new_debt = debt(state, strong) - prev['stop_debt']
        if new_debt <= EPS:
            return None, 'NO_NEW_DEBT', dict(grown=grown, new_debt=new_debt)
        return min(base, new_debt / row['slope']), 'NEW_RESPONSIBILITY', dict(grown=grown, new_debt=new_debt)

    def propose(self, frame, state, row, strong, weak, ask, peak, step, operations, crossing, flips,
                count_used, max_owners=4096):
        """state: planned state (canonical owners + same-plan NEWs, CANCEL releases nothing).
        row: the original economics.proposal row for this frame, unchanged.
        Returns (op or None, diagnostic). op is a NEW dict without key; the caller assigns the key and
        calls accepted(op)."""
        diag = dict(t=frame['t'], index=frame['index'], mode=self.mode, original_reason=row['reason'],
                    eligible=False, reason=None)
        self.rows.append(diag)
        if self.mode == 'OFF':
            diag['reason'] = 'INERT'
            return None, diag
        self.observe(frame, state, strong, weak, flips)
        w = self.active              # a work stopped by observe() is no longer serviced
        # Time rules: STOP290 blocks every NEW; send + latency must arrive before the market end.
        if frame['t'] - frame['start'] >= self.cutoff_ms:
            diag['reason'] = 'STOP290'
            return None, diag
        if frame['t'] + self.latency_ms >= frame['end']:
            diag['reason'] = 'ARRIVAL_AFTER_END'
            return None, diag
        if row.get('eligible'):
            diag['reason'] = 'ORIGINAL_REPAIR_PRIORITY'
            return None, diag
        if ask is None or not 0 < float(ask) < 1:
            diag['reason'] = 'NO_VALID_ASK'
            return None, diag
        p = float(ask)
        if w is None:
            if row['reason'] != TRIGGER:
                diag['reason'] = 'NOT_TRIGGERED'
                return None, diag
            cap, why, info = self._birth_cap(state, strong, row, flips)
            diag.update(birth_rule=why, birth_info=info)
            if cap is None:
                diag['reason'] = why
                return None, diag
            q0 = floor_step(cap, step)
            if q0 <= EPS or q0 * p < 1. - EPS:
                diag['reason'] = 'BIRTH_BELOW_MINIMUM'
                diag['birth_qty'] = q0
                return None, diag
            w = dict(work_id=len(self.works) + 1, side=weak, other=strong, status='ACTIVE', target=q0,
                     birth_ask=p, budget_cash=q0 * p, birth_t=frame['t'], birth_index=frame['index'],
                     birth_flips=flips, birth_rule=why, birth_info=info, birth_debt=debt(state, strong),
                     birth_row=deepcopy(row), birth_state=deepcopy(state), filled=0., keys=[], spent_quoted=0.)
            self.works.append(w)
            diag['born'] = w['work_id']
        diag['work_id'] = w['work_id']
        side = w['side']
        if side != weak:
            diag['reason'] = 'WRONG_PHYSICAL_SIDE'
            return None, diag
        if p > w['birth_ask'] + EPS:
            diag['reason'] = 'PRICE_ABOVE_BIRTH_LIMIT'
            return None, diag
        if any(o['kind'] == 'NEW' and o.get('route') == 'ACTIVE' for o in operations):
            diag['reason'] = 'EXISTING_ACTIVE_PLAN_PRIORITY'
            return None, diag
        if count_used >= self.count_cap:
            diag['reason'] = 'ORIGINAL_COMBINED_ACTIVE_CAP'
            return None, diag
        if len(state['owners']) >= max_owners:
            diag['reason'] = 'RESOURCE_OWNER_LIMIT'
            return None, diag
        reserved = self.reserved(w, state)
        open_need = w['target'] - w['filled'] - reserved
        cash_room = max(0., row['worst_G'] - row['retained_G_floor']) / p
        q = floor_step(min(open_need, row['visible_depth'], cash_room, row.get('net_unreserved', open_need)), step)
        diag.update(open_need=open_need, reserved=reserved, filled=w['filled'], cash_room_qty=cash_room, quantity=q)
        if q <= EPS or q * p < 1. - EPS:
            diag['reason'] = 'REMAINDER_RESERVED_OR_BELOW_MINIMUM'
            return None, diag
        owners = [{'key': o['key'], 'side': o['side'], 'price': o['limit']} for o in state['owners']]
        conflicts = crossing(side, p, owners)
        if conflicts:
            diag.update(reason='PENDING_OR_PLAN_OWN_CROSS', conflicts=conflicts)
            return None, diag
        adm = admission(state, strong, side, p, q, peak, True)
        if not adm['allowed']:
            diag.update(reason='FROZEN_EARNINGS_ADMISSION', admission=adm)
            return None, diag
        assert row['worst_G'] - p * q >= row['retained_G_floor'] - EPS
        assert w['filled'] + reserved + q <= w['target'] + EPS
        op = dict(kind='NEW', side=side, route='ACTIVE', price=p, qty=q, role='ACTIVE_BOUNDED_PARTIAL_REPAIR',
                  work_id=w['work_id'])
        diag.update(eligible=True, reason='BOUNDED_PARTIAL_REPAIR', quoted_cost=p * q)
        return op, diag

    def accepted(self, frame, op):
        w = self.active
        assert w is not None and op['work_id'] == w['work_id'] and op['key'] not in w['keys']
        w['keys'].append(op['key'])
        w['spent_quoted'] += op['qty'] * op['price']
        self.orders.append(dict(t=frame['t'], index=frame['index'], **op))

    def summary(self):
        return dict(mode=self.mode, works=[{k: v for k, v in w.items() if k not in ('birth_state',)} for w in self.works],
                    orders=self.orders, receipts=len(self.receipts))


def hook(service, frame, state, ops_out, decided, strong, weak, bids, peak, flips, count_used, crossing, carriers, governor_q,
         risk_q, validate, pid):
    """Runtime seam, called only after the original general_finite_active.apply returned without a qualified repair.
    Returns the (possibly extended) operations and the diagnostic row. OFF returns ops_out unchanged."""
    if service.mode == 'OFF':
        return ops_out, None
    if strong not in ('UP', 'DOWN') or weak not in ('UP', 'DOWN'):
        return ops_out, None
    if not decided:
        # V59b: no bounded partial repair before the strategy has chosen a side (DECIDE).
        diag = dict(t=frame['t'], index=frame['index'], mode=service.mode, eligible=False, reason='BEFORE_DECIDE')
        service.rows.append(diag)
        return ops_out, diag
    if flips < 1:
        # V59d: no bounded partial repair before the first observed strategy FLIP (runtime event, no future data).
        diag = dict(t=frame['t'], index=frame['index'], mode=service.mode, eligible=False, reason='BEFORE_FIRST_FLIP')
        service.rows.append(diag)
        return ops_out, diag
    wp = frame['world_profile']; step = float(wp['quantity_step'])
    service.sync_fills({k: float(c.filled) for k, c in carriers.items() if service.owns(k)})
    planned = with_plan(state, ops_out)
    ask = ((frame.get('quotes') or {}).get(weak) or {}).get('ask')
    depth = float(bids[max(bids)]) if bids else 0.
    row = proposal(planned, strong, ask, depth, peak, step)
    op, diag = service.propose(frame, planned, row, strong, weak, ask, peak, step, ops_out, crossing, flips,
                               count_used, int(wp['max_live_owners']))
    if op is None:
        return ops_out, diag
    price = float(op['price']); q = float(op['qty'])
    q = min(q, float(governor_q(planned, weak, price, q)))
    q = min(q, float(risk_q(planned, weak, price, q)))
    q = floor_step(q, step)
    if q <= EPS or q * price < 1. - EPS:
        diag.update(eligible=False, reason='GOVERNOR_OR_RISK_FLOOR', governed_quantity=q)
        return ops_out, diag
    if abs(price / float(wp['tick']) - round(price / float(wp['tick']))) > 1e-8:
        diag.update(eligible=False, reason='OFF_TICK_PRICE')
        return ops_out, diag
    try:
        validate(wp['asset'], 'ACTIVE', price, q, quantity_step=wp['quantity_step'])
    except Exception as exc:
        diag.update(eligible=False, reason='VALIDATE_REJECT', error=repr(exc)[:200])
        return ops_out, diag
    n = int(frame['own_view']['n']) + sum(o['kind'] == 'NEW' for o in ops_out)
    new = {'kind': 'NEW', 'key': f'{weak}_{n}', 'parent_id': pid(weak), 'side': weak, 'route': 'ACTIVE',
           'price': price, 'qty': q, 'role': 'ACTIVE_BOUNDED_PARTIAL_REPAIR'}
    service.accepted(frame, dict(new, work_id=op['work_id']))
    diag.update(key=new['key'], quantity=q, quoted_cost=q * price)
    return [*ops_out, new], diag


SERVICE = BPR(os.environ.get('V12G_BPR', 'OFF'))


def finish(out):
    payload = dict(SERVICE.summary(), rows=SERVICE.rows)
    with gzip.GzipFile(filename=str(Path(out) / 'bpr_trace.json.gz'), mode='wb', mtime=0) as f:
        f.write(json.dumps(payload, sort_keys=True, separators=(',', ':'), default=float).encode())
    works = SERVICE.works
    return dict(mode=SERVICE.mode, works=len(works), orders=len(SERVICE.orders),
                requested_qty=sum(o['qty'] for o in SERVICE.orders),
                quoted_cash=sum(o['qty'] * o['price'] for o in SERVICE.orders),
                statuses=[w['status'] for w in works], legacy_context_written=False, live_eligible=False)


def _save_exit():
    out = os.environ.get('BTC5M_LAN_RESULT_DIR')
    if out and Path(out).is_dir() and not (Path(out) / 'bpr_trace.json.gz').exists():
        finish(out)


atexit.register(_save_exit)
