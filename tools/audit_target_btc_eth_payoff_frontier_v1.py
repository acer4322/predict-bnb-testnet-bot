from __future__ import annotations

import json, math, sqlite3, statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT = ROOT / 'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_PAYOFF_FRONTIER_V1.json'
EPS = 1e-9


def pct(vals, p):
    a = sorted(v for v in vals if v is not None and math.isfinite(v))
    if not a:
        return None
    if len(a) == 1:
        return a[0]
    x = (len(a)-1)*p
    lo = int(math.floor(x)); hi = int(math.ceil(x))
    if lo == hi: return a[lo]
    w = x-lo
    return a[lo]*(1-w)+a[hi]*w


def stats(vals):
    a = [v for v in vals if v is not None and math.isfinite(v)]
    return {
        'n': len(a),
        'mean': (sum(a)/len(a) if a else None),
        'median': pct(a, .5),
        'p25': pct(a, .25),
        'p75': pct(a, .75),
        'p90': pct(a, .90),
    }


def rate(rows, key):
    return (sum(1 for r in rows if r.get(key))/len(rows)) if rows else None


def summarize(rows):
    primary = [r for r in rows if r['primary']]
    req_cross = [r for r in primary if r['floorRecoveryRequiresCrossing']]
    no_req_cross = [r for r in primary if not r['floorRecoveryRequiresCrossing']]
    over = [r for r in rows if r['crossesBalance']]
    nonover = [r for r in rows if not r['crossesBalance']]
    closer_econ = sum(1 for r in primary if r['floorErrNorm'] < r['gapErrNorm'])
    closer_gap = sum(1 for r in primary if r['gapErrNorm'] < r['floorErrNorm'])
    ties = len(primary)-closer_econ-closer_gap
    cross_req = rate(req_cross, 'crossesBalance')
    cross_no = rate(no_req_cross, 'crossesBalance')
    return {
        'nWeakSideParents': len(rows),
        'crossBalanceRate': rate(rows, 'crossesBalance'),
        'floorImproveRate': rate(rows, 'floorImprove'),
        'postFloorNonnegativeRate': rate(rows, 'postFloorNonnegative'),
        'crossBalanceFloorImproveBestPositiveRate': rate(over, 'floorImproveBestPositive'),
        'nonCrossFloorImproveBestPositiveRate': rate(nonover, 'floorImproveBestPositive'),
        'primary': {
            'n': len(primary),
            'economicIntervalFeasibleByDefinition': True,
            'floorRecoveryRequiresCrossingRate': rate(primary, 'floorRecoveryRequiresCrossing'),
            'observedInsideEconomicIntervalRate': rate(primary, 'insideEconomicInterval'),
            'crossRateWhenFloorRecoveryRequiresCrossing': cross_req,
            'crossRateWhenFloorRecoveryDoesNotRequireCrossing': cross_no,
            'crossRateRiskRatio': (cross_req/cross_no if cross_req is not None and cross_no not in (None,0) else None),
            'gapErrorNorm': stats([r['gapErrNorm'] for r in primary]),
            'floorRecoveryErrorNorm': stats([r['floorErrNorm'] for r in primary]),
            'economicTargetCloserRate': closer_econ/len(primary) if primary else None,
            'gapTargetCloserRate': closer_gap/len(primary) if primary else None,
            'tieRate': ties/len(primary) if primary else None,
            'floorRecoveryToGapRatio': stats([r['xFloor']/r['gap'] for r in primary if r['gap'] > EPS]),
            'observedQtyToGapRatio': stats([r['qty']/r['gap'] for r in primary if r['gap'] > EPS]),
            'observedQtyToFloorRecoveryRatio': stats([r['qty']/r['xFloor'] for r in primary if r['xFloor'] > EPS]),
        },
        'crossBalanceTopology': {
            'n': len(over),
            'postFloor': stats([r['postFloor'] for r in over]),
            'postBest': stats([r['postBest'] for r in over]),
            'floorDelta': stats([r['postFloor']-r['preFloor'] for r in over]),
        },
        'nonCrossTopology': {
            'n': len(nonover),
            'postFloor': stats([r['postFloor'] for r in nonover]),
            'postBest': stats([r['postBest'] for r in nonover]),
            'floorDelta': stats([r['postFloor']-r['preFloor'] for r in nonover]),
        }
    }


def build_rows():
    con = sqlite3.connect(DB)
    cur = con.execute('''SELECT asset, market_id, role, side, first_event_ms, average_price, shares, parent_id
                         FROM target_parent_orders
                         WHERE average_price IS NOT NULL AND shares IS NOT NULL AND shares>0
                         ORDER BY asset, market_id, first_event_ms, parent_id''')
    markets = defaultdict(list)
    for asset, mid, role, side, t, price, qty, pid in cur:
        markets[(asset, int(mid))].append({
            'asset': asset, 'marketId': int(mid), 'role': role, 'side': side,
            't': int(t), 'price': float(price), 'qty': float(qty), 'parentId': pid,
        })
    con.close()

    market_ids = defaultdict(list)
    for asset, mid in markets:
        market_ids[asset].append(mid)
    split_map = {}
    for asset, mids in market_ids.items():
        mids = sorted(set(mids)); n=len(mids)
        for i, mid in enumerate(mids):
            frac=(i+1)/n
            split_map[(asset,mid)] = 'TRAIN60' if frac<=.60 else ('VALID20' if frac<=.80 else 'TEST20')

    rows=[]; blocks=[]
    for (asset, mid), evs in markets.items():
        U=D=C=0.0
        states=[]
        for e in evs:
            side=e['side'].upper(); q=e['price']; x=e['qty']
            preU,preD,preC=U,D,C
            if abs(preU-preD)<=EPS:
                isweak=False; weak=None; strong=None
            elif preU<preD:
                isweak=(side=='UP'); weak=preU; strong=preD
            else:
                isweak=(side=='DOWN'); weak=preD; strong=preU
            preUpP=preU-preC; preDownP=preD-preC
            preFloor=min(preUpP,preDownP); preBest=max(preUpP,preDownP)
            rec=None
            if isweak and 0<q<1:
                gap=abs(preU-preD)
                weakP=weak-preC; strongP=strong-preC
                xfloor=max(0.0,(preC-weak)/(1-q))
                xcap=max(0.0,(strong-preC)/q)
                feasible=xfloor<=xcap+1e-9
                primary=(weakP<0 and strongP>0 and feasible and xfloor>EPS and gap>EPS)
            else:
                gap=xfloor=xcap=None; feasible=primary=False

            if side=='UP': U += x
            elif side=='DOWN': D += x
            C += q*x
            postUpP=U-C; postDownP=D-C; postFloor=min(postUpP,postDownP); postBest=max(postUpP,postDownP)
            state={'preU':preU,'preD':preD,'preC':preC,'preFloor':preFloor,'preBest':preBest,'postU':U,'postD':D,'postC':C,'postFloor':postFloor,'postBest':postBest}
            states.append(state)
            if isweak:
                gapErr=abs(x-gap)/max(gap,EPS)
                floorErr=abs(x-xfloor)/max(xfloor,EPS) if xfloor is not None and xfloor>EPS else None
                rec={**e, **state, 'split':split_map[(asset,mid)], 'gap':gap,'xFloor':xfloor,'xCap':xcap,
                     'economicIntervalFeasible':feasible,'primary':primary,
                     'crossesBalance':x>gap+1e-9,
                     'floorRecoveryRequiresCrossing': bool(xfloor is not None and xfloor>gap+1e-9),
                     'insideEconomicInterval': bool(feasible and x>=xfloor-1e-9 and x<=xcap+1e-9),
                     'gapErrNorm':gapErr,'floorErrNorm':floorErr,
                     'floorImprove':postFloor>preFloor+1e-9,
                     'postFloorNonnegative':postFloor>=-1e-9,
                     'floorImproveBestPositive':postFloor>preFloor+1e-9 and postBest>0}
                rows.append(rec)

        # descriptive 3s same-side blocks; starts at first event of each block, advances to first unconsumed event
        i=0
        while i<len(evs):
            start=evs[i]; st=states[i]; side=start['side'].upper(); t0=start['t']
            j=i; qty=0.0; notional=0.0
            while j<len(evs):
                e=evs[j]
                if e['side'].upper()!=side or e['t']-t0>3000: break
                qty += e['qty']; notional += e['price']*e['qty']; j+=1
            preU,preD,preC=st['preU'],st['preD'],st['preC']
            if abs(preU-preD)>EPS:
                weakSide='UP' if preU<preD else 'DOWN'
                if side==weakSide:
                    weak=preU if side=='UP' else preD; strong=preD if side=='UP' else preU
                    q=start['price']; gap=abs(preU-preD)
                    if 0<q<1:
                        xfloor=max(0.0,(preC-weak)/(1-q)); xcap=max(0.0,(strong-preC)/q)
                        feasible=xfloor<=xcap+1e-9
                        weakP=weak-preC; strongP=strong-preC
                        primary=(weakP<0 and strongP>0 and feasible and xfloor>EPS and gap>EPS)
                        # block post ledger uses actual notional of constituent parents, not first-price proxy
                        bU=preU+(qty if side=='UP' else 0); bD=preD+(qty if side=='DOWN' else 0); bC=preC+notional
                        bFloor=min(bU-bC,bD-bC); bBest=max(bU-bC,bD-bC)
                        blocks.append({
                            'asset':asset,'marketId':mid,'role':start['role'],'split':split_map[(asset,mid)],'side':side,
                            't':t0,'price0':q,'qty':qty,'notional':notional,'nParents':j-i,
                            'preU':preU,'preD':preD,'preC':preC,'gap':gap,'xFloor':xfloor,'xCap':xcap,
                            'preFloor':min(preU-preC,preD-preC),'preBest':max(preU-preC,preD-preC),
                            'postFloor':bFloor,'postBest':bBest,'economicIntervalFeasible':feasible,'primary':primary,
                            'crossesBalance':qty>gap+1e-9,
                            'floorRecoveryRequiresCrossing':xfloor>gap+1e-9,
                            'insideEconomicInterval':feasible and qty>=xfloor-1e-9 and qty<=xcap+1e-9,
                            'gapErrNorm':abs(qty-gap)/max(gap,EPS),
                            'floorErrNorm':abs(qty-xfloor)/max(xfloor,EPS) if xfloor>EPS else None,
                            'floorImprove':bFloor>min(preU-preC,preD-preC)+1e-9,
                            'postFloorNonnegative':bFloor>=-1e-9,
                            'floorImproveBestPositive':bFloor>min(preU-preC,preD-preC)+1e-9 and bBest>0,
                        })
            i=max(j,i+1)
    return rows,blocks


def group_summaries(rows):
    out={}
    for asset in ('BTC','ETH'):
        for role in ('MAKER','TAKER'):
            k=f'{asset}_{role}'
            grp=[r for r in rows if r['asset']==asset and r['role']==role]
            out[k]={'ALL':summarize(grp),'TEST20':summarize([r for r in grp if r['split']=='TEST20'])}
    return out


def main():
    rows,blocks=build_rows()
    result={
        'version':'TARGET_BTC_ETH_PAYOFF_FRONTIER_V1',
        'researchOnly':True,'actionAuthority':False,
        'source':str(DB.relative_to(ROOT)),
        'parentLevel':group_summaries(rows),
        'block3sLevel':group_summaries(blocks),
        'coverage':{'weakSideParents':len(rows),'weakSideBlocks':len(blocks)},
        'interpretationRules':[
            'Crossing absNet does not by itself prove payoff optimization, but a high crossing rate rejects absNet as a hard quantity cap.',
            'Special attention is paid to states where xFloor>gap: reaching non-loss floor mathematically requires crossing the inventory-balance point.',
            'Single-parent and 3s execution-block views are both reported to reduce parent-slicing bias.'
        ]
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    brief={}
    for level in ('parentLevel','block3sLevel'):
        brief[level]={}
        for k,v in result[level].items():
            s=v['TEST20']; p=s['primary']
            brief[level][k]={
                'nWeak':s['nWeakSideParents'],'crossRate':s['crossBalanceRate'],'primaryN':p['n'],
                'xFloorGtGapRate':p['floorRecoveryRequiresCrossingRate'],
                'crossIfXFloorGtGap':p['crossRateWhenFloorRecoveryRequiresCrossing'],
                'crossIfXFloorLeGap':p['crossRateWhenFloorRecoveryDoesNotRequireCrossing'],
                'crossRR':p['crossRateRiskRatio'],
                'medianGapErr':p['gapErrorNorm']['median'],'medianFloorErr':p['floorRecoveryErrorNorm']['median'],
                'econCloserRate':p['economicTargetCloserRate'],'gapCloserRate':p['gapTargetCloserRate'],
                'insideEconInterval':p['observedInsideEconomicIntervalRate']
            }
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':result['coverage'],'test20':brief},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
