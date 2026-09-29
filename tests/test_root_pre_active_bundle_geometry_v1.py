import pytest
from tools.analyze_root_pre_active_bundle_geometry_v1 import minimum_notional_geometry


def test_observed_composite_not_no_option():
    g = minimum_notional_geometry(.60, .49, 1/.51, 1)
    assert g['activeOverflowQty'] == 0
    assert g['activeUnpaidQty'] == pytest.approx(.2941176470588234)
    assert g['passiveOverflowQty'] == pytest.approx(.08003201280512218)
    assert g['sameQtyPassiveBelowMinimum']


@pytest.mark.parametrize('a,p,m', [(.6,.59,1), (.8,.3,5), (.25,.24,.5)])
def test_minimum_notional_identity_not_market_threshold(a,p,m):
    g = minimum_notional_geometry(a,p,100,m)
    assert g['activeQty']*p < m
    assert g['passiveQty'] > g['activeQty']
    assert g['passiveQty']*p == pytest.approx(m)


def test_invalid_prices_rejected():
    with pytest.raises(ValueError): minimum_notional_geometry(.4,.5,2,1)
    with pytest.raises(ValueError): minimum_notional_geometry(float('nan'),.3,2,1)
