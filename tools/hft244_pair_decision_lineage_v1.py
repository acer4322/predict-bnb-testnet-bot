"""Bounded behavior-inert call-site observer. No economic/runtime authority."""
from collections import Counter
import hashlib
import json
import math

from .hft244_pair_route_legality_v1 import crossing_owners


def blob(x):
    return json.dumps(x, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def pure_quotes(book, lots, owners):
    """Reproduce frozen Minimal first admissible quote, NOT complete authority."""
    result = {}
    for side in ('UP', 'DOWN'):
        opp = 'DOWN' if side == 'UP' else 'UP'
        levels = sorted(book['bids'], reverse=True) if side == 'UP' else [1-p for p in sorted(book['asks'])]
        held = sum(q for q, p in lots[opp])
        avg = sum(q*p for q, p in lots[opp])/held if held > 1e-9 else None
        used = {round(o['price'], 10) for o in owners if o['side'] == side}
        seen = set(); legal = []; first = None; blocked = 0
        for p in levels:
            p = round(float(p), 10)
            if p <= 1e-9 or p >= 1-1e-9 or p in seen:
                continue
            q = 1/p
            if not math.isfinite(q) or q <= 1e-9 or q > 12+1e-9:
                continue
            seen.add(p); legal.append(p)
            if p in used:
                continue
            if avg is not None and avg+p > 1.0000001:
                blocked += 1
                continue
            if first is None:
                first = [p, q, None]
        result[side] = dict(first=first, oppositeAverage=avg, oppositeUnmatched=held,
                            used=sorted(used), levelCount=len(legal), levelHash=hashlib.sha256(blob(legal)).hexdigest(),
                            pairRejectedUnusedLevels=blocked,
                            hasPhysicalSlot=len(owners) < 4)
    return result


def categories(c, t):
    """Localize differing call-site inputs/outcomes; not terminal causal shares."""
    cats = []
    if c['roles'] != t['roles']:
        cats.append('CORE_OWNERSHIP_OR_ROLE_PRIORITY')
    if c['candidates'] != t['candidates']:
        def side_cap(f):
            roles = [x['decision'] for x in f['roles'] if x['scope']=='CONTINUATION']
            return bool(roles and sum(o['side']==roles[-1][0] for o in f['state']['owners'])>=4)
        if side_cap(c) != side_cap(t):
            cats.append('PHYSICAL_CAPACITY')
        else:
            cats.append('PAIR_FRONTIER_OR_SELECTED_SIDE')
    if c['submits'] != t['submits']:
        cs = [x['reason'] for x in c['submits']]
        ts = [x['reason'] for x in t['submits']]
        if cs != ts and 'PHYSICAL_CAPACITY' in cs+ts:
            cats.append('PHYSICAL_CAPACITY')
        if cs != ts and 'CONSERVATIVE_CROSS_GUARD' in cs+ts:
            cats.append('CONSERVATIVE_CROSS_GUARD')
        if not cats:
            cats.append('SUBMISSION_OR_NATIVE_ACTION')
    if c['cancels'] != t['cancels']:
        cats.append('CANCEL_LIFECYCLE')
    if c['expiryEligible'] != t['expiryEligible']:
        cats.append('EXPIRY_ELIGIBILITY_NOT_ACK_PROOF')
    if c['receipts'] != t['receipts']:
        cats.append('NON_DIRECT_RECEIPT_DIVERGENCE')
    return cats


class Recorder:
    def __init__(self, keep=False, reference=None):
        self.frames = [] if keep else None
        self.reference = reference
        self.count = 0; self.bytes = 0; self.hash = hashlib.sha256()
        self.first = []; self.category_first = {}; self.counts = Counter()
        self.matched_pre = 0; self.differences = 0; self.first_endogenous = None

    def append(self, frame):
        data = blob(frame)
        if len(data) > 65536 or self.count >= 20000:
            raise RuntimeError('INSUFFICIENT_TRACE: frame/clock resource limit')
        self.hash.update(data); self.count += 1
        if self.frames is not None:
            self.bytes += len(data)
            if self.bytes > 32*1024**2:
                raise RuntimeError('INSUFFICIENT_TRACE: control RAM buffer limit')
            # Deep freeze before any simulator state is mutated on the next clock.
            self.frames.append(json.loads(data))
        if self.reference is None:
            return
        if self.count > len(self.reference):
            raise ValueError('unmatched exogenous decision count')
        c = self.reference[self.count-1]
        if (c['tick'], c['t'], c['qv']) != (frame['tick'], frame['t'], frame['qv']):
            raise ValueError('exogenous clock/quote mismatch')
        if not c['postReady']:
            # Planned direct submit is separate from these decision fields.
            keys = ('state', 'roles', 'candidates', 'submits', 'cancels', 'receipts', 'expiryEligible')
            if any(c[k] != frame[k] for k in keys):
                raise ValueError('C/T observational prefix mismatch')
            self.matched_pre += 1
            return
        cats = categories(c, frame)
        if not cats:
            return
        self.differences += 1; self.counts.update(cats)
        event = dict(index=self.count-1, t=frame['t'], categories=cats, C=c, T=frame)
        if len(self.first) < 24:
            self.first.append(event)
        for cat in cats:
            if cat not in self.category_first:
                self.category_first[cat] = event
        endogenous = [x for x in cats if x != 'NON_DIRECT_RECEIPT_DIVERGENCE']
        if endogenous and self.first_endogenous is None:
            self.first_endogenous = event

    def summary(self):
        if self.reference is not None and self.count != len(self.reference):
            raise ValueError('unmatched suffix decision count')
        brief = lambda e: {k:e[k] for k in ('index','t','categories')} if e else None
        retained = {e['index'] for e in self.first}
        extra = {e['index']:e for e in self.category_first.values() if e['index'] not in retained}
        return dict(clocks=self.count, rollingHash=self.hash.hexdigest(), controlBytes=self.bytes,
                    matchedPreBoundaryClocks=self.matched_pre, differingClocks=self.differences,
                    categoryCounts=dict(self.counts), firstEndogenous=brief(self.first_endogenous),
                    first24=self.first, categoryFirst={k:brief(e) for k,e in self.category_first.items()},
                    additionalCategoryFirst=list(extra.values()))


def make_sim(Parent):
    class Observed(Parent):
        def __init__(self, tape, arm, recorder):
            self._probe_trace = recorder
            self._probe_trace_frame = None
            self._probe_trace_pending_receipts = []
            self._probe_trace_pending_cancels = []
            self._probe_trace_expiry = []
            self._probe_trace_tick = 0
            self._probe_trace_scope = 'CONTINUATION'
            super().__init__(tape, arm)

        def _trace_origin(self, key):
            o = self.orders[key]
            base = [int(o['placed']), o['side'], o['price'], o['qty'], self.key_role.get(key, 'UNASSIGNED')]
            # Preserve duplicate same-clock origins without using branch-local ids.
            earlier = sum(1 for k, other in self.orders.items() if other['n'] < o['n'] and
                [int(other['placed']), other['side'], other['price'], other['qty'], self.key_role.get(k, 'UNASSIGNED')] == base)
            return base+[earlier]

        def _trace_state(self):
            owners = []
            for slot, key in self.slot_key.items():
                o = self.orders[key]; snap = self.snap(o)
                owners.append(dict(slot=slot, key=key, origin=self._trace_origin(key), side=o['side'], price=o['price'],
                    remaining=self._remaining(key), status=snap['status'],
                    cancelRequested=bool(o.get('cancelRequested')), role=self.key_role.get(key, 'UNASSIGNED')))
            lots = {s: [[float(q), float(p)] for q, p in self.un[s]] for s in ('UP', 'DOWN')}
            menu = pure_quotes(self.book, lots, owners)
            return dict(inventory=dict(self.inv), cost=self.cost, fifo=lots, owners=owners,
                        quoteMenu=menu, pairReserve=self.pairReserve, pairedQty=self.pairedQty)

        def process(self, t):
            super().process(t)
            self._probe_trace_tick += 1
            if len(self._receipt_ledger.seen) > 500:
                raise RuntimeError('INSUFFICIENT_TRACE: receipt limit')
            for r in self._receipt_delta_rows:
                if int(r['receive_ts']) > int(t)*1000000:
                    raise ValueError('future native receipt in observer')
                if r['key'] == self._probe_key:
                    continue  # Direct receipt is verified separately in native parity.
                self._probe_trace_pending_receipts.append(dict(origin=self._trace_origin(r['key']),
                    receive_ts=r['receive_ts'], exchange_ts=r['exchange_ts'], qty=r['qty'],
                    price=r['contractPrice'], side=self.orders[r['key']]['side'], maker=r['maker']))

        def _request_cancel(self, t, sid, reason):
            key = self.slot_key.get(sid)
            origin = self._trace_origin(key) if key else None
            ok = super()._request_cancel(t, sid, reason)
            event = dict(t=int(t), origin=origin, reason=reason, accepted=bool(ok))
            if key == self._probe_key:
                return ok  # Its own lifecycle belongs to the planned intervention.
            target = self._probe_trace_frame
            if target is None:
                self._probe_trace_pending_cancels.append(event)
            else:
                target['cancels'].append(event)
            return ok

        def cancel_expired(self, t):
            # Read-only eligibility snapshot; original method alone calls cancel.
            # An eligible request is not an acknowledged cancellation.
            for key, o in self.orders.items():
                if key == self._probe_key:
                    continue
                s = self.snap(o)
                if s.get('status') in ('NEW', 'PARTIALLY_FILLED') and t-o['placed'] >= 5000:
                    cur = self.bt.orders(0).get(o['n'])
                    if cur is not None and bool(cur.cancellable):
                        self._probe_trace_expiry.append(dict(t=int(t), origin=self._trace_origin(key)))
            return super().cancel_expired(t)

        def _probe_select(self, qv):
            self._probe_trace_scope = 'PROBE_SELECTION'
            try:
                return super()._probe_select(qv)
            finally:
                self._probe_trace_scope = 'CONTINUATION'

        def _role_decision(self, qv):
            value = super()._role_decision(qv)
            if self._probe_trace_frame is not None:
                self._probe_trace_frame['roles'].append(dict(scope=self._probe_trace_scope, decision=list(value)))
            return value

        def _candidate_from_levels(self, side, require_pair=True, require_budget=False):
            value = super()._candidate_from_levels(side, require_pair, require_budget)
            f = self._probe_trace_frame
            if f is not None:
                represented = f['state']['quoteMenu'][side]['first']
                if (list(value) if value is not None else None) != represented:
                    raise ValueError('pure candidate reconstruction mismatch')
                f['candidates'].append(dict(side=side, value=list(value) if value is not None else None))
            return value

        def _submit_role(self, t, side, role, p, q, proj, source):
            capacity = len(self.slot_key) >= self.max_slots
            hits = [] if capacity else crossing_owners(side, p, self._reservations())
            guarded = bool(hits and self._probe_arm != 'A' and self._probe_stage != 'UNSELECTED')
            ok = super()._submit_role(t, side, role, p, q, proj, source)
            if self._probe_trace_frame is not None:
                self._probe_trace_frame['submits'].append(dict(side=side, role=role, price=p, qty=q,
                    accepted=bool(ok), reason='PHYSICAL_CAPACITY' if capacity else
                    'CONSERVATIVE_CROSS_GUARD' if guarded else 'SUBMITTED' if ok else 'OTHER_REJECTION'))
            return ok

        def _open_one_option(self, t, qv, end):
            f = dict(t=int(t), tick=self._probe_trace_tick, postReady=self._probe_ready is not None,
                     qv=qv, state=self._trace_state(), roles=[], candidates=[], submits=[],
                     cancels=self._probe_trace_pending_cancels, receipts=self._probe_trace_pending_receipts,
                     expiryEligible=self._probe_trace_expiry)
            self._probe_trace_pending_cancels = []; self._probe_trace_pending_receipts = []
            self._probe_trace_expiry = []
            self._probe_trace_frame = f
            try:
                value = super()._open_one_option(t, qv, end)
            finally:
                self._probe_trace_frame = None
            self._probe_trace.append(f)
            return value
    return Observed
