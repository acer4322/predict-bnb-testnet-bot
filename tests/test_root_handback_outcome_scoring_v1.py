import pytest
from tools.score_root_native_handback_outcomes_v1 import classify_fills,cost_interval,distribution


def test_no_wins_distribution_includes_all_losers():
    r=distribution([-.04,-.04,-.02])
    assert r['aggregate']==pytest.approx(-.10)
    assert r['chronologicalDrawdown']==pytest.approx(.10)
    assert r['leaveOneBestOut']==pytest.approx(-.08)
    assert r['profitFactor']==0 and r['positiveMarkets']==0


def test_zero_fee_not_assumed_to_be_full_cost():
    r=cost_interval(-.04,[dict(price=.6,qty=1/.6,route='TAKER_ASSUMED_ACTIVE'),
                           dict(price=.51,qty=1/.51,route='MAKER')],.02)
    assert r['lower']<r['upper']<0
    assert r['undiscountedTakerFee']==pytest.approx(.0133333333333)
    assert r['liquidityUnresolvedEnvelope'][1]>r['upper']


def test_fill_owner_and_cash():
    row=dict(orders={'x':dict(cum=2,side='UP',placed=1,price=.5)},
        fillHistory=[[2,'UP',2,.5]],activeMeta={},cost=1)
    assert classify_fills(row)[0]['route']=='MAKER'
    row['orders']['y']=row['orders']['x'].copy()
    row['activeMeta']={'y':{}}
    with pytest.raises(ValueError,match='Ambiguous'): classify_fills(row)


def test_same_route_owner_ambiguity_is_not_invented_identity():
    row=dict(orders={k:dict(cum=2,side='UP',placed=1,price=.5) for k in ['a','b']},
        fillHistory=[[2,'UP',2,.5],[3,'UP',2,.5]],activeMeta={},cost=2)
    fills=classify_fills(row)
    assert all(f['route']=='MAKER' and f['owner'] is None for f in fills)
