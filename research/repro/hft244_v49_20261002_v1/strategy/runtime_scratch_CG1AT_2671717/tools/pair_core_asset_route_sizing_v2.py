"""Corrected USER research contract: passive minima, not caps or fixed tickets.

ETH passive: submitted quantity >=12 and quoted notional >=1.
Isolated BTC research: NEW quantity exactly15 and quoted notional >=1.
Active: does not inherit these passive minima or an implicit12/18 upper bound.
Neither route is exempt from explicit grants, cash, ownership or venue rules.
These functions describe submitted orders, not each partial execution or leaves.
No native integration, policy import, economic grant creation or automatic sizing.
"""
from decimal import Decimal, ROUND_CEILING
import math

PASSIVE_MIN_SHARES = {'ETH': Decimal('12'), 'BTC': Decimal('15')}
PASSIVE_MIN_NOTIONAL = Decimal('1')
CONTRACT_ID = 'BTC5M_2026085_PASSIVE_FIXED15_ACTIVE_UNCHANGED_RESEARCH_ONLY_V2'


def _asset(asset):
    if not isinstance(asset, str) or asset not in PASSIVE_MIN_SHARES:
        raise ValueError('explicit ETH or BTC asset required')
    return asset


def _positive(value, name):
    if (not isinstance(value, (int, float, Decimal)) or isinstance(value, bool)
            or not math.isfinite(value) or value <= 0):
        raise ValueError(name + ' must be finite and positive')
    return Decimal(str(value))


def _price(price):
    p = _positive(price, 'limit price')
    if p >= 1:
        raise ValueError('interior binary-contract limit price required')
    return p


def minimum_passive_quantity(asset, price, *, quantity_step):
    """Smallest submitted quantity satisfying BOTH passive minima and lot step.

    Returned quantity is a lower bound, not automatic economic permission to buy.
    The supplied quantity step is an explicit caller input, not a verified venue
    specification. A larger separately authorized order is valid too.
    """
    asset = _asset(asset)
    p = _price(price)
    step = _positive(quantity_step, 'quantity step')
    lower = max(PASSIVE_MIN_SHARES[asset], PASSIVE_MIN_NOTIONAL / p)
    qty = (lower / step).to_integral_value(rounding=ROUND_CEILING) * step
    # Guard against decimal division rounding exactly onto an infeasible boundary.
    if qty < PASSIVE_MIN_SHARES[asset] or qty * p < PASSIVE_MIN_NOTIONAL:
        qty += step
    return float(qty)


def validate_size(asset, route, price, qty, *, quantity_step=None):
    """Validate order-size scope only; not a grant or native venue acceptance.

    Do NOT apply this to receipt.qty or remaining_qty. Partial fills and residuals
    can be below a submitted-order minimum. New replacement orders are new orders.
    """
    asset = _asset(asset)
    if route not in ('PASSIVE', 'ACTIVE'):
        raise ValueError('explicit PASSIVE or ACTIVE route required')
    p = _price(price)
    q = _positive(qty, 'quantity')
    if quantity_step is not None:
        step = _positive(quantity_step, 'quantity step')
        if q % step != 0:
            raise ValueError('quantity is off the declared grid')
    if route == 'PASSIVE':
        if asset == 'BTC' and q != Decimal('15'):
            raise ValueError('isolated BTC Passive NEW requires the full15 ticket')
        if q < PASSIVE_MIN_SHARES[asset]:
            raise ValueError('below passive minimum submitted quantity')
        if q * p < PASSIVE_MIN_NOTIONAL:
            raise ValueError('below passive minimum quoted notional')
    # Active deliberately receives NO maker ticket/min-shares/cap test.


def reserve_authorized(ledger, asset, key, parent_id, route, qty, limit,
                       fee_cap, *, now_ms, market_end_ms, quantity_step=None):
    """Use an EXISTING grant. Insufficient authority stays insufficient.

    A minimum-sized candidate cannot silently enlarge grant qty/cash. The shared
    ledger still enforces pending/cancel claims, pools, self-cross and market end.
    """
    validate_size(asset, route, limit, qty, quantity_step=quantity_step)
    return ledger.reserve(key, parent_id, route, qty, limit, fee_cap,
                          now_ms=now_ms, market_end_ms=market_end_ms)


def sizing_declaration(asset, route):
    asset = _asset(asset)
    if route not in ('PASSIVE', 'ACTIVE'):
        raise ValueError('unknown route')
    return {'contract': CONTRACT_ID, 'asset': asset, 'route': route,
            'passiveMinNotional': 1. if route == 'PASSIVE' else None,
            'passiveMinShares': float(PASSIVE_MIN_SHARES[asset]) if route == 'PASSIVE' else None,
            'fixedNotionalTarget': None, 'inheritedSizeUpperBound': None,
            'appliesTo': 'NEW_SUBMITTED_ORDER_NOT_PARTIAL_RECEIPT',
            'economicAuthority': 'EXTERNAL_EXPLICIT_GRANT_REQUIRED',
            'venueLegality': 'SEPARATE_VERIFICATION_REQUIRED',
            'targetOriginalOrderMinimum': 'NOT_VERIFIED_FROM_FILL_ONLY_ARCHIVE',
            'nativeIntegration': False}
