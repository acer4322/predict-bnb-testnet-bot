"""Research NEW-order representation only; never normalize fills or liability.

The frozen kernel selects integer 0.01 lots using floor/ceil then multiplies
by a binary float. Encode that same lot count with a short decimal spelling
before native validation. Genuine off-grid inputs retain their rejection.
"""
from copy import deepcopy
from decimal import Decimal, ROUND_HALF_EVEN
import math


def canonical_quantity(q, step):
    if isinstance(q, bool) or not isinstance(q, (int, float)):
        return q
    if not math.isfinite(q) or q <= 0:
        return q
    ds = Decimal(str(step))
    if not ds.is_finite() or ds <= 0:
        raise ValueError('quantity step must be finite and positive')
    dq = Decimal(str(q))
    if dq % ds == 0:
        return q
    units = (dq / ds).to_integral_value(rounding=ROUND_HALF_EVEN)
    candidate = float(units * ds)
    if candidate <= 0 or not math.isfinite(candidate):
        return q
    tolerance = 2 * max(math.ulp(float(q)), math.ulp(candidate))
    if tolerance >= float(ds) / 4:
        return q  # Too coarse to identify an unambiguous intended lot count.
    if abs(candidate - q) > tolerance:
        return q
    if Decimal(str(candidate)) % ds != 0:
        return q
    return candidate


def encode_proposals(proposals, step):
    """Preserve decisions/legality and copy only the outgoing proposal objects."""
    encoded = deepcopy(proposals)
    changes = []
    for action, proposal in enumerate(encoded):
        for index, order in enumerate(proposal['orders']):
            raw = order['qty']
            clean = canonical_quantity(raw, step)
            if clean != raw:
                order['qty'] = clean
                changes.append({'action': action, 'order_index': index,
                                'raw_qty': raw, 'encoded_qty': clean,
                                'step': step, 'delta': clean - raw})
    return encoded, changes
