from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
import copy

class OpenDecisionDisposition(str, Enum):
    NATIVE_DELEGATED = 'NATIVE_DELEGATED'
    HANDLED_NO_EMISSION = 'HANDLED_NO_EMISSION'

@dataclass
class RetainPendingOnceState:
    market_id: int
    h0_origin_responsibility_id: int
    h0_target_expand_side: str
    h0_source_key: str
    one_shot_used: bool = False
    consumed_decision_ref: str | None = None


def _h0_matches(sim, state: RetainPendingOnceState) -> bool:
    L = getattr(sim, 'q_ladder', None)
    P = getattr(sim, 'q_pending_active', None)
    if L is None or P is None or str(L.get('route')) != 'PENDING_ACTIVE':
        return False
    return (
        int(L.get('originResponsibilityId')) == int(state.h0_origin_responsibility_id)
        and int(P.get('originResponsibilityId')) == int(state.h0_origin_responsibility_id)
        and str(L.get('targetExpandSide')) == str(state.h0_target_expand_side)
        and str(P.get('targetExpandSide')) == str(state.h0_target_expand_side)
        and str(P.get('sourceKey')) == str(state.h0_source_key)
        and L.get('activeKey') is None
        and not bool(L.get('satisfiedElsewhere'))
    )


def _p0_reserved(sim, p0_key: str) -> bool:
    o = getattr(sim, 'orders', {}).get(str(p0_key))
    if not o:
        return False
    qty = float(o.get('qty') or 0.0)
    cum = float(o.get('cum') or 0.0)
    if qty - cum <= 1e-9:
        return False
    return any(str(k) == str(p0_key) for k in getattr(sim, 'slot_key', {}).values())


def eligible_retain_pending_once(sim, state: RetainPendingOnceState, p0_key: str, decision_ref: str) -> tuple[bool, dict]:
    checks = {
        'oneShotUnused': not bool(state.one_shot_used),
        'decisionUnused': state.consumed_decision_ref is None,
        'h0PendingIdentityExact': _h0_matches(sim, state),
        'p0PositivePhysicalReservation': _p0_reserved(sim, p0_key),
        'decisionRefPresent': bool(str(decision_ref)),
    }
    return all(checks.values()), checks


def dispatch_open_decision(sim, *, state: RetainPendingOnceState, action: str, p0_key: str, decision_ref: str, t: int, qv, end: int):
    """Research-only top-level open-decision dispatcher.

    RETAIN_PENDING_ONCE is a first-class no-emission disposition. It never calls
    sim._open_one_option and mutates only the research one-shot state. Disabled/native
    decisions delegate exactly once to the frozen native _open_one_option.
    """
    if str(action) != 'RETAIN_PENDING_ONCE':
        before = int(getattr(sim, 'submits', 0))
        out = sim._open_one_option(int(t), qv, int(end))
        return {
            'disposition': OpenDecisionDisposition.NATIVE_DELEGATED.value,
            'nativeReturn': out,
            'nativeSubmitDelta': int(getattr(sim, 'submits', 0)) - before,
            'checks': {'delegatedExactlyOnceByContract': True},
        }

    ok, checks = eligible_retain_pending_once(sim, state, p0_key, decision_ref)
    if not ok:
        return {'disposition': None, 'rejected': True, 'checks': checks}

    before = {
        'q_ladder': copy.deepcopy(getattr(sim, 'q_ladder', None)),
        'q_pending_active': copy.deepcopy(getattr(sim, 'q_pending_active', None)),
        'slot_key': copy.deepcopy(getattr(sim, 'slot_key', {})),
        'orders': copy.deepcopy(getattr(sim, 'orders', {})),
        'submits': int(getattr(sim, 'submits', 0)),
        'payments': copy.deepcopy(getattr(sim, 'resp_payment_rows', [])),
    }
    state.one_shot_used = True
    state.consumed_decision_ref = str(decision_ref)
    after = {
        'q_ladder': copy.deepcopy(getattr(sim, 'q_ladder', None)),
        'q_pending_active': copy.deepcopy(getattr(sim, 'q_pending_active', None)),
        'slot_key': copy.deepcopy(getattr(sim, 'slot_key', {})),
        'orders': copy.deepcopy(getattr(sim, 'orders', {})),
        'submits': int(getattr(sim, 'submits', 0)),
        'payments': copy.deepcopy(getattr(sim, 'resp_payment_rows', [])),
    }
    invariant = {k: before[k] == after[k] for k in before}
    return {
        'disposition': OpenDecisionDisposition.HANDLED_NO_EMISSION.value,
        'rejected': False,
        'checks': {
            **checks,
            'zeroPhysicalOrLedgerMutation': all(invariant.values()),
            'noSubmit': before['submits'] == after['submits'],
        },
        'invariantParity': invariant,
        'consumedDecisionRef': str(decision_ref),
    }
