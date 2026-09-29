from __future__ import annotations
import json, math
from pathlib import Path

SRC=Path('data/research/lan_worker_returns/mvps-pair1-strictpast-anatomy24-20260908-v1/result.json')
OUT=Path('data/research/r4_v0/p0_provenance_v1/MVPS_PAIR1_STRICTPAST_RUNTIME_SIGNAL_ROBUSTNESS24_V1_20260908.json')
CPS=[0.1,0.2,0.3,0.4]

def val(x,name):
    dom=x.get('dominantSide')
    if name=='makerHeadroom': return x.get('makerRepairHeadroom')
    if name=='activeHeadroom': return x.get('activeRepairHeadroom')
    if name=='signedBookSupport':
        if dom is None:return None
        imb=float(x.get('bookImbalance') or 0.0)
        return imb if dom=='UP' else -imb
    if name=='dominantBid':
        if dom=='UP':return x.get('upBid')
        if dom=='DOWN':return x.get('downBid')
        return None
    if name=='dominantMid':
        if dom=='UP':return (float(x['upBid'])+float(x['upAsk']))/2
        if dom=='DOWN':return (float(x['downBid'])+float(x['downAsk']))/2
        return None
    if name=='directionAgreement':
        return None if dom is None else (1.0 if x.get('selectedDirection')==dom else 0.0)
    if name=='absNet':return x.get('absNetInventory')
    if name=='gross':return x.get('grossInventory')
    if name=='absImbalance':return abs(float(x.get('bookImbalance') or 0.0))
    raise KeyError(name)

def auc(pos,neg):
    # pos = largeWin; higher score should indicate largeWin.
    pairs=0;score=0.0
    for a in pos:
        if a is None:continue
        for b in neg:
            if b is None:continue
            pairs+=1
            if a>b:score+=1
            elif a==b:score+=0.5
    return score/pairs if pairs else None

def med(xs):
    a=sorted(float(x) for x in xs if x is not None and math.isfinite(float(x)))
    if not a:return None
    n=len(a);return a[n//2] if n%2 else (a[n//2-1]+a[n//2])/2

d=json.loads(SRC.read_text(encoding='utf-8'));rows=d['rows']
labels={'largeWin':[r for r in rows if r['pnl']>=50],'largeLoss':[r for r in rows if r['pnl']<=-50],'positive':[r for r in rows if r['pnl']>0],'negative':[r for r in rows if r['pnl']<0]}
features=['makerHeadroom','activeHeadroom','signedBookSupport','dominantBid','dominantMid','directionAgreement','absNet','gross','absImbalance']
out={'version':'MVPS_PAIR1_STRICTPAST_RUNTIME_SIGNAL_ROBUSTNESS24_V1_20260908','researchOnly':True,'runtimeAuthority':False,'source':str(SRC),'features':{},'boundary':['postprocess only; no replay','all feature values strict-past checkpoint state','outcome labels post-hoc only','AUC is diagnostic ranking, no threshold/selector authority']}
for cp in CPS:
    k=str(cp);out['features'][k]={}
    for f in features:
        def vals(group):
            z=[]
            for r in labels[group]:
                x=next((q for q in r['checkpoints'] if abs(float(q['checkpoint'])-cp)<1e-9),None)
                if x:z.append(val(x,f))
            return z
        lw,ll,p,n=vals('largeWin'),vals('largeLoss'),vals('positive'),vals('negative')
        out['features'][k][f]={'largeWinMedian':med(lw),'largeLossMedian':med(ll),'largeWinVsLargeLossAuc':auc(lw,ll),'positiveMedian':med(p),'negativeMedian':med(n),'positiveVsNegativeAuc':auc(p,n),'nLargeWin':sum(x is not None for x in lw),'nLargeLoss':sum(x is not None for x in ll)}
# fixed headroom-recovery pattern at 0.2/0.3: no thresholds beyond sign zero.
def cpv(r,cp,f):
    x=next(q for q in r['checkpoints'] if abs(float(q['checkpoint'])-cp)<1e-9);return val(x,f)
def pattern(r,f):
    a=cpv(r,.2,f);b=cpv(r,.3,f)
    if a is None or b is None:return None
    return {'positiveAt20':a>0,'positiveAt30':b>0,'recoveredBy30':a<=0 and b>0,'persistentlyNonPositive20to30':a<=0 and b<=0}
out['headroomSignPatterns']={}
for f in ('makerHeadroom','activeHeadroom'):
    tab={}
    for g in ('largeWin','largeLoss','positive','negative'):
        ps=[pattern(r,f) for r in labels[g]];ps=[p for p in ps if p]
        tab[g]={'n':len(ps),'positiveAt20Rate':sum(p['positiveAt20'] for p in ps)/len(ps) if ps else None,'positiveAt30Rate':sum(p['positiveAt30'] for p in ps)/len(ps) if ps else None,'recoveredBy30Rate':sum(p['recoveredBy30'] for p in ps)/len(ps) if ps else None,'persistentlyNonPositive20to30Rate':sum(p['persistentlyNonPositive20to30'] for p in ps)/len(ps) if ps else None}
    out['headroomSignPatterns'][f]=tab
OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'ok':True,'out':str(OUT),'cp02':out['features']['0.2'],'cp03':out['features']['0.3'],'patterns':out['headroomSignPatterns']},ensure_ascii=False))
