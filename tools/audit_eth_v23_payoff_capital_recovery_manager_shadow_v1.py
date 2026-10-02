from __future__ import annotations

import json, math
from collections import defaultdict
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/research/r4_v0/p0_provenance_v1'
OFFLINE = BASE / 'ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json'
OUT = BASE / 'ETH_V23_PAYOFF_CAPITAL_RECOVERY_MANAGER_SHADOW_V1.json'
RAW_DIR = ROOT / 'data/research/lan_worker_returns'
RAW_GLOBS = [
    'eth-v23-unseen20-c*/result.json',
    'eth-v23-confirm20-c*-v2/result.json',
]
EPS = 1e-9


def pct(vals, p):
    a = sorted(float(v) for v in vals if v is not None and math.isfinite(float(v)))
    if not a:
        return None
    if len(a) == 1:
        return a[0]
    x = (len(a)-1)*p
    lo, hi = int(math.floor(x)), int(math.ceil(x))
    if lo == hi:
        return a[lo]
    w = x-lo
    return a[lo]*(1-w)+a[hi]*w


def stats(vals):
    a = [float(v) for v in vals if v is not None and math.isfinite(float(v))]
    return {
        'n': len(a),
        'mean': sum(a)/len(a) if a else None,
        'median': pct(a, .5),
        'p25': pct(a, .25),
        'p75': pct(a, .75),
        'p90': pct(a, .90),
        'min': min(a) if a else None,
        'max': max(a) if a else None,
    }


def rate(rows, key):
    return sum(bool(r.get(key)) for r in rows)/len(rows) if rows else None


def reconstruct(fill_trace, t, inclusive=True):
    U=D=C=0.0
    used=[]
    for f in sorted(fill_trace, key=lambda x: (int(x['t']), str(x.get('side')))):
        ft=int(f['t'])
        if ft < t or (inclusive and ft == t):
            q=float(f['qty']); p=float(f['price']); s=str(f['side']).upper()
            if s=='UP': U += q
            elif s=='DOWN': D += q
            else: continue
            C += q*p
            used.append(f)
    return {'up':U,'down':D,'cash':C,'pnlUp':U-C,'pnlDown':D-C,
            'floor':min(U-C,D-C),'best':max(U-C,D-C),'absNet':abs(U-D),'fillsUsed':len(used)}


def shadow(state, repair_side, q):
    if q is None or not (0 < float(q) < 1):
        return None
    q=float(q)
    weak = state['pnlUp'] if repair_side=='UP' else state['pnlDown']
    strong = state['pnlDown'] if repair_side=='UP' else state['pnlUp']
    gap = state['absNet']
    xfloor=max(0.0, -weak/(1-q))
    xcap=max(0.0, strong/q)
    # after buying exactly the current payoff/share gap, both outcome payoffs are equal
    floor_bal = weak + (1-q)*gap
    floor_bal2 = strong - q*gap
    if abs(floor_bal-floor_bal2) > 1e-6:
        raise RuntimeError(f'payoff balance identity violated: {floor_bal} vs {floor_bal2}')
    feasible = (xfloor <= gap+1e-9 and xfloor <= xcap+1e-9 and floor_bal >= -1e-9)
    safe_excess = max(0.0, floor_bal/q) if floor_bal > 0 else 0.0
    return {
        'q':q,
        'weakPayoff':weak,
        'strongPayoff':strong,
        'floorDeficitDollars':max(0.0,-weak),
        'floorRecoveryQty':xfloor,
        'balanceQty':gap,
        'gapMinusFloorRecoveryQty':gap-xfloor,
        'floorRecoveryToGapRatio':xfloor/gap if gap>EPS else None,
        'strongNonLossCapQty':xcap,
        'floorAtBalance':floor_bal,
        'nonLossFeasible':feasible,
        'safeExcessCapAfterBalance':safe_excess,
        'safeExcessToGapRatio':safe_excess/gap if gap>EPS else None,
        'maxTotalQtyKeepingNonLoss':gap+safe_excess if feasible else None,
    }


def summarize(rows):
    def world(name):
        avail=[r[name] for r in rows if r.get(name)]
        return {
            'n':len(avail),
            'nonLossFeasibleRate': sum(x['nonLossFeasible'] for x in avail)/len(avail) if avail else None,
            'floorRecoveryToGapRatio': stats([x['floorRecoveryToGapRatio'] for x in avail]),
            'gapMinusFloorRecoveryQty': stats([x['gapMinusFloorRecoveryQty'] for x in avail]),
            'floorAtBalance': stats([x['floorAtBalance'] for x in avail]),
            'safeExcessCapAfterBalance': stats([x['safeExcessCapAfterBalance'] for x in avail]),
            'safeExcessToGapRatio': stats([x['safeExcessToGapRatio'] for x in avail]),
            'oldGapExceedsMinRecoveryRate': sum(x['balanceQty']>x['floorRecoveryQty']+1e-9 for x in avail)/len(avail) if avail else None,
        }
    return {
        'n':len(rows),
        'firstFillAbsNet':stats([r['stateAfterFirstFill']['absNet'] for r in rows]),
        'firstFillFloor':stats([r['stateAfterFirstFill']['floor'] for r in rows]),
        'firstFillBest':stats([r['stateAfterFirstFill']['best'] for r in rows]),
        'preFirstFillFloor':stats([r['stateBeforeFirstFill']['floor'] for r in rows]),
        'floorDeficitDollars':stats([max(0,-r['repairWeakPayoff']) for r in rows]),
        'economicCeiling':world('atEconomicCeiling'),
        'legalPassive':world('atLegalPassive'),
        'bestAsk':world('atBestAsk'),
        'firstRepairSubmit':world('atFirstRepairSubmit'),
        'marketableImmediateNonLossImpossibleRate':1-world('atBestAsk')['nonLossFeasibleRate'] if world('atBestAsk')['nonLossFeasibleRate'] is not None else None,
        'hasPositiveSafeSurplusBudgetAtCeilingRate':sum(r['atEconomicCeiling']['safeExcessCapAfterBalance']>1e-9 for r in rows)/len(rows) if rows else None,
        'trajectory':{
            'cyclesWithRepairSubmitWithin30s':sum(bool(r['repairSubmitTrajectory30s']) for r in rows),
            'anyNonLossInfeasibleSubmitRate':sum(any(not x['shadow']['nonLossFeasible'] for x in r['repairSubmitTrajectory30s']) for r in rows)/len(rows) if rows else None,
            'firstToInfeasibleSubmitMs':stats([r['firstToInfeasibleSubmitMs'] for r in rows]),
            'maxSubmittedPrice30s':stats([max((x['price'] for x in r['repairSubmitTrajectory30s']),default=None) for r in rows]),
        }
    }


def main():
    offline=json.loads(OFFLINE.read_text(encoding='utf-8'))
    cycles=offline['rows']
    raw_by_mid={}
    sources=[]
    for pat in RAW_GLOBS:
        for p in sorted(RAW_DIR.glob(pat)):
            d=json.loads(p.read_text(encoding='utf-8')); sources.append(str(p.relative_to(ROOT)))
            for row in d.get('rows',[]):
                mid=int(row['marketId'])
                raw_by_mid[mid]=row['V23']['causal']
    # next cycle first-fill boundary by market
    bymid=defaultdict(list)
    for c in cycles: bymid[int(c['marketId'])].append(c)
    for xs in bymid.values(): xs.sort(key=lambda c:int(c['firstFillAt']))
    next_fill={}
    for mid,xs in bymid.items():
        for i,c in enumerate(xs): next_fill[(mid,int(c['firstFillAt']))]=int(xs[i+1]['firstFillAt']) if i+1<len(xs) else None

    rows=[]; audit={'cyclesExpected':len(cycles),'cyclesWithRawTrace':0,'matchedFirstFillExact':0,'absNetIdentityPass':0,'repairSideIsLowerPayoff':0,'firstRepairSubmitFound':0}
    for c in cycles:
        mid=int(c['marketId']); t=int(c['firstFillAt']); causal=raw_by_mid.get(mid)
        if not causal:
            rows.append({'marketId':mid,'cycleIndex':c['cycleIndex'],'error':'missing raw V23 trace'}); continue
        audit['cyclesWithRawTrace']+=1
        ft=causal.get('fillTrace',[]); st=causal.get('submitTrace',[])
        before=reconstruct(ft,t,inclusive=False); after=reconstruct(ft,t,inclusive=True)
        exact=[f for f in ft if int(f['t'])==t and str(f['side']).upper()==str(c['firstSide']).upper() and abs(float(f['price'])-float(c['firstPrice']))<1e-9]
        if exact:audit['matchedFirstFillExact']+=1
        if abs(after['absNet']-abs(after['pnlUp']-after['pnlDown']))<1e-8:audit['absNetIdentityPass']+=1
        repair_side='UP' if after['pnlUp']<after['pnlDown'] else ('DOWN' if after['pnlDown']<after['pnlUp'] else str(c['oppSide']).upper())
        if repair_side==str(c['oppSide']).upper():audit['repairSideIsLowerPayoff']+=1
        nextt=next_fill[(mid,t)]
        end30=t+30000
        if nextt is not None:end30=min(end30,nextt)
        repair_subs=[s for s in st if int(s['t'])>=t and int(s['t'])<=end30 and str(s.get('lane'))=='REPAIR' and str(s['side']).upper()==repair_side]
        repair_subs.sort(key=lambda s:int(s['t']))
        first_sub=repair_subs[0] if repair_subs else None
        if first_sub:audit['firstRepairSubmitFound']+=1
        worlds={
            'atEconomicCeiling':shadow(after,repair_side,c.get('economicCeiling')),
            'atLegalPassive':shadow(after,repair_side,c.get('firstLegalPassivePrice')),
            'atBestAsk':shadow(after,repair_side,c.get('firstBestAsk')),
            'atFirstRepairSubmit':shadow(after,repair_side,first_sub.get('price') if first_sub else None),
        }
        traj=[]; first_bad=None
        for s in repair_subs:
            sh=shadow(after,repair_side,float(s['price']))
            traj.append({'t':int(s['t']),'lagMs':int(s['t'])-t,'price':float(s['price']),'qty':float(s['qty']),'parentId':s.get('parentId'),'shadow':sh})
            if first_bad is None and sh and not sh['nonLossFeasible']: first_bad=int(s['t'])-t
        weakp=after['pnlUp'] if repair_side=='UP' else after['pnlDown']
        strongp=after['pnlDown'] if repair_side=='UP' else after['pnlUp']
        rows.append({
            'marketId':mid,'cycleIndex':int(c['cycleIndex']),'firstFillAt':t,'firstSide':c['firstSide'],'firstPrice':c['firstPrice'],'repairSide':repair_side,
            'completedWithin30s':bool(c['completedWithin30sByTrace']),'eventuallyCompleted':bool(c['eventuallyCompletedByTrace']),
            'packageRepairFillLagMs':c.get('packageRepairFillLagMs'),'firstLegalBehindTicks':c.get('firstLegalBehindTicks'),'firstCeilingBehindTicks':c.get('firstCeilingBehindTicks'),
            'stateBeforeFirstFill':before,'stateAfterFirstFill':after,'repairWeakPayoff':weakp,'repairStrongPayoff':strongp,
            'oldAbsNetResponsibilityQty':after['absNet'],
            **worlds,
            'firstRepairSubmit':first_sub,
            'repairSubmitTrajectory30s':traj,
            'firstToInfeasibleSubmitMs':first_bad,
        })
    good=[r for r in rows if 'error' not in r]
    completed=[r for r in good if r['completedWithin30s']]
    failed=[r for r in good if not r['completedWithin30s']]
    # similarity/heterogeneity descriptors
    deficit_per_gap=[max(0,-r['repairWeakPayoff'])/r['oldAbsNetResponsibilityQty'] for r in good if r['oldAbsNetResponsibilityQty']>EPS]
    result={
        'version':'ETH_V23_PAYOFF_CAPITAL_RECOVERY_MANAGER_SHADOW_V1','researchOnly':True,'actionAuthority':False,
        'sources':sources,'coverage':{'cycles':len(good),'completedWithin30s':len(completed),'failedWithin30s':len(failed)},'audit':audit,
        'all':summarize(good),'completed':summarize(completed),'failed':summarize(failed),
        'responsibilityHeterogeneity':{
            'floorDeficitPerAbsNetShare':stats(deficit_per_gap),
            'note':'Even at equal/similar share gap, dollar deficit depends on cumulative cash/payoff state; absNet alone does not identify capital-recovery requirement.'
        },
        'rows':rows,
        'interpretationBoundary':[
            'This shadow does not authorize any quantity or Taker/Maker action.',
            'Economic-ceiling quantities describe payoff geometry; actual fillability remains an independent execution problem.',
            'Safe excess cap is only a budget envelope. A separate strict-past directional-value authority is required before intentionally crossing balance.'
        ]
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    brief={k:{
        'n':result[k]['n'],
        'gapMed':result[k]['firstFillAbsNet']['median'],'floorMed':result[k]['firstFillFloor']['median'],'deficitMed':result[k]['floorDeficitDollars']['median'],
        'ceilingRecoveryGapRatioMed':result[k]['economicCeiling']['floorRecoveryToGapRatio']['median'],
        'ceilingGapMinusRecoveryMed':result[k]['economicCeiling']['gapMinusFloorRecoveryQty']['median'],
        'ceilingSafeExcessMed':result[k]['economicCeiling']['safeExcessCapAfterBalance']['median'],
        'ceilingSafeExcessGapRatioMed':result[k]['economicCeiling']['safeExcessToGapRatio']['median'],
        'bestAskNonLossFeasibleRate':result[k]['bestAsk']['nonLossFeasibleRate'],
        'bestAskFloorAtBalanceMed':result[k]['bestAsk']['floorAtBalance']['median'],
    } for k in ('all','completed','failed')}
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'audit':audit,'coverage':result['coverage'],'brief':brief},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
