"""0-BE algebra/source audit. No hypothetical fills are counted as execution."""
import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = 'data/research/lan_worker_returns/root-pre-active-option-audit3-20260910-v1/COMPACT.json'
HISTORY = 'data/research/r4_v0/p0_provenance_v1/MS4_R246_FOUR_MARKET_MECHANISM_ABLATION_RESULT_20260906.json'
OUT = 'data/research/r4_v0/p0_provenance_v1/ROOT_PRE_ACTIVE_COMPLETE_BUNDLE_GEOMETRY_COMPACT_V1_20260910.json'


def minimum_notional_geometry(active_price, passive_price, debt, minimum_notional):
    """Conditional identity under supplied venue mechanics, not a trading rule."""
    if not all(math.isfinite(x) for x in (active_price, passive_price, debt, minimum_notional)):
        raise ValueError('non-finite')
    if not (0 < passive_price < active_price < 1 and debt >= 0 and minimum_notional > 0):
        raise ValueError('requires cheaper passive price and positive venue minimum')
    aq = minimum_notional/active_price
    pq = minimum_notional/passive_price
    return dict(activeQty=aq, passiveQty=pq,
                sameActiveQtyPassiveNotional=aq*passive_price,
                sameQtyPassiveBelowMinimum=aq*passive_price < minimum_notional,
                activeRepairQty=min(debt, aq), activeOverflowQty=max(0., aq-debt),
                passiveRepairQty=min(debt, pq), passiveOverflowQty=max(0., pq-debt),
                activeUnpaidQty=max(0., debt-aq), passiveUnpaidQty=max(0., debt-pq))


def main():
    source = ROOT/SOURCE; history = ROOT/HISTORY
    assert source.stat().st_size < 512*1024 and history.stat().st_size < 1024**2
    assert hashlib.sha256(source.read_bytes()).hexdigest() == '02863c71a1a4bf3e431f2ca019622e4ee29f63dff542e9e350c89dff4541cd86'
    assert hashlib.sha256(history.read_bytes()).hexdigest() == 'b7ea8dda4b58da2b513eaf71317f4d8446e31ff260a12d893ec6e0326cddbdbf'
    data = json.loads(source.read_text()); old = json.loads(history.read_text())
    rows = []
    for r in data['rows']:
        for w in r['witnesses']:
            if not w['activeAccepted'] or not w['passive'].get('transportReached'): continue
            a = w['activeReceipt']; p = w['passive']['candidate']; sp = p['split']
            g = minimum_notional_geometry(a['activePrice'], p['price'], a['debt'], 1.)
            assert abs(g['activeQty']-a['qty']) <= 1e-9
            assert abs(g['passiveQty']-p['qty']) <= 1e-9
            assert abs(g['passiveRepairQty']-sp['repairQty']) <= 1e-9
            assert abs(g['passiveOverflowQty']-sp['overflowQty']) <= 1e-9
            rows.append(dict(marketId=r['marketId'], t=w['t'], geometry=g,
                activeAccepted=True, passiveTransportProposalOnly=True,
                passiveExchangeAcceptance='UNTESTED',
                activeNominalSpend=a['activePrice']*a['qty'],
                passiveNominalSpend=p['price']*p['qty'],
                passiveOverflowRisk=sp['overflowRisk'],
                publicLagMs=w['t']-w['public']['availableMs'] if w['public'] else None,
                physicalDifference='PURE_REPAIR_VS_COMPOSITE_REPAIR_PLUS_EXPAND',
                trainingReady=False))
    historical = [dict(marketId=e['marketId'], prefixParity=e['commonOrigin']['prefixParity'],
        activeVsControl=e['delta']['ACTIVE_vs_R240'],
        quarantineVsActive=e['delta']['QUARANTINE_vs_ACTIVE'],
        activeFills=e['terminal']['ACTIVE']['fills'],
        quarantineFills=e['terminal']['ACTIVE_CREDIT_QUARANTINE']['fills']) for e in old['evidence']]
    result = dict(version='ROOT_PRE_ACTIVE_COMPLETE_BUNDLE_GEOMETRY_V1',
        originalVerdict=data['verdict'], semanticUpdate='COMPOSITE_PROPOSAL_EXISTS_PURE_ROUTE_MATCH_ABSENT',
        source=SOURCE, sourceSha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        historicalSource=HISTORY, historicalSha256=hashlib.sha256(history.read_bytes()).hexdigest(),
        observations=rows, historicalReuse=historical,
        evidenceClass='SOURCE_AUDIT_AND_CONDITIONAL_ACCOUNTING_IDENTITY_NOT_FILL_SIMULATION',
        additionalBE=0, modelsTrained=0, freshUsed=0, promotion=False)
    (ROOT/OUT).write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result))


if __name__ == '__main__':
    main()
