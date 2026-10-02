from __future__ import annotations
import json, math, sqlite3
from collections import defaultdict
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/research/r4_v0/p0_provenance_v1/target_eth_btc_strategy_compare_snapshot_v1.db'
OUT=ROOT/'data/research/r4_v0/p0_provenance_v1/TARGET_BTC_ETH_SAFE_BASE_PAYOFF_ASYMMETRY_V1.json'
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

def summarize(rows):
    if not rows:return {'n':0}
    before=[r for r in rows if r['qtyVsGap']=='BEFORE_BALANCE']; at=[r for r in rows if r['qtyVsGap']=='AT_BALANCE']; cross=[r for r in rows if r['qtyVsGap']=='CROSS_BALANCE']
    return {
        'n':len(rows),'markets':len(set(r['marketId'] for r in rows)),
        'beforeBalanceRate':len(before)/len(rows),'atBalanceRate':len(at)/len(rows),'crossBalanceRate':len(cross)/len(rows),
        'asymmetricSafeRate':sum(r['asymmetricSafe'] for r in rows)/len(rows),
        'postAbsNet':stats([r['postAbsNet'] for r in rows]),'postAbsNetRatio':stats([r['postAbsNetRatio'] for r in rows]),
        'postFloor':stats([r['postFloor'] for r in rows]),'postBest':stats([r['postBest'] for r in rows]),
        'postBestMinusFloor':stats([r['postBest']-r['postFloor'] for r in rows]),
        'floorToBestRatio':stats([r['floorToBestRatio'] for r in rows]),
        'qtyToGap':stats([r['qtyToGap'] for r in rows]),
        'beforeBalance':{
            'n':len(before),'postFloor':stats([r['postFloor'] for r in before]),'postBest':stats([r['postBest'] for r in before]),
            'postAbsNet':stats([r['postAbsNet'] for r in before]),'floorToBestRatio':stats([r['floorToBestRatio'] for r in before])},
        'crossBalance':{
            'n':len(cross),'postFloor':stats([r['postFloor'] for r in cross]),'postBest':stats([r['postBest'] for r in cross]),
            'postAbsNet':stats([r['postAbsNet'] for r in cross]),'floorToBestRatio':stats([r['floorToBestRatio'] for r in cross])}
    }

def main():
    con=sqlite3.connect(DB)
    evs=defaultdict(list)
    for a,m,role,side,t,p,q,pid in con.execute('''select asset,market_id,role,side,first_event_ms,average_price,shares,parent_id from target_parent_orders where average_price is not null and shares>0 order by asset,market_id,first_event_ms,parent_id'''):
        evs[(a,int(m))].append((str(role),str(side).upper(),int(t),float(p),float(q),pid))
    con.close()
    mids=defaultdict(list)
    for a,m in evs:mids[a].append(m)
    split={}
    for a,ms in mids.items():
        xs=sorted(set(ms));n=len(xs)
        for i,m in enumerate(xs):split[(a,m)]='TRAIN60' if (i+1)/n<=.6 else ('VALID20' if (i+1)/n<=.8 else 'TEST20')
    rows=[]
    for (a,m),xs in evs.items():
        U=D=C=0.0; seen_safe=False
        for role,side,t,p,q,pid in xs:
            preU,preD,preC=U,D,C; preFloor=min(U-C,D-C); preGap=abs(U-D)
            if side=='UP':U+=q
            else:D+=q
            C+=p*q
            postFloor=min(U-C,D-C);postBest=max(U-C,D-C);postGap=abs(U-D);gross=U+D
            if preFloor < -EPS and postFloor >= -EPS:
                if preGap<=EPS: cls='AT_BALANCE';ratio=None
                else:
                    ratio=q/preGap
                    cls='BEFORE_BALANCE' if q < preGap-EPS else ('CROSS_BALANCE' if q>preGap+EPS else 'AT_BALANCE')
                fbr=postFloor/postBest if postBest>EPS else None
                rows.append({'asset':a,'marketId':m,'role':role,'split':split[(a,m)],'t':t,'side':side,'price':p,'qty':q,
                             'preFloor':preFloor,'preGap':preGap,'qtyToGap':ratio,'qtyVsGap':cls,
                             'postFloor':postFloor,'postBest':postBest,'postAbsNet':postGap,'postAbsNetRatio':postGap/gross if gross>EPS else 0,
                             'floorToBestRatio':fbr,'asymmetricSafe':postBest>postFloor+EPS,'firstSafeReentryInMarket':not seen_safe})
                seen_safe=True
    out={'version':'TARGET_BTC_ETH_SAFE_BASE_PAYOFF_ASYMMETRY_V1','researchOnly':True,'actionAuthority':False,'coverage':{'events':len(rows),'markets':len(set((r['asset'],r['marketId']) for r in rows))},'groups':{}}
    for a in ('BTC','ETH'):
        for role in ('MAKER','TAKER'):
            g=[r for r in rows if r['asset']==a and r['role']==role]
            out['groups'][f'{a}_{role}']={'ALL':summarize(g),'TEST20':summarize([r for r in g if r['split']=='TEST20']),
                                         'FIRST_PER_MARKET_ALL':summarize([r for r in g if r['firstSafeReentryInMarket']]),
                                         'FIRST_PER_MARKET_TEST20':summarize([r for r in g if r['firstSafeReentryInMarket'] and r['split']=='TEST20'])}
    OUT.write_text(json.dumps(out,indent=2),encoding='utf-8')
    brief={}
    for k,v in out['groups'].items():
        s=v['TEST20'];f=v['FIRST_PER_MARKET_TEST20'];brief[k]={'test20':{x:s.get(x) for x in ['n','markets','beforeBalanceRate','atBalanceRate','crossBalanceRate','asymmetricSafeRate']},
        'postFloorMed':s.get('postFloor',{}).get('median'),'postBestMed':s.get('postBest',{}).get('median'),'postAbsNetMed':s.get('postAbsNet',{}).get('median'),'floorToBestMed':s.get('floorToBestRatio',{}).get('median'),
        'firstTest20':{x:f.get(x) for x in ['n','markets','beforeBalanceRate','atBalanceRate','crossBalanceRate','asymmetricSafeRate']},'firstFloorToBestMed':f.get('floorToBestRatio',{}).get('median')}
    print(json.dumps({'out':str(OUT.relative_to(ROOT)),'coverage':out['coverage'],'brief':brief},indent=2))
if __name__=='__main__':main()
