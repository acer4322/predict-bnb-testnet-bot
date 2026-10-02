"""Post-hoc Target benchmark on the already frozen ten-market comparison.

Standard-library, bounded compact reads only. No HFT, model training, database
queries, runtime Target authority or market selection based on Target outcomes.
"""
from pathlib import Path
import collections
import hashlib
import json
import math
import statistics

BASE = Path('data/research/r4_v0/p0_provenance_v1')
OUT = BASE / 'PAIR_CORE_CROSSASSET10_TARGET_ALIGNMENT_SCORE_V1_20260910.json'
EPS = 1e-9  # Numerical zero only; not a trading or selection threshold.


def sign(value):
    if value is None:
        return 'UNKNOWN'
    if not math.isfinite(value):
        raise ValueError('Non-finite economic value')
    return 'WIN' if value > EPS else 'LOSS' if value < -EPS else 'ZERO'


def load(path, sources, cap=1024*1024):
    p = Path(path)
    if p.stat().st_size > cap:
        raise ValueError(f'Unexpected input size: {p}')
    raw = p.read_bytes()
    sources.append(dict(path=p.as_posix(), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    return json.loads(raw.decode('utf-8-sig'))


def main():
    if OUT.exists():
        raise FileExistsError('Preserve immutable result; choose a new version to rerun')
    sources = []
    cohort = load(BASE/'PAIR_CORE_CROSSASSET10_COHORT_V1_20260910.json', sources)
    score = load(BASE/'PAIR_CORE_CROSSASSET10_SCORE_V1_20260910.json', sources)
    assert sources[-1]['sha256'] == 'ea7725f33b180fdea2020c92a0ef386fe82f73568c7552ce1d58698232fe0fd5'
    assert sources[0]['sha256'] == score['cohortSha256']
    assert score['complete'] and len(cohort['rows']) == 10 and len(score['rows']) == 40
    meta = {r['marketId']: r for r in cohort['rows']}
    assert len(meta) == 10
    targets = {}
    p = Path('data/research/market_capsule_v1/source_bundle_50_v1/market_results.jsonl')
    assert p.stat().st_size < 100000
    raw = p.read_bytes()
    sources.append(dict(path=p.as_posix(), bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest()))
    assert sources[-1]['sha256'] == 'b3af5b7629ef317e89691a9ec9eba0fdfa7422d4bba52a3e0f5d24f45a432ef8'
    for r in map(json.loads, raw.decode('utf-8-sig').splitlines()):
        mid = int(r['market_id'])
        if mid not in meta:
            continue
        assert meta[mid]['asset'] == r['asset'] == 'BTC' and mid not in targets
        pnl = r['net_pnl_usdt']
        assert abs(r['payout_usdt'] + r['sell_proceeds_usdt'] - r['buy_notional_usdt'] - pnl) < 1e-6
        targets[mid] = dict(asset='BTC', marketId=mid, winner=r['winner'],
            pnlReported=pnl, buyNotional=r['buy_notional_usdt'], sellProceeds=r['sell_proceeds_usdt'],
            UP=r['up_position_shares']+r['sell_proceeds_usdt']-r['buy_notional_usdt'],
            DOWN=r['down_position_shares']+r['sell_proceeds_usdt']-r['buy_notional_usdt'],
            fills=r['fill_count'], parents=r['parent_count'],
            accountingVersion=r['accounting_version'], originalPnlField='net_pnl_usdt',
            costCaveat='NO_EXPLICIT_FEE: do not rename this verified net profit', source=p.as_posix())
    for i in range(4):
        p = Path(f'data/research/lan_worker_returns/eth-pair-only-large100-s{i}-20260905-v1/result.json')
        shard = load(p, sources, 100000)
        for r in shard['rows']:
            mid = int(r['marketId'])
            if mid not in meta:
                continue
            assert meta[mid]['asset'] == 'ETH' and mid not in targets
            targets[mid] = dict(asset='ETH', marketId=mid, winner=r['winnerPostHocOnly'],
                pnlReported=r['targetPnlPostHocOnly'], buyNotional=r['targetBuyPostHocOnly'],
                accountingVersion='ARCHIVED_TARGET_POSTHOC_SCORE',
                originalPnlField='targetPnlPostHocOnly',
                costCaveat='Explicit fee/receipt/endpoint basis is not saved in this source row',
                source=p.as_posix())
    assert set(targets) == set(meta)
    rows = []
    for mid, cm in meta.items():
        t = targets[mid]
        assert t['pnlReported'] is not None and t['buyNotional'] > 0
        t['sign'] = sign(t['pnlReported'])
        t['returnOnGrossBuy'] = t['pnlReported']/t['buyNotional']
        arms = {}
        for r in score['rows']:
            if r['marketId'] != mid:
                continue
            assert r['asset'] == t['asset'] and r['winnerPosthoc'] == t['winner'] and r['correctness']
            our_sign = sign(r['settledGross'])
            arms[r['arm']] = dict(pnlGross=r['settledGross'], buyNotional=r['cost'],
                returnOnGrossBuy=r['settledGross']/r['cost'] if r['cost'] > 0 else None,
                sign=our_sign, pairCategory='TARGET_'+t['sign']+'__OUR_'+our_sign,
                signMatches=our_sign==t['sign'], UP=r['UP'], DOWN=r['DOWN'],
                nativeReceiptCount=r['receiptAudit'].get('nativeReceipts') if isinstance(r.get('receiptAudit'),dict) else None,
                ownerClockFillEvents=r['fills'], takerExercised=r['takerExercised'])
        assert set(arms) == {'A','C','T','X'}
        rows.append(dict(asset=cm['asset'], marketId=mid, windowStartMs=cm['windowStartMs'],
                         windowEndMs=cm['windowEndMs'], target=t, our=arms))
    summary = {}
    for asset in ('BTC','ETH','COMBINED_DESCRIPTIVE'):
        rr = [r for r in rows if asset=='COMBINED_DESCRIPTIVE' or r['asset']==asset]
        tc = collections.Counter(r['target']['sign'] for r in rr)
        entry = dict(markets=len(rr), targetSigns=dict(tc),
            constantAllLossMatchCount=tc['LOSS'], constantAllWinMatchCount=tc['WIN'],
            targetReportedPnlTotal=sum(r['target']['pnlReported'] for r in rr), arms={})
        for arm in ('A','C','T','X'):
            cells=collections.Counter(r['our'][arm]['pairCategory'] for r in rr)
            ww=cells['TARGET_WIN__OUR_WIN']; ll=cells['TARGET_LOSS__OUR_LOSS']
            entry['arms'][arm]=dict(sameWin=ww,sameLoss=ll,
                targetWinOurLoss=cells['TARGET_WIN__OUR_LOSS'], targetLossOurWin=cells['TARGET_LOSS__OUR_WIN'],
                sameSignCount=sum(r['our'][arm]['signMatches'] for r in rr),
                targetWinCaptureRate=ww/tc['WIN'] if tc['WIN'] else None,
                targetLossMatchRate=ll/tc['LOSS'] if tc['LOSS'] else None,
                otherCategories={k:v for k,v in cells.items() if 'ZERO' in k or 'UNKNOWN' in k},
                ourGrossTotal=sum(r['our'][arm]['pnlGross'] for r in rr))
        summary[asset]=entry
    timeline=load(BASE/'PAIR_CORE_TARGET_MATCHED_BTC5_TIMELINE_AUDIT_V1_20260910.json',sources)
    assert len(timeline['rows'])==5
    terminal_same=0;early_same=0
    for tr in timeline['rows']:
        terminal_same += tr['targetFull']['surplusSide']==tr['ourSnapshots']['X']['surplusSideFinal']
        o=tr['ourSnapshots']['X'];os='UP' if o['UPbefore']>o['DOWNbefore']+EPS else 'DOWN' if o['DOWNbefore']>o['UPbefore']+EPS else 'TIE'
        early_same += tr['targetEventBeforeFence']['surplusSide']==os
    result=dict(version='PAIR_CORE_CROSSASSET10_TARGET_ALIGNMENT_SCORE_V1',
        mode='RETROSPECTIVE_FIXED_COHORT_TARGET_COMPARISON', newHFT=0,training=0,policyChanged=False,
        sourceFiles=sources,rows=rows,summary=summary,
        btcPayoffShape=dict(markets=5,terminalSurplusSameSide=terminal_same,
            earlySurplusSameSideAtInheritedFence=early_same,
            caution='Realized inventory shape, NOT prospective direction prediction or an instruction to reverse trades'),
        milestone='SAME_LOSS_LOCAL_MATCH_ONLY__NO_INCREMENTAL_TARGET_WIN_ALIGNMENT',
        positiveEconomicsAndTargetSimilarityAreSeparate=True,
        ourWinTargetLossMustNotBePunished=True,
        limitations=['All ten are consumed, not independent validation; Target benchmark added after prior scoring.',
            'Each stratum is five markets; BTC and ETH come from different dates.',
            'Target observed trading versus OUR model replay have different action/horizon/queue constraints.',
            'Target BTC fields exclude explicit fees; ETH scoring fee basis unknown; OUR full net cost unresolved.',
            'Low positive Target ETH pnl 0.3721006859 is cost-sensitive; exact gross-label sign is provisional.',
            'Outcome/size-normalized similarity alone does not identify repair mechanism.',
            'No new model, runtime Target feed, delayed supervision authority, HFT or permission changes.'])
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    print(json.dumps(dict(output=OUT.as_posix(),sha256=hashlib.sha256(OUT.read_bytes()).hexdigest(),
        summary=summary,btcPayoffShape=result['btcPayoffShape'],milestone=result['milestone']),ensure_ascii=False))


if __name__=='__main__':
    main()
