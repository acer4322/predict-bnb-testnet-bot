from __future__ import annotations
import json, math
from pathlib import Path
from collections import defaultdict

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'data/research/r4_v0/p0_provenance_v1'
OFF=BASE/'ETH_V23_FIRSTFILL_FRONTIER_OFFLINE_V1.json'
OUT=BASE/'ETH_V23_PAYOFF_MINIMUM_REPAIR_COUNTERFACTUAL_V1.json'
RAW=ROOT/'data/research/lan_worker_returns'
EPS=1e-9

def pct(vals,p):
    a=sorted(float(v) for v in vals if v is not None and math.isfinite(float(v)))
    if not a:return None
    if len(a)==1:return a[0]
    x=(len(a)-1)*p;lo=int(math.floor(x));hi=int(math.ceil(x));w=x-lo
    return a[lo] if lo==hi else a[lo]*(1-w)+a[hi]*w

def stats(vals):
    a=[float(v) for v in vals if v is not None and math.isfinite(float(v))]
    return {'n':len(a),'mean':sum(a)/len(a) if a else None,'median':pct(a,.5),'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9),'min':min(a) if a else None,'max':max(a) if a else None}

def reconstruct(trace,t,inclusive=False):
    U=D=C=0.0
    for f in sorted(trace,key=lambda x:(int(x['t']),str(x['side']))):
        ft=int(f['t'])
        if ft<t or (inclusive and ft==t):
            q=float(f['qty']);p=float(f['price']);s=str(f['side']).upper()
            if s=='UP':U+=q
            elif s=='DOWN':D+=q
            else:continue
            C+=q*p
    return make_state(U,D,C)

def make_state(U,D,C):
    pu=U-C;pd=D-C
    return {'up':U,'down':D,'cash':C,'pnlUp':pu,'pnlDown':pd,'floor':min(pu,pd),'best':max(pu,pd),'absNet':abs(U-D)}

def apply(st,side,qty,price):
    U,D,C=st['up'],st['down'],st['cash']
    if side=='UP':U+=qty
    else:D+=qty
    C+=qty*price
    return make_state(U,D,C)

def main():
    off=json.loads(OFF.read_text(encoding='utf-8'))
    cycles=[r for r in off['rows'] if r.get('completedWithin30sByTrace') and r.get('packageRepairFillAt') is not None]
    raw_by_mid={};sources=[]
    for pat in ('eth-v23-unseen20-c*/result.json','eth-v23-confirm20-c*-v2/result.json'):
        for p in sorted(RAW.glob(pat)):
            d=json.loads(p.read_text(encoding='utf-8'));sources.append(str(p.relative_to(ROOT)))
            for r in d.get('rows',[]):raw_by_mid[int(r['marketId'])]=r['V23']['causal']
    rows=[];audit={'expectedCompletedCycles':len(cycles),'rawFound':0,'exactRepairFillMatched':0,'repairSideIsWeak':0,'xFloorNotAboveActualQty':0}
    for c in cycles:
        mid=int(c['marketId']);t=int(c['packageRepairFillAt']);side=str(c['oppSide']).upper();price=float(c['packageRepairFillPrice']);causal=raw_by_mid.get(mid)
        if not causal:
            rows.append({'marketId':mid,'cycleIndex':c['cycleIndex'],'error':'missing raw'});continue
        audit['rawFound']+=1;trace=causal.get('fillTrace',[]);pre=reconstruct(trace,t,False)
        exact=[f for f in trace if int(f['t'])==t and str(f['side']).upper()==side and abs(float(f['price'])-price)<1e-8]
        actual_qty=sum(float(f['qty']) for f in exact)
        if actual_qty>EPS:audit['exactRepairFillMatched']+=1
        weak=pre['pnlUp'] if side=='UP' else pre['pnlDown'];strong=pre['pnlDown'] if side=='UP' else pre['pnlUp']
        if weak<=strong+1e-8:audit['repairSideIsWeak']+=1
        xfloor=max(0.0,-weak/(1-price)) if 0<price<1 else None
        if xfloor is not None and xfloor<=actual_qty+1e-8:audit['xFloorNotAboveActualQty']+=1
        xcf=min(actual_qty,xfloor) if xfloor is not None else actual_qty
        actual=apply(pre,side,actual_qty,price);cf=apply(pre,side,xcf,price)
        gap=pre['absNet']; saved=actual_qty-xcf
        rows.append({
            'marketId':mid,'cycleIndex':int(c['cycleIndex']),'repairFillAt':t,'repairSide':side,'price':price,
            'pre':pre,'preWeakPayoff':weak,'preStrongPayoff':strong,'preGap':gap,
            'actualQty':actual_qty,'actualQtyToGap':actual_qty/gap if gap>EPS else None,
            'payoffMinimumQty':xcf,'rawXFloor':xfloor,'payoffMinimumQtyToGap':xcf/gap if gap>EPS else None,
            'savedQty':saved,'savedQtyFractionOfActual':saved/actual_qty if actual_qty>EPS else None,'savedNotional':saved*price,
            'actualPost':actual,'payoffMinimumPost':cf,
            'bestPayoffRetainedVsActual':cf['best']-actual['best'],
            'floorReserveForegoneVsActual':actual['floor']-cf['floor'],
            'payoffMinimumSafeBase':cf['floor']>=-1e-8 and cf['best']>1e-8,
            'payoffMinimumLeavesAsymmetry':cf['absNet']>1e-8,
            'actualReachesOrCrossesBalance':actual_qty>=gap-1e-8,
            'actualCrossesBalance':actual_qty>gap+1e-8,
        })
    good=[r for r in rows if 'error' not in r]
    result={
        'version':'ETH_V23_PAYOFF_MINIMUM_REPAIR_COUNTERFACTUAL_V1','researchOnly':True,'actionAuthority':False,
        'executionAssumption':'local quantity monotonicity at an already-observed fill event; no later replay assumed',
        'coverage':{'cycles':len(good)},'audit':audit,
        'summary':{
            'actualQty':stats([r['actualQty'] for r in good]),
            'preGap':stats([r['preGap'] for r in good]),
            'actualQtyToGap':stats([r['actualQtyToGap'] for r in good]),
            'payoffMinimumQty':stats([r['payoffMinimumQty'] for r in good]),
            'payoffMinimumQtyToGap':stats([r['payoffMinimumQtyToGap'] for r in good]),
            'savedQty':stats([r['savedQty'] for r in good]),
            'savedQtyFractionOfActual':stats([r['savedQtyFractionOfActual'] for r in good]),
            'savedNotional':stats([r['savedNotional'] for r in good]),
            'actualPostFloor':stats([r['actualPost']['floor'] for r in good]),
            'actualPostBest':stats([r['actualPost']['best'] for r in good]),
            'payoffMinimumPostFloor':stats([r['payoffMinimumPost']['floor'] for r in good]),
            'payoffMinimumPostBest':stats([r['payoffMinimumPost']['best'] for r in good]),
            'bestPayoffRetainedVsActual':stats([r['bestPayoffRetainedVsActual'] for r in good]),
            'floorReserveForegoneVsActual':stats([r['floorReserveForegoneVsActual'] for r in good]),
            'payoffMinimumSafeBaseRate':sum(r['payoffMinimumSafeBase'] for r in good)/len(good) if good else None,
            'payoffMinimumLeavesAsymmetryRate':sum(r['payoffMinimumLeavesAsymmetry'] for r in good)/len(good) if good else None,
            'actualReachesOrCrossesBalanceRate':sum(r['actualReachesOrCrossesBalance'] for r in good)/len(good) if good else None,
            'actualCrossesBalanceRate':sum(r['actualCrossesBalance'] for r in good)/len(good) if good else None,
        },
        'rows':rows,
        'boundary':[
            'The counterfactual stops immediately after the package repair fill. It does not claim later V23 behavior would be unchanged.',
            'A floor=0 stopping point is a diagnostic lower bound for capital recovery, not a proposed final policy.',
            'Any deliberate surplus extension beyond this point requires a separate directional-value authority.'
        ]
    }
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':result['coverage'],'audit':audit,'summary':result['summary']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
