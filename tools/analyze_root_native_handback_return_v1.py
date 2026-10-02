"""Bounded saved-fill attribution; fee scenario is not verified full net PnL."""
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
REL='data/research/lan_worker_returns/root-native-composite-handback3-20260910-v1/COMPACT.json'
OUT='data/research/r4_v0/p0_provenance_v1/ROOT_NATIVE_HANDBACK_FILL_ATTRIBUTION_COMPACT_V1_20260910.json'


def fee_scenario(price,qty,coefficient=.02):
    return coefficient*min(price,1-price)*qty


def main():
    p=ROOT/REL; assert p.stat().st_size<=512*1024
    sha=hashlib.sha256(p.read_bytes()).hexdigest()
    assert sha=='f2e28782d271c92c44681e24c494e5ff4ae31c91eb2f336beb9743ae88b6d197'
    data=json.loads(p.read_text()); out=[]
    for s,p in zip(data['rows'][::2],data['rows'][1::2]):
        if not s['witness']: continue
        w=s['witness']; pk=p['selectedPassive']['key']; t=w['t']
        sf=[f for f in s['fillHistory'] if f[0]>=t]; pf=[f for f in p['fillHistory'] if f[0]>=t]
        # This attribution is deliberately limited to the observed one-fill suffix.
        assert len(sf)==len(pf)==1 and sf[0][1]==pf[0][1]
        assert [f for f in s['fillHistory'] if f[0]<t]==[f for f in p['fillHistory'] if f[0]<t]
        akey=s['selectedActive']['key']
        later=[k for k,v in p['activeMeta'].items() if v['submitAt']>=t]
        assert len(later)==1
        bkey=later[0]; a=sf[0]; b=pf[0]
        for row,key,f in [(s,akey,a),(p,bkey,b)]:
            assert abs(row['orders'][key]['cum']-f[2])<=1e-8
            assert row['serviceLedger'][key]['sourceKey']==w['pendingCoreEvidence']['sourceKey']
        dc=b[2]*b[3]-a[2]*a[3]; dq=b[2]-a[2]
        expected={side:(dq if side==a[1] else 0)-dc for side in ['UP','DOWN']}
        assert all(abs(p[side]-s[side]-expected[side])<1e-8 for side in expected)
        df=fee_scenario(b[3],b[2])-fee_scenario(a[3],a[2])
        out.append(dict(marketId=s['marketId'],selectedPassiveFilledQty=p['orders'][pk]['cum'],
            nativeActiveFill=a,handbackLaterActiveFill=b,
            responsibilitySource=w['pendingCoreEvidence']['sourceKey'],
            extraFilledQty=dq,extraAcquisitionCash=dc,terminalGrossDelta=expected,
            serviceSpendS=s['serviceLedger'][akey]['spent'],serviceSpendP=p['serviceLedger'][bkey]['spent'],
            absNetIntegralDelta=p['netIntegralShareMs']-s['netIntegralShareMs'],
            takerFeeScenario=dict(coefficient=.02,feeS=fee_scenario(a[3],a[2]),
                feeP=fee_scenario(b[3],b[2]),deltaFee=df,
                deltaEndpointsAfterThisFeeOnly={side:d-df for side,d in expected.items()},
                status='CONDITIONAL_ON_HISTORICAL_FEE_FORMULA_NOT_VERIFIED_BTC_COHORT_FULL_NET',
                excludes='rebate allocation, fee rounding, referral status, funding/deployment costs'),
            fixedDelayRuleJustified=False,trainingReady=False))
    result=dict(source=REL,sourceSha256=sha,additionalBE=0,attribution=out,
        verdict='ZERO_FILL_SELECTED_CARRIER_LATER_NATIVE_ACTIVE_MEDIATES_GROSS_RESPONSE',
        economicEdge='NOT_IDENTIFIED',promotion=False)
    (ROOT/OUT).write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__': main()
