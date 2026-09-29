"""User-specified research sizing; not venue approval or economic authority.

PASSIVE: exactly one quoted cash unit, ETH max12 / BTC max18 shares. Reject
oversized candidates, never clamp quantity. ACTIVE: caller-selected quantity,
checked by the explicit shared grant ledger, not inherited passive ticket sizing.
No engine import, dispatch, threshold tuning, or mutation of legacy modules.
"""
import math

EPS = 1e-9
PASSIVE_CAPS = {'ETH': 12., 'BTC': 18.}


def _asset(asset):
    if asset not in PASSIVE_CAPS:
        raise ValueError('explicit supported asset ETH or BTC required')
    return asset


def _positive(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def passive_quantity(asset, price):
    """None means the fixed cash ticket exceeds the asset's share cap."""
    cap = PASSIVE_CAPS[_asset(asset)]
    if not _positive(price) or not EPS < price < 1. - EPS:
        raise ValueError('finite interior contract price required')
    qty = 1. / price
    return qty if qty <= cap + EPS else None


def validate_size(asset, route, price, qty):
    """Validate sizing semantics only. Active size is NOT thereby authorized."""
    _asset(asset)
    if route not in ('PASSIVE', 'ACTIVE'):
        raise ValueError('explicit PASSIVE or ACTIVE route required')
    if not _positive(price) or not EPS < price < 1. - EPS or not _positive(qty):
        raise ValueError('finite positive quantity/interior price required')
    if route == 'PASSIVE':
        expected = passive_quantity(asset, price)
        if expected is None:
            raise ValueError('passive fixed-cash ticket exceeds asset share cap')
        if abs(qty - expected) > EPS:
            raise ValueError('passive ticket must remain exactly one quoted cash unit')


def reserve_authorized(ledger, asset, key, parent_id, route, qty, limit,
                       fee_cap, *, now_ms, market_end_ms):
    """Attach the sizing rule to existing shared authority, cash and slot checks.

    No grant is issued here. The ledger still rejects missing/exhausted authority,
    pending double reservation, own crossing, ended markets and full route pools.
    Caller must separately establish tick/lot/minimum/fees/native submission rules.
    """
    validate_size(asset, route, limit, qty)
    return ledger.reserve(key, parent_id, route, qty, limit, fee_cap,
                          now_ms=now_ms, market_end_ms=market_end_ms)


def make_passive_sim(minimal, asset):
    """Isolated Minimal subclass; changes only asset-specific passive sizing.

    Must rebuild from the visible book: calling super()._live_price_levels first
    would irreversibly lose BTC prices removed by the legacy ETH12 filter.
    Used-price, Pair, priority, cutoff, cancel and native behavior remain inherited.
    This factory does NOT enable an Active manager or remove the 180s boundary.
    """
    asset = _asset(asset)

    class AssetSizedMinimal(minimal.MinimalPairRoleSim):
        def _live_price_levels(self, side):
            if side not in ('UP', 'DOWN'):
                raise ValueError('unknown side')
            vals = ([float(p) for p in sorted(self.book['bids'], reverse=True)]
                    if side == 'UP' else
                    [1. - float(a) for a in sorted(self.book['asks'])])
            out, seen = [], set()
            for raw in vals:
                p = minimal.v2.kprice(raw)
                if not math.isfinite(p) or p <= EPS or p >= 1. - EPS or p in seen:
                    continue
                if passive_quantity(asset, p) is None:
                    continue
                seen.add(p)
                out.append(p)
            return out

        def _submit_role(self, t, side, role, p, q, proj, source):
            # This entry is ordinary passive only. Active requires its own route
            # entry with explicit economic quantity, not bypass via role names.
            validate_size(asset, 'PASSIVE', p, q)
            return super()._submit_role(t, side, role, p, q, proj, source)

        def sizing_declaration(self):
            return dict(asset=asset, route='PASSIVE', quotedNotional=1.,
                        maxShares=PASSIVE_CAPS[asset], overflow='REJECT_NOT_CLAMP',
                        activeManager=False, venueLegality='NOT_ESTABLISHED_BY_SIZING',
                        scope='ASSET_SIZING_ONLY_LEGACY_OTHER_RULES_INHERITED')

    return AssetSizedMinimal
