"""Research-local exact quantity seam; no teacher, automatic grant or HFT import.

Use with frozen MinimalPairRoleSim via make_student_class. Book visibility is
independent of 1/price, size minima and cash. Reuse the existing explicit grant
ledger and planner; a partial plan is REJECTED, never silently resized. A native
receipt adapter must be installed before a real rollout. Test doubles are not HFT.
"""
from dataclasses import dataclass
from copy import deepcopy
import math

from tools.pair_core_asset_route_sizing_v2 import validate_size
from tools.pair_core_objective_quantity_planner_v1 import (
    ExecutionLimits, prepare, commit_reservation,
)

TERMINAL = {'FILLED', 'CANCELED', 'CANCELLED', 'EXPIRED', 'REJECTED'}


@dataclass(frozen=True)
class QuantityIntent:
    parent_id: int
    requested_qty: float
    route: str
    provenance: str


@dataclass(frozen=True)
class VenueGrid:
    tick: float
    quantity_step: float
    active_min_quantity: float
    active_min_notional: float
    provenance: str


@dataclass(frozen=True)
class ExactResult:
    reason: str
    intent: QuantityIntent | None = None
    plan: object | None = None


def raw_passive_levels(book, side):
    """Only real positive-depth interior prices; no size or financial filters."""
    if side not in ('UP', 'DOWN'):
        raise ValueError('explicit side required')
    levels = book['bids'] if side == 'UP' else book['asks']
    vals = []
    for price, depth in levels.items():
        p, q = float(price), float(depth)
        if not math.isfinite(p) or not math.isfinite(q) or q <= 0:
            continue
        p = round(p if side == 'UP' else 1-p, 10)
        if 0 < p < 1:
            vals.append(p)
    return sorted(set(vals), reverse=True)


def prepare_exact(ledger, asset, side, price, intent, *, key, quote_reference,
                  now_ms, market_end_ms, grid, allowed_routes=('PASSIVE',)):
    """Return the exact request or an explicit reason. Never mutate authority."""
    if not isinstance(intent, QuantityIntent) or not intent.provenance:
        return ExactResult('MISSING_EXPLICIT_QUANTITY_INTENT')
    if intent.route not in allowed_routes:
        return ExactResult('UNSUPPORTED_CHANNEL', intent)
    if (not isinstance(grid, VenueGrid) or not grid.provenance
            or any(not isinstance(v, (int, float)) or isinstance(v, bool)
                   or not math.isfinite(v) or v <= 0
                   for v in (grid.tick, grid.quantity_step, grid.active_min_quantity))
            or not isinstance(grid.active_min_notional, (int, float))
            or isinstance(grid.active_min_notional, bool)
            or not math.isfinite(grid.active_min_notional) or grid.active_min_notional < 0):
        return ExactResult('INVALID_DECLARED_VENUE_GRID', intent)
    try:
        validate_size(asset, intent.route, price, intent.requested_qty,
                      quantity_step=grid.quantity_step)
    except ValueError as exc:
        return ExactResult('SIZE_REJECTED: '+str(exc), intent)
    if type(intent.parent_id) is not int or intent.parent_id not in ledger.grants:
        return ExactResult('NO_EXPLICIT_GRANT', intent)
    if side != ledger.grants[intent.parent_id].side:
        return ExactResult('GRANT_SIDE_MISMATCH', intent)
    is_passive = intent.route == 'PASSIVE'
    limits = ExecutionLimits(
        grid.tick, grid.quantity_step,
        (18. if asset == 'BTC' else 12.) if is_passive else grid.active_min_quantity,
        1. if is_passive else grid.active_min_notional,
        float(intent.requested_qty),
    )
    result = prepare(ledger, intent.parent_id, intent.route, price, key=key,
        quote_reference=quote_reference, now_ms=now_ms,
        market_end_ms=market_end_ms, limits=limits)
    if result.plan is None:
        return ExactResult(result.reason, intent)
    if result.plan.quantity != float(intent.requested_qty):
        return ExactResult('EXACT_REQUEST_UNAFFORDABLE_NOT_RESIZED', intent)
    return ExactResult('EXACT_PLANNED_NOT_SUBMITTED', intent, result.plan)


def make_student_class(frozen_minimal_class):
    """Local subclass. No edits to frozen baseline, globals, live or risk settings."""
    class ExactQuantityMinimalStudent(frozen_minimal_class):
        def __init__(self, *args, quantity_provider, quantity_ledger, quantity_asset,
                     quantity_grid, audit_sink=None, **kwargs):
            self.quantity_provider = quantity_provider
            self.quantity_ledger = quantity_ledger
            self.quantity_asset = quantity_asset
            self.quantity_grid = quantity_grid
            self.quantity_audit_sink = audit_sink
            self.quantity_event_count = 0
            self.quantity_context = None
            self.quantity_ready = None
            self.quantity_rejections = {}
            self.quantity_payments = {}
            self.quantity_seen_receipts = set()
            super().__init__(*args, **kwargs)

        def quantity_state(self):
            return dict(inv=deepcopy(self.inv), cost=float(self.cost),
                unpaired={s:list(self.un[s]) for s in ('UP','DOWN')},
                pending=[dict(key=k, side=self.orders[k]['side'],
                    requestedQty=float(self.orders[k]['qty']),
                    confirmedQty=float(self.orders[k].get('cum') or 0.),
                    price=float(self.orders[k]['price']),
                    status=self.orders[k].get('status'),
                    cancelRequested=bool(self.orders[k].get('cancelRequested')),
                    role=self.key_role.get(k)) for k in self.slot_key.values()
                    if k in self.orders],
                grants={str(pid):self.quantity_ledger.account(pid)
                        for pid in self.quantity_ledger.grants})

        def _quantity_emit(self, event, **fields):
            self.quantity_event_count += 1
            if self.quantity_audit_sink is not None:
                self.quantity_audit_sink(dict(event=event, sequence=self.quantity_event_count, **fields))

        def _quantity_reject(self, reason, **fields):
            self.quantity_rejections[reason] = self.quantity_rejections.get(reason, 0)+1
            self._quantity_emit('QUANTITY_REJECT', reason=reason, **fields)

        def _live_price_levels(self, side):
            # Also used by inherited reanchor logic: an unfilled order does not
            # become stale merely because a *new* quantity request is illegal.
            return raw_passive_levels(self.book, side)

        def _role_decision(self, qv):
            answer = super()._role_decision(qv)
            if self.quantity_context is not None:
                self.quantity_context['role'] = answer[1]
            return answer

        def _candidate_from_levels(self, side, require_pair=True, require_budget=False):
            self.quantity_ready = None
            if self.quantity_context is None:
                self._quantity_reject('NO_DECISION_CONTEXT')
                return None
            used = self._used_prices(side)
            for p in self._live_price_levels(side):
                if round(p,10) in used:
                    continue
                self.minimal_pair_checks += 1
                if not self._pair_ok(side,p):
                    self.minimal_pair_blocks += 1
                    self.veto['MINIMAL_PAIR_ECONOMICS'] += 1
                    continue
                context = dict(deepcopy(self.quantity_context), side=side, price=p,
                               own=self.quantity_state(), asset=self.quantity_asset)
                intent = self.quantity_provider(context)
                key = f'{side}_{self.n}'
                ref = f"BOOK_RECEIPT:{context['now_ms']}:{side}:{p}"
                result = prepare_exact(self.quantity_ledger,self.quantity_asset,side,p,intent,
                    key=key,quote_reference=ref,now_ms=context['now_ms'],
                    market_end_ms=context['market_end_ms'],grid=self.quantity_grid)
                if result.plan is None:
                    self._quantity_reject(result.reason, t=context['now_ms'],
                        side=side, price=p,
                        requestedQty=intent.requested_qty if isinstance(intent,QuantityIntent) else None)
                    continue
                self.quantity_ready = result
                return p, result.plan.quantity, None
            return None

        def _submit_role(self, t, side, role, p, q, proj, source):
            result = self.quantity_ready
            if (result is None or result.plan is None or result.plan.side!=side
                    or result.plan.limit_price!=p or result.plan.quantity!=q
                    or result.plan.now_ms!=t or result.plan.key!=f'{side}_{self.n}'):
                self._quantity_reject('NO_MATCHING_EXACT_PLAN', t=t)
                return False
            plan=result.plan
            commit_reservation(self.quantity_ledger,plan,
                               quote_reference=plan.quote_reference,now_ms=t)
            before=self.quantity_state()
            try:
                ok=super()._submit_role(t,side,role,p,q,proj,source)
            except Exception:
                # Ambiguous native send must NOT release its reservation.
                self._quantity_emit('SUBMIT_EXCEPTION_RESERVATION_RETAINED', t=t, key=plan.key)
                raise
            finally:
                self.quantity_ready=None
            if not ok:
                if plan.key in self.orders:
                    raise RuntimeError('submit declined but owner exists; reconcile before continuing')
                self.quantity_ledger.confirm_terminal(plan.key,filled=0.,payment=0.)
            else:
                if self.orders[plan.key]['qty'] != q:
                    raise RuntimeError('native owner changed requested quantity')
            self._quantity_emit('EXACT_SUBMIT_RESULT',t=t,key=plan.key,side=side,role=role,
                price=p,requestedQty=result.intent.requested_qty,submittedQty=q if ok else 0.,
                intentProvenance=result.intent.provenance,ok=bool(ok),before=before,
                after=self.quantity_state())
            return ok

        def _open_one_option(self,t,qv,end):
            self.quantity_context=dict(now_ms=int(t),market_end_ms=int(end),
                                       quotes=deepcopy(qv),role=None)
            before=self.quantity_state()
            try:
                super()._open_one_option(t,qv,end)
                self._quantity_emit('OWN_DECISION',t=int(t),before=before,
                    after=self.quantity_state(),role=self.quantity_context['role'],
                    blockedBeforeRole=self.quantity_context['role'] is None)
            finally:
                self.quantity_context=None
                self.quantity_ready=None

        def process(self,t):
            super().process(t)
            if not hasattr(self,'_receipt_delta_rows'):
                raise RuntimeError('verified canonical receipt adapter required')
            for r in self._receipt_delta_rows:
                seq=int(r['sequence'])
                if seq in self.quantity_seen_receipts:
                    continue
                key=r['key']
                if key not in self.quantity_ledger.carriers:
                    raise RuntimeError('native fill has no explicit quantity owner')
                self.quantity_seen_receipts.add(seq)
                payment,fees=self.quantity_payments.get(key,(0.,0.))
                payment += float(r['qty'])*float(r['contractPrice'])
                fees += float(r['fee'])
                self.quantity_ledger.confirm_cumulative(key,float(r['cumulative_qty']),payment,fees)
                self.quantity_payments[key]=(payment,fees)
            for key,c in self.quantity_ledger.carriers.items():
                if c.state=='TERMINAL' or key not in self.orders:
                    continue
                o=self.orders[key]
                s=self.snap(o)
                status=str(s.get('status') or '').upper()
                if status in TERMINAL:
                    if abs(float(s.get('cumExecQty') or 0.)-c.filled)>1e-8:
                        raise RuntimeError('terminal arrived without reconciled receipts')
                    self.quantity_ledger.confirm_terminal(key,filled=c.filled,payment=c.payment,fees=c.fees)
                elif o.get('cancelRequested'):
                    self.quantity_ledger.request_cancel(key)
            self.quantity_ledger.invariants()
            self._quantity_emit('OWN_RECEIPT_STATE',t=int(t),
                receipts=deepcopy(self._receipt_delta_rows),after=self.quantity_state())

    ExactQuantityMinimalStudent.__name__='ExactQuantityMinimalStudentV1'
    return ExactQuantityMinimalStudent
