from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict
from pathlib import Path
import numpy as np
from scipy.stats import spearmanr

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_SURPLUS_TRANSFER_EFFICIENCY_V1.json'
EPS=1e-9

def pct(a,p):
    a=sorted(x for x in a if x is not None and math.isfinite(x))
    if not a:return None
    z=(len(a)-1)*p;lo=int(math.floor(z));hi=int(math.ceil(z));w=z-lo
    return a[lo] if lo==hi else a[lo]*(1-w)+a[hi]*w

def stats(a):
    a=[x for x in a if x is not None and math.isfinite(x)]
    return {'n':len(a),'mean':sum(a)/len(a) if a else None,'median':pct(a,.5),'p25':pct(a,.25),'p75':pct(a,.75),'p90':pct(a,.9)}

def corr(x,y):
    pairs=[(a,b) for a,b in zip(x,y) if a is not None and b is not None and math.isfinite(a) and math.isfinite(b)]
    if len(pairs)<3:return {'n':len(pairs),'spearman':None,'p':None}
    a,b=zip(*pairs);r,p=spearmanr(a,b);return {'n':len(pairs),'spearman':float(r),'p':float(p)}

def summarize(rows):
    cross=[r for r in rows if r['cross']]; non=[r for r in rows if not r['cross']]
    bins=[('LE025',lambda q:q<=.25),('025_050',lambda q:.25<q<=.5),('050_075',lambda q:.5<q<=.75),('GT075',lambda q:q>.75)]
    pricebins={}
    for name,f in bins:
        g=[r for r in rows if f(r['price'])]; pricebins[name]={'n':len(g),'crossRate':sum(r['cross'] for r in g)/len(g) if g else None}
    safe=[r for r in cross if r['safeExcessCap'] is not None and r['safeExcessCap']>EPS]
    return {
        'n':len(rows),'crossRate':len(cross)/len(rows) if rows else None,
        'priceBins':pricebins,
        'crossPrice':stats([r['price'] for r in cross]),'nonCrossPrice':stats([r['price'] for r in non]),
        'crossTransferEfficiency':stats([r['eff'] for r in cross]),'nonCrossTransferEfficiency':stats([r['eff'] for r in non]),
        'crossExcessQty':stats([r['excess'] for r in cross]),'crossExcessToGap':stats([r['excess']/r['gap'] for r in cross if r['gap']>EPS]),
        'corrEfficiencyExcessQty':corr([r['eff'] for r in cross],[r['excess'] for r in cross]),
        'corrEfficiencyExcessToGap':corr([r['eff'] for r in cross],[r['excess']/r['gap'] if r['gap']>EPS else None for r in cross]),
        'safeBudgetCross':{
            'n':len(safe),
            'insideNonLossRegionRate':sum(r['excess']<=r['safeExcessCap']+EPS for r in safe)/len(safe) if safe else None,
            'excessToSafeCap':stats([r['excess']/r['safeExcessCap'] for r in safe]),
            'floorAtBalance':stats([r['floorAtBalance'] for r in safe]),
        }
    }

def main():
    con=sqlite3.connect(DB)
    evs=defaultdict(list)
    for a,m,role,side,t,p,q,pid in con.execute('''SELECT asset,market_id,role,side,first_event_ms,average_price,shares,parent_id FROM target_parent_orders WHERE average_price IS NOT NULL AND shares>0 ORDER BY asset,market_id,first_event_ms,parent_id'''):
        evs[(a,int(m))].append((role,str(side).upper(),int(t),float(p),float(q),pid))
    con.close()
    mids=defaultdict(list)
    for a,m in evs:mids[a].append(m)
    split={}
    for a,ms in mids.items():
        xs=sorted(set(ms));n=len(xs)
        for i,m in enumerate(xs):split[(a,m)]='TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')
    rows=[]
    for (a,m),xs in evs.items():
        U=D=C=0.0
        for role,side,t,p,q,pid in xs:
            if abs(U-D)>EPS and 0<p<1:
                weak='UP' if U<D else 'DOWN'
                if side==weak:
                    strongShares=max(U,D);weakShares=min(U,D);gap=strongShares-weakShares
                    preStrongPayoff=strongShares-C;preWeakPayoff=weakShares-C
                    floorBal=preStrongPayoff-p*gap
                    excess=max(0.0,q-gap);cross=q>gap+EPS;eff=(1-p)/p
                    safeCap=max(0.0,floorBal/p) if floorBal>0 else None
                    rows.append({'asset':a,'marketId':m,'role':role,'split':split[(a,m)],'price':p,'qty':q,'gap':gap,'cross':cross,'excess':excess,'eff':eff,'preStrongPayoff':preStrongPayoff,'preWeakPayoff':preWeakPayoff,'floorAtBalance':floorBal,'safeExcessCap':safeCap})
            if side=='UP':U+=q
            else:D+=q
            C+=p*q
    out={'version':'TARGET_BTC_ETH_SURPLUS_TRANSFER_EFFICIENCY_V1','researchOnly':True,'actionAuthority':False,'groups':{},'coverage':{'weakSideRows':len(rows)}}
    for a in ('BTC','ETH'):
        for role in ('MAKER','TAKER'):
            g=[r for r in rows if r['asset']==a and r['role']==role]
            out['groups'][f'{a}_{role}']={'ALL':summarize(g),'TEST20':summarize([r for r in g if r['split']=='TEST20'])}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    brief={}
    for k,v in out['groups'].items():
        s=v['TEST20'];brief[k]={'n':s['n'],'crossRate':s['crossRate'],'priceBins':s['priceBins'],'crossPriceMed':s['crossPrice']['median'],'nonCrossPriceMed':s['nonCrossPrice']['median'],'crossEffMed':s['crossTransferEfficiency']['median'],'nonCrossEffMed':s['nonCrossTransferEfficiency']['median'],'rhoEffExcessGap':s['corrEfficiencyExcessToGap'],'safeBudget':s['safeBudgetCross']}
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'test20':brief},indent=2))
if __name__=='__main__':main()
