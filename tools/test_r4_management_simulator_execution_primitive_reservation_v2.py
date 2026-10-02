from __future__ import annotations
import json,math,statistics,sys
from pathlib import Path
from collections import defaultdict,Counter
ROOT=Path(__file__).resolve().parents[1]
P=ROOT/'data/research/r4_v0/p0_provenance_v1'
DEV=ROOT/'data/research/lan_worker_returns/r4-execv2-dev21/dev21.json'
VAL=ROOT/'data/research/lan_worker_returns/r4-execv2-val23/val23.json'
OUT=P/'r4_management_simulator_execution_primitive_reservation_v2.json'
RATE_TARGETS=['anyFill5s','completed5s','cancelRequest5s']

def bucket(r):
    # Frozen V1 bucket definition for direct comparability.
    return (round(float(r['requestedQty'])/6),round(float(r['price'])*5),int(float(r['secondsLeft'])//30),min(2,int(r['ownerCount'])),min(2,int(float(r['weakResponsibilityCount']))),min(2,int(float(r['events15s'])//2)))

def backoffs(b):
    yield b
    yield b[:4]+(None,None)
    yield (b[0],b[1],None,b[3],None,None)
    yield (b[0],None,None,b[3],None,None)
    yield tuple([None]*len(b))

def build(rows):
    tab=defaultdict(list)
    for r in rows:
        b=bucket(r)
        for k in backoffs(b):tab[k].append(r)
    return tab

def mean(xs):return sum(xs)/len(xs) if xs else None

def predict(tab,r):
    for k in backoffs(bucket(r)):
        xs=tab.get(k) or []
        if len(xs)>=3 or k==(None,)*6:
            out={t:mean([float(x[t]) for x in xs]) for t in RATE_TARGETS}
            out['fillQty5s']=mean([float(x['fillQty5s']) for x in xs])
            pos=[x for x in xs if float(x.get('fillQty5s') or 0)>1e-9 and float(x.get('unresolvedQty') or 0)>1e-9]
            out['positiveFillFraction']=mean([min(1.0,float(x['fillQty5s'])/float(x['unresolvedQty'])) for x in pos]) if pos else 1.0
            tt=[float(x['timeToFirstFillMs']) for x in xs if x.get('timeToFirstFillMs') is not None]
            out['timeToFirstFillMs']=statistics.median(tt) if tt else None
            out['support']=len(xs);out['bucket']=str(k);return out
    return None

def dist(rows):
    pos=[r for r in rows if float(r.get('fillQty5s') or 0)>1e-9]
    partial=[r for r in pos if float(r.get('fillQty5s') or 0)<float(r.get('unresolvedQty') or 0)-1e-9]
    full=[r for r in pos if float(r.get('fillQty5s') or 0)>=float(r.get('unresolvedQty') or 0)-1e-9]
    fr=[float(r['fillQty5s'])/max(1e-9,float(r['unresolvedQty'])) for r in pos]
    return {'roots':len(rows),'positiveFillRoots':len(pos),'partialPositiveRoots':len(partial),'fullPositiveRoots':len(full),'positiveFillRate':len(pos)/len(rows) if rows else 0.0,'medianPositiveFillFraction':statistics.median(fr) if fr else None,'meanPositiveFillFraction':mean(fr) if fr else None}

def main():
    dev=json.loads(DEV.read_text(encoding='utf-8'));val=json.loads(VAL.read_text(encoding='utf-8'));dr=dev.get('rows',[]);vr=val.get('rows',[])
    tab=build(dr);pred=[predict(tab,r) for r in vr]
    if any(x is None for x in pred):raise SystemExit('PREDICTION_FALLBACK_FAILED')
    rates={}
    for t in RATE_TARGETS:
        a=mean([float(r[t]) for r in vr]);p=mean([float(x[t]) for x in pred]);rates[t]={'actual':a,'predicted':p,'absError':abs(p-a),'pass':abs(p-a)<=.12}
    actual_qty=[float(r['fillQty5s']) for r in vr];pred_qty=[float(x['fillQty5s']) for x in pred];scale=max(1.,mean([abs(x) for x in actual_qty]));qmae=mean([abs(a-b) for a,b in zip(actual_qty,pred_qty)])/scale
    ats=[float(r['timeToFirstFillMs']) for r in vr if r.get('timeToFirstFillMs') is not None];pts=[float(p['timeToFirstFillMs']) for r,p in zip(vr,pred) if r.get('timeToFirstFillMs') is not None and p.get('timeToFirstFillMs') is not None]
    if ats and pts:am=statistics.median(ats);pm=statistics.median(pts);terr=abs(pm-am)/max(1.,am)
    else:am=pm=None;terr=math.inf
    pospairs=[(r,p) for r,p in zip(vr,pred) if float(r.get('fillQty5s') or 0)>1e-9 and float(r.get('unresolvedQty') or 0)>1e-9]
    cmae=mean([abs(min(1.0,float(r['fillQty5s'])/float(r['unresolvedQty']))-float(p['positiveFillFraction'])) for r,p in pospairs]) if pospairs else None
    expected_qty=[float(p['anyFill5s'])*float(p['positiveFillFraction'])*float(r['unresolvedQty']) for r,p in zip(vr,pred)]
    eqmae=mean([abs(a-b) for a,b in zip(actual_qty,expected_qty)])/scale
    exact_all=all(bool(x.get('executionCoreExact')) for x in dev.get('markets',[])+val.get('markets',[]))
    over=sum(1 for x in dr+vr if float(x.get('overfill5s') or 0)>1e-9)
    dup=int(dev.get('duplicateExecutionCredit') or 0)+int(val.get('duplicateExecutionCredit') or 0)
    structural={'executionCoreExactAll':exact_all,'overfillRoots':over,'duplicateExecutionCredit':dup,'pass':exact_all and over==0 and dup==0}
    legacy_pass=all(x['pass'] for x in rates.values()) and qmae<=.35 and terr<=.35 and structural['pass']
    rep={'version':'R4_MANAGEMENT_SIMULATOR_EXECUTION_PRIMITIVE_RESERVATION_V2','researchOnly':True,'developmentMarkets':len(dev.get('markets',[])),'validationMarkets':len(val.get('markets',[])),'developmentRoots':len(dr),'validationRoots':len(vr),'structuralAudit':structural,'developmentDistribution':dist(dr),'validationDistribution':dist(vr),'rateMetrics':rates,'legacyFillQtyNormalizedMAE':qmae,'expectedFillQtyNormalizedMAEDiagnostic':eqmae,'conditionalPositiveFillFractionMAE':cmae,'timeToFirstFillMedianActualMs':am,'timeToFirstFillMedianPredMs':pm,'timeToFirstFillMedianRelativeError':terr,'fallbackBuckets':dict(Counter(x['bucket'] for x in pred)),'legacyGatePass':legacy_pass,'interpretation':'Frozen V1 hierarchical empirical execution kernel rerun on pending-submit-reservation-correct Fresh21 -> Unseen23. Expected-quantity and conditional-positive-size metrics are diagnostics only; validation was not used to fit.'}
    OUT.write_text(json.dumps(rep,indent=2),encoding='utf-8');print(json.dumps(rep,indent=2))
if __name__=='__main__':main()
