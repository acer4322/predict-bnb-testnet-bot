from __future__ import annotations
import bisect, json, math, sqlite3
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_FAVORABLE_CROSS_GAP_PAYOFF_CYCLE_V1.json'
EPS=1e-9

def pct(a,p):
    a=sorted(x for x in a if x is not None and math.isfinite(x))
    if not a:return None
    z=(len(a)-1)*p; lo=int(math.floor(z)); hi=int(math.ceil(z))
    if lo==hi:return a[lo]
    w=z-lo; return a[lo]*(1-w)+a[hi]*w

def stats(a):
    a=[x for x in a if x is not None and math.isfinite(x)]
    return {'n':len(a),'mean':sum(a)/len(a) if a else None,'median':pct(a,.5),'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9)}

def rate(rows, pred):
    return sum(1 for r in rows if pred(r))/len(rows) if rows else None

def rr(a,b):
    return a/b if a is not None and b not in (None,0) else None

def future_summary(rows, fav_key):
    fav=[r for r in rows if r.get(fav_key) is True]
    unf=[r for r in rows if r.get(fav_key) is False]
    def one(g):
        return {
            'n':len(g),
            'floorImprove10sRate':rate(g,lambda r:r['floor10']>r['postFloor']+EPS),
            'floorImprove30sRate':rate(g,lambda r:r['floor30']>r['postFloor']+EPS),
            'floorNonnegative10sRate':rate(g,lambda r:r['floor10']>=-EPS),
            'floorNonnegative30sRate':rate(g,lambda r:r['floor30']>=-EPS),
            'bestPositive10sRate':rate(g,lambda r:r['best10']>0),
            'bestPositive30sRate':rate(g,lambda r:r['best30']>0),
            'floorDelta10s':stats([r['floor10']-r['postFloor'] for r in g]),
            'floorDelta30s':stats([r['floor30']-r['postFloor'] for r in g]),
            'bestDelta10s':stats([r['best10']-r['postBest'] for r in g]),
            'bestDelta30s':stats([r['best30']-r['postBest'] for r in g]),
        }
    return {'FAVORABLE':one(fav),'UNFAVORABLE':one(unf)}

def summarize(rows):
    fav_avg=[r for r in rows if r['avgPairEdge']>EPS]
    unf_avg=[r for r in rows if r['avgPairEdge']<=EPS]
    last_known=[r for r in rows if r['lastPairEdge'] is not None]
    fav_last=[r for r in last_known if r['lastPairEdge']>EPS]
    unf_last=[r for r in last_known if r['lastPairEdge']<=EPS]
    cross=[r for r in rows if r['crossesBalance']]
    non=[r for r in rows if not r['crossesBalance']]
    ca=rate(fav_avg,lambda r:r['crossesBalance']); ua=rate(unf_avg,lambda r:r['crossesBalance'])
    cl=rate(fav_last,lambda r:r['crossesBalance']); ul=rate(unf_last,lambda r:r['crossesBalance'])
    cross_avg_fav=[r for r in cross if r['avgPairEdge']>EPS]
    cross_last_known=[r for r in cross if r['lastPairEdge'] is not None]
    return {
        'n':len(rows),'crossRate':rate(rows,lambda r:r['crossesBalance']),
        'avgCostProxy':{
            'favorableN':len(fav_avg),'unfavorableN':len(unf_avg),
            'crossRateFavorable':ca,'crossRateUnfavorable':ua,'crossRateRiskRatio':rr(ca,ua),
            'pairEdgeCross':stats([r['avgPairEdge'] for r in cross]),
            'pairEdgeNonCross':stats([r['avgPairEdge'] for r in non]),
            'crossFuture':future_summary(cross,'avgFavorable')
        },
        'lastPriceProxy':{
            'knownN':len(last_known),'favorableN':len(fav_last),'unfavorableN':len(unf_last),
            'crossRateFavorable':cl,'crossRateUnfavorable':ul,'crossRateRiskRatio':rr(cl,ul),
            'pairEdgeCross':stats([r['lastPairEdge'] for r in cross_last_known]),
            'pairEdgeNonCross':stats([r['lastPairEdge'] for r in non if r['lastPairEdge'] is not None]),
            'crossFuture':future_summary(cross_last_known,'lastFavorable')
        },
        'crossQtyExcess':stats([r['qty']-r['gap'] for r in cross]),
        'crossQtyToGapRatio':stats([r['qty']/r['gap'] for r in cross if r['gap']>EPS]),
    }

def main():
    con=sqlite3.connect(DB)
    cur=con.execute('''SELECT asset,market_id,role,side,first_event_ms,average_price,shares,parent_id
                       FROM target_parent_orders
                       WHERE average_price IS NOT NULL AND shares IS NOT NULL AND shares>0
                       ORDER BY asset,market_id,first_event_ms,parent_id''')
    mk=defaultdict(list)
    for asset,mid,role,side,t,p,q,pid in cur:
        mk[(asset,int(mid))].append({'asset':asset,'marketId':int(mid),'role':role,'side':side.upper(),'t':int(t),'price':float(p),'qty':float(q),'parentId':pid})
    con.close()
    mids=defaultdict(list)
    for a,m in mk:mids[a].append(m)
    split={}
    for a,x in mids.items():
        x=sorted(set(x)); n=len(x)
        for i,m in enumerate(x):
            f=(i+1)/n; split[(a,m)]='TRAIN60' if f<=.6 else ('VALID20' if f<=.8 else 'TEST20')
    rows=[]
    for (asset,mid),evs in mk.items():
        U=D=CU=CD=0.0; lastU=lastD=None
        state_after=[]; times=[]; weak_candidates=[]
        for idx,e in enumerate(evs):
            C=CU+CD; preU,preD=U,D
            if abs(U-D)>EPS:
                weakSide='UP' if U<D else 'DOWN'; strongSide='DOWN' if weakSide=='UP' else 'UP'
                if e['side']==weakSide:
                    strongShares=D if strongSide=='DOWN' else U
                    strongCost=CD if strongSide=='DOWN' else CU
                    strongAvg=strongCost/strongShares if strongShares>EPS else None
                    strongLast=lastD if strongSide=='DOWN' else lastU
                    gap=abs(U-D); avgEdge=1-(e['price']+strongAvg) if strongAvg is not None else None
                    lastEdge=1-(e['price']+strongLast) if strongLast is not None else None
                    weak_candidates.append({'idx':idx,**e,'split':split[(asset,mid)],'gap':gap,
                        'preFloor':min(U-C,D-C),'preBest':max(U-C,D-C),
                        'strongAvgCost':strongAvg,'strongLastPrice':strongLast,
                        'avgPairEdge':avgEdge,'lastPairEdge':lastEdge,
                        'avgFavorable':avgEdge is not None and avgEdge>EPS,
                        'lastFavorable':None if lastEdge is None else lastEdge>EPS,
                        'crossesBalance':e['qty']>gap+EPS})
            # apply action
            if e['side']=='UP': U+=e['qty']; CU+=e['price']*e['qty']; lastU=e['price']
            else: D+=e['qty']; CD+=e['price']*e['qty']; lastD=e['price']
            C=CU+CD
            state_after.append((U,D,C,min(U-C,D-C),max(U-C,D-C)))
            times.append(e['t'])
        for r in weak_candidates:
            i=r['idx']; U1,D1,C1,pf,pb=state_after[i]
            r['postFloor']=pf; r['postBest']=pb
            for h,name in ((10000,'10'),(30000,'30')):
                j=bisect.bisect_right(times,r['t']+h)-1
                if j<i:j=i
                st=state_after[j]; r['floor'+name]=st[3]; r['best'+name]=st[4]
            rows.append(r)
    out={'version':'TARGET_BTC_ETH_FAVORABLE_CROSS_GAP_PAYOFF_CYCLE_V1','researchOnly':True,'actionAuthority':False,
         'coverage':{'weakSideParents':len(rows)},'groups':{}}
    for asset in ('BTC','ETH'):
        for role in ('MAKER','TAKER'):
            k=f'{asset}_{role}'; g=[r for r in rows if r['asset']==asset and r['role']==role and r['avgPairEdge'] is not None]
            out['groups'][k]={'ALL':summarize(g),'TEST20':summarize([r for r in g if r['split']=='TEST20'])}
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
    brief={}
    for k,v in out['groups'].items():
        s=v['TEST20']; a=s['avgCostProxy']; l=s['lastPriceProxy']
        brief[k]={'n':s['n'],'crossRate':s['crossRate'],
                  'avgEdge_crossFav':a['crossRateFavorable'],'avgEdge_crossUnfav':a['crossRateUnfavorable'],'avgEdge_RR':a['crossRateRiskRatio'],
                  'avgEdge_medianCross':a['pairEdgeCross']['median'],'avgEdge_medianNon':a['pairEdgeNonCross']['median'],
                  'lastEdge_crossFav':l['crossRateFavorable'],'lastEdge_crossUnfav':l['crossRateUnfavorable'],'lastEdge_RR':l['crossRateRiskRatio'],
                  'crossFav_floorDelta10':a['crossFuture']['FAVORABLE']['floorDelta10s']['median'],
                  'crossUnfav_floorDelta10':a['crossFuture']['UNFAVORABLE']['floorDelta10s']['median'],
                  'crossFav_floorDelta30':a['crossFuture']['FAVORABLE']['floorDelta30s']['median'],
                  'crossUnfav_floorDelta30':a['crossFuture']['UNFAVORABLE']['floorDelta30s']['median'],
                  'crossFav_bestPos30':a['crossFuture']['FAVORABLE']['bestPositive30sRate'],
                  'crossUnfav_bestPos30':a['crossFuture']['UNFAVORABLE']['bestPositive30sRate']}
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'test20':brief},indent=2))
if __name__=='__main__':main()
