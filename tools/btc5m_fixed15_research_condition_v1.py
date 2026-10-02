"""User-selected historical-market Passive ticket; not a venue-wide rule.

Preserve shared validation for Active and non-BTC instruments. For this isolated
BTC research condition, replace the old strategy minimum18 with exactly15 on
NEW Passive orders only. Existing partial receipts/leaves never call this gate.
"""
from decimal import Decimal

TICKET = 15.
CONTRACT = 'BTC5M_2026085_PASSIVE_FIXED15_ACTIVE_UNCHANGED_RESEARCH_ONLY'


def make_validator(base):
    def validate(asset, route, price, qty, *, quantity_step=None):
        if asset != 'BTC' or route != 'PASSIVE':
            return base(asset, route, price, qty, quantity_step=quantity_step)
        # The existing Active branch performs the same finite/positive, price,
        # asset and quantity-grid checks without the old Passive research minima.
        base(asset, 'ACTIVE', price, qty, quantity_step=quantity_step)
        if Decimal(str(qty)) != Decimal('15'):
            raise ValueError('isolated BTC Passive NEW requires the full15 ticket')
        if Decimal(str(qty)) * Decimal(str(price)) < Decimal('1'):
            raise ValueError('below passive minimum quoted notional')
    return validate


def self_test(base):
    validate = make_validator(base)
    validate('BTC', 'PASSIVE', .07, 15., quantity_step=.01)
    for price, qty in ((.06, 15.), (.01, 100.), (.5, 14.99), (.5, 30.), (.5, .53)):
        try:
            validate('BTC', 'PASSIVE', price, qty, quantity_step=.01)
        except ValueError:
            pass
        else:
            raise AssertionError((price, qty))
    # Active sizes are passed through, including fractional and low-price cases.
    for price, qty in ((.5, .53), (.02, 59.61), (.5, 30.)):
        assert validate('BTC', 'ACTIVE', price, qty, quantity_step=.01) == base(
            'BTC', 'ACTIVE', price, qty, quantity_step=.01)
    validate('ETH', 'PASSIVE', .1, 12., quantity_step=.01)
    try:
        base('BTC', 'PASSIVE', .5, 15., quantity_step=.01)
    except ValueError:
        pass
    else:
        raise AssertionError('shared historical18 rule unexpectedly changed')


def instrument(source, replace):
    marker = 'theta=helper.theta_for_mask(initial_parameters(qref),10)'
    source = replace(source, marker, marker + ';theta=list(theta);theta[7]=math.log(15.)')
    marker = '  from tools.pair_core_asset_route_sizing_v2 import validate_size'
    return replace(source, marker, marker + '\n  validate_size=_ticket_validator_factory(validate_size)')
