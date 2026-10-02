from tools.run_root_family_support_stagea12_v1 import support_verdict
from tools.run_root_native_composite_handback_v1 import make_fork


def rows(ids):
    return [dict(ordinal=i,exercised=i in ids,publicJoined=True,marketActivityPass=True,
        delta={'UP':.1,'DOWN':-.1},S={'fills':3,'qty':3},P={'fills':3,'qty':3}) for i in range(12)]


def test_single_witness_and_one_half_cannot_pass():
    assert support_verdict(rows([2]))=='INSUFFICIENT_INDEPENDENT_SOURCE_SUPPORT'
    assert support_verdict(rows([0,1,2,3]))=='INSUFFICIENT_INDEPENDENT_SOURCE_SUPPORT'


def test_only_source_not_profitability():
    assert support_verdict(rows([0,1,6,7]))=='CROSS_MARKET_SOURCE_SUPPORTED_ONLY'


def test_zero_response_and_collapse_stops():
    r=rows([0,1,6,7])
    for x in r: x['delta']={'UP':0,'DOWN':0}
    assert support_verdict(r)=='RESPONSE_DEGENERATE'
    r=rows([0,1,6,7]); r[0]['marketActivityPass']=False
    assert support_verdict(r)=='SOURCE_ACTIVITY_COLLAPSE'


def test_factory_preserves_submit_hook_and_native_bypass():
    class Base:
        def _submit_active(self,*a): return 'native'
    cls=make_fork(Base,None)
    assert '_submit_role_v8' in cls.__dict__
    sim=object.__new__(cls); sim._lab={'branch':'N'}
    assert sim._submit_active(1,'UP','SATELLITE_REPAIR',2,0,{})=='native'
