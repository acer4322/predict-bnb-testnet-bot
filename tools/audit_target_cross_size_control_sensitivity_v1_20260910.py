"""Secondary descriptive checks: fresh observed order identities, and weak-side
next-fill association. These are NOT causal tests or identified Target decisions.
Runs only compact data postprocess, no HFT or model fit.
"""
from pathlib import Path
from collections import defaultdict,Counter
import gzip,json,hashlib,statistics,math
from tools.audit_target_cross_size_control_v1_20260910 import BASE,PREFIX,INPUTS,EPS,describe,overlap_comparison


def mean(x):return statistics.mean(x) if x else None


def rate(rows,key):
    by=defaultdict(list)
    for r in rows:
        if r.get(key) is not None:by[r['marketId']].append(float(r[key]))
    return dict(events=sum(len(v) for v in by.values()),markets=len(by),
                equalMarketRate=mean([mean(v) for v in by.values()]))


def group_features(r):
    p=r['observedYesPrice'];pb=0 if p<.3 else 1 if p<.5 else 2 if p<.7 else 3
    return (r['phase'],pb)


def within_risk_comparison(rows):
    sets=[]
    for high in (False,True):
        rs=[r for r in rows if r['commonPriceDomain'] and r['normalizedAbsNet'] is not None
            and r['postNet']!=0 and r['nextBuysWeak'] is not None and (r['normalizedAbsNet']>=.5)==high]
        cnt=Counter(r['marketId'] for r in rs);cells={}
        for r in rs:
            k=group_features(r);w=1/cnt[r['marketId']]/len(cnt)
            c=cells.setdefault(k,dict(mass=0.,total=0.,markets=set(),events=0));c['mass']+=w
            c['total']+=w*r['nextBuysWeak'];c['markets'].add(r['marketId']);c['events']+=1
        sets.append(dict(rows=rs,cells=cells))
    low,high=sets;keys=[k for k in low['cells'] if k in high['cells'] and len(low['cells'][k]['markets'])>=3 and len(high['cells'][k]['markets'])>=3]
    ws={k:min(low['cells'][k]['mass'],high['cells'][k]['mass']) for k in keys};mass=sum(ws.values())
    def val(g):return sum(ws[k]*g['cells'][k]['total']/g['cells'][k]['mass'] for k in keys)/mass if mass else None
    return dict(commonCells=len(keys),overlapMass=mass,lowEvents=len(low['rows']),highEvents=len(high['rows']),
        nextWeakRateLowerImbalance=val(low),nextWeakRateHigherImbalance=val(high),
        highMinusLow=val(high)-val(low) if mass else None,
        interpretation='association after phase and last-executed-price coarse standardization; .5 is a descriptive cut, not policy threshold or causal intervention')


def main():
    out=BASE/'TARGET_CROSS_SIZE_CONTROL_SENSITIVITY_V1_20260910.json'
    if out.exists():raise FileExistsError(str(out))
    mainpath=BASE/(PREFIX+'_SCORE.json');d=json.loads(mainpath.read_text(encoding='utf-8-sig'))
    assert hashlib.sha256(mainpath.read_bytes()).hexdigest()=='406d2d6d968d190988d27675da5054dece86903cf481b5c673569d5cf627aaed'
    rp=Path(d['eventStates']['path']);assert rp.stat().st_size<8*1024**2
    assert hashlib.sha256(rp.read_bytes()).hexdigest()==d['eventStates']['sha256']
    rows=[];expanded=0
    with gzip.open(rp,'rt',encoding='utf-8') as f:
        for line in f:
            expanded+=len(line.encode());assert expanded<32*1024**2;rows.append(json.loads(line))
    assert len(rows)==d['eventBatches'];per=defaultdict(list)
    for r in rows:per[(r['asset'],r['regime'],r['marketId'])].append(r)
    events=defaultdict(list)
    for regime,name,wanted in INPUTS:
        h=hashlib.sha256()
        with (BASE/name).open('rb') as f:
            for line in f:
                h.update(line);e=json.loads(line);events[(e['asset'],regime,e['marketId'])].append(e)
        assert h.hexdigest()==wanted
    for key,bs in per.items():
        ev=defaultdict(list)
        for e in events[key]:ev[e['t']].append(e)
        seen=set()
        for r in bs:
            es=ev[r['t']];identities={(e.get('order') or e.get('order_id'),e['side'],e['quote'],e['role']) for e in es}
            r['allFirstObservedOrderBatch']=not bool(identities&seen)
            r['containsRepeatedOrder']=bool(identities&seen);seen|=identities
        for i,r in enumerate(bs):
            r['nextBuysWeak']=None;r['nextAllNewOrders']=None
            if i+1==len(bs):continue
            nxt=bs[i+1];r['nextAllNewOrders']=nxt['allFirstObservedOrderBatch']
            dg=nxt['postGross']-nxt['preGross'];dn=nxt['postNet']-nxt['preNet']
            up=(dg+dn)/2;down=(dg-dn)/2
            if nxt['buyOnly'] and abs(r['postNet'])>EPS and ((up>EPS and abs(down)<=EPS) or (down>EPS and abs(up)<=EPS)):
                r['nextBuysWeak']=(down>EPS if r['postNet']>0 else up>EPS)
    summary={};firstsets={}
    for a in ('BTC','ETH'):
        for regime in ('HISTORICAL','RECENT'):
            name=a+'_'+regime;rs=[r for r in rows if r['asset']==a and r['regime']==regime]
            mids=sorted({r['marketId'] for r in rs});new=[r for r in rs if r['allFirstObservedOrderBatch']]
            nextnew=[r for r in rs if r['nextAllNewOrders']]
            firstsets[name]=nextnew
            summary[name]=dict(events=len(rs),markets=len(mids),allFirstObservedOrdersEvents=len(new),
                firstObservedOrdersOnly=describe(new,mids),
                overallNextWeak=rate(rs,'nextBuysWeak'),
                nextWeakByRelativeImbalance=[dict(bin=[i*.25,(i+1)*.25],**rate([r for r in rs if r['normalizedAbsNet'] is not None and min(3,int(r['normalizedAbsNet']*4))==i and r['commonPriceDomain']],'nextBuysWeak')) for i in range(4)],
                adjustedHigherVsLowerImbalance=within_risk_comparison(rs),
                adjustedHigherVsLowerImbalanceNextNewOrder=within_risk_comparison(nextnew))
    comparisons=[overlap_comparison(firstsets[a+'_HISTORICAL'],firstsets[a+'_RECENT'],a+'_OLD_NEW_NEXT_FIRST_OBSERVED_ORDERS_ONLY') for a in ('BTC','ETH')]
    result=dict(version='TARGET_CROSS_SIZE_CONTROL_SENSITIVITY_V1',primaryScoreSha256=hashlib.sha256(mainpath.read_bytes()).hexdigest(),
       expandedStateBytes=expanded,summary=summary,newOrderOverlapComparisons=comparisons,newHFT=0,modelFits=0,
       limitations=['First observed fill per order is NOT original placement time or known full fill; exact original quantities remain unknown.',
          'Prefix accounting uses ALL fills; subset only changes scored events, not reconstructed inventory.',
          'Conditional weak-side buying is realized next-fill direction, not private intended direction or venue opportunity.',
          'Next opposite-side fills are not necessarily economic Repair; overcross can create opposite exposure.',
          'Posthoc sensitivity after main audit, fixed bins unchanged; no equivalence significance, CI or prediction promotion.'])
    out.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    brief={k:dict(events=v['events'],freshObserved=v['allFirstObservedOrdersEvents'],
       firstWeak=v['firstObservedOrdersOnly']['weakSide'],risk=v['adjustedHigherVsLowerImbalance'],
       riskNew=v['adjustedHigherVsLowerImbalanceNextNewOrder'],nextWeakBins=v['nextWeakByRelativeImbalance']) for k,v in summary.items()}
    print(json.dumps(dict(output=out.as_posix(),bytes=out.stat().st_size,sha256=hashlib.sha256(out.read_bytes()).hexdigest(),
       summary=brief,nextNewComparisons=[{k:v for k,v in x.items() if k!='cells'} for x in comparisons]),ensure_ascii=False))


if __name__=='__main__':main()
