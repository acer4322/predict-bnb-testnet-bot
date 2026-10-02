"""Research-only V2 adapter. Uses observed order state, never elapsed age."""
from __future__ import annotations

from dataclasses import asdict
import math

from tools.eth_repair_modular.persistent_execution_roles import CORE, SATELLITE
from tools.eth_repair_modular.role_order_readiness import CancelObservation, ReanchorIntentLedger

EPS = 1e-9
TERMINAL = {'FILLED', 'CANCELED', 'CANCELLED', 'REJECTED', 'EXPIRED'}


def observe_cancel_readiness(sim, key):
    """Read the same local/HFT order surfaces as the existing cancel authority."""
    order = sim.orders.get(key)
    terminal = bool(sim.carrierLedger.get(key, {}).get('terminalConfirmed'))
    if order is None:
        return CancelObservation(False, False, None, False, False, terminal)
    try:
        snap = sim.snap(order)
        status = snap.get('status')
    except Exception as exc:
        return CancelObservation(True, False, None, False, False, terminal, type(exc).__name__)
    terminal = terminal or (status is not None and str(status).upper() in TERMINAL)
    if terminal:
        return CancelObservation(True, True, str(status), False, False, True)
    try:
        current = sim.bt.orders(0).get(order['n'])
        present = current is not None
        cancellable = present and bool(current.cancellable)
    except Exception as exc:
        return CancelObservation(True, True, str(status), False, False, False, type(exc).__name__)
    return CancelObservation(True, True, str(status), present, cancellable, False)


def fresh_satellite_handoff(sim, hybrid, t, meta, rows):
    """Return a newly checked proposal, or unchanged existing authorization.

    Called only after old-terminal evidence. No reservation or order mutation.
    A price increase needs an additional fresh V16 allow; no quantity uplift.
    """
    detail = dict(t=int(t), event='TERMINAL_SATELLITE_REVALIDATION', key=meta['key'])
    original = dict(meta)
    if meta.get('hybridFanoutApprovedPrice') is None:
        return original, {**detail, 'decision': 'ORIGINAL_NON_SATELLITE_AUTHORITY'}
    entry = sim.carrierLedger.get(meta['key'], {})
    if not entry.get('terminalConfirmed'):
        raise ValueError('fresh replacement proposal requires terminal evidence')
    pid, side = int(meta['parentId']), meta['side']
    live = [r for r in rows if r['key'] != meta['key']]
    if any(r['parentId'] != pid or r['side'] != side for r in live):
        return original, {**detail, 'decision': 'RETAIN_ORIGINAL_FOREIGN_SCOPE'}
    cores = [r for r in live if sim.executionRoles.role(r['key']) == CORE]
    if not cores:
        return original, {**detail, 'decision': 'RETAIN_ORIGINAL_NO_LIVE_CORE'}
    target, bid, ask, priority = sim._priority_target(side)
    approved = float(meta['hybridFanoutApprovedPrice'])
    detail.update(approvedPrice=approved, target=target, bid=bid, ask=ask)
    if (target is None or priority is None or not priority.improved or
            not math.isfinite(float(target)) or not (EPS < target < ask - EPS)):
        return original, {**detail, 'decision': 'RETAIN_ORIGINAL_NO_CURRENT_MAKER_PROPOSAL'}
    if abs(target - approved) <= EPS:
        return original, {**detail, 'decision': 'RETAIN_ORIGINAL_CURRENT_PRICE_UNCHANGED'}
    debt = float(sim._parent_debt_now(pid))
    sim._sync_parent_occupancy()
    available = float(sim.parentExecutionOccupancy.available(pid, debt))
    remainder = max(0.0, float(entry['submittedQty']) - float(entry['actualFilled']))
    legal = 1.0 / target
    approved_qty = float(meta['hybridFanoutApprovedQty'])
    qty = min(legal, approved_qty, remainder, available)
    before = float(sim._current_payoffs()['floor'])
    after, floors = sim._floor_after_buys(side, [(r['price'], r['remaining']) for r in live] + [(target, qty)])
    safe = (after is not None and math.isfinite(after) and after >= before - 1e-7
            and all(math.isfinite(f) and f >= before - 1e-7 for f in floors))
    detail.update(debt=debt, available=available, oldRemaining=remainder, approvedQty=approved_qty,
                  legalQty=legal, proposedQty=qty, floorBefore=before, floorAfter=after,
                  jointFloorSafe=safe, coreKeys=[r['key'] for r in cores])
    if not all(math.isfinite(v) for v in (qty, legal, available, debt)) or qty + EPS < legal or not safe:
        return original, {**detail, 'decision': 'RETAIN_ORIGINAL_LEGAL_QUOTA_OR_FLOOR_BLOCK'}
    # This is deliberately stricter than the original priority satellite route.
    # A new upward price is not authorized by an earlier cheap-core lease.
    if target > approved + EPS:
        audit = sim._econ(int(t), side, qty, target)
        detail['upwardV16'] = audit
        if not bool(audit.get('allow')):
            return original, {**detail, 'decision': 'RETAIN_ORIGINAL_UPWARD_V16_BLOCK'}
    proposal = dict(original)
    proposal.update(hybridFanoutApprovedPrice=target, hybridFanoutApprovedQty=qty,
                    hybridFanoutApprovedAt=int(t), hybridFanoutCoreKeys=[r['key'] for r in cores])
    return proposal, {**detail, 'decision': 'FRESH_SATELLITE_PROPOSAL'}


def make_readiness_adapter(hybrid):
    from tools.run_gpt6_repair_candidate_v1 import make_adapter
    baseline = make_adapter(hybrid, True, True)

    class ReadinessAwareRoleAdapter(baseline):
        def __init__(self, *args, **kwargs):
            self.readinessIntents = ReanchorIntentLedger()
            self.readinessEvents = []
            self._readinessContext = None
            self._lastReadinessEvidence = {}
            super().__init__(*args, **kwargs)

        def _observe_intent(self, t, key):
            observation = observe_cancel_readiness(self, key)
            intent = self.readinessIntents.observe(key, observation)
            signature = (intent.state, observation)
            if self._lastReadinessEvidence.get(key) != signature:
                self.readinessEvents.append(dict(t=int(t), event='ORDER_READINESS_OBSERVED',
                    key=key, parentId=intent.parent_id, rootKey=intent.root_key,
                    state=intent.state, reason=observation.reason, **asdict(observation)))
                self._lastReadinessEvidence[key] = signature
            return observation

        def _request_cancel(self, t, row, reason):
            rows = [r for r in self.gptLiveRows if r['parentId'] == row['parentId'] and r['side'] == row['side']]
            chosen = self.executionRoles.select(rows, reason)
            role = self.executionRoles.entries.get(chosen['key']) if chosen else None
            previous = self._readinessContext
            # Structural/safety cancellation retains its original authority.
            self._readinessContext = (chosen, role) if (reason == 'FRONTIER_REANCHOR' and
                                                       role and role.role == SATELLITE) else None
            try:
                # Re-run original role selection and every original economic/physical preflight.
                return super()._request_cancel(t, row, reason)
            finally:
                self._readinessContext = previous

        def _cancel_key(self, t, key):
            context = self._readinessContext
            if context is None or context[0]['key'] != key:
                return super()._cancel_key(t, key)
            chosen, role = context
            # Reached only after the original D legal/quota/Floor preflight.
            is_new = key not in self.readinessIntents.entries
            self.readinessIntents.request(key, chosen['parentId'], chosen['side'], role.root_key, t)
            if is_new:
                self.readinessEvents.append(dict(t=int(t), event='UNSENT_REANCHOR_INTENT',
                    key=key, parentId=role.parent_id, rootKey=role.root_key,
                    createsReservation=False, createsCancelPending=False))
            observation = self._observe_intent(t, key)
            if not self.readinessIntents.can_dispatch(key):
                return False
            # Do not dispatch against a pre-existing cancellation from another authority.
            carrier = self.carrierLedger.get(key, {})
            if carrier.get('cancelRequested') or key in self.cancelRequestedAt:
                self.readinessIntents.record_dispatch(key, accepted=False, t=t)
                self.readinessEvents.append(dict(t=int(t), event='EXISTING_CANCEL_OWNERSHIP_WAIT', key=key))
                return False
            try:
                ok = bool(super()._cancel_key(t, key))
            except Exception as exc:
                self.readinessIntents.record_dispatch(key, accepted=False, t=t)
                self.readinessEvents.append(dict(t=int(t), event='CANCEL_OUTCOME_UNKNOWN', key=key,
                                                error=type(exc).__name__))
                raise
            self.readinessIntents.record_dispatch(key, accepted=ok, t=t)
            self.readinessEvents.append(dict(t=int(t), event='READY_CANCEL_DISPATCH', key=key,
                parentId=role.parent_id, rootKey=role.root_key, ready=observation.ready,
                accepted=ok, outcome='CANCEL_ACCEPTED' if ok else 'CANCEL_OUTCOME_UNKNOWN'))
            return ok

        def _maybe_reanchor(self, t):
            # Original callback after fill processing. Never run a timer or advance HFT.
            rows = self._managed_live(int(t))
            by_key = {r['key']: r for r in rows}
            quotes = hybrid.lock.v1.quotes(self.book)
            for key, intent in list(self.readinessIntents.entries.items()):
                self._observe_intent(t, key)
                if intent.state not in ('WAIT_READINESS', 'READY'):
                    continue
                row = by_key.get(key)
                scoped = [r for r in rows if r['parentId'] == intent.parent_id and r['side'] == intent.side]
                behind = bool(scoped and quotes and intent.side in quotes and
                              float(quotes[intent.side]['bid']) > max(r['price'] for r in scoped) + EPS)
                core = any(self.executionRoles.role(r['key']) == CORE for r in scoped)
                if row is None or not behind or not core:
                    self.readinessIntents.abandon_unsubmitted(key)
                    self.readinessEvents.append(dict(t=int(t), event='UNSENT_INTENT_INVALIDATED',
                                                     key=key, reason='CURRENT_SCOPE_CORE_OR_FRONTIER_CHANGED'))
            # Current original trigger/structural capacity/selection/preflight must still allow.
            return super()._maybe_reanchor(t)

        def _submit_replacement(self, t, meta, rows, best):
            key = meta['key']
            terminal = bool(self.carrierLedger.get(key, {}).get('terminalConfirmed'))
            if not terminal:
                return False
            role = self.executionRoles.role(key)
            proposal = meta
            event = None
            if role == SATELLITE and meta.get('hybridFanoutApprovedPrice') is not None:
                # A late fill can consume old unfilled quantity while cancel is in flight.
                # Even the original-price fallback must not reuse the consumed tranche.
                entry = self.carrierLedger[key]
                bounded_meta = dict(meta)
                bounded_meta['hybridFanoutApprovedQty'] = min(float(meta['hybridFanoutApprovedQty']),
                    max(0.0, float(entry['submittedQty']) - float(entry['actualFilled'])))
                proposal, event = fresh_satellite_handoff(self, hybrid, t, bounded_meta, rows)
            n0 = self.n
            ok = super()._submit_replacement(t, proposal, rows, best)
            if event is not None:
                event.update(submitted=bool(ok), oldTerminalConfirmed=terminal,
                             replacementKey=f"{meta['side']}_{n0}" if ok else None)
                self.readinessEvents.append(event)
            return ok

    return ReadinessAwareRoleAdapter
